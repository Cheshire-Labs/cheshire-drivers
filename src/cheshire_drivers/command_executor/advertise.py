"""How a device's driver is described to the server and released: one copy for orca-client and a check run."""

import logging

from cheshire_drivers.driver_introspection import derive_capabilities, describe_driver
from cheshire_drivers.gateway_protocol import DeviceConnectInfo
from cheshire_drivers.interfaces import BaseDriver, ITransporterDriver

logger = logging.getLogger("cheshire_drivers.command_executor")


def interfaces_of_class(driver_class: type) -> frozenset[str]:
    """The abstract interfaces a driver class declares: what a client advertises and what a server expects."""
    return frozenset(getattr(driver_class, "interfaces", frozenset()))


def interfaces_of(driver: BaseDriver | ITransporterDriver) -> frozenset[str]:
    """The abstract interfaces the driver's class declares."""
    return interfaces_of_class(type(driver))


def connect_info(name: str, kind: str, driver: BaseDriver | ITransporterDriver) -> DeviceConnectInfo:
    """What the server is told about a device: its interfaces, its extra commands and each command's signature."""
    cls = type(driver)
    return DeviceConnectInfo(
        name=name,
        type=kind,
        interfaces=interfaces_of(driver),
        capabilities=derive_capabilities(cls),
        provides_state=bool(getattr(cls, "provides_state", False)),
        methods=describe_driver(driver),
    )


async def release_driver(name: str, driver: BaseDriver | ITransporterDriver) -> None:
    """Hand the device back without moving it, then dispose of the driver. A failure is logged, so one driver cannot stop the rest."""
    steps = (driver.disconnect, driver._shutdown) if isinstance(driver, BaseDriver) else (driver.disconnect,)
    for step in steps:
        try:
            await step()
        except Exception:
            logger.exception("Could not release %r", name)
