"""Recording PLR arm backend shared by the transporter-wrapper tests.

Speaks the `pylabrobot.brooks.precise_flex` contract: joint poses are
Axis->value dicts, cartesian verbs take a location plus an approach direction,
and free mode is start_freedrive_mode/stop_freedrive_mode. Every backend call is
recorded so tests can assert the exact joint values and axes the wrapper emits,
without any hardware.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, List, Optional, Union

from pylabrobot.brooks.precise_flex import (
    Axis,
    ElbowOrientation,
    PreciseFlexCartesianPose,
    WorkEnvelope,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.rotation import Rotation

# Joint tuple order used by initial_joints and the crossover constants:
# [rail, base, shoulder, elbow, wrist, gripper].
_JOINT_ORDER = (
    Axis.RAIL,
    Axis.BASE,
    Axis.SHOULDER,
    Axis.ELBOW,
    Axis.WRIST,
    Axis.GRIPPER,
)

_CallArg = Union[Coordinate, Dict[int, float], int, float, str, None, tuple[int, ...]]


class _Configuration:
    """Just enough of the arm's resolved configuration for the pre-flight reach check."""

    def __init__(self, work_envelope: WorkEnvelope) -> None:
        self.work_envelope = work_envelope


class RecordingArmBackend:
    """Mock PreciseFlex that records calls for wrapper unit tests.

    A force-sensing close settles the gripper axis where a held labware would hold
    it, so a composed pick sees a labware in the jaws. Set ``grip_catches_nothing``
    for the arm that closes on an empty nest.
    """

    # The arm reads these off the gripper axis' soft limits at bring-up.
    min_gripper_width = 60.0
    max_gripper_width = 120.0
    closed_gripper_position = 75.5
    # What a held labware holds the jaws above the grip position, in axis units.
    HELD_LABWARE_UNITS = 3.0
    WORK_ENVELOPE = WorkEnvelope(inner=100.0, outer=800.0, zmin=0.0, zmax=400.0)

    def __init__(
        self,
        has_rail: bool = True,
        initial_joints: Optional[List[float]] = None,
        grip_catches_nothing: bool = False,
        discovery_fails: bool = False,
        gripper_joint_range: tuple[Optional[float], Optional[float]] = (None, None),
    ):
        self.grip_catches_nothing = grip_catches_nothing
        # PyLabRobot reads the configuration best-effort, so an arm can finish bring-up
        # never having got one.
        self.discovery_fails = discovery_fails
        self._gripper_joint_range = gripper_joint_range
        self._has_rail = has_rail
        self._joints: List[float] = (
            list(initial_joints) if initial_joints is not None else [0.0, 170.0, 0.0, 150.0, 0.0, 75.0]
        )
        self.calls: List[tuple[str, tuple[_CallArg, ...]]] = []
        self.grip_widths: List[tuple[str, Optional[float]]] = []
        self.speeds: List[Optional[float]] = []

    @property
    def grip_positions(self) -> List[tuple[str, float]]:
        """Every gripper-axis target, as (open|close, position), in order."""
        out: List[tuple[str, float]] = []
        for name, args in self.calls:
            if name == "move_gripper_joint_position":
                position, kind = args
                assert isinstance(position, float) and isinstance(kind, str)
                out.append((kind, position))
        return out

    def moves(self) -> List[Coordinate]:
        """Where every cartesian move was told to go, in order."""
        out: List[Coordinate] = []
        for name, args in self.calls:
            if name == "move_to_location":
                location = args[0]
                assert isinstance(location, Coordinate)
                out.append(location)
        return out

    # -- lifecycle --

    async def setup(self, skip_home: bool = False) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def halt(self) -> None:
        pass

    async def home(self) -> None:
        self.calls.append(("home", ()))

    async def move_to_safe(self) -> None:
        pass

    # -- gripper --

    async def move_gripper(self, width: float, force_sensing: bool = False) -> None:
        self.grip_widths.append(("close" if force_sensing else "open", width))

    async def move_gripper_joint_position(self, position: float, force_sensing: bool = False) -> None:
        self.calls.append(
            ("move_gripper_joint_position", (position, "close" if force_sensing else "open"))
        )
        if force_sensing and not self.grip_catches_nothing:
            position = self.closed_gripper_position + self.HELD_LABWARE_UNITS
        self._joints[_JOINT_ORDER.index(Axis.GRIPPER)] = position

    async def set_grasp_data(
        self, plate_width: float, finger_speed_pct: float, grasp_force: float
    ) -> None:
        self.calls.append(("set_grasp_data", (plate_width, finger_speed_pct, grasp_force)))

    @asynccontextmanager
    async def at_speed(self, speed_pct: Optional[float]) -> AsyncIterator[None]:
        # Recorded as a pair around the calls it wraps, so a test can tell which legs
        # ran inside the scope rather than only that it was entered.
        self.speeds.append(speed_pct)
        self.calls.append(("at_speed_enter", (speed_pct,)))
        try:
            yield
        finally:
            self.calls.append(("at_speed_exit", ()))

    async def is_gripper_closed(self) -> bool:
        return True

    @property
    def has_configuration(self) -> bool:
        return not self.discovery_fails

    @property
    def configuration(self) -> _Configuration:
        if self.discovery_fails:
            raise RuntimeError("Configuration is not available until setup() has run.")
        return _Configuration(self.WORK_ENVELOPE)

    @property
    def gripper_joint_range(self) -> tuple[Optional[float], Optional[float]]:
        return self._gripper_joint_range

    # -- state --

    async def request_joint_position(self) -> Dict[int, float]:
        return {axis: value for axis, value in zip(_JOINT_ORDER, self._joints)}

    async def request_gripper_pose(self) -> PreciseFlexCartesianPose:
        return PreciseFlexCartesianPose(
            location=Coordinate(x=0, y=0, z=0),
            rotation=Rotation(x=0, y=0, z=0),
        )

    async def request_monitor_speed(self) -> int:
        return 50

    async def set_monitor_speed(self, speed_pct: int) -> None:
        self.calls.append(("set_monitor_speed", (speed_pct,)))

    # -- motion --

    async def move_to_joint_position(self, position: Dict[int, float]) -> None:
        self.calls.append(("move_to_joint_position", (position,)))

    async def move_to_location(
        self,
        location: Coordinate,
        direction: float,
        orientation: Optional[ElbowOrientation] = None,
    ) -> None:
        self.calls.append(("move_to_location", (location, direction, orientation)))

    async def move_one_axis(self, axis: Axis, position: float) -> None:
        self.calls.append(("move_one_axis", (axis, position)))

    async def move_one_axis_relative(self, axis: Axis, distance: float) -> None:
        self.calls.append(("move_one_axis_relative", (axis, distance)))

    async def start_freedrive_mode(self, free_axes: Optional[List[int]] = None) -> None:
        self.calls.append(("start_freedrive_mode", (tuple(free_axes) if free_axes else (),)))

    async def stop_freedrive_mode(self) -> None:
        self.calls.append(("stop_freedrive_mode", ()))
