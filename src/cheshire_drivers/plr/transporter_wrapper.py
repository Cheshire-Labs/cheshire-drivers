"""Arm/SCARA transporter wrapper and factory.

Home for the PreciseFlex/SCARA arm wrapper, alongside the other real PyLabRobot
drivers in the `cheshire_drivers.plr` package. Wraps `pylabrobot.brooks.precise_flex`.
Import these symbols from `cheshire_drivers.plr`.
"""

import logging
from contextlib import suppress
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pylabrobot.brooks.precise_flex import (
    Axis,
    ElbowOrientation,
    PreciseFlex as PLRArmBackend,
    WorkEnvelope,
)
from pylabrobot.resources import Coordinate

from cheshire_drivers.plr_wrappers import VendorSurfaceForwarding
from cheshire_drivers.homing_models import HomeRequest
from cheshire_drivers.interfaces import ITransporterDriver
from cheshire_drivers.move_parameters import MoveParameters
from cheshire_drivers.plr.arm_pick_place import ArmPickPlace
from cheshire_drivers.teachpoints import (
    CartesianCoordinates,
    JointCoordinates,
    Teachpoint,
)
from cheshire_drivers.transporter_models import (
    CloseGripperRequest,
    EnsureSeededRequest,
    GetCartesianPositionRequest,
    GetJointPositionRequest,
    GetSpeedRequest,
    HaltRequest,
    InitializeRequest,
    MoveSingleAxisRelativeRequest,
    MoveSingleAxisRequest,
    MoveToCoordsRequest,
    MoveToSafeRequest,
    OpenGripperRequest,
    PickAtCoordsRequest,
    PlaceAtCoordsRequest,
    ResetWorldRequest,
    SeedPositionRequest,
    SetFreeModeRequest,
    SetSpeedRequest,
    UnseedPositionRequest,
)

__all__ = [
    "PLRArmBackend",
    "PLRTransporterBackendWrapper",
    "convert_cartesian_to_plr_coord",
    "convert_joint_to_plr_dict",
    "transporter_driver",
]


def convert_cartesian_to_plr_coord(
    coords: CartesianCoordinates,
    orientation: str | None = None,
) -> tuple[Coordinate, float, ElbowOrientation | None]:
    """Split a teachpoint's cartesian pose into what the arm's verbs take.

    The arm takes the tool point, the approach `direction` (the pose's yaw) and
    the elbow orientation separately; pitch and roll are the arm's own fixed
    plate-gripping wrist, so the teachpoint's values for those are not sent.

    Raises:
        ValueError: If orientation is provided but is not 'left' or 'right'
    """
    elbow: ElbowOrientation | None = None
    if orientation is not None:
        elbow_lower = orientation.lower()
        if elbow_lower not in ("left", "right"):
            raise ValueError(
                f"Invalid orientation '{orientation}'. Must be 'left' or 'right' (case-insensitive)."
            )
        elbow = elbow_lower  # type: ignore[assignment]

    return Coordinate(coords.x, coords.y, coords.z), coords.yaw, elbow


def _grip_height(location: Coordinate, params: MoveParameters) -> Coordinate:
    """The taught point raised to where this labware is actually gripped.

    A teachpoint is taught holding one labware; a taller or shorter one sits at a
    different height in the same nest. The offset is zero when the labware being
    moved is the one the position was taught on, which is why an unconfigured
    deployment behaves exactly as it did before.
    """
    if params.z_offset == 0.0:
        return location
    return Coordinate(location.x, location.y, location.z + params.z_offset)


def convert_joint_to_plr_dict(coords: JointCoordinates) -> dict[int, float]:
    """Convert JointCoordinates to a PLR Axis->value joint pose.

    Maps by joint NAME, not position: the arm keys joints by the Axis enum, so
    each named joint reaches its own axis regardless of the enum's numbering.
    """
    return {
        Axis.RAIL: coords.rail,
        Axis.BASE: coords.base,
        Axis.SHOULDER: coords.shoulder,
        Axis.ELBOW: coords.elbow,
        Axis.WRIST: coords.wrist,
        Axis.GRIPPER: coords.gripper,
    }


logger = logging.getLogger(__name__)


def _joint_tuple_to_plr_dict(
    joints: tuple[float, float, float, float, float, float],
) -> dict[int, float]:
    """Convert a (rail, base, shoulder, elbow, wrist, gripper) tuple to an Axis pose.

    The crossover constants are stored in that fixed order. The gripper slot is
    dropped, for the reason `_pose_without_gripper` gives.
    """
    rail, base, shoulder, elbow, wrist, _ = joints
    return {
        Axis.RAIL: rail,
        Axis.BASE: base,
        Axis.SHOULDER: shoulder,
        Axis.ELBOW: elbow,
        Axis.WRIST: wrist,
    }


def _pose_without_gripper(coords: JointCoordinates) -> dict[int, float]:
    """A joint pose with no jaw position in it, so the arm keeps its own.

    Teachpoints carry no gripper, and a jaw position read back and re-commanded is
    checked as a taught target: out of range it fails as a pose to re-teach instead
    of as the recoverable arm state it is. An axis the pose omits is merged from the
    arm's live position, which is what preserving it was always trying to do.
    """
    pose = convert_joint_to_plr_dict(coords)
    del pose[Axis.GRIPPER]
    return pose


@runtime_checkable
class _LinkControl(Protocol):
    """Arms that separate opening the link from the homing bring-up (the PreciseFlex)."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...


@runtime_checkable
class _GranularBringUp(Protocol):
    """Arms that can be powered up without being homed (the PreciseFlex).

    Includes `disconnect` so that satisfying this also satisfies `_LinkControl`.
    Otherwise a backend with only connect + initialize would take the granular
    path and then fail inside `connect`, instead of falling back to `setup`.
    """

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def initialize(self) -> None: ...


@runtime_checkable
class _SingleAxisMover(Protocol):
    async def move_one_axis(self, axis: Axis, position: float) -> None: ...


_Vendor = TypeVar("_Vendor")


class PLRTransporterBackendWrapper(VendorSurfaceForwarding, ITransporterDriver):
    """Wrapper adapting PyLabRobot arm backends to ITransporterDriver interface.

    Handles teachpoint management, access pattern conversion, gateway traversal,
    and automatic crossover maneuvers when changing elbow orientation.
    """

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

    DEFAULT_JAW_OPENING = 14.0
    """How far an open with no opening named stands off the grip position.

    Only for a caller with no move in hand, such as an operator pressing a button. A
    pick opens by the opening its own resolved move carries."""

    def __init__(self, backend: PLRArmBackend) -> None:
        # The railed PreciseFlex moveJ/wherej wire order is unvalidated on this PLR.
        # Production wraps the backend directly, so reject a real railed one here.
        if isinstance(backend, PLRArmBackend) and getattr(backend, "_has_rail", False):
            raise NotImplementedError(
                "has_rail=True is not supported on this PyLabRobot version: the upstream "
                "railed moveJ/wherej joint order is not validated against the Brooks firmware. "
                "Restore rail-first ordering and re-validate on hardware before enabling a rail."
            )
        self._backend = backend
        self._pick_place = ArmPickPlace(backend)
        self._is_initialized = False
        self._is_linked = False

    @property
    def name(self) -> str:
        """Returns the name of the transporter."""
        return type(self._backend).__name__

    @property
    def is_initialized(self) -> bool:
        """Returns whether the transporter is initialized or not."""
        return self._is_initialized

    @property
    def is_connected(self) -> bool:
        """Whether the link to the arm is open, independent of bring-up.

        The arm can be linked but not yet powered up, so this is not the same
        question as `is_initialized`.
        """
        return self._is_linked

    async def initialize(self, request: InitializeRequest) -> None:
        """Bring the arm up so it accepts commands.

        MOVES NOTHING on a backend that offers the granular verbs, which is the
        point: PLR's `setup()` ends in a home sweep, and homing an arm that is
        holding a plate drives it through whatever it holds. Homing is `home`.

        A backend that offers only `setup()` HOMES HERE. PLR has not made the
        granular split part of its backend contract, so the fallback has to
        stay, but it is logged rather than silent: an operator reading "moves
        nothing" about a verb that just swept the envelope is exactly the
        failure this split exists to prevent.
        """
        if isinstance(self._backend, _GranularBringUp):
            await self.connect()
            await self._backend.initialize()
        else:
            logger.warning(
                "%s has no granular bring-up, so initialize falls back to PyLabRobot's "
                "setup(), WHICH HOMES THE ARM. Check nothing is in its path.",
                type(self._backend).__name__,
            )
            await self._backend.setup()
            # setup() opened the link as part of bring-up; record it, or the arm
            # reports itself unlinked while it is up and taking commands.
            self._is_linked = True
        self._is_initialized = True

    def _vendor(self, verbs: type[_Vendor], operation: str) -> _Vendor:
        """The backend, for a vendor-only verb the arm interface does not carry."""
        if not isinstance(self._backend, verbs):
            raise NotImplementedError(
                f"{operation} is a vendor extra this arm's backend "
                f"({type(self._backend).__name__}) does not expose"
            )
        return self._backend

    async def connect(self) -> None:
        """Open the link, or do nothing if it is already open. Moves nothing.

        PLR's socket `setup()` swaps in a brand new connection and drops the
        old one on the floor. The PreciseFlex attaches the robot per
        connection, so re-linking a live arm orphans the attached session and
        every later command comes back "no robot attached".

        Opening the socket is not proof it works. An arm left idle for a couple
        of hours accepts a connect and then aborts the first real read, so a
        caller that trusted connect got a link it could not use and a failure
        landed on whatever command came next. One read here makes connect
        answer for the link it claims to have opened.
        """
        if self._is_linked:
            return
        await self._vendor(_LinkControl, "connect").connect()
        try:
            await self._backend.request_joint_position()
        except Exception:
            # Hand the dead socket back before reporting, or `connect` no-ops
            # on the next attempt and the arm is stranded until a restart.
            with suppress(Exception):
                await self._vendor(_LinkControl, "disconnect").disconnect()
            self._is_linked = False
            raise
        self._is_linked = True

    async def disconnect(self) -> None:
        """Drop the link and the power with it. Moves nothing.

        Frees the link even when the handback throws, which a link that has
        already died will. `connect` no-ops while the link looks open, so
        leaving that set would strand the arm until someone restarts the client.
        """
        try:
            await self._vendor(_LinkControl, "disconnect").disconnect()
        finally:
            self._is_initialized = False
            self._is_linked = False

    async def home(self, request: HomeRequest) -> None:
        """Homes the transporter."""
        await self._backend.home()

    async def move_to_safe(self, request: MoveToSafeRequest) -> None:
        """Moves the transporter to a safe position."""
        await self._backend.move_to_safe()

    async def open_gripper(self, request: OpenGripperRequest) -> None:
        """Open past the grip position, by a stated opening or the default one.

        Not a sweep to the stop: the servo overshoots a commanded end, and an axis
        parked outside its limits blocks every later move until the arm is homed.

        The opening is the number a pick already opens by, so a caller that resolves
        it for moves passes the same one here and the jaws an operator opens match
        the jaws a pick opens.
        """
        if request.position is not None:
            await self._backend.move_gripper_joint_position(request.position, force_sensing=False)
            return
        opening = self.DEFAULT_JAW_OPENING if request.jaw_opening is None else request.jaw_opening
        await self._backend.move_gripper_joint_position(
            self._backend.closed_gripper_position + opening, force_sensing=False
        )

    async def close_gripper(self, request: CloseGripperRequest) -> None:
        """Close to the calibrated grip position, or to a position the caller names.

        Force sensing stops the jaws on contact, so the commanded position is a floor
        rather than a target and a held resource is not crushed.
        """
        position = (
            self._backend.closed_gripper_position
            if request.position is None
            else request.position
        )
        await self._backend.move_gripper_joint_position(position, force_sensing=True)

    async def _move_to_joint_coords(self, tp: Teachpoint) -> None:
        """Move using joint-space coordinates, leaving the jaws where they are."""
        coords = tp.coordinates
        assert isinstance(coords, JointCoordinates)
        await self._backend.move_to_joint_position(_pose_without_gripper(coords))

    async def get_joint_position(self, request: GetJointPositionRequest) -> JointCoordinates:
        """Get current joint positions from the robot."""
        plr_joints = await self._backend.request_joint_position()
        # The arm returns an Axis->value pose; read by named axis, not by position.
        return JointCoordinates(
            rail=plr_joints.get(Axis.RAIL, 0.0),
            base=plr_joints[Axis.BASE],
            shoulder=plr_joints[Axis.SHOULDER],
            elbow=plr_joints[Axis.ELBOW],
            wrist=plr_joints[Axis.WRIST],
            gripper=plr_joints[Axis.GRIPPER],
        )

    def _get_axis_index(self, joint_name: str) -> Axis:
        """The arm's Axis member for a joint name.

        The numbering is the arm's own and does not shift with a rail, so unlike
        the old hand-built map there is no rail case to get wrong.
        """
        try:
            return Axis[joint_name.upper()]
        except KeyError:
            raise ValueError(f"Unknown joint name: {joint_name}") from None

    async def _move_one_axis(self, joint_name: str, position: float) -> None:
        """Move a single joint to the specified position.

        Args:
            joint_name: One of 'shoulder', 'elbow', 'wrist'
            position: Target position in degrees

        Note:
            This method requires a backend that supports move_one_axis
            (e.g., PreciseFlexBackend); otherwise raises NotImplementedError.
        """
        axis = self._get_axis_index(joint_name)
        await self._vendor(_SingleAxisMover, "move_joint").move_one_axis(axis, position)

    async def _needs_crossover(self, teachpoint: Teachpoint) -> bool:
        """Check if crossover maneuver is needed to reach teachpoint.

        Crossover is needed when the robot must change elbow orientation
        (left <-> right) to reach the target position.
        """
        current_joints = await self.get_joint_position(GetJointPositionRequest())
        current_elbow = current_joints.elbow
        current_is_left = current_elbow > 180

        if teachpoint.is_joint_space():
            assert isinstance(teachpoint.coordinates, JointCoordinates)
            target_is_left = teachpoint.coordinates.elbow > 180
        else:
            # Cartesian - orientation is required
            if teachpoint.orientation is None:
                raise ValueError(
                    f"Cartesian teachpoint '{teachpoint.position_id}' must specify orientation "
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
        current_joints = await self.get_joint_position(GetJointPositionRequest())
        currently_right = current_joints.elbow < 180

        await self._backend.move_to_joint_position(_joint_tuple_to_plr_dict(self.SAFE_LOC))
        await self._backend.move_to_joint_position(
            _joint_tuple_to_plr_dict(self.LEFTY_J if currently_right else self.RIGHTY_J)
        )

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
        current_joints = await self.get_joint_position(GetJointPositionRequest())
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

    def _work_envelope(self) -> WorkEnvelope | None:
        """The arm's reachable envelope, read off the controller during bring-up.

        The arm is asked, not the wrapper's own record of having called `initialize`:
        reading the configuration is best-effort inside PyLabRobot, so bring-up can
        finish with the arm still not knowing its own limits.

        None before bring-up, because an arm nobody has asked what it can reach cannot
        refuse a target for being out of reach. After bring-up a missing envelope means
        the read failed, and this is the only reach check a pick gets, so it is reported
        rather than quietly turning that check off.
        """
        if self._backend.has_configuration:
            return self._backend.configuration.work_envelope
        if self._is_initialized:
            raise RuntimeError(
                f"{type(self._backend).__name__} finished bring-up without reading its "
                "configuration, so no pick or place can be checked against the arm's "
                "reach. Check the controller link and run initialize again; the driver "
                "log records what the read failed with."
            )
        return None

    async def _move_through_waypoint(self, waypoint: Teachpoint) -> None:
        """Move the arm through a gateway waypoint that the caller resolved.

        Used by `pick_at_coords` / `place_at_coords` to traverse the
        gateway chain inlined into the wire payload. The waypoint is a
        fully-flattened `Teachpoint`; no store lookup happens here. The
        system layer (orca-core's `Transporter.pick`/`.place`) walks
        the chain and supplies it via `request.gateway_path`.
        """
        if await self._needs_crossover(waypoint):
            await self._perform_crossover_maneuver()
        if waypoint.is_joint_space():
            await self._move_to_joint_coords(waypoint)
        else:
            assert isinstance(waypoint.coordinates, CartesianCoordinates)
            location, direction, elbow = convert_cartesian_to_plr_coord(
                waypoint.coordinates, waypoint.orientation
            )
            await self._backend.move_to_location(location, direction, orientation=elbow)

    async def pick_at_coords(self, request: PickAtCoordsRequest) -> None:
        """Pick plate at coordinates specified by teachpoint.

        Traverses the pre-resolved `gateway_path` (outermost first), performs
        the pick at `teachpoint`, then retraces the path in reverse. When
        `gateway_path` is empty this is the direct-target pick (no gateway).
        """
        teachpoint = request.teachpoint

        for waypoint in request.gateway_path:
            await self._move_through_waypoint(waypoint)

        if await self._needs_crossover(teachpoint):
            await self._perform_crossover_maneuver()

        assert isinstance(teachpoint.coordinates, CartesianCoordinates)
        location, direction, elbow = convert_cartesian_to_plr_coord(
            teachpoint.coordinates, teachpoint.orientation
        )
        handling = request.handling
        await self._pick_place.pick(
            _grip_height(location, handling),
            direction,
            handling,
            orientation=elbow,
            envelope=self._work_envelope(),
        )

        for waypoint in reversed(request.gateway_path):
            await self._move_through_waypoint(waypoint)

    async def place_at_coords(self, request: PlaceAtCoordsRequest) -> None:
        """Place plate at coordinates specified by teachpoint.

        Traverses the pre-resolved `gateway_path` (outermost first), performs
        the place at `teachpoint`, then retraces the path in reverse. When
        `gateway_path` is empty this is the direct-target place (no gateway).
        """
        teachpoint = request.teachpoint

        for waypoint in request.gateway_path:
            await self._move_through_waypoint(waypoint)

        if await self._needs_crossover(teachpoint):
            await self._perform_crossover_maneuver()

        assert isinstance(teachpoint.coordinates, CartesianCoordinates)
        location, direction, elbow = convert_cartesian_to_plr_coord(
            teachpoint.coordinates, teachpoint.orientation
        )
        handling = request.handling
        await self._pick_place.place(
            _grip_height(location, handling),
            direction,
            handling,
            orientation=elbow,
            envelope=self._work_envelope(),
        )

        for waypoint in reversed(request.gateway_path):
            await self._move_through_waypoint(waypoint)

    async def move_to_coords(self, request: MoveToCoordsRequest) -> None:
        """Move to coordinates specified by teachpoint."""
        teachpoint = request.teachpoint
        # Check if crossover is needed before moving
        if await self._needs_crossover(teachpoint):
            await self._perform_crossover_maneuver()

        if teachpoint.is_joint_space():
            await self._move_to_joint_coords(teachpoint)
        else:
            assert isinstance(teachpoint.coordinates, CartesianCoordinates)
            location, direction, elbow = convert_cartesian_to_plr_coord(
                teachpoint.coordinates, teachpoint.orientation
            )
            await self._backend.move_to_location(location, direction, orientation=elbow)

    async def move_single_axis(self, request: MoveSingleAxisRequest) -> None:
        """Move a single axis to absolute position."""
        await self._backend.move_one_axis(self._get_axis_index(request.axis), request.position)

    async def move_single_axis_relative(self, request: MoveSingleAxisRelativeRequest) -> None:
        """Move a single axis by relative distance from current position."""
        await self._backend.move_one_axis_relative(
            self._get_axis_index(request.axis), request.distance
        )

    async def set_free_mode(self, request: SetFreeModeRequest) -> None:
        """Enable/disable free mode (freedrive) for specified axes."""
        axes = request.axes
        if axes == "none":
            await self._backend.stop_freedrive_mode()
        elif axes == "all":
            await self._backend.start_freedrive_mode()
        elif isinstance(axes, list) and len(axes) == 1:
            await self._backend.start_freedrive_mode([int(self._get_axis_index(axes[0]))])
        else:
            # No per-axis subset on the wire; freeing everything is the honest fallback.
            await self._backend.start_freedrive_mode()

    async def get_cartesian_position(self, request: GetCartesianPositionRequest) -> CartesianCoordinates:
        """Get current position in Cartesian coordinates from the robot."""
        plr_coords = await self._backend.request_gripper_pose()
        # The arm solves this from its own kinematics rather than asking the controller.
        return CartesianCoordinates(
            x=plr_coords.location.x,
            y=plr_coords.location.y,
            z=plr_coords.location.z,
            roll=plr_coords.rotation.x,
            pitch=plr_coords.rotation.y,
            yaw=plr_coords.rotation.z,
        )

    async def set_speed(self, request: SetSpeedRequest) -> None:
        """Set movement speed as percentage of maximum (0.0 to 1.0)."""
        # PLR expects 0-100, our interface uses 0.0-1.0
        await self._backend.set_monitor_speed(round(request.speed * 100.0))

    async def get_speed(self, request: GetSpeedRequest) -> float:
        """Get current movement speed setting as percentage (0.0 to 1.0)."""
        # PLR returns 0-100, convert to 0.0-1.0
        return await self._backend.request_monitor_speed() / 100.0

    async def halt(self, request: HaltRequest) -> None:
        """Emergency stop - immediately halt all movement."""
        await self._backend.halt()


    # -- World-state sync wire ops --
    #
    # Real-hardware drivers ignore these. Physical state is the source of truth;
    # the production driver does not maintain a synthetic graph the way
    # SimTransporterDriver does. The methods exist on this wrapper so the
    # ITransporterDriver ABC is satisfiable, but they are intentionally no-ops.

    async def seed_position(self, request: SeedPositionRequest) -> None:
        """No-op on real hardware: physical state is the source of truth."""

    async def ensure_seeded(self, request: EnsureSeededRequest) -> None:
        """No-op on real hardware: physical state is the source of truth."""

    async def unseed_position(self, request: UnseedPositionRequest) -> None:
        """No-op on real hardware: physical state is the source of truth."""

    async def reset_world(self, request: ResetWorldRequest) -> None:
        """No-op on real hardware: physical state is the source of truth."""


def transporter_driver(backend: ITransporterDriver | PLRArmBackend) -> ITransporterDriver:
    """Wrap a PLR arm backend into an ITransporterDriver, or pass through if already one."""
    if isinstance(backend, PLRArmBackend):
        return PLRTransporterBackendWrapper(backend)
    return backend
