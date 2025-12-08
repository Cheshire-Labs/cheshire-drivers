import asyncio
from typing import List, Literal, Union

from cheshire_drivers.interfaces import ICentrifugeDriver, ISealerDriver, IShakerDriver, ITransporterDriver, AxisName
from pylabrobot.sealing.backend import SealerBackend as PLRSealerBackend
from pylabrobot.shaking.backend import ShakerBackend as PLRShakerBackend
from pylabrobot.centrifuge.backend import CentrifugeBackend as PLRCentrifugeBackend

from pylabrobot.arms.backend import SCARABackend as PLRArmBackend, VerticalAccess, HorizontalAccess

# Re-export PLR backends for use by orca-core
__all__ = [
    "PLRArmBackend",
    "PLRSealerBackend",
    "PLRShakerBackend",
    "PLRCentrifugeBackend",
    "PLRTransporterBackendWrapper",
    "PLRSealerBackendWrapper",
    "PLRShakerBackendWrapper",
    "PLRCentrifugeBackendWrapper",
    "convert_cartesian_to_plr_coord",
    "convert_joint_to_plr_list",
]
from pylabrobot.arms.backend import PreciseFlexCartesianCoords as PLRCartesianCoords
from pylabrobot.arms.precise_flex.coords import ElbowOrientation

from pylabrobot.resources import Coordinate, Rotation

from cheshire_drivers.teachpoints import Teachpoint, TeachpointsRegistry, JointCoordinates, CartesianCoordinates


def convert_cartesian_to_plr_coord(
    coords: CartesianCoordinates,
    orientation: str | None = None
) -> PLRCartesianCoords:
    """Convert CartesianCoordinates to PyLabRobot CartesianCoords.

    Args:
        coords: Cartesian coordinates (x, y, z, roll, pitch, yaw)
        orientation: Optional elbow orientation ('left' or 'right', case-insensitive)

    Returns:
        PLRCartesianCoords with proper Coordinate, Rotation, and ElbowOrientation

    Raises:
        ValueError: If orientation is provided but is not 'left' or 'right'
    """
    # Handle orientation (case-insensitive, optional)
    elbow = None
    if orientation is not None:
        orientation_lower = orientation.lower()
        if orientation_lower == "left":
            elbow = ElbowOrientation.LEFT
        elif orientation_lower == "right":
            elbow = ElbowOrientation.RIGHT
        else:
            raise ValueError(f"Invalid orientation '{orientation}'. Must be 'left' or 'right' (case-insensitive).")

    return PLRCartesianCoords(
        Coordinate(coords.x, coords.y, coords.z),
        Rotation(coords.roll, coords.pitch, coords.yaw),
        elbow
    )


def convert_joint_to_plr_list(coords: JointCoordinates) -> list[float]:
    """Convert JointCoordinates to PLR joint list format.

    Args:
        coords: Joint coordinates with all 6 joints

    Returns:
        List of 6 floats: [rail, base, shoulder, elbow, wrist, gripper]
    """
    return [
        coords.rail,
        coords.base,
        coords.shoulder,
        coords.elbow,
        coords.wrist,
        coords.gripper
    ]


class PLRTransporterBackendWrapper(ITransporterDriver):
    """Wrapper adapting PyLabRobot arm backends to ITransporterDriver interface.

    Handles teachpoint management, access pattern conversion, gateway traversal,
    and automatic crossover maneuvers when changing elbow orientation.
    """

    # Default plate width for SBS microplates (85.48mm nominal)
    DEFAULT_PLATE_WIDTH = 85.0

    # Crossover joint positions [rail, base, shoulder, elbow, wrist, gripper]
    SAFE_LOC = (0.0, 170.0, 0.0, 180.0, -180.0, 0.0)
    RIGHTY_J = (0.0, 170.0, 10.0, 120.0, -130.0, 0.0)
    LEFTY_J = (0.0, 170.0, -20.0, 240.0, -225.0, 0.0)

    # Legacy 6-step crossover constants (for _perform_crossover_6step)
    SAFE_SHOULDER = 0.0
    ELBOW_EXTEND_RIGHT = 90.0
    ELBOW_EXTEND_LEFT = 270.0
    SAFE_ELBOW_RIGHT = 135.0
    SAFE_ELBOW_LEFT = 225.0
    ELBOW_CROSSOVER = 180.0

    def __init__(self, backend: PLRArmBackend) -> None:
        self._backend = backend
        self._teachpoints = TeachpointsRegistry()
        self._is_initialized = False

    @property
    def name(self) -> str:
        """Returns the name of the transporter."""
        return type(self._backend).__name__

    @property
    def is_initialized(self) -> bool:
        """Returns whether the transporter is initialized or not."""
        return self._is_initialized

    async def initialize(self) -> None:
        """Initializes the transporter."""
        await self._backend.setup()
        self._is_initialized = True

    async def home(self) -> None:
        """Homes the transporter."""
        await self._backend.home()

    async def move_to_safe(self) -> None:
        """Moves the transporter to a safe position."""
        await self._backend.move_to_safe()

    def _resolve_gateway_path(self, teachpoint: Teachpoint) -> List[Teachpoint]:
        """Resolve gateway chain, returning ordered list of waypoints to traverse.

        Detects circular references to prevent infinite loops.
        """
        path: List[Teachpoint] = []
        visited: set[str] = set()
        current_gateway = teachpoint.gateway

        while current_gateway:
            if current_gateway in visited:
                raise ValueError(f"Circular gateway reference detected: {current_gateway}")
            visited.add(current_gateway)

            try:
                gateway_tp = self._teachpoints.get(current_gateway)
            except KeyError:
                raise ValueError(f"Gateway '{current_gateway}' not found in teachpoints")

            path.append(gateway_tp)
            current_gateway = gateway_tp.gateway

        # Reverse so we traverse from outermost gateway inward
        return list(reversed(path))

    async def _move_to_joint_coords(self, tp: Teachpoint) -> None:
        """Move using joint-space coordinates, preserving current gripper state."""
        coords = tp.coordinates
        assert isinstance(coords, JointCoordinates)

        # Get current gripper position to preserve it (teachpoints don't store gripper)
        current_joints = await self._backend.get_joint_position()
        current_gripper = list(current_joints)[5]  # gripper is 6th element (index 5)

        # Create JointCoordinates with current gripper state
        coords_with_gripper = JointCoordinates(
            rail=coords.rail,
            base=coords.base,
            shoulder=coords.shoulder,
            elbow=coords.elbow,
            wrist=coords.wrist,
            gripper=current_gripper
        )

        joint_list = convert_joint_to_plr_list(coords_with_gripper)
        await self._backend.move_to(joint_list)

    async def get_joint_position(self) -> JointCoordinates:
        """Get current joint positions from the robot."""
        plr_joints = await self._backend.get_joint_position()
        # PLR returns joint values as iterable: [rail, base, shoulder, elbow, wrist, gripper]
        joints_list = list(plr_joints)
        return JointCoordinates(
            rail=joints_list[0],
            base=joints_list[1],
            shoulder=joints_list[2],
            elbow=joints_list[3],
            wrist=joints_list[4],
            gripper=joints_list[5]
        )

    def _get_axis_index(self, joint_name: str) -> int:
        """Get the axis index for a joint, accounting for has_rail configuration.

        Args:
            joint_name: One of 'rail', 'base', 'shoulder', 'elbow', 'wrist', 'gripper'

        Returns:
            The axis number to use with move_one_axis command (1-based per Brooks TCS API).
        """
        has_rail = getattr(self._backend, '_has_rail', True)

        if has_rail:
            # With rail: 1=rail, 2=base, 3=shoulder, 4=elbow, 5=wrist, 6=gripper
            axis_map = {
                'rail': 1, 'base': 2, 'shoulder': 3,
                'elbow': 4, 'wrist': 5, 'gripper': 6
            }
        else:
            # Without rail: 1=base, 2=shoulder, 3=elbow, 4=wrist, 5=gripper
            axis_map = {
                'base': 1, 'shoulder': 2, 'elbow': 3,
                'wrist': 4, 'gripper': 5
            }

        if joint_name not in axis_map:
            raise ValueError(f"Unknown joint name: {joint_name}")
        return axis_map[joint_name]

    async def _move_one_axis(self, joint_name: str, position: float) -> None:
        """Move a single joint to the specified position.

        Args:
            joint_name: One of 'shoulder', 'elbow', 'wrist'
            position: Target position in degrees

        Note:
            This method requires a backend that supports move_one_axis
            (e.g., PreciseFlexBackend). Will raise AttributeError if not supported.
        """
        axis = self._get_axis_index(joint_name)
        # Use profile_index 1 (standard motion profile)
        # Type ignore: move_one_axis is defined on PreciseFlexBackend but not SCARABackend base
        await self._backend.move_one_axis(axis, position, 1)  # type: ignore[attr-defined]

    async def _needs_crossover(self, teachpoint: Teachpoint) -> bool:
        """Check if crossover maneuver is needed to reach teachpoint.

        Crossover is needed when the robot must change elbow orientation
        (left <-> right) to reach the target position.
        """
        current_joints = await self.get_joint_position()
        current_elbow = current_joints.elbow
        current_is_left = current_elbow > 180

        if teachpoint.is_joint_space():
            assert isinstance(teachpoint.coordinates, JointCoordinates)
            target_is_left = teachpoint.coordinates.elbow > 180
        else:
            # Cartesian - orientation is required
            if teachpoint.orientation is None:
                raise ValueError(
                    f"Cartesian teachpoint '{teachpoint.name}' must specify orientation "
                    "(left/right) for crossover detection"
                )
            target_is_left = teachpoint.orientation.lower() == "left"

        return current_is_left != target_is_left

    async def _perform_crossover_maneuver(
        self,
        strategy: Literal["2step", "6step"] = "2step"
    ) -> None:
        """Perform the crossover maneuver to change elbow orientation.

        Args:
            strategy: Which crossover algorithm to use:
                - "2step": Move to SafeLoc then target config (default)
                - "6step": Single-axis moves with plate clearance extension
        """
        if strategy == "2step":
            await self._crossover_2step()
        elif strategy == "6step":
            await self._crossover_6step()

    async def _crossover_2step(self) -> None:
        """Crossover using 2 full joint moves to predefined positions.

        Sequence:
        1. Move to SafeLoc (elbow at 180, wrist at -180)
        2. Move to target config (Righty_j or Lefty_j)
        """
        current_joints = await self.get_joint_position()
        currently_right = current_joints.elbow < 180
        current_gripper = current_joints.gripper

        # Step 1: Move to SafeLoc
        safe = list(self.SAFE_LOC)
        safe[5] = current_gripper
        await self._backend.move_to(safe)

        # Step 2: Move to target config
        target = list(self.LEFTY_J if currently_right else self.RIGHTY_J)
        target[5] = current_gripper
        await self._backend.move_to(target)

    async def _crossover_6step(self) -> None:
        """6-step crossover using single-axis moves with plate clearance.

        Extends elbow outward first to give plate clearance before rotating wrist.
        Works mechanically but may cause wrist spin issues with TCS due to
        joint-space vs tool-space mismatch.

        Sequence:
        1. Shoulder → 0°
        2. Elbow → 90° or 270° (extend outward)
        3. Wrist → ±180° (shortest path)
        4. Elbow → 135° or 225° (tuck)
        5. Elbow → 180° (cross under bar)
        6. Elbow → exit to opposite side
        """
        current_joints = await self.get_joint_position()
        current_elbow = current_joints.elbow
        current_wrist = current_joints.wrist
        currently_right = current_elbow < 180

        await self._move_one_axis('shoulder', self.SAFE_SHOULDER)

        extend_angle = self.ELBOW_EXTEND_RIGHT if currently_right else self.ELBOW_EXTEND_LEFT
        await self._move_one_axis('elbow', extend_angle)

        wrist_target = 180.0 if current_wrist >= 0 else -180.0
        await self._move_one_axis('wrist', wrist_target)

        tuck_angle = self.SAFE_ELBOW_RIGHT if currently_right else self.SAFE_ELBOW_LEFT
        await self._move_one_axis('elbow', tuck_angle)

        await self._move_one_axis('elbow', self.ELBOW_CROSSOVER)

        exit_angle = self.SAFE_ELBOW_LEFT if currently_right else self.SAFE_ELBOW_RIGHT
        await self._move_one_axis('elbow', exit_angle)

    async def move_to_position(self, position_name: str) -> None:
        """Move robot to a position without picking/placing (for waypoints)."""
        tp = self._teachpoints.get(position_name)
        if tp is None:
            raise ValueError(f"The position '{position_name}' is not taught for {self.name}")

        # Check if crossover maneuver is needed before moving
        if await self._needs_crossover(tp):
            await self._perform_crossover_maneuver()

        if tp.is_joint_space():
            await self._move_to_joint_coords(tp)
        else:
            assert isinstance(tp.coordinates, CartesianCoordinates)
            plr_coords = convert_cartesian_to_plr_coord(tp.coordinates, tp.orientation)
            await self._backend.move_to(plr_coords)

    def _teachpoint_to_plr_access(self, tp: Teachpoint):
        """Convert teachpoint access parameters to PLR AccessPattern.

        Args:
            tp: Teachpoint with access configuration

        Returns:
            VerticalAccess or HorizontalAccess based on access_type

        Raises:
            ValueError: If access_type is invalid or required parameters are None
        """
        if tp.access_type is None:
            raise ValueError(f"Teachpoint '{tp.name}' has no access_type specified. Must be 'vertical' or 'horizontal'.")

        access_type_lower = tp.access_type.lower()

        if access_type_lower == "vertical":
            # vertical_clearance = distance above teachpoint for approach/depart
            return VerticalAccess(
                approach_height_mm=tp.vertical_clearance,
                clearance_mm=tp.vertical_clearance,
                gripper_offset_mm=tp.gripper_offset
            )
        elif access_type_lower == "horizontal":
            # horizontal_clearance = distance outside slot for approach/depart
            # vertical_clearance = lift height after horizontal retract
            return HorizontalAccess(
                approach_distance_mm=tp.horizontal_clearance,
                clearance_mm=tp.horizontal_clearance,
                lift_height_mm=tp.vertical_clearance,
                gripper_offset_mm=tp.gripper_offset
            )
        else:
            raise ValueError(f"Invalid access_type '{tp.access_type}' for teachpoint '{tp.name}'. Must be 'vertical' or 'horizontal'.")

    async def pick(self, position_name: str, labware_type: str) -> None:
        tp = self._teachpoints.get(position_name)
        if tp is None:
            raise ValueError(f"The position '{position_name}' is not taught for {self.name}")

        # Resolve and traverse gateway path (entry)
        gateway_path = self._resolve_gateway_path(tp)
        for waypoint in gateway_path:
            await self.move_to_position(waypoint.name)

        # Check if crossover is needed before final approach to pick location
        if await self._needs_crossover(tp):
            await self._perform_crossover_maneuver()

        # Execute actual pick
        assert isinstance(tp.coordinates, CartesianCoordinates)
        plr_coords = convert_cartesian_to_plr_coord(tp.coordinates, tp.orientation)
        access = self._teachpoint_to_plr_access(tp)
        await self._backend.pick_plate(plr_coords, self.DEFAULT_PLATE_WIDTH, access)

        # Traverse gateway path in reverse (exit)
        for waypoint in reversed(gateway_path):
            await self.move_to_position(waypoint.name)

    async def place(self, position_name: str, labware_type: str) -> None:
        tp = self._teachpoints.get(position_name)
        if tp is None:
            raise ValueError(f"The position '{position_name}' is not taught for {self.name}")

        # Resolve and traverse gateway path (entry)
        gateway_path = self._resolve_gateway_path(tp)
        for waypoint in gateway_path:
            await self.move_to_position(waypoint.name)

        # Check if crossover is needed before final approach to place location
        if await self._needs_crossover(tp):
            await self._perform_crossover_maneuver()

        # Execute actual place
        assert isinstance(tp.coordinates, CartesianCoordinates)
        plr_coords = convert_cartesian_to_plr_coord(tp.coordinates, tp.orientation)
        access = self._teachpoint_to_plr_access(tp)
        await self._backend.place_plate(plr_coords, access)

        # Traverse gateway path in reverse (exit)
        for waypoint in reversed(gateway_path):
            await self.move_to_position(waypoint.name)

    def get_teachpoints(self) -> List[Teachpoint]:
        return self._teachpoints.list()

    def load_teachpoints(self, teachpoints: List[Teachpoint]) -> None:
        """Load taught positions from a list of Teachpoint objects."""
        self._teachpoints.clear()
        [self._teachpoints.add(t) for t in teachpoints]

    async def pick_at_coords(self, teachpoint: Teachpoint) -> None:
        """Pick plate at coordinates specified by teachpoint."""
        # Check if crossover is needed before moving
        if await self._needs_crossover(teachpoint):
            await self._perform_crossover_maneuver()

        assert isinstance(teachpoint.coordinates, CartesianCoordinates)
        plr_coords = convert_cartesian_to_plr_coord(teachpoint.coordinates, teachpoint.orientation)
        access = self._teachpoint_to_plr_access(teachpoint)
        await self._backend.pick_plate(plr_coords, self.DEFAULT_PLATE_WIDTH, access)

    async def place_at_coords(self, teachpoint: Teachpoint) -> None:
        """Place plate at coordinates specified by teachpoint."""
        # Check if crossover is needed before moving
        if await self._needs_crossover(teachpoint):
            await self._perform_crossover_maneuver()

        assert isinstance(teachpoint.coordinates, CartesianCoordinates)
        plr_coords = convert_cartesian_to_plr_coord(teachpoint.coordinates, teachpoint.orientation)
        access = self._teachpoint_to_plr_access(teachpoint)
        await self._backend.place_plate(plr_coords, access)

    async def move_to_coords(self, teachpoint: Teachpoint) -> None:
        """Move to coordinates specified by teachpoint."""
        # Check if crossover is needed before moving
        if await self._needs_crossover(teachpoint):
            await self._perform_crossover_maneuver()

        assert isinstance(teachpoint.coordinates, CartesianCoordinates)
        plr_coords = convert_cartesian_to_plr_coord(teachpoint.coordinates, teachpoint.orientation)
        await self._backend.move_to(plr_coords)

    async def move_single_axis(self, axis: AxisName, position: float) -> None:
        """Move a single axis to absolute position."""
        axis_num = self._get_axis_index(axis)
        # Use profile_index 1 (standard motion profile)
        await self._backend.move_one_axis(axis_num, position, 1)  # type: ignore[attr-defined]

    async def move_single_axis_relative(self, axis: AxisName, distance: float) -> None:
        """Move a single axis by relative distance from current position."""
        current = await self.get_joint_position()
        current_pos = getattr(current, axis)
        await self.move_single_axis(axis, current_pos + distance)

    async def set_free_mode(self, axes: Union[List[AxisName], Literal["all", "none"]]) -> None:
        """Enable/disable free mode (freedrive) for specified axes."""
        if axes == "none":
            await self._backend.set_free_mode(False)  # type: ignore[attr-defined]
        elif axes == "all":
            await self._backend.set_free_mode(True, 0)  # type: ignore[attr-defined]
        elif isinstance(axes, list) and len(axes) == 1:
            axis_num = self._get_axis_index(axes[0])
            await self._backend.set_free_mode(True, axis_num)  # type: ignore[attr-defined]
        else:
            # PLR only supports one axis at a time, enable all for multiple
            await self._backend.set_free_mode(True, 0)  # type: ignore[attr-defined]


class PLRSealerBackendWrapper(ISealerDriver):
    def __init__(self, backend: PLRSealerBackend):
        self._backend = backend
        self._is_initialized = False

    async def initialize(self) -> None:
        await self._backend.setup()
        self._is_initialized = True

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def open(self) -> None:
        await self._backend.open()

    async def close(self) -> None:
        await self._backend.close()

    async def seal(self, temperature: int, duration: float) -> None:
        await self._backend.seal(temperature=temperature, duration=duration)

    async def set_temperature(self, temperature: float) -> None:
        await self._backend.set_temperature(temperature)

    async def get_temperature(self) -> float:
        return await self._backend.get_temperature()


class PLRShakerBackendWrapper(IShakerDriver):
    def __init__(self, backend: PLRShakerBackend):
        self._backend = backend
        self._is_initialized = False

    async def initialize(self) -> None:
        await self._backend.setup()
        self._is_initialized = True

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def stop(self) -> None:
        await self._backend.stop()

    def serialize(self) -> dict:
        return {"type": self.__class__.__name__}

    @property
    def supports_locking(self) -> bool:
        """Check if the shaker supports locking the plate"""
        return self._backend.supports_locking

    async def unlock_plate(self) -> None:
        await self._backend.unlock_plate()

    async def lock_plate(self):
        await self._backend.lock_plate()

    async def shake(self, speed: float, duration: float) -> None:
        await self._backend.shake(speed)
        await asyncio.sleep(duration)
        await self.stop_shaking()

    async def stop_shaking(self) -> None:
        await self._backend.stop_shaking()

    async def open(self) -> None:
        await self.unlock_plate()

    async def close(self) -> None:
        await self.lock_plate()

class PLRCentrifugeBackendWrapper(ICentrifugeDriver):
    def __init__(self, backend: PLRCentrifugeBackend):
        self._backend = backend
        self._is_initialized = False
        self._acceleration: float = 7.0

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def initialize(self) -> None:
        await self._backend.setup()

    async def stop(self) -> None:
        await self._backend.stop()

    async def set_acceleration(self, acceleration: float) -> None:
        self._acceleration = acceleration

    async def centrifuge(self, g: float, duration: float) -> None:
        """Spin the centrifuge at a specified speed for a specified duration."""
        await self._backend.spin(g, duration, self._acceleration)

    async def open(self) -> None:
        await self._backend.open_door()

    async def close(self) -> None:
        await self._backend.close_door()
