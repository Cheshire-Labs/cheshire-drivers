from __future__ import annotations
from typing import Any, List, Dict
import json

class AccessConfig:
    """Defines how a robotic arm approaches and retracts from a location.

    Access configs are defined in JSON teachpoint files and can be referenced by multiple
    teachpoints to avoid duplication. Parameters specify vertical or horizontal access strategies.
    """
    def __init__(
        self,
        name: str,
        access_type: str,
        gripper_offset: float = 20.0,
        retract_distance: float = 100.0,
        vertical_clearance: float = 50.0,
        z_above: float = 10.0
    ):
        self.name = name
        self.access_type = access_type  # "vertical" or "horizontal"
        self.gripper_offset = gripper_offset  # Gripper height compensation (always used)
        self.retract_distance = retract_distance  # For vertical access: how far to pull back
        self.vertical_clearance = vertical_clearance  # For horizontal access: clearance distance
        self.z_above = z_above  # For horizontal access: extra height above nest

class CartesianCoordinates:
    def __init__(self, x: float, y: float, z: float, yaw: float, pitch: float, roll: float):
        self.x = x
        self.y = y
        self.z = z
        self.yaw = yaw
        self.pitch = pitch
        self.roll = roll


class JointCoordinates:
    """Joint coordinates using semantic naming - all 6 joints.

    Rail defaults to 0.0 for robots without rail.
    Gripper defaults to 0.0 (teachpoints typically don't store gripper state).
    """
    def __init__(
        self,
        rail: float = 0.0,
        base: float = 0.0,
        shoulder: float = 0.0,
        elbow: float = 0.0,
        wrist: float = 0.0,
        gripper: float = 0.0
    ):
        self.rail = rail
        self.base = base
        self.shoulder = shoulder
        self.elbow = elbow
        self.wrist = wrist
        self.gripper = gripper

class Teachpoint:
    def __init__(
        self,
        name: str,
        coordinates: CartesianCoordinates | JointCoordinates | None = None,
        orientation: str | None = None,
        access_type: str | None = None,
        gripper_offset: float = 20.0,
        retract_distance: float = 100.0,
        vertical_clearance: float = 50.0,
        z_above: float = 10.0,
        gateway: str | None = None
    ) -> None:
        self.name = name
        self.coordinates = coordinates  # Can be CartesianCoordinates or JointCoordinates
        self.orientation = orientation
        # "vertical" or "horizontal" for pick/place destinations; None for waypoints
        self.access_type = access_type
        self.gripper_offset = gripper_offset  # Gripper height compensation (always used)
        # For VERTICAL access only:
        self.retract_distance = retract_distance  # How far to pull back horizontally
        # For HORIZONTAL access only:
        self.vertical_clearance = vertical_clearance  # Vertical clearance distance
        self.z_above = z_above  # Extra height above nest slot
        # Gateway waypoint - robot must pass through this teachpoint before reaching destination
        self.gateway = gateway

        # Name of access config this teachpoint uses (for JSON save reconstruction)
        self._access_config_name: str | None = None

        # Validate: Cartesian teachpoints must have orientation for crossover detection
        if self.is_cartesian() and self.orientation is None:
            raise ValueError(
                f"Cartesian teachpoint '{self.name}' must specify orientation (left/right)"
            )

    def is_joint_space(self) -> bool:
        """Returns True if this teachpoint uses joint-space coordinates."""
        return isinstance(self.coordinates, JointCoordinates)

    def is_cartesian(self) -> bool:
        """Returns True if this teachpoint uses Cartesian coordinates."""
        return isinstance(self.coordinates, CartesianCoordinates)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize teachpoint to dictionary for network transmission."""
        result: Dict[str, Any] = {"name": self.name}

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
                "retract_distance": self.retract_distance,
                "vertical_clearance": self.vertical_clearance,
                "z_above": self.z_above,
            })

        if self.gateway is not None:
            result["gateway"] = self.gateway

        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Teachpoint":
        """Deserialize teachpoint from dictionary.

        Detects coordinate type by presence of 'j1' (joint-space) or 'x' (Cartesian) keys.
        Defaults to joint-space if neither is present.
        """
        # Detect coordinate type: "base" = new format, "j1" = legacy format
        if "base" in data:
            coordinates: CartesianCoordinates | JointCoordinates | None = JointCoordinates(
                base=float(data["base"]),
                shoulder=float(data["shoulder"]),
                elbow=float(data["elbow"]),
                wrist=float(data["wrist"]),
                rail=float(data.get("rail", 0.0)),
            )
            orientation = None
        elif "j1" in data:
            # Legacy format: j1=rail, j2=base, j3=shoulder, j4=elbow, j5=wrist
            coordinates = JointCoordinates(
                rail=float(data["j1"]),
                base=float(data["j2"]),
                shoulder=float(data["j3"]),
                elbow=float(data["j4"]),
                wrist=float(data["j5"]),
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
            name=data["name"],
            coordinates=coordinates,
            orientation=orientation,
            access_type=data.get("access_type"),
            gripper_offset=float(data.get("gripper_offset", 20.0)),
            retract_distance=float(data.get("retract_distance", 100.0)),
            vertical_clearance=float(data.get("vertical_clearance", 50.0)),
            z_above=float(data.get("z_above", 10.0)),
            gateway=data.get("gateway"),
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
                    retract_distance=float(cfg_data.get('retract_distance', 100.0)),
                    vertical_clearance=float(cfg_data.get('vertical_clearance', 50.0)),
                    z_above=float(cfg_data.get('z_above', 10.0))
                )

        # Add hardcoded defaults
        access_configs['default_vertical'] = AccessConfig(
            'default_vertical', 'vertical', 20.0, 100.0, 50.0, 10.0
        )
        access_configs['default_horizontal'] = AccessConfig(
            'default_horizontal', 'horizontal', 20.0, 100.0, 50.0, 10.0
        )

        # Parse teachpoints and resolve access config references
        teachpoints: List[Teachpoint] = []
        for tp_data in data.get('teachpoints', []):
            # Detect coordinate type: "base" = new format, "j1" = legacy format
            if 'base' in tp_data:
                # New format: semantic joint names
                coordinates: CartesianCoordinates | JointCoordinates = JointCoordinates(
                    base=float(tp_data['base']),
                    shoulder=float(tp_data['shoulder']),
                    elbow=float(tp_data['elbow']),
                    wrist=float(tp_data['wrist']),
                    rail=float(tp_data.get('rail', 0.0))
                )
                orientation = None
            elif 'j1' in tp_data:
                # Legacy format: j1=rail, j2=base, j3=shoulder, j4=elbow, j5=wrist
                coordinates = JointCoordinates(
                    rail=float(tp_data['j1']),
                    base=float(tp_data['j2']),
                    shoulder=float(tp_data['j3']),
                    elbow=float(tp_data['j4']),
                    wrist=float(tp_data['j5'])
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
                        f"Teachpoint '{tp_data['name']}' references unknown access config '{config_name}'"
                    )
                cfg = access_configs[config_name]
                access_type = cfg.access_type
                gripper_offset = cfg.gripper_offset
                retract_distance = cfg.retract_distance
                vertical_clearance = cfg.vertical_clearance
                z_above = cfg.z_above
            else:
                # Waypoint without access config
                access_type = None
                gripper_offset = 20.0
                retract_distance = 100.0
                vertical_clearance = 50.0
                z_above = 10.0

            # Create teachpoint with resolved access params
            tp = Teachpoint(
                name=tp_data['name'],
                coordinates=coordinates,
                orientation=orientation,
                access_type=access_type,
                gripper_offset=gripper_offset,
                retract_distance=retract_distance,
                vertical_clearance=vertical_clearance,
                z_above=z_above,
                gateway=tp_data.get('gateway', None)
            )
            tp._access_config_name = config_name
            teachpoints.append(tp)

        return teachpoints


class TeachpointsRegistry:
    def __init__(self) -> None:
        self._registry: Dict[str, Teachpoint] = {}

    def add(self, teachpoint: Teachpoint, overwrite = True) -> None:
        """Add a teachpoint to the registry."""
        if not overwrite and self.exists(teachpoint.name):
            raise KeyError(f"Teachpoint '{teachpoint.name}' already exists and overwrite is disabled")
        self._registry[teachpoint.name] = teachpoint

    def get(self, name: str) -> Teachpoint:
        """Get a teachpoint by name."""
        if name not in self._registry:
            raise KeyError(f"Teachpoint '{name}' not found")
        return self._registry[name]

    def update(self, name: str, teachpoint: Teachpoint) -> None:
        """Update an existing teachpoint."""
        if name not in self._registry:
            raise KeyError(f"Teachpoint '{name}' not found")
        self._registry[name] = teachpoint

    def delete(self, name: str) -> None:
        """Delete a teachpoint by name."""
        if name not in self._registry:
            raise KeyError(f"Teachpoint '{name}' not found")
        del self._registry[name]

    def list(self) -> List[Teachpoint]:
        """Get all teachpoints."""
        return list(self._registry.values())

    def exists(self, name: str) -> bool:
        """Check if a teachpoint exists."""
        return name in self._registry

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
                    'retract_distance': tp.retract_distance,
                    'vertical_clearance': tp.vertical_clearance,
                    'z_above': tp.z_above
                }

        # Build teachpoints list with access references
        teachpoints_list: List[Dict[str, Any]] = []
        for tp in self._registry.values():
            tp_dict: Dict[str, Any] = {'name': tp.name}

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
