from __future__ import annotations
from typing import Any, ClassVar, Iterable, List, Dict, Literal, Optional, Protocol, runtime_checkable
import json

from cheshire_drivers.liquid_handler_models import _StrictModel
from cheshire_drivers.move_parameters import MoveParameterPatch


@runtime_checkable
class ITeachpointStore(Protocol):
    """System-facing teachpoint lookup contract.

    Vocabulary: `position_id` is the world-relative deck-slot identifier
    (one per physical slot). A `Teachpoint` is one transporter's path TO
    that Position. One Position can have N Teachpoints (one per reachable
    transporter); the join key is `(transporter_device_id, position_id)`.
    This store is owned per-Transporter, so callers only pass `position_id`.

    The system layer (orca-core's `Transporter.pick`/`.place`) calls
    `await store.resolve(position_id)` to materialize a fully-flattened
    `Teachpoint` plus walk any gateway chain before dispatching
    `pick_at_coords` / `place_at_coords` / `move_to_coords` to the
    driver. Drivers do NOT bind or read the store on the dispatch path;
    the wire payload IS the source of truth.

    The store may be backed by an in-process dict (source-available local seed) or
    by Postgres in a hosted deployment.

    `resolve(position_id)` is the wire-source-of-truth lookup that returns a
    fully flattened Teachpoint with access fields inlined. Conceptually
    `resolve` answers "what teachpoint value should the driver receive?"
    while `get` is the raw CRUD lookup; for in-memory and DB-backed
    stores those are the same value because the `Teachpoint` constructor
    inlines access fields, but the explicit `resolve` name documents the
    contract that callers depend on.

    `list_sync` exists for sync consumers (the System graph builder in
    orca-core). Stores backed by storage that requires async I/O may
    hold a build-time snapshot to satisfy `list_sync`.
    """

    async def get(self, position_id: str) -> Optional[Teachpoint]: ...

    async def resolve(self, position_id: str) -> Optional[Teachpoint]: ...

    def list_sync(self) -> List[Teachpoint]: ...


class InMemoryTeachpointStore:
    """In-process `ITeachpointStore` for source-available local use.

    Constructed with the teachpoints the topology declared (or loaded from
    a JSON file). Mutations are sync so callers can update the store from
    any context.
    """

    def __init__(self, teachpoints: Iterable[Teachpoint] = ()) -> None:
        self._by_position_id: Dict[str, Teachpoint] = {tp.position_id: tp for tp in teachpoints}

    async def get(self, position_id: str) -> Optional[Teachpoint]:
        return self._by_position_id.get(position_id)

    async def resolve(self, position_id: str) -> Optional[Teachpoint]:
        """Return the fully-flattened teachpoint for `position_id`, or None if missing.

        Equivalent to `get` for in-memory stores: the `Teachpoint` constructor
        inlines access fields at construction time, so the stored value is
        already wire-ready.
        """
        return self._by_position_id.get(position_id)

    def list_sync(self) -> List[Teachpoint]:
        return list(self._by_position_id.values())

    def replace_all(self, teachpoints: Iterable[Teachpoint]) -> None:
        """Refresh the store with a new list."""
        self._by_position_id = {tp.position_id: tp for tp in teachpoints}


class NullTeachpointStore:
    """`ITeachpointStore` that holds no teachpoints and rejects all lookups.

    Bound on orca-client when no local seed exists. The gateway resolves
    position ids its side and pushes fully-flattened `Teachpoint`
    values through `pick_at_coords` / `place_at_coords` / `move_to_coords`,
    so the wire-side driver never needs to call `get` / `resolve`. The
    store fails loudly on any lookup, surfacing wire-protocol drift
    instead of silently returning None.
    """

    async def get(self, position_id: str) -> Optional[Teachpoint]:
        raise RuntimeError(
            f"NullTeachpointStore: lookup of {position_id!r} is not supported. "
            "orca-client drivers receive fully-resolved Teachpoint values "
            "from the caller via pick_at_coords / place_at_coords / move_to_coords; "
            "they must not call get / resolve."
        )

    async def resolve(self, position_id: str) -> Optional[Teachpoint]:
        raise RuntimeError(
            f"NullTeachpointStore: resolve of {position_id!r} is not supported. "
            "orca-client drivers receive fully-resolved Teachpoint values "
            "from the caller via pick_at_coords / place_at_coords / move_to_coords; "
            "they must not call get / resolve."
        )

    def list_sync(self) -> List[Teachpoint]:
        return []


class AccessConfig(_StrictModel):
    """Defines how a robotic arm approaches and retracts from a location.

    Access configs are defined in JSON teachpoint files and can be referenced by multiple
    teachpoints to avoid duplication.

    For VERTICAL access (stacks, deck positions):
        - vertical_clearance: Distance (mm) above teachpoint for approach/depart
        - gripper_offset: Extra height (mm) the arm rises leaving a station with a
          plate in the jaws, so the skirt clears what it was sitting in. An empty
          retreat does not take it.

    For HORIZONTAL access (hotel-style carriers):
        - horizontal_clearance: Distance (mm) outside slot for approach/depart
        - vertical_clearance: Distance (mm) to lift after horizontal retract
        - gripper_offset: Unused. A shelf is left by rising only what the station
          declares and then withdrawing level, because a full lift would meet the
          shelf above.
    """
    name: str
    access_type: Literal["vertical", "horizontal"]
    gripper_offset: float = 20.0
    vertical_clearance: float = 20.0
    horizontal_clearance: float = 100.0

class CartesianCoordinates(_StrictModel):
    coord_type: ClassVar[str] = "cartesian"
    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    roll: float

    def __init__(
        self,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        yaw: float | None = None,
        pitch: float | None = None,
        roll: float | None = None,
        **data: Any,
    ) -> None:
        if x is not None:
            data.setdefault("x", x)
        if y is not None:
            data.setdefault("y", y)
        if z is not None:
            data.setdefault("z", z)
        if yaw is not None:
            data.setdefault("yaw", yaw)
        if pitch is not None:
            data.setdefault("pitch", pitch)
        if roll is not None:
            data.setdefault("roll", roll)
        super().__init__(**data)

    def to_dict(self) -> Dict[str, float]:
        return self.model_dump()


class JointCoordinates(_StrictModel):
    """Joint coordinates using semantic naming - all 6 joints.

    Rail defaults to 0.0 for robots without rail.
    Gripper defaults to 0.0 (teachpoints typically don't store gripper state).
    """
    coord_type: ClassVar[str] = "joint"
    rail: float = 0.0
    base: float = 0.0
    shoulder: float = 0.0
    elbow: float = 0.0
    wrist: float = 0.0
    gripper: float = 0.0

    def __init__(
        self,
        rail: float | None = None,
        base: float | None = None,
        shoulder: float | None = None,
        elbow: float | None = None,
        wrist: float | None = None,
        gripper: float | None = None,
        **data: Any,
    ) -> None:
        if rail is not None:
            data.setdefault("rail", rail)
        if base is not None:
            data.setdefault("base", base)
        if shoulder is not None:
            data.setdefault("shoulder", shoulder)
        if elbow is not None:
            data.setdefault("elbow", elbow)
        if wrist is not None:
            data.setdefault("wrist", wrist)
        if gripper is not None:
            data.setdefault("gripper", gripper)
        super().__init__(**data)

    def to_dict(self) -> Dict[str, float]:
        return self.model_dump()

class Teachpoint:
    """One transporter's coordinates to reach a Position.

    `position_id` is the world-relative deck-slot identifier this teachpoint
    targets. A single Position can have N Teachpoints (one per transporter
    that can reach it); the per-Transporter `ITeachpointStore` keys on
    `position_id` only, while a hosted cross-transporter facade keys on
    `(transporter_device_id, position_id)`.
    """

    def __init__(
        self,
        position_id: str,
        coordinates: CartesianCoordinates | JointCoordinates | None = None,
        orientation: str | None = None,
        access: AccessConfig | None = None,
        access_type: str | None = None,
        gripper_offset: float = 20.0,
        vertical_clearance: float = 20.0,
        horizontal_clearance: float = 100.0,
        gateway: str | None = None,
        taught_with: str | None = None,
        by_labware: Dict[str, MoveParameterPatch] | None = None,
    ) -> None:
        if access is not None and access_type is not None:
            raise ValueError(
                f"Teachpoint '{position_id}': pass either 'access' or individual access params, not both"
            )

        self.position_id = position_id
        self.coordinates = coordinates
        self.orientation = orientation
        self.gateway = gateway
        self.taught_with = taught_with
        self.by_labware: Dict[str, MoveParameterPatch] = dict(by_labware or {})

        if access is not None:
            self.access_type = access.access_type
            self.gripper_offset = access.gripper_offset
            self.vertical_clearance = access.vertical_clearance
            self.horizontal_clearance = access.horizontal_clearance
            self._access_config_name: str | None = access.name
        else:
            self.access_type = access_type
            self.gripper_offset = gripper_offset
            self.vertical_clearance = vertical_clearance
            self.horizontal_clearance = horizontal_clearance
            self._access_config_name = None

        if self.is_cartesian() and self.orientation is None:
            raise ValueError(
                f"Cartesian teachpoint '{self.position_id}' must specify orientation (left/right)"
            )

        if self.access_type is not None and not self.is_cartesian():
            raise ValueError(
                f"Teachpoint '{self.position_id}' has access_type but uses joint coordinates. "
                "Pick/place locations require Cartesian coordinates for access patterns."
            )

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        """Provide a Pydantic core schema so Teachpoint participates in JSON schema generation.

        Without this hook, Pydantic falls back to IsInstanceSchema for the
        Teachpoint field type (since Teachpoint is a hand-written class, not a
        BaseModel). IsInstanceSchema has no JSON schema representation, which
        breaks orca-client's handshake: it calls model_json_schema() on every
        driver-method Request to populate DeviceConnectInfo.methods, and the
        transporter Request models carry Teachpoint fields.

        Delegates validation/JSON-schema to TeachpointModel (the wire mirror)
        and runs Teachpoint.from_dict to produce the dataclass instance the
        driver methods consume. Lazy-imports TeachpointModel to avoid the
        import cycle with transporter_models.
        """
        from pydantic_core import core_schema
        from cheshire_drivers.transporter_models import TeachpointModel

        def _validate(value: object) -> "Teachpoint":
            if isinstance(value, cls):
                return value
            if isinstance(value, TeachpointModel):
                return cls.from_dict(value.model_dump(exclude_none=True))
            if isinstance(value, dict):
                TeachpointModel.model_validate(value)
                return cls.from_dict(value)
            raise ValueError(
                f"teachpoint must be Teachpoint, TeachpointModel, or dict, "
                f"got {type(value).__name__}"
            )

        def _serialize(value: "Teachpoint") -> Dict[str, Any]:
            return value.to_dict()

        return core_schema.no_info_plain_validator_function(
            _validate,
            serialization=core_schema.plain_serializer_function_ser_schema(_serialize),
            json_schema_input_schema=TeachpointModel.__pydantic_core_schema__,
        )

    @property
    def access_config_name(self) -> str | None:
        """Name of the AccessConfig this teachpoint references, or None.

        ``None`` means the teachpoint was constructed with inline access
        fields (``access_type=...``, ``gripper_offset=...``, etc.) rather
        than a named ``AccessConfig``. Inline-access teachpoints can be
        used directly with drivers (the wire path uses them) but are
        rejected by every persistent teachpoint store: they have no
        AccessConfig row to round-trip through, and silent calibration
        defaulting on re-read would be dangerous.

        Use ``access=AccessConfig(name=..., ...)`` at construction to make
        a teachpoint persistable across any store backend.
        """
        return self._access_config_name

    def is_joint_space(self) -> bool:
        """Returns True if this teachpoint uses joint-space coordinates."""
        return isinstance(self.coordinates, JointCoordinates)

    def is_cartesian(self) -> bool:
        """Returns True if this teachpoint uses Cartesian coordinates."""
        return isinstance(self.coordinates, CartesianCoordinates)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize teachpoint to dictionary for network transmission."""
        result: Dict[str, Any] = {"position_id": self.position_id}

        if self.is_joint_space():
            coords = self.coordinates
            assert isinstance(coords, JointCoordinates)
            result.update({
                "base": coords.base,
                "shoulder": coords.shoulder,
                "elbow": coords.elbow,
                "wrist": coords.wrist,
            })
            if coords.rail != 0.0:
                result["rail"] = coords.rail
        elif self.is_cartesian():
            coords = self.coordinates
            assert isinstance(coords, CartesianCoordinates)
            result.update({
                "x": coords.x,
                "y": coords.y,
                "z": coords.z,
                "yaw": coords.yaw,
                "pitch": coords.pitch,
                "roll": coords.roll,
                "orientation": self.orientation,
            })

        if self.access_type is not None:
            result.update({
                "access_type": self.access_type,
                "gripper_offset": self.gripper_offset,
                "vertical_clearance": self.vertical_clearance,
                "horizontal_clearance": self.horizontal_clearance,
            })

        if self.gateway is not None:
            result["gateway"] = self.gateway

        if self.taught_with is not None:
            result["taught_with"] = self.taught_with
        if self.by_labware:
            result["by_labware"] = {
                labware_type: patch.model_dump(exclude_none=True)
                for labware_type, patch in self.by_labware.items()
            }

        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Teachpoint:
        """Deserialize teachpoint from dictionary.

        Detects coordinate type by presence of 'base' (joint-space) or 'x' (Cartesian) keys.
        Defaults to no coordinates if neither is present (waypoint without position).
        """
        # Detect coordinate type by field presence
        if "base" in data:
            coordinates: CartesianCoordinates | JointCoordinates | None = JointCoordinates(
                base=float(data["base"]),
                shoulder=float(data["shoulder"]),
                elbow=float(data["elbow"]),
                wrist=float(data["wrist"]),
                rail=float(data.get("rail", 0.0)),
            )
            orientation = None
        elif "x" in data:
            coordinates = CartesianCoordinates(
                x=float(data["x"]),
                y=float(data["y"]),
                z=float(data["z"]),
                yaw=float(data["yaw"]),
                pitch=float(data["pitch"]),
                roll=float(data["roll"]),
            )
            orientation = data.get("orientation")
        else:
            coordinates = None
            orientation = None

        return cls(
            position_id=data["position_id"],
            coordinates=coordinates,
            orientation=orientation,
            access_type=data.get("access_type"),
            gripper_offset=float(data.get("gripper_offset", 20.0)),
            vertical_clearance=float(data.get("vertical_clearance", 20.0)),
            horizontal_clearance=float(data.get("horizontal_clearance", 100.0)),
            gateway=data.get("gateway"),
            taught_with=data.get("taught_with"),
            by_labware={
                labware_type: MoveParameterPatch.model_validate(patch)
                for labware_type, patch in (data.get("by_labware") or {}).items()
            },
        )

    @staticmethod
    def load_teachpoints_from_file(file_path: str) -> List[Teachpoint]:
        with open(file_path, 'r') as f:
            data = json.load(f)

        # Parse access configs from JSON
        access_configs: Dict[str, AccessConfig] = {}
        if 'access_configs' in data:
            for name, cfg_data in data['access_configs'].items():
                access_configs[name] = AccessConfig(
                    name=name,
                    access_type=cfg_data['access_type'],
                    gripper_offset=float(cfg_data.get('gripper_offset', 20.0)),
                    vertical_clearance=float(cfg_data.get('vertical_clearance', 20.0)),
                    horizontal_clearance=float(cfg_data.get('horizontal_clearance', 100.0)),
                )

        # Add hardcoded defaults only if not defined in JSON
        if 'default_vertical' not in access_configs:
            access_configs['default_vertical'] = AccessConfig(
                name='default_vertical', access_type='vertical',
                gripper_offset=20.0, vertical_clearance=20.0, horizontal_clearance=50.0,
            )
        if 'default_horizontal' not in access_configs:
            access_configs['default_horizontal'] = AccessConfig(
                name='default_horizontal', access_type='horizontal',
                gripper_offset=20.0, vertical_clearance=20.0, horizontal_clearance=100.0,
            )

        # Parse teachpoints and resolve access config references
        teachpoints: List[Teachpoint] = []
        for tp_data in data.get('teachpoints', []):
            # Detect coordinate type by field presence
            if 'base' in tp_data:
                # Joint coordinates
                coordinates: CartesianCoordinates | JointCoordinates = JointCoordinates(
                    base=float(tp_data['base']),
                    shoulder=float(tp_data['shoulder']),
                    elbow=float(tp_data['elbow']),
                    wrist=float(tp_data['wrist']),
                    rail=float(tp_data.get('rail', 0.0))
                )
                orientation = None
            else:
                # Cartesian coordinates
                coordinates = CartesianCoordinates(
                    x=float(tp_data['x']),
                    y=float(tp_data['y']),
                    z=float(tp_data['z']),
                    yaw=float(tp_data['yaw']),
                    pitch=float(tp_data['pitch']),
                    roll=float(tp_data['roll'])
                )
                orientation = tp_data.get('orientation', None)

            # Resolve access config (optional for waypoints)
            config_name = tp_data.get('access')
            if config_name is not None:
                if config_name not in access_configs:
                    raise ValueError(
                        f"Teachpoint '{tp_data['position_id']}' references unknown access config '{config_name}'"
                    )
                cfg = access_configs[config_name]
            else:
                cfg = None

            tp = Teachpoint(
                position_id=tp_data['position_id'],
                coordinates=coordinates,
                orientation=orientation,
                access=cfg,
                gateway=tp_data.get('gateway', None)
            )
            teachpoints.append(tp)

        return teachpoints


class TeachpointsRegistry:
    def __init__(self) -> None:
        self._registry: Dict[str, Teachpoint] = {}

    def add(self, teachpoint: Teachpoint, overwrite = True) -> None:
        """Add a teachpoint to the registry."""
        if not overwrite and self.exists(teachpoint.position_id):
            raise KeyError(f"Teachpoint '{teachpoint.position_id}' already exists and overwrite is disabled")
        self._registry[teachpoint.position_id] = teachpoint

    def get(self, position_id: str) -> Teachpoint:
        """Get a teachpoint by position_id."""
        if position_id not in self._registry:
            raise KeyError(f"Teachpoint '{position_id}' not found")
        return self._registry[position_id]

    def update(self, position_id: str, teachpoint: Teachpoint) -> None:
        """Update an existing teachpoint."""
        if position_id not in self._registry:
            raise KeyError(f"Teachpoint '{position_id}' not found")
        self._registry[position_id] = teachpoint

    def delete(self, position_id: str) -> None:
        """Delete a teachpoint by position_id."""
        if position_id not in self._registry:
            raise KeyError(f"Teachpoint '{position_id}' not found")
        del self._registry[position_id]

    def list(self) -> List[Teachpoint]:
        """Get all teachpoints."""
        return list(self._registry.values())

    def exists(self, position_id: str) -> bool:
        """Check if a teachpoint exists."""
        return position_id in self._registry

    def save(self, filepath: str) -> None:
        """Saves teachpoints to file using access config references."""
        # Reconstruct unique access configs from teachpoints
        # Note: If multiple teachpoints claim same config name but have different params,
        # only the first one's params are used (assumes data is not corrupted)
        access_configs_dict: Dict[str, Dict[str, Any]] = {}

        for tp in self._registry.values():
            config_name = tp._access_config_name
            if config_name is None:
                continue  # Waypoint without access config

            # Skip hardcoded defaults (always available in load)
            if config_name.startswith('default_'):
                continue

            # Only write each config once (first occurrence)
            if config_name not in access_configs_dict:
                access_configs_dict[config_name] = {
                    'access_type': tp.access_type,
                    'gripper_offset': tp.gripper_offset,
                    'vertical_clearance': tp.vertical_clearance,
                    'horizontal_clearance': tp.horizontal_clearance,
                }

        # Build teachpoints list with access references
        teachpoints_list: List[Dict[str, Any]] = []
        for tp in self._registry.values():
            tp_dict: Dict[str, Any] = {'position_id': tp.position_id}

            # Serialize coordinates based on type
            if tp.is_joint_space():
                coords = tp.coordinates
                assert isinstance(coords, JointCoordinates)
                tp_dict.update({
                    'base': coords.base,
                    'shoulder': coords.shoulder,
                    'elbow': coords.elbow,
                    'wrist': coords.wrist,
                })
                if coords.rail != 0.0:
                    tp_dict['rail'] = coords.rail
            elif tp.is_cartesian():
                coords = tp.coordinates
                assert isinstance(coords, CartesianCoordinates)
                tp_dict.update({
                    'x': coords.x,
                    'y': coords.y,
                    'z': coords.z,
                    'yaw': coords.yaw,
                    'pitch': coords.pitch,
                    'roll': coords.roll,
                    'orientation': tp.orientation,
                })

            # Add access config reference if present
            if tp._access_config_name is not None:
                tp_dict['access'] = tp._access_config_name

            if tp.gateway:
                tp_dict['gateway'] = tp.gateway
            teachpoints_list.append(tp_dict)

        # Build final JSON structure
        data: Dict[str, Any] = {}
        if access_configs_dict:
            data['access_configs'] = access_configs_dict
        data['teachpoints'] = teachpoints_list

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

    def clear(self) -> None:
        self._registry = {}
