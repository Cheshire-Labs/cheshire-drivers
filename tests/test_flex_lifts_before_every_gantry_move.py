"""The Flex lifts every populated mount before any command moves its gantry.

The lift goes out just before the command's first move, so a request the driver
refuses still sends nothing to the robot.
"""

from collections.abc import Awaitable, Callable

import pytest

from cheshire_drivers.gantry_models import GantryBusyError
from cheshire_drivers.liquid_handler_models import (
    DropTipsRequest,
    MovePlateRequest,
    PickUpTipsRequest,
    TipPick,
)
from cheshire_drivers.pipette_motion_models import MoveChannelRelativeRequest, MoveChannelToRequest
from cheshire_drivers.plr.lift_before_first_move import command_moves_in_xy
from cheshire_drivers.plr.opentrons_flex import FlexLiquidHandlerDriver

from tests.test_a_loaded_gripper_refuses_to_travel import _fail_a_gripper_move
from tests.test_opentrons_flex_pipetting import (
    _COLUMN_1,
    _EIGHT_CHANNEL,
    _SINGLE_CHANNEL,
    _bench,
    _column_aspirate,
    _pick_up_column,
    _types,
)

pytestmark = pytest.mark.asyncio

_TWO_TIPS = PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=["A1", "B1"])])


async def _pick_up(driver: FlexLiquidHandlerDriver) -> None:
    await _pick_up_column(driver)


async def _aspirate(driver: FlexLiquidHandlerDriver) -> None:
    await _pick_up_column(driver)
    await driver.aspirate(_column_aspirate())


async def _to_waste(driver: FlexLiquidHandlerDriver) -> None:
    await _pick_up_column(driver)
    await driver.drop_tips(DropTipsRequest(to_waste=True))


async def _move_plate(driver: FlexLiquidHandlerDriver) -> None:
    await driver.move_plate(MovePlateRequest(plate="plate_1", to_position="D1-slot"))


async def _jog_in_x(driver: FlexLiquidHandlerDriver) -> None:
    await driver.move_channel_relative(MoveChannelRelativeRequest(channel=0, dx=5.0))


@pytest.mark.parametrize("step", [_pick_up, _aspirate, _to_waste, _move_plate, _jog_in_x])
async def test_every_mount_is_up_before_each_first_move(step: Callable[[FlexLiquidHandlerDriver], Awaitable[None]]) -> None:
    driver, transport = await _bench(_EIGHT_CHANNEL, _SINGLE_CHANNEL, gripper=True)
    before = len(transport.commands)

    await step(driver)

    sent = transport.commands[before:]
    moves = [i for i, c in enumerate(sent) if command_moves_in_xy(c["commandType"], c["params"])]
    lifts = [i for i, c in enumerate(sent) if c["commandType"] == "retractAxis"]
    assert moves, "the step should have moved the gantry"
    for move in moves:
        lifted = [sent[i]["params"]["axis"] for i in lifts if i < move]
        assert lifted[-3:] == ["leftZ", "rightZ", "extensionZ"], f"{sent[move]['commandType']} went out unlifted"


async def test_a_jog_in_z_alone_sends_no_lift() -> None:
    driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
    before = len(transport.commands)

    await driver.move_channel_to(MoveChannelToRequest(channel=0, z=40.0))

    assert "retractAxis" not in _types(transport.commands[before:])


async def test_a_vendor_head_move_lifts_first() -> None:
    driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
    before = len(transport.commands)

    await getattr(driver, "left.move_to")(x=100.0, y=100.0, z=50.0)

    assert _types(transport.commands[before:])[:3] == ["retractAxis", "retractAxis", "moveToCoordinates"]


async def test_pipetting_is_refused_while_the_gripper_may_hold_a_plate() -> None:
    """A loaded gripper refuses every travel, not only a park: the plate goes wherever the gantry goes."""
    driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
    await _fail_a_gripper_move(driver)
    before = len(transport.commands)

    with pytest.raises(GantryBusyError, match="jaws"):
        await _pick_up_column(driver)

    assert [c for c in transport.commands[before:] if command_moves_in_xy(c["commandType"], c["params"])] == []
    assert "retractAxis" not in _types(transport.commands[before:])


async def test_a_refused_request_sends_no_lift() -> None:
    driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
    before = len(transport.commands)

    with pytest.raises(ValueError):
        await driver.pick_up_tips(_TWO_TIPS)

    assert transport.commands[before:] == []


async def test_a_lift_armed_by_a_refused_request_does_not_fire_on_the_next_command() -> None:
    driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
    with pytest.raises(ValueError):
        await driver.pick_up_tips(_TWO_TIPS)
    before = len(transport.commands)

    await driver.move_channel_to(MoveChannelToRequest(channel=0, z=40.0))

    assert "retractAxis" not in _types(transport.commands[before:])


async def test_a_vendor_head_jog_in_x_lifts_first() -> None:
    driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
    before = len(transport.commands)

    await getattr(driver, "left.move_relative")(axis="x", distance=5.0)

    assert _types(transport.commands[before:]) == ["retractAxis", "retractAxis", "moveRelative"]


async def test_a_vendor_head_jog_in_z_sends_no_lift() -> None:
    driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
    before = len(transport.commands)

    await getattr(driver, "left.move_relative")(axis="z", distance=-5.0)

    assert _types(transport.commands[before:]) == ["moveRelative"]


async def test_opening_the_jaws_by_vendor_command_is_never_refused_for_a_loaded_gripper() -> None:
    """Opening the jaws is how a loaded gripper is cleared, so it must not wait for that."""
    driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
    await _fail_a_gripper_move(driver)

    await getattr(driver, "gripper.open_jaw")()

    assert "retractAxis" not in _types(transport.commands)[-3:]
