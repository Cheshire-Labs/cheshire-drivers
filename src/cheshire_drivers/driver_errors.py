"""What a driver failure knew, and what it left the instrument in.

Two things a driver knows and used to drop on the way upstream.

The text. A driver exception often knows more than its message string says.
PLR's ``OpentronsCommandError`` puts only the robot's ``detail`` in the message
and keeps ``errorType``, ``errorCode``, ``errorInfo`` and the nested
``wrappedErrors`` -- which name the axis that stalled -- on the exception
itself. Anything that sends ``str(exc)`` discards all of it at the only point
it could have been kept.

What it means for the machine. A driver that refuses a command it never ran
and a driver that fails part-way through one leave the instrument in different
states, and every caller upstream needs to tell them apart: one is waited on
and asked again, the other means go and look at the machine. That was read off
exception class names at one site and a string attribute at another, and the
guess is what turned a correct refusal into a device fault on 2026-09-02.
``InstrumentOutcome`` is the driver saying it instead.
"""

import sys
from enum import Enum
from typing import ClassVar, Iterator, Mapping, Optional, Sequence

from pydantic import JsonValue


class InstrumentOutcome(str, Enum):
    """What a failed command left the instrument in, and whether to ask again.

    Two questions, and four answers cover both, because the pairs are not
    independent: only a command that moved nothing is worth asking again.

    Not a severity and not a cause. Two failures with the same cause can leave
    the machine in different states, and the state is what decides what a
    caller does next.
    """

    REFUSED = "refused"
    """The driver checked its own state and did not act. Nothing moved and the
    SAME REQUEST WILL SUCCEED LATER, so a caller waits and asks again."""

    REJECTED = "rejected"
    """Nothing acted on it and nothing ever will: a command this driver does
    not have, a device the agent does not hold, a payload that will not
    deserialize.

    Apart from REFUSED because waiting is the whole difference. A caller that
    treats the two alike either retries a command that can never work -- 450
    dispatches over a 15-minute patience budget, and then a diagnosis naming a
    cause it never established -- or gives up on a handler that was merely
    busy."""

    FAILED = "failed"
    """It ran and stopped part-way. The instrument is somewhere between where
    it started and where it was going, and somebody has to look."""

    UNKNOWN = "unknown"
    """It was dispatched and no answer came back. Nothing has told the
    instrument to stop, so it may still be moving."""

    @property
    def moved_nothing(self) -> bool:
        """Whether the instrument is untouched, so no fault is owed."""
        return self in (InstrumentOutcome.REFUSED, InstrumentOutcome.REJECTED)

    @property
    def worth_asking_again(self) -> bool:
        """Whether the same request can succeed if the caller waits."""
        return self is InstrumentOutcome.REFUSED


class DriverError(RuntimeError):
    """A driver error that says what it left the instrument in.

    ``FAILED`` is the default because it is the assumption that costs least
    when it is wrong: a caller told the machine is stopped part-way goes and
    looks, which is safe on a machine that merely refused. The reverse -- a
    real failure reported as a refusal -- is a caller asking a broken
    instrument again.

    An error that is not one of these still classifies. ``outcome_of`` answers
    ``FAILED`` for it, for the same reason.

    ``RuntimeError`` rather than ``Exception`` so that a driver error adopting
    this base stays catchable by everything that already catches it. Driver
    errors upstream are caught by class and by ``RuntimeError``, and this base
    is meant to be adopted widely.
    """

    outcome: ClassVar[InstrumentOutcome] = InstrumentOutcome.FAILED


class DriverRefusedError(DriverError):
    """The driver declined because it is busy. Nothing moved; ask again later."""

    outcome: ClassVar[InstrumentOutcome] = InstrumentOutcome.REFUSED


class DriverRejectedError(DriverError):
    """Nothing acted on it and nothing ever will. Do not ask again."""

    outcome: ClassVar[InstrumentOutcome] = InstrumentOutcome.REJECTED


class DriverOutcomeUnknownError(DriverError):
    """The command was dispatched and its answer never came back.

    A driver's own read timing out on a move it already started. Distinct from
    the control plane never hearing from the agent at all, which the control
    plane knows without being told.
    """

    outcome: ClassVar[InstrumentOutcome] = InstrumentOutcome.UNKNOWN


def outcome_of(exc: BaseException) -> InstrumentOutcome:
    """What this error says it left the instrument in.

    Declared, never guessed: a driver that has not said gets ``FAILED``, and
    the way to change that is to raise an error that says so.
    """
    outcome = getattr(type(exc), "outcome", None)
    if isinstance(outcome, InstrumentOutcome):
        return outcome
    return InstrumentOutcome.FAILED

# A cause chain or a wrapped-error tree is data from the instrument, so both
# get a ceiling rather than being trusted to be small.
_MAX_CAUSES = 5
_MAX_WRAP_DEPTH = 3
_MAX_WRAP_BREADTH = 10
_MAX_LENGTH = 2000
_TRUNCATED = " ... (truncated)"


def explain_driver_error(exc: BaseException) -> str:
    """The error text to send upstream, with the driver's structured detail folded in."""
    text = str(exc) or type(exc).__name__
    segments = [text]
    for link in _raised_from(exc):
        for part in _opentrons_parts(link):
            if part not in text and part not in segments:
                segments.append(part)
    return _within_the_ceiling(segments)


def _within_the_ceiling(segments: list[str]) -> str:
    """Join what fits, dropping whole segments from the middle.

    Cutting the tail would drop the structured detail this exists to deliver
    and keep only the message the operator already had. The first segment is
    the driver's own sentence and the last is the innermost cause, so both
    ends are kept and the middle is what goes.
    """
    explained = " | ".join(segments)
    if len(explained) <= _MAX_LENGTH:
        return explained
    kept = [segments[0], segments[-1]] if len(segments) > 1 else [segments[0]]
    while len(" | ".join(kept)) > _MAX_LENGTH and len(kept) > 1:
        kept.pop(0)
    dropped = len(segments) - len(kept)
    joined = " | ".join(kept)
    if len(joined) > _MAX_LENGTH:
        # One segment on its own is over the ceiling. Nothing to drop, so this
        # is the only place a cut lands mid-sentence. Room is left for the
        # marker so the ceiling is a ceiling.
        joined = joined[:_MAX_LENGTH - len(_TRUNCATED)] + _TRUNCATED
    if dropped:
        joined += f" | ... ({dropped} more dropped to fit)"
    return joined


def _raised_from(exc: BaseException) -> Iterator[BaseException]:
    """``exc``, then each error it was explicitly raised from."""
    seen: set[int] = set()
    link: Optional[BaseException] = exc
    while link is not None and id(link) not in seen and len(seen) < _MAX_CAUSES:
        seen.add(id(link))
        yield link
        link = link.__cause__


def _opentrons_parts(exc: BaseException) -> list[str]:
    """What an Opentrons command failure knows beyond its message string.

    Imports pylabrobot only once something else has. The wire models beside
    this module are what the CLI imports, and pylabrobot costs it 0.25s.
    """
    if "pylabrobot.opentrons.robot" not in sys.modules:
        return []
    from cheshire_drivers._plr_compat import ensure_plr_stubs

    ensure_plr_stubs()
    from pylabrobot.opentrons.robot import OpentronsCommandError
    if not isinstance(exc, OpentronsCommandError):
        return []
    error: Mapping[str, JsonValue] = exc.error
    parts = [str(exc)]
    for field in ("errorType", "errorCode"):
        value = error.get(field)
        if value:
            parts.append(f"{field}={value}")
    info = error.get("errorInfo")
    if info:
        parts.append(f"errorInfo={info}")
    parts.extend(_wrapped_details(error))
    return parts


def _wrapped_details(
    error: Mapping[str, JsonValue], depth: int = 0,
) -> Iterator[str]:
    """Each nested error's own words, outermost first."""
    if depth >= _MAX_WRAP_DEPTH:
        return
    wrapped = error.get("wrappedErrors")
    if not isinstance(wrapped, Sequence) or isinstance(wrapped, (str, bytes)):
        return
    # Breadth as well as depth: the array is whatever the instrument sent, and
    # a wide one builds the whole string before anything can cap it.
    for item in wrapped[:_MAX_WRAP_BREADTH]:
        if not isinstance(item, Mapping):
            continue
        said = " ".join(
            str(item[field]) for field in ("errorType", "detail") if item.get(field)
        )
        if said:
            yield f"underlying: {said}"
        yield from _wrapped_details(item, depth + 1)
    if len(wrapped) > _MAX_WRAP_BREADTH:
        yield f"... ({len(wrapped) - _MAX_WRAP_BREADTH} more not shown)"
