"""Per-command logical-duration metadata for driver interfaces.

Each abstract async method on a driver interface carries a
:class:`CommandTiming` via the ``@command_timing(typical=..., max=...)``
decorator. The gateway dispatcher consults these through the device
handshake (``MethodInfo.duration``) to pick a per-command timeout
instead of a single per-device-kind default.

The decorator stores the timing as an attribute on the method itself
(``fn.__command_timing__``), so:

- A rename takes the metadata with it (no string-key drift between a
  separate dict and the method name).
- A concrete-class override re-decorates the method to change its
  timing, and the change is local to that class.
- Discovery walks the MRO via :func:`collect_command_timings` and reads
  the attribute off each method.
"""

from typing import Callable, Dict, Mapping, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Self


F = TypeVar("F", bound=Callable[..., object])


class CommandTiming(BaseModel):
    """Logical-duration metadata for a single driver command.

    ``typical_seconds`` is the expected normal-case duration -- informational,
    surfaced on operator dashboards via ``MethodInfo.duration``.
    ``max_seconds`` is the hard upper bound the dispatcher uses to decide
    when a command has overrun.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    typical_seconds: float = Field(ge=0.0)
    max_seconds: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _max_at_least_typical(self) -> Self:
        if self.max_seconds < self.typical_seconds:
            raise ValueError(
                f"max_seconds ({self.max_seconds}) must be >= "
                f"typical_seconds ({self.typical_seconds})"
            )
        return self


_ATTR = "__command_timing__"


def command_timing(*, typical: float, max: float) -> Callable[[F], F]:
    """Attach logical-duration metadata to an async driver method.

    Usage::

        class IShakerDriver(BaseDriver, ABC):
            @abstractmethod
            @command_timing(typical=60.0, max=7200.0)
            async def shake(self, request: ShakeRequest) -> None: ...

    Order against ``@abstractmethod`` does not matter -- both decorators
    only set attributes on the underlying function and neither replaces
    it. The PEP convention is ``@abstractmethod`` outermost.
    """
    timing = CommandTiming(typical_seconds=typical, max_seconds=max)

    def _wrap(fn: F) -> F:
        setattr(fn, _ATTR, timing)
        return fn

    return _wrap


def collect_command_timings(driver_cls: type) -> Mapping[str, CommandTiming]:
    """Collect every ``@command_timing`` reachable through the driver's MRO.

    Walks the MRO from most-base to most-derived; a derived-class method
    that re-decorates the same name overrides the base-class entry.
    Underscore-prefixed members and properties are skipped (properties
    are synchronous attribute reads and do not dispatch through the
    timeout-aware command path).

    Returns a mapping from method name to its :class:`CommandTiming`.
    """
    merged: Dict[str, CommandTiming] = {}
    for base in reversed(driver_cls.__mro__):
        if base is object:
            continue
        for name, member in vars(base).items():
            if name.startswith("_"):
                continue
            if isinstance(member, property):
                continue
            timing = getattr(member, _ATTR, None)
            if isinstance(timing, CommandTiming):
                merged[name] = timing
    return merged
