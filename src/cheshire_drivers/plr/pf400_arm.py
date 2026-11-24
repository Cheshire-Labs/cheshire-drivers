"""A PF400 arm on real hardware: how its elbow crosses sides, its jaws' closed value, and its `arm` setup block."""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from cheshire_drivers.teachpoints import JointCoordinates, Teachpoint

CLOSED_GRIPPER_POSITION = 75.5
"""Gripper-axis value at the standard gripper's minimum jaw width, in the controller's units."""

SAFE_POSE = JointCoordinates(rail=0.0, base=170.0, shoulder=0.0, elbow=180.0, wrist=-180.0)
RIGHT_POSE = JointCoordinates(rail=0.0, base=170.0, shoulder=10.0, elbow=120.0, wrist=-130.0)
LEFT_POSE = JointCoordinates(rail=0.0, base=170.0, shoulder=-20.0, elbow=240.0, wrist=-225.0)


def _is_left(elbow: float) -> bool:
    return elbow > 180


def _target_is_left(target: Teachpoint) -> bool:
    if isinstance(target.coordinates, JointCoordinates):
        return _is_left(target.coordinates.elbow)
    if target.orientation is None:
        raise ValueError(
            f"Cartesian teachpoint '{target.position_id}' must specify orientation (left/right) for crossover detection"
        )
    return target.orientation.lower() == "left"


class PF400Crossover:
    def poses(self, current: JointCoordinates, target: Teachpoint) -> tuple[JointCoordinates, ...]:
        currently_left = _is_left(current.elbow)
        if _target_is_left(target) == currently_left:
            return ()
        return SAFE_POSE, RIGHT_POSE if currently_left else LEFT_POSE


PF400_CROSSOVER = PF400Crossover()


class PF400ArmSetup(BaseModel):
    """The `arm` block of a PF400's device setup: the gripper bolted to it, which the controller cannot report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gripper_length: float = Field(default=162.0, description="Wrist axis to tool tip, mm. Bring-up reads the real one.")
    gripper_z_offset: float = Field(default=0.0, description="Wrist plate to tool tip vertical offset, mm.")
    closed_gripper_position: float = Field(
        default=CLOSED_GRIPPER_POSITION, description="Firmware jaw value at the gripper's minimum width.",
    )
    is_dual_gripper: bool = False
    has_rail: bool = False
    timeout: int = Field(default=20, description="How long the controller waits for motor power, seconds.")


def arm_setup(block: Mapping[str, JsonValue] | None) -> PF400ArmSetup:
    """The block read as the PF400's; ValueError on a field it does not take."""
    try:
        return PF400ArmSetup.model_validate(dict(block or {}))
    except ValidationError as refused:
        raise ValueError(f"the PF400 arm block is not valid: {refused}") from refused
