"""Pydantic models for shaker driver interface.

Mirrors `liquid_handler_models` and `transporter_models` for the shaker
category. Used by `IShakerDriver` methods. Serializable for wire transport
(WebSocket, REST, etc.) while also usable as plain Python objects for
in-process calls.

All models reject unknown fields (`extra='forbid'`). Silent-drop is a wire
bug we do not tolerate.

Empty marker requests (e.g. `LockPlateRequest`) preserve uniform wire shape
for parameterless category-specific methods. Mirrors the precedent set by
`GetDeckStateRequest` (LH) and `HomeRequest` (homing).
"""

from pydantic import Field

from cheshire_drivers.liquid_handler_models import _StrictModel


class ShakeRequest(_StrictModel):
    speed: float = Field(..., gt=0, le=2000, description="Shake speed in revolutions per minute (RPM)")
    duration: float = Field(..., gt=0, description="Shake duration in seconds")


class StopShakingRequest(_StrictModel):
    pass


class LockPlateRequest(_StrictModel):
    pass


class UnlockPlateRequest(_StrictModel):
    pass
