"""The devices one client runs, with their drivers and locks: one copy for orca-client and a check run."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from cheshire_drivers.command_executor.advertise import connect_info, interfaces_of, release_driver
from cheshire_drivers.command_executor.answer import read_link
from cheshire_drivers.driver_hooks import refuse_unless_it_runs_hooks
from cheshire_drivers.gateway_protocol import (
    AssignedDevice,
    DeviceConnectInfo,
    DeviceLinkInfo,
    DeviceStatusInfo,
    DriverMode,
    EffectiveMode,
)
from cheshire_drivers.interfaces import BaseDriver, ITransporterDriver

logger = logging.getLogger("cheshire_drivers.command_executor")

LIVE_AND_SIM: Mapping[EffectiveMode, DriverMode] = MappingProxyType({"LIVE": "LIVE", "SIM": "SIM"})
"""A lab PC's orca-client: a LIVE command runs on the real driver and a SIM command on the simulator."""


@dataclass(frozen=True)
class DeviceDrivers:
    """The drivers built for one device. `live` is None for a device with no real driver."""

    live: BaseDriver | ITransporterDriver | None
    sim: BaseDriver | ITransporterDriver | None


class DeviceRegistry:
    """Each device's live driver, simulator and one lock, which both share. `runs_on` maps a mode to its driver."""

    def __init__(self, runs_on: Mapping[EffectiveMode, DriverMode] = LIVE_AND_SIM) -> None:
        self._runs_on = runs_on
        self._drivers: dict[str, BaseDriver | ITransporterDriver] = {}
        self._sim_drivers: dict[str, BaseDriver | ITransporterDriver] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._device_types: dict[str, str] = {}
        self._assigned: dict[str, AssignedDevice] = {}
        self._applying = asyncio.Lock()

    async def apply(
        self, devices: Sequence[AssignedDevice], build: Callable[[AssignedDevice], Awaitable[DeviceDrivers]],
    ) -> dict[str, str]:
        """Run exactly these devices, and return each build error by device name. Devices are not initialized here.

        One assignment is applied at a time, and a device stays listed as assigned until its new drivers are held,
        so a cancelled apply leaves nothing held that a later one cannot release.
        """
        async with self._applying:
            wanted = {device.name: device for device in devices}
            for name in [held for held in self._device_types if held not in wanted]:
                await self.remove(name)
                self._assigned.pop(name, None)

            errors: dict[str, str] = {}
            for name, device in wanted.items():
                if name in self._device_types and self._assigned.get(name) == device:
                    continue
                try:
                    if name in self._locks:
                        await self.replace(name, lambda device=device: self._build_and_register(device, build))
                    else:
                        await self._build_and_register(device, build)
                except Exception as refused:
                    self._assigned.pop(name, None)
                    logger.error("Could not build device %r: %s", name, refused, exc_info=True)
                    errors[name] = str(refused)
                    continue
                self._assigned[name] = device
                logger.info("  • %s (%s)", name, device.kind)
            return errors

    async def _build_and_register(
        self, device: AssignedDevice, build: Callable[[AssignedDevice], Awaitable[DeviceDrivers]],
    ) -> None:
        drivers = await build(device)
        try:
            self.register(name=device.name, device_type=device.kind, driver=drivers.live, sim_driver=drivers.sim)
        except BaseException:
            for built in (drivers.live, drivers.sim):
                if built is not None:
                    await release_driver(device.name, built)
            raise

    def register(
        self,
        name: str,
        device_type: str,
        driver: BaseDriver | ITransporterDriver | None = None,
        sim_driver: BaseDriver | ITransporterDriver | None = None,
    ) -> None:
        """Hold a device's drivers. A driver that does not run the command hook is refused."""
        for held in (driver, sim_driver):
            if held is not None:
                refuse_unless_it_runs_hooks(name, held)
        # A replaced device keeps its lock, so a command queued on it runs on the new drivers.
        self._locks.setdefault(name, asyncio.Lock())
        self._device_types[name] = device_type

        if driver is not None:
            self._drivers[name] = driver
        if sim_driver is not None:
            self._sim_drivers[name] = sim_driver

        advertised = self._advertised_driver(name)
        if advertised is not None and not interfaces_of(advertised):
            logger.error(
                "Driver class %s for device %r declares no `interfaces` ClassVar (or it is empty). It will not be "
                "advertised, so commands sent to it fail capability validation on the server. Declare "
                "`interfaces: ClassVar[frozenset[str]] = frozenset({...})` on the driver class.",
                type(advertised).__name__,
                name,
            )

        logger.debug("Registered device: %s (%s)", name, device_type)

    async def remove(self, device_id: str) -> None:
        """Release a device's drivers and forget it, once any command running on it has finished."""
        lock = self._locks.get(device_id)
        if lock is not None:
            async with lock:
                await self._release_drivers(device_id)
        self._forget(device_id)

    async def replace(self, device_id: str, register_new: Callable[[], Awaitable[None]]) -> None:
        """Release a device's drivers and register new ones under its lock. A failed register leaves it removed."""
        async with self._locks[device_id]:
            await self._release_drivers(device_id)
            try:
                await register_new()
            except BaseException:
                self._forget(device_id)
                raise

    def _forget(self, device_id: str) -> None:
        self._locks.pop(device_id, None)
        self._device_types.pop(device_id, None)

    async def _release_drivers(self, device_id: str) -> None:
        for held in (self._drivers.pop(device_id, None), self._sim_drivers.pop(device_id, None)):
            if held is not None:
                await release_driver(device_id, held)

    def get_driver(self, device_id: str) -> BaseDriver | ITransporterDriver | None:
        """The device's live driver, or None when it has none."""
        return self._drivers.get(device_id)

    def get_sim_driver(self, device_id: str) -> BaseDriver | ITransporterDriver | None:
        """The device's simulator, or None when it has none."""
        return self._sim_drivers.get(device_id)

    def driver_for(self, device_name: str, effective_mode: EffectiveMode) -> BaseDriver | ITransporterDriver | None:
        """The driver a command in this mode runs on, or None when this registry does not run that mode."""
        runs_on = self._runs_on.get(effective_mode)
        if runs_on == "SIM":
            return self.get_sim_driver(device_name)
        if runs_on == "LIVE":
            return self.get_driver(device_name)
        return None

    def lock_for(self, device_name: str) -> asyncio.Lock | None:
        """The device's one dispatch lock, or None when the device is not held."""
        return self._locks.get(device_name)

    def is_busy(self, device_id: str) -> bool:
        """True while a command holds this device's dispatch lock."""
        lock = self.lock_for(device_id)
        return lock is not None and lock.locked()

    def get_all_device_ids(self) -> list[str]:
        return list(self._device_types.keys())

    def connect_info_list(self) -> list[DeviceConnectInfo]:
        """What the server is told about each device held."""
        return [
            connect_info(device_id, self._device_types[device_id], driver)
            for device_id, driver in self._advertised_devices()
        ]

    def _advertised_driver(self, device_id: str) -> BaseDriver | ITransporterDriver | None:
        return self._drivers.get(device_id) or self._sim_drivers.get(device_id)

    def _advertised_devices(self) -> list[tuple[str, BaseDriver | ITransporterDriver]]:
        out: list[tuple[str, BaseDriver | ITransporterDriver]] = []
        for device_id in self._device_types:
            driver = self._advertised_driver(device_id)
            if driver is None or not interfaces_of(driver):
                continue
            out.append((device_id, driver))
        return out

    def status_report(self) -> dict[str, DeviceStatusInfo]:
        """What this client sees on each advertised device: a link per driver, a status per device."""
        out: dict[str, DeviceStatusInfo] = {}
        for device_id, _ in self._advertised_devices():
            per_mode: tuple[tuple[DriverMode, BaseDriver | ITransporterDriver | None], ...] = (
                ("LIVE", self._drivers.get(device_id)),
                ("SIM", self._sim_drivers.get(device_id)),
            )
            links: dict[DriverMode, DeviceLinkInfo] = {}
            for mode, driver in per_mode:
                if driver is not None:
                    links[mode] = read_link(device_id, mode, driver)
            out[device_id] = DeviceStatusInfo(
                status="busy" if self.is_busy(device_id) else "ready",
                links=links,
            )
        return out

    async def cleanup_all(self) -> None:
        """Release every driver at shutdown. One driver's failure does not stop the rest."""
        logger.info("Cleaning up devices...")
        for device_id, driver in list(self._drivers.items()) + list(self._sim_drivers.items()):
            await release_driver(device_id, driver)
        logger.info("Device cleanup complete")
