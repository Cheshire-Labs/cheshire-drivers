"""A simulated arm with no remembered joints refuses a jog and says why; after a move to a joint teachpoint it jogs."""

from collections.abc import Awaitable, Callable

import pytest

from cheshire_drivers.driver_errors import InstrumentOutcome, outcome_of
from cheshire_drivers.homing_models import HomeRequest
from cheshire_drivers.sims import SimTransporterDriver, SleepSim
from cheshire_drivers.teachpoints import JointCoordinates, Teachpoint
from cheshire_drivers.transporter_models import (
    GetCartesianPositionRequest,
    GetJointPositionRequest,
    MoveSingleAxisRelativeRequest,
    MoveSingleAxisRequest,
    MoveToCoordsRequest,
    MoveToSafeRequest,
)

pytestmark = pytest.mark.asyncio

_SAYS_SO = "arm_1 is simulated and has no position to jog from: its last move was not to a joint teachpoint"

_JOINT_PAD = Teachpoint(
    position_id="pad_1",
    coordinates=JointCoordinates(base=100.0, shoulder=10.0, elbow=120.0, wrist=30.0),
    orientation="right",
)


async def _nothing(arm: SimTransporterDriver) -> None:
    return


async def _home(arm: SimTransporterDriver) -> None:
    await arm.move_to_coords(MoveToCoordsRequest(teachpoint=_JOINT_PAD))
    await arm.home(HomeRequest())


async def _move_to_safe(arm: SimTransporterDriver) -> None:
    await arm.move_to_coords(MoveToCoordsRequest(teachpoint=_JOINT_PAD))
    await arm.move_to_safe(MoveToSafeRequest())


@pytest.mark.parametrize("after", [_nothing, _home, _move_to_safe], ids=["start-up", "home", "move-to-safe"])
async def test_a_jog_with_no_remembered_joints_fails_and_says_to_move_to_a_joint_teachpoint(
    after: Callable[[SimTransporterDriver], Awaitable[None]],
) -> None:
    arm = SimTransporterDriver(name="arm_1", sim_strategy=SleepSim(sim_time=0.0))
    await after(arm)

    with pytest.raises(NotImplementedError, match=_SAYS_SO):
        await arm.move_single_axis(MoveSingleAxisRequest(axis="elbow", position=90.0))
    with pytest.raises(NotImplementedError, match="Move it to a joint teachpoint first"):
        await arm.move_single_axis_relative(MoveSingleAxisRelativeRequest(axis="elbow", distance=5.0))


async def test_a_jog_after_a_move_to_a_joint_teachpoint_moves_the_remembered_joint() -> None:
    arm = SimTransporterDriver(name="arm_1", sim_strategy=SleepSim(sim_time=0.0))
    await arm.move_to_coords(MoveToCoordsRequest(teachpoint=_JOINT_PAD))

    await arm.move_single_axis(MoveSingleAxisRequest(axis="elbow", position=90.0))
    await arm.move_single_axis_relative(MoveSingleAxisRelativeRequest(axis="elbow", distance=5.0))

    assert (await arm.get_joint_position(GetJointPositionRequest())).elbow == 95.0


async def _read_the_joints(arm: SimTransporterDriver) -> None:
    await arm.get_joint_position(GetJointPositionRequest())


async def _read_the_tool_point(arm: SimTransporterDriver) -> None:
    await arm.get_cartesian_position(GetCartesianPositionRequest())


async def _jog(arm: SimTransporterDriver) -> None:
    await arm.move_single_axis(MoveSingleAxisRequest(axis="elbow", position=90.0))


@pytest.mark.parametrize("ask", [_read_the_joints, _read_the_tool_point, _jog], ids=["joints", "tool-point", "jog"])
async def test_a_sim_arm_with_no_position_says_nothing_moved(ask: Callable[[SimTransporterDriver], Awaitable[None]]) -> None:
    arm = SimTransporterDriver(name="arm_1", sim_strategy=SleepSim(sim_time=0.0))

    with pytest.raises(NotImplementedError) as refused:
        await ask(arm)

    assert outcome_of(refused.value) is InstrumentOutcome.REJECTED
