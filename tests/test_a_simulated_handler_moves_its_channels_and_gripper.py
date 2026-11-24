"""A liquid handler's simulator answers its motion commands from where it last sent each channel and its gripper."""

import pytest

from cheshire_drivers.driver_errors import InstrumentOutcome, outcome_of
from cheshire_drivers.gripper_models import (
    GetGripperPositionRequest,
    GetGripperRotationRequest,
    GetJawWidthRequest,
    GripperPosition,
    MoveGripperRelativeRequest,
    MoveGripperToRequest,
    RotateGripperRequest,
    SetJawWidthRequest,
)
from cheshire_drivers.pipette_motion_models import (
    ChannelPosition,
    GetChannelPositionRequest,
    MoveChannelRelativeRequest,
    MoveChannelToRequest,
)
from cheshire_drivers.plr.liquid_handler import ChatterboxSTARLiquidHandlerDriver
from cheshire_drivers.sims import (
    SimFlexLiquidHandlerDriver,
    SimOT2LiquidHandlerDriver,
    SimSTARLiquidHandlerDriver,
    SleepSim,
)


def _star() -> SimSTARLiquidHandlerDriver:
    return SimSTARLiquidHandlerDriver("star", sim_strategy=SleepSim(0))


@pytest.mark.asyncio
async def test_a_channel_reads_back_where_it_was_sent_holding_the_axes_a_move_leaves_out() -> None:
    star = _star()
    await star.move_channel_to(MoveChannelToRequest(channel=1, x=10.0, y=20.0, z=30.0))
    await star.move_channel_to(MoveChannelToRequest(channel=1, z=5.0))
    await star.move_channel_relative(MoveChannelRelativeRequest(channel=1, dx=1.5))

    position = await star.get_channel_position(GetChannelPositionRequest(channel=1))

    assert position == ChannelPosition(channel=1, x=11.5, y=20.0, z=5.0, z_reference="tip_end")


@pytest.mark.asyncio
async def test_a_channel_with_nothing_on_record_refuses_a_read_a_jog_and_a_partial_move() -> None:
    star = _star()
    for command in (
        star.get_channel_position(GetChannelPositionRequest(channel=0)),
        star.move_channel_relative(MoveChannelRelativeRequest(channel=0, dz=1.0)),
        star.move_channel_to(MoveChannelToRequest(channel=0, z=1.0)),
    ):
        with pytest.raises(NotImplementedError, match="no position for channel 0") as refused:
            await command
        assert outcome_of(refused.value) is InstrumentOutcome.REJECTED


@pytest.mark.asyncio
async def test_a_channel_is_read_against_the_datum_it_was_sent_to() -> None:
    star = _star()
    await star.move_channel_to(MoveChannelToRequest(channel=0, x=1.0, y=2.0, z=3.0, z_reference="stop_disk"))

    assert (await star.get_channel_position(GetChannelPositionRequest(channel=0, z_reference="stop_disk"))).z == 3.0
    with pytest.raises(NotImplementedError, match="against stop_disk"):
        await star.get_channel_position(GetChannelPositionRequest(channel=0, z_reference="tip_end"))


@pytest.mark.asyncio
@pytest.mark.parametrize("simulator", [SimOT2LiquidHandlerDriver, SimFlexLiquidHandlerDriver])
async def test_an_opentrons_simulator_refuses_the_stop_disk_datum_as_the_robot_does(
    simulator: type[SimOT2LiquidHandlerDriver] | type[SimFlexLiquidHandlerDriver],
) -> None:
    with pytest.raises(ValueError, match="reference the tip end"):
        await simulator("ot", sim_strategy=SleepSim(0)).move_channel_to(
            MoveChannelToRequest(channel=0, x=1.0, y=2.0, z=3.0, z_reference="stop_disk"),
        )


@pytest.mark.asyncio
async def test_the_gripper_reads_back_its_position_jaw_width_and_rotation() -> None:
    star = _star()
    await star.move_gripper_to(MoveGripperToRequest(x=100.0, y=200.0, z=50.0))
    await star.move_gripper_relative(MoveGripperRelativeRequest(dy=-10.0))
    await star.set_jaw_width(SetJawWidthRequest(width=82.0))
    await star.rotate_gripper(RotateGripperRequest(angle=90.0))

    assert await star.get_gripper_position(GetGripperPositionRequest()) == GripperPosition(x=100.0, y=190.0, z=50.0)
    assert await star.get_jaw_width(GetJawWidthRequest()) == 82.0
    assert await star.get_gripper_rotation(GetGripperRotationRequest()) == 90.0


@pytest.mark.asyncio
async def test_a_gripper_with_nothing_on_record_refuses_each_read_and_a_jog() -> None:
    star = ChatterboxSTARLiquidHandlerDriver(name="star")
    for command, what in (
        (star.get_gripper_position(GetGripperPositionRequest()), "gripper position"),
        (star.move_gripper_relative(MoveGripperRelativeRequest(dx=1.0)), "gripper position"),
        (star.get_jaw_width(GetJawWidthRequest()), "jaw width"),
        (star.get_gripper_rotation(GetGripperRotationRequest()), "gripper rotation"),
    ):
        with pytest.raises(NotImplementedError, match=f"star is simulated and has no {what} on record"):
            await command
