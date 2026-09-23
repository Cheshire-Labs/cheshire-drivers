"""Pydantic models for the liquid-handler gripper driver interfaces.

Both the jaw and the motion surface are split by what the hardware actually exposes, so no
driver has to pretend it supports a control model its hardware lacks:

- IForceGripperJaw -- close until a grip force is reached (Opentrons Flex).
- IWidthGripperJaw -- drive the jaw to an opening width, read it (Hamilton iSWAP,
  PreciseFlex).
- IGripperMotion -- absolute placement, the floor every gripper honors.
- IGripperPosition -- read the position and jog by a delta. Both need position feedback,
  which the Opentrons Flex has no command for at all.

All models reject unknown fields (`extra='forbid'` via `_StrictModel`).
"""

from typing import Optional

from typing_extensions import Self

from pydantic import model_validator

from cheshire_drivers.liquid_handler_models import _StrictModel


# --- IGripperMotion: absolute placement, the floor every gripper honors ---


class MoveGripperToRequest(_StrictModel):
    """Move the gripper to an absolute deck-frame position (mm).

    All three axes are required. Holding one axis at its current value means reading that value
    first, so a partial move needs position feedback and belongs to IGripperPosition, not to the
    motion floor. Contrast MoveChannelToRequest, whose axes are optional because every pipette
    this contract targets can report its own position."""

    x: float
    y: float
    z: float


# --- IGripperPosition: read + jog. Needs position feedback ---


class MoveGripperRelativeRequest(_StrictModel):
    """Jog the gripper by a delta on each supplied axis (mm)."""

    dx: Optional[float] = None
    dy: Optional[float] = None
    dz: Optional[float] = None

    @model_validator(mode="after")
    def _at_least_one_delta(self) -> Self:
        if self.dx is None and self.dy is None and self.dz is None:
            raise ValueError("MoveGripperRelativeRequest: supply at least one of dx, dy, dz to jog.")
        return self


class GetGripperPositionRequest(_StrictModel):
    """Read the gripper's current deck-frame position."""


class GripperPosition(_StrictModel):
    """The gripper's current deck-frame position (mm)."""

    x: float
    y: float
    z: float


# --- IForceGripperJaw: close-until-force grippers (Opentrons Flex) ---


class GripWithForceRequest(_StrictModel):
    """Close the jaw until it grips at `force` newtons. Omit to use the robot's default force."""

    force: Optional[float] = None


class ReleaseJawRequest(_StrictModel):
    """Open the jaw fully."""


# --- IWidthGripperJaw: set-to-width grippers (Hamilton iSWAP, PreciseFlex) ---


class SetJawWidthRequest(_StrictModel):
    """Drive the jaw to `width` mm (opening or closing to that opening)."""

    width: float


class GetJawWidthRequest(_StrictModel):
    """Read the current jaw opening (mm)."""


class JawWidthResponse(_StrictModel):
    """Response payload for ``get_jaw_width``.

    The interface returns a bare float, which cannot be registered as a wire Response model, so
    the readback rides in a single-field model. Mirrors SpeedResponse."""

    width: float


# --- IGripperRotation: rotate the gripper (Hamilton iSWAP) ---


class RotateGripperRequest(_StrictModel):
    """Rotate the gripper to `angle` degrees."""

    angle: float


class GetGripperRotationRequest(_StrictModel):
    """Read the gripper's current rotation (degrees)."""


class GripperRotationResponse(_StrictModel):
    """Response payload for ``get_gripper_rotation``. Single-field for the same reason as
    JawWidthResponse."""

    angle: float
