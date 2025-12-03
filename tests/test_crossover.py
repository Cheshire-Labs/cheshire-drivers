"""Tests for crossover maneuver logic in PLRTransporterBackendWrapper.

These tests verify the crossover detection and maneuver sequence logic.
The actual motion control will need validation on real hardware.
"""
from typing import Any, List, Union

import pytest
from pylabrobot.arms.backend import SCARABackend, VerticalAccess, HorizontalAccess
from pylabrobot.arms.precise_flex.coords import PreciseFlexCartesianCoords
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.rotation import Rotation

from cheshire_drivers.teachpoints import Teachpoint, JointCoordinates, CartesianCoordinates
from cheshire_drivers.plr_wrappers import PLRTransporterBackendWrapper


class MockPLRBackend(SCARABackend):
    """Mock PLR arm backend for unit testing crossover logic."""

    def __init__(self, has_rail: bool = True, initial_joints: List[float] | None = None):
        super().__init__()
        self._has_rail = has_rail
        self._joints = initial_joints or [0.0, 170.0, 0.0, 150.0, 0.0, 75.0]
        self.calls: List[tuple[str, tuple[Any, ...]]] = []

    async def setup(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def halt(self) -> None:
        pass

    async def home(self) -> None:
        pass

    async def move_to_safe(self) -> None:
        pass

    async def get_joint_position(self) -> List[float]:
        return self._joints.copy()

    async def get_cartesian_position(self) -> PreciseFlexCartesianCoords:
        return PreciseFlexCartesianCoords(
            location=Coordinate(x=0, y=0, z=0),
            rotation=Rotation(x=0, y=0, z=0)
        )

    async def move_to(self, position: Union[PreciseFlexCartesianCoords, List[float]]) -> None:
        self.calls.append(('move_to', (position,)))

    async def move_one_axis(self, axis: int, position: float, profile: int) -> None:
        self.calls.append(('move_one_axis', (axis, position, profile)))
        if self._has_rail:
            self._joints[axis] = position

    async def approach(
        self,
        position: Union[PreciseFlexCartesianCoords, List[float]],
        access: Union[VerticalAccess, HorizontalAccess, None] = None
    ) -> None:
        pass

    async def pick_plate(
        self,
        position: Union[PreciseFlexCartesianCoords, List[float]],
        access: Union[VerticalAccess, HorizontalAccess, None] = None
    ) -> None:
        self.calls.append(('pick_plate', (position, access)))

    async def place_plate(
        self,
        position: Union[PreciseFlexCartesianCoords, List[float]],
        access: Union[VerticalAccess, HorizontalAccess, None] = None
    ) -> None:
        self.calls.append(('place_plate', (position, access)))

    async def open_gripper(self) -> None:
        pass

    async def close_gripper(self) -> None:
        pass

    async def is_gripper_closed(self) -> bool:
        return True


class TestCrossoverDetection:
    """Verify crossover is detected when orientation changes."""

    @pytest.mark.asyncio
    async def test_right_to_left_needs_crossover(self):
        backend = MockPLRBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 75])  # elbow<180 = right
        wrapper = PLRTransporterBackendWrapper(backend)
        tp = Teachpoint(name="left", coordinates=JointCoordinates(elbow=220))  # elbow>180 = left

        assert await wrapper._needs_crossover(tp) is True

    @pytest.mark.asyncio
    async def test_same_orientation_no_crossover(self):
        backend = MockPLRBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 75])  # right
        wrapper = PLRTransporterBackendWrapper(backend)
        tp = Teachpoint(name="right", coordinates=JointCoordinates(elbow=120))  # also right

        assert await wrapper._needs_crossover(tp) is False


class TestCrossoverManeuverSequence:
    """Verify crossover strategies work correctly."""

    @pytest.mark.asyncio
    async def test_default_strategy_is_2step(self):
        """Verify default strategy is '2step'."""
        backend = MockPLRBackend(has_rail=False, initial_joints=[0, 170, 0, 135, 0, 75])
        wrapper = PLRTransporterBackendWrapper(backend)

        await wrapper._perform_crossover_maneuver()

        move_to_calls = [c for c in backend.calls if c[0] == 'move_to']
        assert len(move_to_calls) == 2

    @pytest.mark.asyncio
    async def test_6step_strategy_from_right(self):
        """Verify '6step' strategy sequence from right config."""
        backend = MockPLRBackend(has_rail=False, initial_joints=[0, 170, 0, 135, 0, 75])
        wrapper = PLRTransporterBackendWrapper(backend)

        await wrapper._perform_crossover_maneuver(strategy="6step")

        moves = [c[1] for c in backend.calls if c[0] == 'move_one_axis']
        assert len(moves) == 6
        assert moves[0] == (2, 0.0, 1)      # shoulder to 0
        assert moves[1] == (3, 90.0, 1)     # elbow extend outward (right)
        assert moves[2] == (4, 180.0, 1)    # wrist to +180
        assert moves[3] == (3, 135.0, 1)    # elbow tuck (right safe)
        assert moves[4] == (3, 180.0, 1)    # elbow under bar
        assert moves[5] == (3, 225.0, 1)    # elbow exit (left safe)

    @pytest.mark.asyncio
    async def test_6step_strategy_from_left(self):
        """Verify '6step' strategy sequence from left config."""
        # Order: [rail, base, shoulder, elbow, wrist, gripper]
        backend = MockPLRBackend(has_rail=False, initial_joints=[0, 170, 0, 225, -75, 0])
        wrapper = PLRTransporterBackendWrapper(backend)

        await wrapper._perform_crossover_maneuver(strategy="6step")

        moves = [c[1] for c in backend.calls if c[0] == 'move_one_axis']
        assert len(moves) == 6
        assert moves[0] == (2, 0.0, 1)       # shoulder to 0
        assert moves[1] == (3, 270.0, 1)     # elbow extend outward (left)
        assert moves[2] == (4, -180.0, 1)    # wrist to -180
        assert moves[3] == (3, 225.0, 1)     # elbow tuck (left safe)
        assert moves[4] == (3, 180.0, 1)     # elbow under bar
        assert moves[5] == (3, 135.0, 1)     # elbow exit (right safe)

    @pytest.mark.asyncio
    async def test_2step_strategy_from_right(self):
        """Verify '2step' strategy from right config."""
        backend = MockPLRBackend(has_rail=False, initial_joints=[0, 170, 0, 135, 0, 75])
        wrapper = PLRTransporterBackendWrapper(backend)

        await wrapper._perform_crossover_maneuver(strategy="2step")

        move_to_calls = [c[1] for c in backend.calls if c[0] == 'move_to']
        assert len(move_to_calls) == 2
        assert move_to_calls[0] == ([0.0, 170.0, 0.0, 180.0, -180.0, 75],)
        assert move_to_calls[1] == ([0.0, 170.0, -20.0, 240.0, -225.0, 75],)

    @pytest.mark.asyncio
    async def test_2step_strategy_from_left(self):
        """Verify '2step' strategy from left config."""
        backend = MockPLRBackend(has_rail=False, initial_joints=[0, 170, 0, 225, 0, 50])
        wrapper = PLRTransporterBackendWrapper(backend)

        await wrapper._perform_crossover_maneuver(strategy="2step")

        move_to_calls = [c[1] for c in backend.calls if c[0] == 'move_to']
        assert len(move_to_calls) == 2
        assert move_to_calls[0] == ([0.0, 170.0, 0.0, 180.0, -180.0, 50],)
        assert move_to_calls[1] == ([0.0, 170.0, 10.0, 120.0, -130.0, 50],)


class TestTeachpointOrientation:
    """Verify Cartesian teachpoints require orientation."""

    def test_cartesian_requires_orientation(self):
        with pytest.raises(ValueError, match="must specify orientation"):
            Teachpoint(
                name="no_orient",
                coordinates=CartesianCoordinates(x=100, y=0, z=0, yaw=0, pitch=0, roll=0),
                access_type="horizontal"
            )

    def test_joint_no_orientation_ok(self):
        tp = Teachpoint(name="joint", coordinates=JointCoordinates(elbow=150))
        assert tp.orientation is None
