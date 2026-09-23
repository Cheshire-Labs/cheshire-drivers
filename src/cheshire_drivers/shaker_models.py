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
    # ge=0 (not gt) preserves the older contract: callers historically passed
    # any float and the driver accepted it. Tests rely on duration=0 for
    # variable-override propagation checks; physical zero-shake is a valid
    # no-op in those harness paths. RPM caps live at the REST/MCP layer.
    speed: float = Field(..., ge=0, description="Shake speed in revolutions per minute (RPM)")
    duration: float = Field(..., ge=0, description="Shake duration in seconds")


class StopShakingRequest(_StrictModel):
    pass


class LockPlateRequest(_StrictModel):
    pass


class UnlockPlateRequest(_StrictModel):
    pass
