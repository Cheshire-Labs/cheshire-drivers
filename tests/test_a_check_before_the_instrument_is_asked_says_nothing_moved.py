"""A check a driver makes before it asks the instrument says that nothing moved.

A command that fails such a check has done nothing, so the caller must not treat the instrument, or the labware the
command names, as left part-way. The error still reports under the name of the check's own error.
"""

from collections.abc import Awaitable

import pytest
import pytest_asyncio

from cheshire_drivers.driver_errors import (
    CheckedBeforeAskingError,
    DriverRejectedError,
    InstrumentOutcome,
    before_the_instrument_is_asked,
    outcome_of,
    report_failure,
)
from cheshire_drivers.liquid_handler_models import (
    Aspirate96Request,
    AspirateRequest,
    AspirateTarget,
    Dispense96Request,
    DispenseRequest,
    DispenseTarget,
    DropTips96Request,
    DropTipsRequest,
    LabwareStateResponse,
    MixRequest,
    MovePlateRequest,
    PickUpTips96Request,
    PickUpTipsRequest,
    ReturnTips96Request,
    TipPick,
)
from cheshire_drivers.plr import ChatterboxLiquidHandlerDriver
from pylabrobot.legacy.liquid_handling.errors import ChannelizedError
from pylabrobot.resources import Resource as PLRResource
from pylabrobot.resources.errors import HasTipError, NoTipError
from tests.test_opentrons_flex_pipetting import _COLUMN_1, _EIGHT_CHANNEL, _bench, _column_aspirate
from tests.test_volume_tracking_lenient import _configure_with_occupancy, _reset_plr_tracking_flags

__all__ = ["_reset_plr_tracking_flags"]

PLRArgument = PLRResource | float | bool | None


@pytest_asyncio.fixture
async def driver() -> ChatterboxLiquidHandlerDriver:
    driver = ChatterboxLiquidHandlerDriver(num_channels=8)
    await _configure_with_occupancy(driver)
    return driver


def test_an_error_raised_in_the_block_is_rejected_and_keeps_its_own_name() -> None:
    with pytest.raises(CheckedBeforeAskingError) as refused:
        with before_the_instrument_is_asked():
            raise ValueError("'plate_9' has no well 'Z9'.")

    assert isinstance(refused.value, DriverRejectedError)
    assert isinstance(refused.value, ValueError), "a caller that catches the check's own error still does"
    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED
    report = report_failure(refused.value)
    assert report.error_type == "ValueError"
    assert report.error == "'plate_9' has no well 'Z9'."
    assert report.instrument_outcome is InstrumentOutcome.REJECTED


@pytest.mark.asyncio
async def test_an_aspirate_from_labware_the_deck_lacks_says_nothing_moved(
    driver: ChatterboxLiquidHandlerDriver,
) -> None:
    with pytest.raises(Exception) as refused:
        await driver.aspirate(AspirateRequest(
            aspirations=[AspirateTarget(labware="no_such_plate", positions=["A1"], volumes=[5.0])],
        ))

    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED


@pytest.mark.asyncio
async def test_a_pick_up_from_a_rack_the_deck_lacks_says_nothing_moved(
    driver: ChatterboxLiquidHandlerDriver,
) -> None:
    with pytest.raises(Exception) as refused:
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="no_such_rack", positions=["A1"])]))

    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED


@pytest.mark.asyncio
async def test_a_pick_up_onto_a_channel_that_already_holds_a_tip_says_nothing_moved(
    driver: ChatterboxLiquidHandlerDriver,
) -> None:
    await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="tips", positions=["A1"])]))

    with pytest.raises(Exception) as refused:
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="tips", positions=["B1"])]))

    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED
    assert report_failure(refused.value).error_type == "HasTipError"


@pytest.mark.asyncio
async def test_a_drop_with_no_tip_on_the_channel_says_nothing_moved(driver: ChatterboxLiquidHandlerDriver) -> None:
    with pytest.raises(Exception) as refused:
        await driver.drop_tips(DropTipsRequest(drops=[TipPick(tip_rack="tips", positions=["A1"])], to_waste=False))

    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED
    assert report_failure(refused.value).error_type == "NoTipError"


@pytest.mark.asyncio
async def test_a_plate_move_of_labware_the_deck_lacks_says_nothing_moved(driver: ChatterboxLiquidHandlerDriver) -> None:
    with pytest.raises(Exception) as refused:
        await driver.move_plate(MovePlateRequest(plate="no_such_plate", to_position="no_such_site"))

    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED


@pytest.mark.asyncio
async def test_a_96_head_pick_up_from_a_rack_the_deck_lacks_says_nothing_moved(
    driver: ChatterboxLiquidHandlerDriver,
) -> None:
    with pytest.raises(Exception) as refused:
        await driver.pick_up_tips96(PickUpTips96Request(tip_rack="no_such_rack"))

    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED


@pytest.mark.asyncio
async def test_initialize_before_the_deck_is_laid_out_says_nothing_moved() -> None:
    with pytest.raises(Exception) as refused:
        await ChatterboxLiquidHandlerDriver(num_channels=8).initialize()

    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED


def _asked_of_a_bare_head(driver: ChatterboxLiquidHandlerDriver, command: str) -> Awaitable[LabwareStateResponse]:
    """A liquid command sent while the head holds no tip: PyLabRobot checks the head before it calls the backend."""
    if command == "aspirate":
        return driver.aspirate(AspirateRequest(
            aspirations=[AspirateTarget(labware="sample_plate", positions=["A1"], volumes=[5.0])],
        ))
    if command == "dispense":
        return driver.dispense(DispenseRequest(
            dispenses=[DispenseTarget(labware="sample_plate", positions=["A1"], volumes=[5.0])],
        ))
    return driver.mix(MixRequest(labware="sample_plate", positions=["A1"], volume=5.0, repetitions=1))


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["aspirate", "dispense", "mix"])
async def test_a_liquid_command_pylabrobot_refuses_for_a_bare_head_says_nothing_moved(
    driver: ChatterboxLiquidHandlerDriver, command: str,
) -> None:
    with pytest.raises(Exception) as refused:
        await _asked_of_a_bare_head(driver, command)

    assert report_failure(refused.value).error_type == "NoTipError"
    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED


def _asked_of_the_96_head(driver: ChatterboxLiquidHandlerDriver, command: str) -> Awaitable[LabwareStateResponse]:
    if command == "aspirate96":
        return driver.aspirate96(Aspirate96Request(labware="sample_plate", volume=5.0, flow_rate=None, liquid_height=None))
    if command == "dispense96":
        return driver.dispense96(Dispense96Request(labware="sample_plate", volume=5.0, flow_rate=None, liquid_height=None))
    if command == "pick_up_tips96":
        return driver.pick_up_tips96(PickUpTips96Request(tip_rack="tips"))
    if command == "drop_tips96":
        return driver.drop_tips96(DropTips96Request(tip_rack="tips", to_waste=False))
    return driver.return_tips96(ReturnTips96Request())


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["aspirate96", "dispense96", "pick_up_tips96", "drop_tips96", "return_tips96"])
@pytest.mark.parametrize("tip_check", [NoTipError("no tip on the 96 head"), HasTipError("the 96 head has tips")])
async def test_a_96_head_command_pylabrobot_refuses_on_its_tip_check_says_nothing_moved(
    driver: ChatterboxLiquidHandlerDriver, monkeypatch: pytest.MonkeyPatch, command: str, tip_check: Exception,
) -> None:
    """PyLabRobot raises its tip check bare, before the backend. The simulated 96 head never refuses, so the check
    is raised here in its place."""
    async def refuses(*args: PLRArgument, **kwargs: PLRArgument) -> None:
        raise tip_check

    assert driver._lh is not None
    monkeypatch.setattr(driver._lh, command, refuses)

    with pytest.raises(type(tip_check)) as refused:
        await _asked_of_the_96_head(driver, command)

    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED


@pytest.mark.asyncio
async def test_a_channel_error_from_the_backend_is_not_a_refusal(
    driver: ChatterboxLiquidHandlerDriver, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the bare tip check is a refusal. A tip error a backend reports per channel came after the robot moved."""
    await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="tips", positions=["A1"])]))

    async def channel_failed(*args: PLRArgument, **kwargs: PLRArgument) -> None:
        raise ChannelizedError(errors={0: NoTipError("lost its tip")})

    assert driver._lh is not None
    monkeypatch.setattr(driver._lh, "aspirate", channel_failed)

    answer = await driver.aspirate(AspirateRequest(
        aspirations=[AspirateTarget(labware="sample_plate", positions=["A1"], volumes=[5.0])],
    ))

    assert answer.success is False
    assert [error.channel_id for error in answer.per_channel_errors] == [0]


@pytest.mark.asyncio
async def test_a_flex_aspirate_with_no_tips_mounted_says_nothing_moved() -> None:
    driver, transport = await _bench(_EIGHT_CHANNEL)
    before = list(transport.commands)

    with pytest.raises(ValueError) as refused:
        await driver.aspirate(_column_aspirate())

    assert transport.commands == before, "nothing was sent to the robot"
    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED


@pytest.mark.asyncio
async def test_a_flex_aspirate_from_labware_the_deck_lacks_says_nothing_moved() -> None:
    driver, transport = await _bench(_EIGHT_CHANNEL)
    before = list(transport.commands)

    with pytest.raises(Exception) as refused:
        await driver.aspirate(AspirateRequest(
            aspirations=[AspirateTarget(labware="no_such_plate", positions=_COLUMN_1, volumes=[20.0] * 8)],
        ))

    assert transport.commands == before
    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED
    assert report_failure(refused.value).error_type == "ResourceNotFoundError"


@pytest.mark.asyncio
async def test_a_flex_plate_move_of_labware_the_deck_lacks_says_nothing_moved() -> None:
    driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
    before = list(transport.commands)

    with pytest.raises(Exception) as refused:
        await driver.move_plate(MovePlateRequest(plate="no_such_plate", to_position="C2-slot"))

    assert transport.commands == before
    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED
