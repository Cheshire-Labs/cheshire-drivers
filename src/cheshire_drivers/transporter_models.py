"""Pydantic models for transporter driver interface.

These models define the serialization boundary for all transporter operations.
Used by ITransporterDriver methods. Serializable for wire transport (WebSocket,
REST, etc.) while also usable as plain Python objects for in-process calls.

All models reject unknown fields (`extra='forbid'`). Silent-drop is a wire bug we
do not tolerate.

`TeachpointModel` is a wire-only mirror of `Teachpoint.to_dict()` used by
gateway controller pre-flight to validate the flat-dict shape clients send for
`pick_at_coords` / `place_at_coords` / `move_to_coords`. The driver-side
Requests (`PickAtCoordsRequest`, etc.) carry the legacy `Teachpoint` dataclass
directly; a `mode='before'` field validator transparently converts incoming
dicts via `Teachpoint.from_dict` so wire deserialization in orca-client
constructs a real `Teachpoint` for the driver method to use.

Vocabulary: `position_id` is the world-relative deck-slot identifier (one per
physical slot). `Teachpoint` carries the robot-relative coordinates that reach
that Position. One Position can have N Teachpoints (one per transporter that
can reach it); the join key is `(transporter_device_id, position_id)`.
"""

from typing import Dict, List, Literal, Optional, Union

from pydantic import ConfigDict, field_validator, model_validator
from typing_extensions import Self

from cheshire_drivers.labware_models import LabwareIdentity
from cheshire_drivers.liquid_handler_models import _StrictModel
from cheshire_drivers.move_parameters import MoveParameterPatch, MoveParameters
from cheshire_drivers.teachpoints import (
    CartesianCoordinates,
    JointCoordinates,
    Teachpoint,
)


AxisName = Literal["rail", "base", "shoulder", "elbow", "wrist", "gripper"]
"""Joint axis names accepted by the transporter driver. Mirrors the AxisName
Literal exported from `cheshire_drivers.interfaces` to avoid an import cycle."""


class TeachpointModel(_StrictModel):
    """Wire-shape Pydantic mirror of `Teachpoint.to_dict()` flat-dict output.

    Used by `validate_transporter_payload` to reject malformed teachpoint dicts
    at the network boundary before they reach the driver. The shape mirrors
    exactly what `Teachpoint.to_dict()` emits and what a hosted deployment's
    `TeachpointResolver` returns from the database; all coordinate / access /
    gateway fields are optional because joint and cartesian coordinates are
    mutually exclusive and waypoints may carry no coordinates at all.
    """

    position_id: str
    coord_type: str = "joint"

    rail: Optional[float] = None
    base: Optional[float] = None
    shoulder: Optional[float] = None
    elbow: Optional[float] = None
    wrist: Optional[float] = None

    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    yaw: Optional[float] = None
    pitch: Optional[float] = None
    roll: Optional[float] = None
    orientation: Optional[str] = None

    access_type: Optional[str] = None
    gripper_offset: Optional[float] = None
    vertical_clearance: Optional[float] = None
    horizontal_clearance: Optional[float] = None

    gateway: Optional[str] = None

    access_config_name: Optional[str] = None
    """Name of the persistent AccessConfig this teachpoint references.

    Populated by server-side stores when a teachpoint is materialized from a
    named ``AccessConfig`` row. ``None`` for teachpoints constructed with
    inline access fields. cheshire-drivers itself does not consume this; it
    rides on the wire so cloud-side bookkeeping can round-trip the name.
    """

    taught_with: Optional[str] = None
    """The labware type this position was taught with, if it was taught on one.

    Grip height is measured against whatever sat in the nest when somebody
    jogged the arm here. Moving a taller labware to the same position needs the
    difference, and without knowing what it was taught on there is nothing to
    take the difference from.
    """

    by_labware: Optional[Dict[str, MoveParameterPatch]] = None
    """Per-labware-type overrides for this one position, as sparse patches.

    The narrowest layer that a site can express: this labware, here. A nest that
    grips most plates fine but needs 3mm more clearance for one deep-well plate
    says so here rather than in that plate's profile, which would apply it
    everywhere the plate goes.
    """

    @model_validator(mode="after")
    def _validate_coordinate_exclusivity(self) -> "TeachpointModel":
        joint_fields = (self.base, self.shoulder, self.elbow, self.wrist)
        joint_present = any(f is not None for f in joint_fields)
        cartesian_fields = (self.x, self.y, self.z, self.yaw, self.pitch, self.roll)
        cartesian_present = any(f is not None for f in cartesian_fields)

        if joint_present and cartesian_present:
            raise ValueError(
                f"Teachpoint '{self.position_id}' has both joint and cartesian "
                f"coordinates; pass one or the other"
            )
        if joint_present and not all(f is not None for f in joint_fields):
            raise ValueError(
                f"Teachpoint '{self.position_id}' has partial joint coordinates; "
                f"base/shoulder/elbow/wrist are all required when joint coords used"
            )
        if cartesian_present and not all(f is not None for f in cartesian_fields):
            raise ValueError(
                f"Teachpoint '{self.position_id}' has partial cartesian coordinates; "
                f"x/y/z/yaw/pitch/roll are all required when cartesian coords used"
            )
        return self

    @classmethod
    def from_driver_teachpoint(cls, tp: Teachpoint) -> "TeachpointModel":
        """Build the wire model from a hydrated `Teachpoint` value object.

        The `Teachpoint` already carries resolved access fields (the runtime
        store resolves the `AccessConfig` at hydration time). This factory
        splays the typed coordinates into the flat wire shape and carries
        through the persistent `access_config_name` when present.
        """
        gripper_offset = tp.gripper_offset if tp.access_type is not None else None
        vertical_clearance = (
            tp.vertical_clearance if tp.access_type is not None else None
        )
        horizontal_clearance = (
            tp.horizontal_clearance if tp.access_type is not None else None
        )

        if isinstance(tp.coordinates, CartesianCoordinates):
            return cls(
                position_id=tp.position_id,
                coord_type="cartesian",
                x=tp.coordinates.x,
                y=tp.coordinates.y,
                z=tp.coordinates.z,
                yaw=tp.coordinates.yaw,
                pitch=tp.coordinates.pitch,
                roll=tp.coordinates.roll,
                access_type=tp.access_type,
                gripper_offset=gripper_offset,
                vertical_clearance=vertical_clearance,
                horizontal_clearance=horizontal_clearance,
                orientation=tp.orientation,
                gateway=tp.gateway,
                access_config_name=tp.access_config_name,
            )
        if isinstance(tp.coordinates, JointCoordinates):
            return cls(
                position_id=tp.position_id,
                coord_type="joint",
                rail=tp.coordinates.rail,
                base=tp.coordinates.base,
                shoulder=tp.coordinates.shoulder,
                elbow=tp.coordinates.elbow,
                wrist=tp.coordinates.wrist,
                access_type=tp.access_type,
                gripper_offset=gripper_offset,
                vertical_clearance=vertical_clearance,
                horizontal_clearance=horizontal_clearance,
                orientation=tp.orientation,
                gateway=tp.gateway,
                access_config_name=tp.access_config_name,
            )
        return cls(
            position_id=tp.position_id,
            coord_type="joint",
            access_type=tp.access_type,
            gripper_offset=gripper_offset,
            vertical_clearance=vertical_clearance,
            horizontal_clearance=horizontal_clearance,
            orientation=tp.orientation,
            gateway=tp.gateway,
            access_config_name=tp.access_config_name,
        )


def _teachpoint_validator(v: object) -> Teachpoint:
    """Field validator: accept `Teachpoint` directly OR a wire dict.

    Used by every Request model that carries a teachpoint. When the input is a
    dict, `TeachpointModel.model_validate` enforces the wire shape (rejects
    unknown fields, validates coordinate exclusivity) before
    `Teachpoint.from_dict` constructs the dataclass the driver method consumes.
    """
    if isinstance(v, Teachpoint):
        return v
    if isinstance(v, dict):
        TeachpointModel.model_validate(v)
        return Teachpoint.from_dict(v)
    raise ValueError(
        f"teachpoint must be Teachpoint or dict, got {type(v).__name__}"
    )


class _TeachpointCarryingRequest(_StrictModel):
    """Base for requests carrying a `Teachpoint`.

    Allows the legacy `Teachpoint` dataclass as a field type and wires the
    dict-or-instance coercion via a single field validator so subclasses do
    not duplicate the boilerplate.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    teachpoint: Teachpoint
    # Set by the external-control (ad-hoc) surface; when True the sim transporter
    # skips ALL world validation (incl. expected_labware) and only sims motion.
    external_control: bool = False

    @field_validator("teachpoint", mode="before")
    @classmethod
    def _coerce_teachpoint(cls, v: object) -> Teachpoint:
        return _teachpoint_validator(v)


def _teachpoint_list_validator(v: object) -> List[Teachpoint]:
    """Field validator: coerce a list of teachpoints (or wire dicts) into Teachpoints.

    Used by `PickAtCoordsRequest` / `PlaceAtCoordsRequest` to carry a
    pre-resolved gateway path. Each item in the list is wire-validated and
    materialized to a `Teachpoint` via the same shape rules as the
    `teachpoint` field.
    """
    if not isinstance(v, list):
        raise ValueError(
            f"gateway_path must be list[Teachpoint] or list[dict], got {type(v).__name__}"
        )
    return [_teachpoint_validator(item) for item in v]


# --- Teachpoint-coordinate commands ---


class PickAtCoordsRequest(_TeachpointCarryingRequest):
    """Pick at a fully-flattened teachpoint, optionally traversing a gateway chain.

    `gateway_path` is the ordered list of waypoints (outermost first, innermost
    last) that the driver must visit before the final pick at `teachpoint`.
    A hosted deployment resolves the chain server-side via `ITeachpointStore.resolve` so
    orca-client never does a name lookup. After picking the driver retraces
    the path in reverse to exit. An empty list (the default) means no gateway
    traversal -- equivalent to the previous behaviour for direct pick targets.

    `expected_labware` carries the orca-core labware identity of the plate
    being picked. When present, orca-client's transporter sim cross-checks
    that the named position holds this exact labware (by id) before the pick
    proceeds. Optional during rollout so older wire callers that have not
    been threaded through still work; `None` skips the cross-check.
    """

    labware_type: str = ""
    gateway_path: List[Teachpoint] = []
    expected_labware: Optional[LabwareIdentity] = None
    handling: MoveParameters
    """Every scalar the arm needs for this one move, already resolved.

    The sender owns the layering (defaults, then the labware, then the site,
    then how this labware is being carried right now); by the time it reaches
    a driver it is plain numbers, so no driver needs a labware catalog to
    work out how to hold something."""

    @field_validator("gateway_path", mode="before")
    @classmethod
    def _coerce_gateway_path(cls, v: object) -> List[Teachpoint]:
        return _teachpoint_list_validator(v)


class PlaceAtCoordsRequest(_TeachpointCarryingRequest):
    """Place at a fully-flattened teachpoint, optionally traversing a gateway chain.

    See `PickAtCoordsRequest` for `gateway_path` semantics.

    `expected_labware` carries the orca-core labware identity of the plate
    being placed. orca-client's transporter sim cross-checks that the
    resource currently held by the gripper matches this identity (by
    `labware_id`) BEFORE the place proceeds. Mismatch raises
    `SimTransporterValidationError` at the place site rather than at the
    next pick (which would surface as world drift). Mirror of the pick-side
    check on `PickAtCoordsRequest`. Optional during rollout; `None` skips
    the cross-check.
    """

    labware_type: str = ""
    gateway_path: List[Teachpoint] = []
    expected_labware: Optional[LabwareIdentity] = None
    handling: MoveParameters
    """Every scalar the arm needs for this one move, already resolved.

    The sender owns the layering (defaults, then the labware, then the site,
    then how this labware is being carried right now); by the time it reaches
    a driver it is plain numbers, so no driver needs a labware catalog to
    work out how to hold something."""

    @field_validator("gateway_path", mode="before")
    @classmethod
    def _coerce_gateway_path(cls, v: object) -> List[Teachpoint]:
        return _teachpoint_list_validator(v)


class MoveToCoordsRequest(_TeachpointCarryingRequest):
    pass



# --- World-state sync wire ops ---
#
# These ops let cloud-side code keep orca-client's transporter sim graph in
# sync with the cloud-side labware ledger across process boundaries.
# Workflow-internal: not exposed via MCP/REST/CLI. The driver-side handlers
# (SimTransporterDriver) translate `LabwareIdentity` to a placeholder PLR
# resource so the existing PLR-resource-graph validation runs unchanged.


class SeedPositionRequest(_StrictModel):
    """Declare that `labware` is staged at `position_id`.

    Idempotency NOT guaranteed: raises if the position is already occupied
    (real conflict). Use `EnsureSeededRequest` for idempotent re-sync.
    """

    position_id: str
    labware: LabwareIdentity


class EnsureSeededRequest(_StrictModel):
    """Idempotent seed: no-op if `labware` is already at `position_id`,
    seeds otherwise. If a DIFFERENT labware occupies the position the sim
    reconciles to the engine ledger (evicts the stale occupant, seeds this
    one) rather than vetoing -- the ledger is the source of truth.
    """

    position_id: str
    labware: LabwareIdentity


class UnseedPositionRequest(_StrictModel):
    """Idempotent removal of `labware` from `position_id`. No-op if the
    position does not currently hold this `labware`.
    """

    position_id: str
    labware: LabwareIdentity


# --- Single-axis / motion-mode commands ---


class MoveSingleAxisRequest(_StrictModel):
    axis: AxisName
    position: float


class MoveSingleAxisRelativeRequest(_StrictModel):
    axis: AxisName
    distance: float


class SetFreeModeRequest(_StrictModel):
    axes: Union[List[AxisName], Literal["all", "none"]]


class SetSpeedRequest(_StrictModel):
    speed: float


# --- Empty marker requests ---
#
# Mirrors LH's GetDeckStateRequest (cheshire-drivers da17814): every wire-callable
# transporter command takes a Pydantic Request, including parameterless lifecycle
# methods and query reads. Empty markers preserve uniform wire shape and let the
# pre-flight + executor wrap pipeline stay registry-driven without category-level
# special cases.


class InitializeRequest(_StrictModel):
    pass


class MoveToSafeRequest(_StrictModel):
    pass


class OpenGripperRequest(_StrictModel):
    jaw_opening: Optional[float] = None
    """How far past the calibrated grip position to open, in gripper-axis units.

    The same number a pick opens by, so whatever decides it for a move answers this
    too and the jaws an operator opens match the jaws a pick opens. None takes the
    driver's default opening."""
    position: Optional[float] = None
    """Absolute gripper-axis position, for a caller holding a taught pose rather than
    an opening. Reading one off the arm takes `get_joint_position`."""

    @model_validator(mode="after")
    def _one_way_of_saying_how_far(self) -> Self:
        if self.position is not None and self.jaw_opening is not None:
            raise ValueError(
                "open_gripper takes jaw_opening (a step past the grip position) or "
                "position (an absolute axis target), not both."
            )
        return self


class CloseGripperRequest(_StrictModel):
    position: Optional[float] = None
    """Gripper-axis position to close to. None closes to the calibrated grip position."""


class HaltRequest(_StrictModel):
    pass


class GetJointPositionRequest(_StrictModel):
    pass


class GetCartesianPositionRequest(_StrictModel):
    pass


class GetSpeedRequest(_StrictModel):
    pass


class ResetWorldRequest(_StrictModel):
    """Wipe all labware occupancy from the transporter's sim world graph.

    Parameterless: clears every seeded position and the gripper. Used by the
    engine's authoritative `clear_all_labware` so the sim projection agrees
    with a ledger that now holds no labware. Real-hardware drivers no-op.
    """
    pass


# --- Response models ---


class SpeedResponse(_StrictModel):
    """Response payload for ``get_speed``."""

    speed: float
