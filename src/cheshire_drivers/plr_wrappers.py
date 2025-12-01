import asyncio
from typing import List

from cheshire_drivers.interfaces import ICentrifugeDriver, ISealerDriver, IShakerDriver, ITransporterDriver
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


def convert_joint_to_plr_list(
    coords: JointCoordinates,
    gripper_value: float
) -> list[float]:
    """Convert JointCoordinates to PLR joint list format.

    Args:
        coords: Joint coordinates (j1-j5)
        gripper_value: Current gripper position to preserve

    Returns:
        List of 6 floats: [rail, base, shoulder, elbow, wrist, gripper]
    """
    return [
        coords.j1,   # rail
        coords.j2,   # base
        coords.j3,   # shoulder
        coords.j4,   # elbow
        coords.j5,   # wrist
        gripper_value
    ]


class PLRTransporterBackendWrapper(ITransporterDriver):
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

        # Get current gripper position to preserve it
        # Note: get_joint_position() returns 6 values [rail, base, shoulder, elbow, wrist, gripper]
        # Using index access for type compatibility with JointCoords (List[float])
        current_joints = await self._backend.get_joint_position()
        gripper_value = list(current_joints)[5]  # gripper is 6th element (index 5)

        joint_list = convert_joint_to_plr_list(coords, gripper_value)
        await self._backend.move_to(joint_list)

    async def move_to_position(self, position_name: str) -> None:
        """Move robot to a position without picking/placing (for waypoints)."""
        tp = self._teachpoints.get(position_name)
        if tp is None:
            raise ValueError(f"The position '{position_name}' is not taught for {self.name}")

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
        # Validate access_type
        if tp.access_type is None:
            raise ValueError(f"Teachpoint '{tp.name}' has no access_type specified. Must be 'vertical' or 'horizontal'.")

        access_type_lower = tp.access_type.lower()

        # Validate common parameter
        if tp.gripper_offset is None:
            raise ValueError(f"Teachpoint '{tp.name}' has no gripper_offset specified.")

        if access_type_lower == "vertical":
            # Validate vertical-specific parameters
            if tp.retract_distance is None:
                raise ValueError(f"Teachpoint '{tp.name}' (vertical access) has no retract_distance specified.")

            return VerticalAccess(
                approach_height_mm=tp.retract_distance,
                clearance_mm=tp.retract_distance,
                gripper_offset_mm=tp.gripper_offset
            )
        elif access_type_lower == "horizontal":
            # Validate horizontal-specific parameters
            if tp.vertical_clearance is None:
                raise ValueError(f"Teachpoint '{tp.name}' (horizontal access) has no vertical_clearance specified.")
            if tp.z_above is None:
                raise ValueError(f"Teachpoint '{tp.name}' (horizontal access) has no z_above specified.")

            return HorizontalAccess(
                approach_distance_mm=tp.vertical_clearance,
                clearance_mm=tp.vertical_clearance,
                lift_height_mm=tp.z_above,
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

        # Execute actual pick
        assert isinstance(tp.coordinates, CartesianCoordinates)
        plr_coords = convert_cartesian_to_plr_coord(tp.coordinates, tp.orientation)
        access = self._teachpoint_to_plr_access(tp)
        await self._backend.pick_plate(plr_coords, access)

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
        assert isinstance(teachpoint.coordinates, CartesianCoordinates)
        plr_coords = convert_cartesian_to_plr_coord(teachpoint.coordinates, teachpoint.orientation)
        access = self._teachpoint_to_plr_access(teachpoint)
        await self._backend.pick_plate(plr_coords, access)

    async def place_at_coords(self, teachpoint: Teachpoint) -> None:
        """Place plate at coordinates specified by teachpoint."""
        assert isinstance(teachpoint.coordinates, CartesianCoordinates)
        plr_coords = convert_cartesian_to_plr_coord(teachpoint.coordinates, teachpoint.orientation)
        access = self._teachpoint_to_plr_access(teachpoint)
        await self._backend.place_plate(plr_coords, access)

    async def move_to_coords(self, teachpoint: Teachpoint) -> None:
        """Move to coordinates specified by teachpoint."""
        assert isinstance(teachpoint.coordinates, CartesianCoordinates)
        plr_coords = convert_cartesian_to_plr_coord(teachpoint.coordinates, teachpoint.orientation)
        await self._backend.move_to(plr_coords)

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
        self._acceleration = 7

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
