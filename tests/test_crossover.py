"""Tests for crossover maneuver logic in PLRTransporterBackendWrapper.

These tests verify the crossover detection and maneuver sequence logic.
The actual motion control will need validation on real hardware.
"""
import pytest

from pylabrobot.brooks.precise_flex import Axis

from cheshire_drivers.teachpoints import Teachpoint, JointCoordinates, CartesianCoordinates
from cheshire_drivers.plr import PLRTransporterBackendWrapper

from tests.arm_backend_mock import RecordingArmBackend


class TestCrossoverDetection:
    """Verify crossover is detected when orientation changes."""

    @pytest.mark.asyncio
    async def test_right_to_left_needs_crossover(self):
        backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 75])  # elbow<180 = right
        wrapper = PLRTransporterBackendWrapper(backend)
        tp = Teachpoint(position_id="left", coordinates=JointCoordinates(elbow=220))  # elbow>180 = left

        assert await wrapper._needs_crossover(tp) is True

    @pytest.mark.asyncio
    async def test_same_orientation_no_crossover(self):
        backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 75])  # right
        wrapper = PLRTransporterBackendWrapper(backend)
        tp = Teachpoint(position_id="right", coordinates=JointCoordinates(elbow=120))  # also right

        assert await wrapper._needs_crossover(tp) is False


class TestCrossoverManeuverSequence:
    """Verify crossover strategies work correctly."""

    @pytest.mark.asyncio
    async def test_default_strategy_is_2step(self):
        """Verify default strategy is '2step'."""
        backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 135, 0, 75])
        wrapper = PLRTransporterBackendWrapper(backend)

        await wrapper._perform_crossover_maneuver()

        move_to_calls = [c for c in backend.calls if c[0] == 'move_to_joint_position']
        assert len(move_to_calls) == 2

    @pytest.mark.asyncio
    async def test_6step_strategy_from_right(self):
        """Verify '6step' strategy sequence from right config."""
        backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 135, 0, 75])
        wrapper = PLRTransporterBackendWrapper(backend)

        await wrapper._perform_crossover_maneuver(strategy="6step")

        moves = [c[1] for c in backend.calls if c[0] == 'move_one_axis']
        assert len(moves) == 6
        assert moves[0] == (Axis(2), 0.0)      # shoulder to 0
        assert moves[1] == (Axis(3), 90.0)     # elbow extend outward (right)
        assert moves[2] == (Axis(4), 180.0)    # wrist to +180
        assert moves[3] == (Axis(3), 135.0)    # elbow tuck (right safe)
        assert moves[4] == (Axis(3), 180.0)    # elbow under bar
        assert moves[5] == (Axis(3), 225.0)    # elbow exit (left safe)

    @pytest.mark.asyncio
    async def test_6step_strategy_from_left(self):
        """Verify '6step' strategy sequence from left config."""
        # Order: [rail, base, shoulder, elbow, wrist, gripper]
        backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 225, -75, 0])
        wrapper = PLRTransporterBackendWrapper(backend)

        await wrapper._perform_crossover_maneuver(strategy="6step")

        moves = [c[1] for c in backend.calls if c[0] == 'move_one_axis']
        assert len(moves) == 6
        assert moves[0] == (Axis(2), 0.0)       # shoulder to 0
        assert moves[1] == (Axis(3), 270.0)     # elbow extend outward (left)
        assert moves[2] == (Axis(4), -180.0)    # wrist to -180
        assert moves[3] == (Axis(3), 225.0)     # elbow tuck (left safe)
        assert moves[4] == (Axis(3), 180.0)     # elbow under bar
        assert moves[5] == (Axis(3), 135.0)     # elbow exit (right safe)

    @pytest.mark.asyncio
    async def test_2step_strategy_from_right(self):
        """Verify '2step' strategy from right config.

        Same physical targets as the old list-based wrapper: move to SafeLoc then
        the Lefty config. Values are now carried per named Axis rather than by list
        position, and neither pose names the gripper: a crossover is a rotation, and
        the jaws hold whatever they were already holding.
        """
        backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 135, 0, 75])
        wrapper = PLRTransporterBackendWrapper(backend)

        await wrapper._perform_crossover_maneuver(strategy="2step")

        move_to_calls = [c[1] for c in backend.calls if c[0] == 'move_to_joint_position']
        assert len(move_to_calls) == 2
        assert move_to_calls[0] == ({
            Axis.RAIL: 0.0, Axis.BASE: 170.0, Axis.SHOULDER: 0.0,
            Axis.ELBOW: 180.0, Axis.WRIST: -180.0,
        },)
        assert move_to_calls[1] == ({
            Axis.RAIL: 0.0, Axis.BASE: 170.0, Axis.SHOULDER: -20.0,
            Axis.ELBOW: 240.0, Axis.WRIST: -225.0,
        },)

    @pytest.mark.asyncio
    async def test_2step_strategy_from_left(self):
        """Verify '2step' strategy from left config (SafeLoc then Righty)."""
        backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 225, 0, 50])
        wrapper = PLRTransporterBackendWrapper(backend)

        await wrapper._perform_crossover_maneuver(strategy="2step")

        move_to_calls = [c[1] for c in backend.calls if c[0] == 'move_to_joint_position']
        assert len(move_to_calls) == 2
        assert move_to_calls[0] == ({
            Axis.RAIL: 0.0, Axis.BASE: 170.0, Axis.SHOULDER: 0.0,
            Axis.ELBOW: 180.0, Axis.WRIST: -180.0,
        },)
        assert move_to_calls[1] == ({
            Axis.RAIL: 0.0, Axis.BASE: 170.0, Axis.SHOULDER: 10.0,
            Axis.ELBOW: 120.0, Axis.WRIST: -130.0,
        },)


class TestTeachpointOrientation:
    """Verify Cartesian teachpoints require orientation."""

    def test_cartesian_requires_orientation(self):
        with pytest.raises(ValueError, match="must specify orientation"):
            Teachpoint(
                position_id="no_orient",
                coordinates=CartesianCoordinates(x=100, y=0, z=0, yaw=0, pitch=0, roll=0),
                access_type="horizontal"
            )

    def test_joint_no_orientation_ok(self):
        tp = Teachpoint(position_id="joint", coordinates=JointCoordinates(elbow=150))
        assert tp.orientation is None
