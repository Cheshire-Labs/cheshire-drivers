"""The work every driver command does before it reaches the device, in whichever process runs the driver."""

import functools
import inspect
import uuid
from collections.abc import Awaitable, Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import ClassVar, Concatenate, ParamSpec, TypeVar

from pydantic import BaseModel

P = ParamSpec("P")
R = TypeVar("R")
V = TypeVar("V")

_HOOKED_ATTR = "__cheshire_hooked__"


class DriverHooks:
    """Base of every driver class: each public async method runs `_before_command` first, once per command."""

    runs_hooks: ClassVar[bool] = True
    """False only for a driver that forwards commands to another process, whose driver runs them."""
    _hooked_commands: ClassVar[frozenset[str]] = frozenset()

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if not cls.runs_hooks:
            return
        wrapped: set[str] = set()
        # dir() rather than vars(): an interface's default body (BaseDriver.connect) is a command too.
        for name in dir(cls):
            if name.startswith("_"):
                continue
            member = inspect.getattr_static(cls, name)
            if not inspect.iscoroutinefunction(member) or is_hooked(member):
                continue
            if getattr(member, "__isabstractmethod__", False):
                continue
            setattr(cls, name, _hook_method(name, member))
            wrapped.add(name)
        # An empty set would hide the commands a parent class hooks from the MRO walk.
        if wrapped:
            cls._hooked_commands = frozenset(wrapped)

    async def _before_command(self, command: str, request: BaseModel | None) -> None:
        return None


class DriverWithoutHooksError(ValueError):
    """A driver that runs no hooks cannot be held where commands run."""

    def __init__(self, device_name: str, driver_class: str) -> None:
        super().__init__(
            f"Device {device_name}: {driver_class} runs no command hooks, so it cannot run commands here. "
            "Only a driver that forwards to another process opts out."
        )


def refuse_unless_it_runs_hooks(device_name: str, driver: object) -> None:
    """Raise DriverWithoutHooksError for a driver that has no hooks or opted out."""
    if not isinstance(driver, DriverHooks) or not driver.runs_hooks:
        raise DriverWithoutHooksError(device_name, type(driver).__name__)


def is_hooked(member: Callable[..., Awaitable[R]] | None) -> bool:
    """True when `member` is a command wrapped by the hook."""
    return bool(getattr(member, _HOOKED_ATTR, False))


@dataclass
class _Call:
    driver: DriverHooks
    call_id: str
    open: bool = True


_current_call: ContextVar[_Call | None] = ContextVar("current_driver_call", default=None)


def current_call_id() -> str | None:
    """The id of the driver command running in this context, if one is."""
    call = _current_call.get()
    return call.call_id if call is not None and call.open else None


D = TypeVar("D", bound=DriverHooks)


def _hook_method(
    command: str, method: Callable[Concatenate[D, P], Awaitable[R]],
) -> Callable[Concatenate[D, P], Awaitable[R]]:
    @functools.wraps(method)
    async def hooked(self: D, *args: P.args, **kwargs: P.kwargs) -> R:
        return await _hooked_call(
            self, command, lambda: method(self, *args, **kwargs), _request_of((*args, *kwargs.values())),
        )

    setattr(hooked, _HOOKED_ATTR, True)
    return hooked


def hook_bound(driver: DriverHooks, command: str, function: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Wrap a vendor coroutine reached through the driver, so it runs the driver's hooks as `command`."""
    @functools.wraps(function)
    async def hooked(*args: P.args, **kwargs: P.kwargs) -> R:
        return await _hooked_call(
            driver, command, lambda: function(*args, **kwargs), _request_of((*args, *kwargs.values())),
        )

    setattr(hooked, _HOOKED_ATTR, True)
    return hooked


async def _hooked_call(
    driver: DriverHooks, command: str, run: Callable[[], Awaitable[R]], request: BaseModel | None,
) -> R:
    open_call = _current_call.get()
    # A command a driver calls on itself while one is running is part of that command.
    if not driver.runs_hooks or (open_call is not None and open_call.open and open_call.driver is driver):
        return await run()
    call = _Call(driver, uuid.uuid4().hex)
    token = _current_call.set(call)
    try:
        await driver._before_command(command, request)
        return await run()
    finally:
        call.open = False
        _current_call.reset(token)


def _request_of(values: Iterable[V]) -> BaseModel | None:
    return next((value for value in values if isinstance(value, BaseModel)), None)
