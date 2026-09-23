"""How long a driver waits on the wire before it gives up on a device.

This is NOT a command policy. Per :class:`~cheshire_drivers.interfaces.BaseDriver`,
"the timeout bound and command<->response correlation live in the engine and
transport, not the driver": the engine reads each command's declared
:class:`~cheshire_drivers.command_timings.CommandTiming` through the device
handshake, and cancels an overrunning command so the operator gets the
recoverable-timeout abort/mark-complete decision.

A :class:`WireTimeout` is the backstop underneath that, for a link that stops
answering at all. It has to outlast every command the driver carries. A driver
whose own budget fires first steals the engine's abort path: the operator gets a
transport error where a decision was owed, and the device is left mid-command
with nobody having chosen what to do about it.

**Why one budget rather than one per kind of wait.** A backend usually exposes
several: a per-request ceiling, a per-command ceiling, sometimes a per-read one.
Giving the short waits short budgets is tempting and wrong, twice over. A
declared timing bounds a whole driver command and says nothing about how many
round trips it makes or how long any one of them takes, so no arithmetic over
declared timings yields a per-round-trip number to give them. And whichever wait
expires, the driver command containing it fails, so a short wait inside a long
command hands the operator a transport error while the engine was still willing
to wait. Every wire wait therefore gets the same budget: the one that outlasts
the slowest command the driver declares.

Failing fast on a dead socket is a real want and it is not this. It belongs on
the connect phase, which can be short without ever cutting a command that is
still running. PyLabRobot's Opentrons transport takes one blanket float covering
connect, read, write and pool alike, so there is nothing here to hang it on.
"""

from pydantic import BaseModel, ConfigDict, Field

from cheshire_drivers.command_timings import collect_command_timings
from cheshire_drivers.interfaces import BaseDriver


class WireTimeout(BaseModel):
    """A driver's wait budget for one device link.

    ``seconds`` bounds every wait the driver makes on that link, a single
    request and a whole enqueue-and-poll round alike.
    ``poll_interval_seconds`` is the delay between two status reads: how often
    to ask, not how long to wait, so it is a rate rather than a budget.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    seconds: float = Field(gt=0.0)
    poll_interval_seconds: float = Field(gt=0.0)


def declared_maximum_seconds(driver_cls: type[BaseDriver]) -> float:
    """How long the slowest command ``driver_cls`` declares may take."""
    return max(t.max_seconds for t in collect_command_timings(driver_cls).values())


def covering_seconds(driver_cls: type[BaseDriver], *, margin: float = 1.5) -> float:
    """The wire budget that outlasts every command ``driver_cls`` declares."""
    return declared_maximum_seconds(driver_cls) * margin


def require_covering(seconds: float, driver_cls: type[BaseDriver]) -> None:
    """Refuse a wire budget that would give up before the engine's abort fires."""
    slowest = declared_maximum_seconds(driver_cls)
    if seconds < slowest:
        raise ValueError(
            f"a wire budget of {seconds}s gives up before {driver_cls.__name__} "
            f"finishes its slowest declared command ({slowest}s), which takes the "
            "recoverable-timeout decision away from the operator"
        )


def resolve_wire_timeout(
    override: WireTimeout | None,
    driver_cls: type[BaseDriver],
    *,
    poll_interval: float,
) -> WireTimeout:
    """The budget ``driver_cls`` runs with: the caller's, or one derived to cover it.

    Both are checked, so an override that would give up before the engine does is
    refused at construction rather than accepted quietly.

    ``poll_interval`` has no default on purpose. How often to ask a device for a
    status is a property of that link, not of this rule, and a rate picked here
    would be wrong for some device without anyone noticing.
    """
    budget = override or WireTimeout(
        seconds=covering_seconds(driver_cls), poll_interval_seconds=poll_interval
    )
    require_covering(budget.seconds, driver_cls)
    return budget
