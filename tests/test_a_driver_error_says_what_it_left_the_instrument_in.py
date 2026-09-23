"""A driver failure classifies itself, so no caller upstream has to guess.

On 2026-09-02 a Flex refused `park_gantry` because its head still held tips
mid-transfer. Refusing was correct and the command actuated nothing. The
control plane read the refusal as a failure, latched a device fault, and the
fault then stopped a healthy dispense on an unrelated thread, leaving eight
tips holding 240 uL.

Nothing on the wire said which it was. The control plane matched an exception
class at one site, read a string attribute at another, and used the dispatch
boundary as a stand-in at a third. `InstrumentOutcome` is the driver answering
instead.
"""

import pytest

from cheshire_drivers.driver_errors import (
    DriverError,
    DriverOutcomeUnknownError,
    DriverRefusedError,
    DriverRejectedError,
    InstrumentOutcome,
    outcome_of,
)
from cheshire_drivers.gantry_models import GantryBusyError
from cheshire_drivers.gateway_protocol import PROTOCOL_VERSION, ResponseMessage


def test_a_refusal_says_it_actuated_nothing() -> None:
    """The 09-02 case. This is what stops it latching a fault."""
    assert outcome_of(GantryBusyError("FlexHead8 still holds tips")) is (
        InstrumentOutcome.REFUSED
    )


def test_a_gantry_refusal_is_still_a_runtime_error() -> None:
    """It was a bare `RuntimeError` before it declared an outcome, and callers
    upstream catch it that way."""
    assert isinstance(GantryBusyError("busy"), RuntimeError)


@pytest.mark.parametrize("error, expected", [
    (DriverError("ran and stopped"), InstrumentOutcome.FAILED),
    (DriverRefusedError("did not act"), InstrumentOutcome.REFUSED),
    (DriverRejectedError("no such command"), InstrumentOutcome.REJECTED),
    (DriverOutcomeUnknownError("no answer"), InstrumentOutcome.UNKNOWN),
])
def test_each_base_carries_its_own_outcome(
    error: DriverError, expected: InstrumentOutcome,
) -> None:
    assert outcome_of(error) is expected


@pytest.mark.parametrize("error", [
    RuntimeError("boom"), ValueError("bad"), TimeoutError("late"),
])
def test_an_error_that_has_not_classified_reads_as_failed(
    error: BaseException,
) -> None:
    """The default has to be the one that is safe to be wrong about: a caller
    told the machine stopped part-way goes and looks, which costs nothing on a
    machine that merely refused. The reverse sends a caller back at a broken
    instrument."""
    assert outcome_of(error) is InstrumentOutcome.FAILED


def test_an_outcome_named_on_an_instance_is_not_mistaken_for_a_declaration() -> None:
    """Negative control. The class declares; an attribute someone hung on one
    exception object is not a driver saying anything, and reading it would be
    the attribute probe this replaces."""
    rogue = RuntimeError("boom")
    setattr(rogue, "outcome", InstrumentOutcome.REFUSED)

    assert outcome_of(rogue) is InstrumentOutcome.FAILED


def test_the_wire_carries_the_outcome() -> None:
    response = ResponseMessage(
        command_id="cmd_1", success=False, error="still holds tips",
        error_type="GantryBusyError",
        instrument_outcome=InstrumentOutcome.REFUSED,
    )

    assert response.model_dump(mode="json")["instrument_outcome"] == "refused"


def test_a_success_carries_no_outcome() -> None:
    response = ResponseMessage(command_id="cmd_1", success=True)

    assert response.instrument_outcome is None


def test_the_protocol_version_moved_with_the_field() -> None:
    """Both ends validate the version at the handshake and close 1002 on a
    mismatch, so a new field on the response is a version change. So is a
    new accepted value on a field that already exists: a peer that does not
    know `rejected` fails validation and drops the whole socket, and only
    the version stops those two builds ever pairing."""
    assert PROTOCOL_VERSION == "1.3.0"


@pytest.mark.parametrize("outcome, moved_nothing, ask_again", [
    (InstrumentOutcome.REFUSED, True, True),
    (InstrumentOutcome.REJECTED, True, False),
    (InstrumentOutcome.FAILED, False, False),
    (InstrumentOutcome.UNKNOWN, False, False),
])
def test_the_two_questions_a_caller_asks(
    outcome: InstrumentOutcome, moved_nothing: bool, ask_again: bool,
) -> None:
    """Whether to fault the device, and whether waiting is worth anything.

    REFUSED and REJECTED agree on the first and differ on the second, which is
    the whole reason they are separate members: a caller that waits on a
    rejection spends its entire patience budget on a command that can never
    work, and then reports a cause it never established.
    """
    assert outcome.moved_nothing is moved_nothing
    assert outcome.worth_asking_again is ask_again


def test_only_a_refusal_is_worth_waiting_on() -> None:
    """Negative control: a `moved_nothing` check standing in for the retry
    question would pass every case above except this one."""
    waited_on = [o for o in InstrumentOutcome if o.worth_asking_again]

    assert waited_on == [InstrumentOutcome.REFUSED]
