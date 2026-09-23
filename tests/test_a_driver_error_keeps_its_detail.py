"""What a failing driver knows beyond its message string survives the trip up.

The bench case these pin: a Flex gripper move stalled mid-descent. The robot
named the axis and the error class; ``str(exc)`` carried only "Stall or
Collision Detected", so the one fact worth having was gone before anything
could log it.
"""

from pylabrobot.opentrons.robot import OpentronsCommandError

from cheshire_drivers.driver_errors import explain_driver_error


def _stall() -> OpentronsCommandError:
    return OpentronsCommandError(
        "moveLabware",
        {
            "id": "err-1",
            "errorType": "StallOrCollisionDetectedError",
            "errorCode": "3008",
            "detail": "Stall or Collision Detected",
            "errorInfo": {"axis": "gantry_y"},
            "wrappedErrors": [
                {
                    "errorType": "MotionFailedError",
                    "detail": "Motor stall on gantry_y",
                    "wrappedErrors": [],
                }
            ],
        },
    )


class TestAnOpentronsFailureKeepsItsPayload:
    def test_the_error_class_and_code_reach_the_message(self) -> None:
        explained = explain_driver_error(_stall())

        assert "StallOrCollisionDetectedError" in explained
        assert "3008" in explained

    def test_the_axis_that_stalled_reaches_the_message(self) -> None:
        """The whole point: without this nobody can say what jammed."""
        explained = explain_driver_error(_stall())

        assert "gantry_y" in explained
        assert "Motor stall on gantry_y" in explained

    def test_the_robots_own_words_still_lead(self) -> None:
        assert explain_driver_error(_stall()).startswith(
            "Opentrons command 'moveLabware' failed: Stall or Collision Detected"
        )

    def test_nothing_is_said_twice(self) -> None:
        explained = explain_driver_error(_stall())

        assert explained.count("Stall or Collision Detected") == 1

    def test_a_payload_with_nothing_extra_reads_as_the_plain_message(self) -> None:
        bare = OpentronsCommandError("home", {"detail": "Homing failed"})

        assert explain_driver_error(bare) == (
            "Opentrons command 'home' failed: Homing failed"
        )


class TestAWrappedFailureIsStillFound:
    def test_a_driver_that_reraises_keeps_the_robots_detail(self) -> None:
        """A driver is free to raise its own error; the payload is on the cause."""
        try:
            try:
                raise _stall()
            except OpentronsCommandError as exc:
                raise RuntimeError("move_plate failed") from exc
        except RuntimeError as exc:
            explained = explain_driver_error(exc)

        assert explained.startswith("move_plate failed")
        assert "Motor stall on gantry_y" in explained

    def test_a_cause_cycle_does_not_hang(self) -> None:
        first = RuntimeError("first")
        second = RuntimeError("second")
        first.__cause__ = second
        second.__cause__ = first

        assert explain_driver_error(first) == "first"


class TestOrdinaryErrorsAreUntouched:
    def test_a_plain_error_is_its_own_message(self) -> None:
        assert explain_driver_error(ValueError("bad slot")) == "bad slot"

    def test_an_error_with_no_message_names_its_class(self) -> None:
        assert explain_driver_error(TimeoutError()) == "TimeoutError"


class TestTheCeilingKeepsWhatWasAdded:
    """The head is the message the operator already had; the tail is the
    structured detail this module exists to deliver. Cutting the tail to fit
    keeps only the half that was never the problem.
    """

    def test_the_innermost_cause_survives_a_long_chain(self) -> None:
        innermost = OpentronsCommandError(
            "moveLabware",
            {
                "errorType": "StallOrCollisionDetectedError",
                "errorCode": "3009",
                # The robot's own sentence, as long as one really gets.
                "detail": "Stall or Collision Detected. " + "x" * 1900,
                "errorInfo": {"axis": "leftZ"},
            },
        )
        outer = RuntimeError("moveLabware failed")
        outer.__cause__ = innermost

        explained = explain_driver_error(outer)

        assert explained.startswith("moveLabware failed")
        assert "leftZ" in explained, (
            "the detail this module adds was cut to fit and the message the "
            "operator already had was kept"
        )
        assert "dropped to fit" in explained

    def test_a_chain_that_fits_is_left_whole(self) -> None:
        inner = OpentronsCommandError(
            "moveLabware",
            {"errorCode": "3009", "detail": "Stall"},
        )
        outer = RuntimeError("moveLabware failed")
        outer.__cause__ = inner

        explained = explain_driver_error(outer)

        assert "dropped to fit" not in explained
        assert "3009" in explained


class TestTheCeilingsHold:
    """The array is whatever the instrument sent, so both ceilings are real."""

    def test_the_cap_is_a_cap_including_its_own_marker(self) -> None:
        huge = OpentronsCommandError(
            "moveLabware", {"detail": "y" * 5000},
        )

        explained = explain_driver_error(huge)

        assert len(explained) <= 2000, (
            f"the wire error is {len(explained)} chars against a 2000 ceiling"
        )
        assert explained.endswith("(truncated)")

    def test_a_wide_wrapped_array_is_bounded_and_says_so(self) -> None:
        wide = OpentronsCommandError(
            "moveLabware",
            {
                "detail": "Stall",
                "wrappedErrors": [
                    {"detail": f"cause {i}"} for i in range(40)
                ],
            },
        )

        explained = explain_driver_error(wide)

        assert "cause 0" in explained
        assert "cause 39" not in explained
        assert "30 more not shown" in explained
