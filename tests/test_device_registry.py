"""The devices one client runs: its drivers per mode, one lock per device, what it advertises and what it reports."""

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from cheshire_drivers.command_executor import DeviceDrivers, DeviceRegistry
from cheshire_drivers.device_setup import DeviceSetup, MachineSetup, SimDriverSetup
from cheshire_drivers.driver_hooks import DriverWithoutHooksError
from cheshire_drivers.driver_lookup import DriverRequest, build_sim_driver
from cheshire_drivers.faults import RaiseFault
from cheshire_drivers.gateway_protocol import AssignedDevice
from cheshire_drivers.interfaces import BaseDriver
from cheshire_drivers.sims import (
    SimCentrifugeDriver,
    SimLiquidHandlerDriver,
    SimShakerDriver,
    SimTransporterDriver,
)
from cheshire_drivers.transporter_models import InitializeRequest


@pytest.fixture
def sim_shaker_config() -> AssignedDevice:
    return AssignedDevice(kind="shaker", name="test_shaker")


@pytest_asyncio.fixture
async def device_registry() -> AsyncGenerator[DeviceRegistry, None]:
    """A registry with a sim-only shaker and centrifuge: neither has a live driver."""
    registry = DeviceRegistry()
    registry.register(name="test_shaker", device_type="shaker", sim_driver=SimShakerDriver("test_shaker"))
    registry.register(name="test_centrifuge", device_type="centrifuge", sim_driver=SimCentrifugeDriver("test_centrifuge"))
    yield registry
    await registry.cleanup_all()


@pytest.mark.asyncio
async def test_register_device(sim_shaker_config: AssignedDevice):
    """Test registering a device."""
    registry = DeviceRegistry()

    registry.register(
        name=sim_shaker_config.name,
        device_type=sim_shaker_config.kind,
        driver=SimShakerDriver(sim_shaker_config.name),
    )

    assert registry.get_driver("test_shaker") is not None
    assert [(info.name, info.type) for info in registry.connect_info_list()] == [("test_shaker", "shaker")]


@pytest.mark.asyncio
async def test_every_registered_device_is_advertised(device_registry: DeviceRegistry):
    assert [(info.name, info.type) for info in device_registry.connect_info_list()] == [
        ("test_shaker", "shaker"), ("test_centrifuge", "centrifuge"),
    ]


@pytest.mark.asyncio
async def test_cleanup_all_releases_the_rest_when_one_driver_fails_to_release(
    device_registry: DeviceRegistry, monkeypatch: pytest.MonkeyPatch,
) -> None:
    shaker, centrifuge = device_registry.get_sim_driver("test_shaker"), device_registry.get_sim_driver("test_centrifuge")
    assert isinstance(shaker, BaseDriver) and isinstance(centrifuge, BaseDriver)
    released: list[BaseDriver] = []
    _record_release(centrifuge, released)

    async def fails() -> None:
        raise RuntimeError("the port is gone")

    monkeypatch.setattr(shaker, "_shutdown", fails)

    await device_registry.cleanup_all()

    assert released == [centrifuge]


@pytest.mark.asyncio
async def test_cleanup_all_invokes_shutdown_disposal_hook(
    sim_shaker_config: AssignedDevice,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cleanup_all must call each driver's ``_shutdown`` disposal hook, not only
    ``close``. A device-owned sim server (Opentrons robot-server) stops in
    ``_shutdown``; reverting cleanup to close()-only would leak it."""
    registry = DeviceRegistry()
    driver = SimShakerDriver(sim_shaker_config.name)
    registry.register(
        name=sim_shaker_config.name,
        device_type=sim_shaker_config.kind,
        driver=driver,
    )

    calls: list[str] = []

    async def spy() -> None:
        calls.append("shutdown")

    monkeypatch.setattr(driver, "_shutdown", spy)

    await registry.cleanup_all()
    assert calls == ["shutdown"]


@pytest.mark.asyncio
async def test_status_report_follows_the_live_driver_through_bring_up(
    sim_shaker_config: AssignedDevice,
):
    """The agent reports its own driver, so the server never has to guess.

    Bring-up is the only lifecycle verb orca dispatches, and on a backend whose
    link has no separate existence it opens the link too. A report that stayed
    closed through it renders a working bench offline.
    """
    registry = DeviceRegistry()
    driver = SimShakerDriver(sim_shaker_config.name)
    registry.register(
        name=sim_shaker_config.name,
        device_type=sim_shaker_config.kind,
        driver=driver,
    )

    fresh = registry.status_report()["test_shaker"].links["LIVE"]
    assert fresh.is_connected is False
    assert fresh.is_initialized is False

    await driver.initialize()

    up = registry.status_report()["test_shaker"].links["LIVE"]
    assert up.is_connected is True
    assert up.is_initialized is True


@pytest.mark.asyncio
async def test_status_report_does_not_let_the_sim_driver_answer_for_the_live_one(
    sim_shaker_config: AssignedDevice,
):
    """A SIM run brings up the paired sim driver and leaves the live one
    untouched. Reporting whichever driver the agent picked would render a whole
    simulated bench offline while it answers every command."""
    registry = DeviceRegistry()
    registry.register(
        name=sim_shaker_config.name,
        device_type=sim_shaker_config.kind,
        driver=SimShakerDriver(sim_shaker_config.name),
        sim_driver=SimShakerDriver(sim_shaker_config.name),
    )
    sim_driver = registry.get_sim_driver("test_shaker")
    assert isinstance(sim_driver, SimShakerDriver)
    await sim_driver.initialize()

    links = registry.status_report()["test_shaker"].links
    assert links["SIM"].is_connected is True
    assert links["SIM"].is_initialized is True
    assert links["LIVE"].is_connected is False
    assert links["LIVE"].is_initialized is False


@pytest.mark.asyncio
async def test_status_report_offers_no_live_link_when_no_live_driver_was_built():
    """An Opentrons LH set up with the vendor sim and no connection has no
    live driver: a LIVE command to it fails with DeviceNotFoundError. Reporting
    its simulator would show an instrument that is up and cannot be commanded."""
    registry = DeviceRegistry()
    registry.register(name="flex_1", device_type="liquid_handler", driver=None, sim_driver=SimLiquidHandlerDriver("flex_1"))
    sim_driver = registry.get_sim_driver("flex_1")
    assert isinstance(sim_driver, SimLiquidHandlerDriver)
    await sim_driver.initialize()

    links = registry.status_report()["flex_1"].links
    assert "LIVE" not in links
    assert links["SIM"].is_connected is True


@pytest.mark.asyncio
async def test_a_simulated_bench_reports_itself_connected_after_bring_up():
    """A device with no real driver runs as SIM and is brought up with `initialize` alone.
    So this is the whole simulated bench: if its report reads closed here, every
    device on the bed renders offline while it executes commands.
    """
    config = AssignedDevice(kind="shaker", name="lab_sim_shaker")
    registry = DeviceRegistry()
    driver = SimShakerDriver(config.name)
    registry.register(name=config.name, device_type=config.kind, driver=None, sim_driver=driver)

    await driver.initialize()

    observed = registry.status_report()["lab_sim_shaker"].observed_link
    assert observed.mode == "SIM"
    assert observed.is_connected is True


class _ArmHoldingItsOwnSession(SimTransporterDriver):
    """An arm whose link is a thing of its own, the way a PF400's is.

    `BaseDriver.is_connected` defaults to tracking bring-up, and asks exactly
    the drivers that hold a per-connection session to override it so the link
    is observable on its own. This is that override: opening the socket is one
    step and powering the arm up is a later one, so the two can be true
    independently and either can be lost without the other.
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._is_linked = False

    @property
    def is_connected(self) -> bool:
        return self._is_linked

    async def connect(self) -> None:
        self._is_linked = True

    async def disconnect(self) -> None:
        self._is_linked = False


@pytest.mark.asyncio
async def test_status_report_keeps_a_devices_link_apart_from_its_bring_up():
    """The report carries both flags because a device can hold either alone.

    Derive one from the other and a PF400 whose socket is open but which has
    not been powered up republishes as disconnected, which is the bench
    symptom this whole surface exists to delete. It fails the other way too:
    an arm that loses its link while it stays powered up would keep reading
    connected, and an operator would send it commands it cannot take.
    """
    registry = DeviceRegistry()
    arm = _ArmHoldingItsOwnSession("pf400_1")
    registry.register(name="pf400_1", device_type="transporter", driver=arm)

    await arm.connect()

    linked = registry.status_report()["pf400_1"].links["LIVE"]
    assert linked.is_connected is True
    assert linked.is_initialized is False

    await arm.initialize(InitializeRequest())
    await arm.disconnect()

    dropped = registry.status_report()["pf400_1"].links["LIVE"]
    assert dropped.is_connected is False
    assert dropped.is_initialized is True


class _ShakerWhoseLinkCannotBeRead(SimShakerDriver):
    """A driver whose link property raises: a serial port that went away."""

    @property
    def is_connected(self) -> bool:
        raise RuntimeError("serial port went away")


@pytest.mark.asyncio
async def test_a_driver_that_cannot_be_read_reports_its_link_closed_not_missing(
    device_registry: DeviceRegistry,
):
    """A missing mode tells the operator there is no driver and to go fix the
    topology. A driver that exists and threw needs the opposite advice, so it
    has to stay in the map. An unguarded read would also take out the whole
    frame, leaving every device unreported.
    """
    device_registry.register(
        name="broken_shaker",
        device_type="shaker",
        driver=_ShakerWhoseLinkCannotBeRead("broken_shaker"),
        sim_driver=SimShakerDriver("broken_shaker"),
    )

    report = device_registry.status_report()

    assert report["broken_shaker"].links["LIVE"].is_connected is False
    assert "SIM" in report["broken_shaker"].links
    assert "SIM" in report["test_shaker"].links


@pytest.mark.asyncio
async def test_status_report_marks_a_device_busy_while_its_lock_is_held(
    device_registry: DeviceRegistry,
):
    """Status has to reflect the lock, or a queued command looks free to send."""
    lock = device_registry.lock_for("test_shaker")
    assert lock is not None
    async with lock:
        report = device_registry.status_report()
    assert report["test_shaker"].status == "busy"
    assert device_registry.status_report()["test_shaker"].status == "ready"


class _Forwarder(SimShakerDriver):
    runs_hooks = False


def test_a_driver_that_does_not_run_the_command_hook_is_refused_when_registered() -> None:
    registry = DeviceRegistry()

    with pytest.raises(DriverWithoutHooksError):
        registry.register(name="fwd_1", device_type="shaker", sim_driver=_Forwarder("fwd_1"))

    assert registry.lock_for("fwd_1") is None


def test_a_command_runs_on_the_driver_its_mode_names() -> None:
    live, sim = SimShakerDriver("shaker_1"), SimShakerDriver("shaker_1")
    registry = DeviceRegistry()
    registry.register(name="shaker_1", device_type="shaker", driver=live, sim_driver=sim)

    assert registry.driver_for("shaker_1", "LIVE") is live
    assert registry.driver_for("shaker_1", "SIM") is sim
    assert registry.driver_for("shaker_1", "CHECK_RUN") is None


def test_a_registry_that_runs_only_check_runs_has_no_driver_for_a_sim_or_live_command() -> None:
    sim = SimShakerDriver("shaker_1")
    registry = DeviceRegistry({"CHECK_RUN": "SIM"})
    registry.register(name="shaker_1", device_type="shaker", sim_driver=sim)

    assert registry.driver_for("shaker_1", "CHECK_RUN") is sim
    assert registry.driver_for("shaker_1", "SIM") is None
    assert registry.driver_for("shaker_1", "LIVE") is None


def _shaker(name: str = "shaker_1", setup: DeviceSetup | None = None) -> AssignedDevice:
    return AssignedDevice(kind="shaker", name=name, setup=setup or DeviceSetup())


def _a_changed_setup() -> DeviceSetup:
    return DeviceSetup(sim=SimDriverSetup(faults=[RaiseFault(method="shake", error_type="RuntimeError")]))


async def _simulator_only(device: AssignedDevice) -> DeviceDrivers:
    return DeviceDrivers(live=None, sim=build_sim_driver(DriverRequest.for_device(device)))


def _record_release(driver: BaseDriver, released: list[BaseDriver]) -> None:
    original = driver._shutdown

    async def recording_shutdown() -> None:
        released.append(driver)
        await original()

    driver._shutdown = recording_shutdown


def _sim_shaker(registry: DeviceRegistry, name: str = "shaker_1") -> SimShakerDriver:
    driver = registry.get_sim_driver(name)
    assert isinstance(driver, SimShakerDriver)
    return driver


@pytest.mark.asyncio
async def test_applying_an_assignment_builds_a_new_device_and_advertises_it() -> None:
    registry = DeviceRegistry()

    errors = await registry.apply([_shaker()], _simulator_only)

    assert errors == {}
    assert registry.get_all_device_ids() == ["shaker_1"]
    assert registry.get_driver("shaker_1") is None
    assert [info.name for info in registry.connect_info_list()] == ["shaker_1"]


@pytest.mark.asyncio
async def test_an_unchanged_device_keeps_its_driver_across_a_second_assignment() -> None:
    registry = DeviceRegistry()
    await registry.apply([_shaker()], _simulator_only)
    first = _sim_shaker(registry)
    released: list[BaseDriver] = []
    _record_release(first, released)

    await registry.apply([_shaker()], _simulator_only)

    assert _sim_shaker(registry) is first
    assert released == []


@pytest.mark.asyncio
async def test_a_device_whose_setup_changed_gets_a_new_driver_and_the_old_one_is_released() -> None:
    registry = DeviceRegistry()
    await registry.apply([_shaker()], _simulator_only)
    first = _sim_shaker(registry)
    released: list[BaseDriver] = []
    _record_release(first, released)

    await registry.apply([_shaker(setup=_a_changed_setup())], _simulator_only)

    assert _sim_shaker(registry) is not first
    assert released == [first]


@pytest.mark.asyncio
async def test_a_device_dropped_from_the_assignment_is_released_and_forgotten() -> None:
    registry = DeviceRegistry()
    await registry.apply([_shaker(), _shaker("shaker_2")], _simulator_only)
    dropped = _sim_shaker(registry, "shaker_2")
    released: list[BaseDriver] = []
    _record_release(dropped, released)

    await registry.apply([_shaker()], _simulator_only)

    assert released == [dropped]
    assert registry.get_all_device_ids() == ["shaker_1"]
    assert registry.get_sim_driver("shaker_2") is None
    assert registry.lock_for("shaker_2") is None


@pytest.mark.asyncio
async def test_a_device_that_fails_to_build_is_in_the_errors_by_name_while_the_others_still_build() -> None:
    registry = DeviceRegistry()
    broken = _shaker("broken", DeviceSetup(sim=SimDriverSetup(faults=[RaiseFault(method="not_a_method", error_type="RuntimeError")])))

    errors = await registry.apply([broken, _shaker()], _simulator_only)

    assert set(errors) == {"broken"}
    assert "not a faultable method" in errors["broken"]
    assert registry.get_all_device_ids() == ["shaker_1"]


@pytest.mark.asyncio
async def test_a_device_being_removed_waits_for_its_running_command() -> None:
    registry = DeviceRegistry()
    registry.register(name="shaker_1", device_type="shaker", driver=SimShakerDriver("shaker_1"))
    lock = registry.lock_for("shaker_1")
    assert lock is not None

    await lock.acquire()
    removing = asyncio.create_task(registry.remove("shaker_1"))
    await asyncio.sleep(0)
    assert registry.get_driver("shaker_1") is not None

    lock.release()
    await removing
    assert registry.get_driver("shaker_1") is None


def test_a_driver_request_for_an_assigned_device_carries_its_setup_and_this_machine() -> None:
    setup = _a_changed_setup()
    machine = MachineSetup(venus_exe_path="D:/Hamilton/HxRun.exe")
    device = AssignedDevice(kind="shaker", name="shaker_1", setup=setup, deck_modeling=True)

    request = DriverRequest.for_device(device, machine, visualize=True)

    assert (request.name, request.kind, request.setup, request.deck_modeling) == ("shaker_1", "shaker", setup, True)
    assert (request.machine, request.visualize) == (machine, True)
    assert DriverRequest.for_device(device).machine == MachineSetup()


@pytest.mark.asyncio
async def test_a_device_whose_rebuild_fails_is_forgotten_and_named_in_the_errors() -> None:
    registry = DeviceRegistry()
    await registry.apply([_shaker()], _simulator_only)
    broken = DeviceSetup(sim=SimDriverSetup(faults=[RaiseFault(method="not_a_method", error_type="RuntimeError")]))

    errors = await registry.apply([_shaker(setup=broken)], _simulator_only)

    assert set(errors) == {"shaker_1"}
    assert registry.get_all_device_ids() == []
    assert registry.lock_for("shaker_1") is None


@pytest.mark.asyncio
async def test_drivers_built_for_a_device_the_registry_refuses_are_released() -> None:
    live = SimShakerDriver("fwd_1")
    released: list[BaseDriver] = []
    _record_release(live, released)

    async def a_forwarding_simulator(device: AssignedDevice) -> DeviceDrivers:
        return DeviceDrivers(live=live, sim=_Forwarder(device.name))

    errors = await DeviceRegistry().apply([_shaker("fwd_1")], a_forwarding_simulator)

    assert set(errors) == {"fwd_1"}
    assert released == [live]


@pytest.mark.asyncio
async def test_an_apply_cancelled_while_a_command_holds_the_device_leaves_nothing_a_later_apply_cannot_release() -> None:
    registry = DeviceRegistry()
    await registry.apply([_shaker()], _simulator_only)
    first = _sim_shaker(registry)
    released: list[BaseDriver] = []
    _record_release(first, released)
    lock = registry.lock_for("shaker_1")
    assert lock is not None

    await lock.acquire()
    changing = asyncio.create_task(registry.apply([_shaker(setup=_a_changed_setup())], _simulator_only))
    await asyncio.sleep(0)
    changing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await changing
    lock.release()

    await registry.apply([_shaker(setup=_a_changed_setup())], _simulator_only)

    assert released == [first]
    assert _sim_shaker(registry) is not first


@pytest.mark.asyncio
async def test_two_assignments_apply_one_after_the_other_and_the_later_one_wins() -> None:
    registry = DeviceRegistry()
    await registry.apply([_shaker()], _simulator_only)
    built: list[DeviceSetup] = []

    async def recording(device: AssignedDevice) -> DeviceDrivers:
        built.append(device.setup)
        return await _simulator_only(device)

    lock = registry.lock_for("shaker_1")
    assert lock is not None
    await lock.acquire()
    older = asyncio.create_task(registry.apply([_shaker(setup=_a_changed_setup())], recording))
    await asyncio.sleep(0)
    newer = asyncio.create_task(registry.apply([_shaker()], recording))
    await asyncio.sleep(0)
    lock.release()
    await asyncio.gather(older, newer)

    assert built == [_a_changed_setup(), DeviceSetup()]


@pytest.mark.asyncio
async def test_a_device_registered_outside_an_assignment_is_released_by_the_next_one() -> None:
    registry = DeviceRegistry()
    driver = SimShakerDriver("shaker_1")
    registry.register(name="shaker_1", device_type="shaker", sim_driver=driver)
    released: list[BaseDriver] = []
    _record_release(driver, released)

    await registry.apply([], _simulator_only)

    assert released == [driver]
