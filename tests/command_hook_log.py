"""Record each time a driver's command hook runs."""

from pydantic import BaseModel

from cheshire_drivers.driver_hooks import DriverHooks

HookCall = tuple[str, BaseModel | None]


def hook_log(driver: DriverHooks) -> list[HookCall]:
    """The (command, request) of every `_before_command` the driver runs from now on; its own hook still runs."""
    log: list[HookCall] = []
    its_own = driver._before_command

    async def record(command: str, request: BaseModel | None) -> None:
        log.append((command, request))
        await its_own(command, request)

    setattr(driver, "_before_command", record)
    return log
