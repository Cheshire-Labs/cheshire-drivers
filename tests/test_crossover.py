"""A PF400 changes elbow side through its set poses; the shared arm wrapper has no poses of its own."""
import pytest

from pylabrobot.brooks.precise_flex import Axis

from cheshire_drivers.plr import PLRTransporterBackendWrapper
from cheshire_drivers.plr.transporter import PreciseFlexTransporterDriver
from cheshire_drivers.plr.pf400_arm import LEFT_POSE, PF400_CROSSOVER, RIGHT_POSE, SAFE_POSE
from cheshire_drivers.teachpoints import CartesianCoordinates, JointCoordinates, Teachpoint
from cheshire_drivers.transporter_models import MoveToCoordsRequest

from tests.arm_backend_mock import RecordingArmBackend

_RIGHT_ELBOW = JointCoordinates(base=170, elbow=150)
_LEFT_ELBOW = JointCoordinates(base=170, elbow=225)


def _joint_target(elbow: float) -> Teachpoint:
    return Teachpoint(position_id="target", coordinates=JointCoordinates(elbow=elbow))


class TestPF400CrossoverPoses:
    def test_right_to_left_passes_the_safe_pose_then_the_left_pose(self):
        assert PF400_CROSSOVER.poses(_RIGHT_ELBOW, _joint_target(220)) == (SAFE_POSE, LEFT_POSE)

    def test_left_to_right_passes_the_safe_pose_then_the_right_pose(self):
        assert PF400_CROSSOVER.poses(_LEFT_ELBOW, _joint_target(120)) == (SAFE_POSE, RIGHT_POSE)

    def test_an_elbow_at_the_safe_pose_counts_as_right(self):
        assert PF400_CROSSOVER.poses(SAFE_POSE, _joint_target(220)) == (SAFE_POSE, LEFT_POSE)

    def test_the_same_side_needs_no_poses(self):
        assert PF400_CROSSOVER.poses(_RIGHT_ELBOW, _joint_target(120)) == ()

    def test_a_cartesian_target_takes_its_side_from_its_orientation(self):
        target = Teachpoint(
            position_id="target",
            coordinates=CartesianCoordinates(x=100, y=0, z=0, yaw=0, pitch=0, roll=0),
            orientation="left",
            access_type="horizontal",
        )
        assert PF400_CROSSOVER.poses(_RIGHT_ELBOW, target) == (SAFE_POSE, LEFT_POSE)


class TestTheWrapperCrossesOverOnlyWithACrossover:
    @pytest.mark.asyncio
    async def test_with_the_pf400_crossover_it_moves_through_its_poses_without_touching_the_jaws(self):
        backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 135, 0, 75])
        wrapper = PLRTransporterBackendWrapper(backend, crossover=PF400_CROSSOVER)

        await wrapper._cross_over_if_needed(_joint_target(220))

        moves = [c[1] for c in backend.calls if c[0] == "move_to_joint_position"]
        assert moves == [
            ({Axis.RAIL: 0.0, Axis.BASE: 170.0, Axis.SHOULDER: 0.0, Axis.ELBOW: 180.0, Axis.WRIST: -180.0},),
            ({Axis.RAIL: 0.0, Axis.BASE: 170.0, Axis.SHOULDER: -20.0, Axis.ELBOW: 240.0, Axis.WRIST: -225.0},),
        ]

    @pytest.mark.asyncio
    async def test_without_one_it_moves_through_nothing(self):
        backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 135, 0, 75])
        wrapper = PLRTransporterBackendWrapper(backend)

        await wrapper._cross_over_if_needed(_joint_target(220))

        assert [c for c in backend.calls if c[0] == "move_to_joint_position"] == []


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


@pytest.mark.asyncio
async def test_the_preciseflex_driver_crosses_over_through_the_pf400_poses(monkeypatch: pytest.MonkeyPatch):
    driver = PreciseFlexTransporterDriver(host="127.0.0.1")
    moves: list[dict[int, float]] = []

    async def request_joint_position() -> dict[int, float]:
        return {Axis.BASE: 170.0, Axis.SHOULDER: 0.0, Axis.ELBOW: 135.0, Axis.WRIST: 0.0, Axis.GRIPPER: 75.0}

    async def move_to_joint_position(pose: dict[int, float]) -> None:
        moves.append(pose)

    monkeypatch.setattr(driver._backend, "request_joint_position", request_joint_position)
    monkeypatch.setattr(driver._backend, "move_to_joint_position", move_to_joint_position)

    await driver.move_to_coords(MoveToCoordsRequest(teachpoint=_joint_target(220)))

    assert [pose[Axis.ELBOW] for pose in moves] == [SAFE_POSE.elbow, LEFT_POSE.elbow, 220.0]
