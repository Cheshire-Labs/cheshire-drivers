"""Wire-shape validation for shaker commands.

Mirrors `cheshire_drivers.lh_request_validation` and
`cheshire_drivers.transporter_request_validation` for the shaker category.
Imported by:

- the gateway server: pre-flight validation in the device controller before
  the WebSocket round-trip (gated on advertised IShaker interface).
- orca-client: dispatch-time wrapping in CommandExecutor._deserialize_params
  before invoking the driver method (gated on driver's IShaker interface).

Both layers must agree on shape; sharing the module guarantees they do.
"""

from typing import Any, Dict, Tuple

from pydantic import BaseModel

from cheshire_drivers.command_responses import EmptyCommandResponse
from cheshire_drivers.shaker_models import (
    LockPlateRequest,
    ShakeRequest,
    StopShakingRequest,
    UnlockPlateRequest,
)


# Every wire-callable category-specific shaker command takes a Pydantic
# Request. BaseDriver inherited methods (initialize, open, close,
# is_initialized) AND cross-category commands (stop) stay parameterless and
# are NOT included here. Cross-category commands have a single shape across
# all device categories; per-category typing would force the wire dispatch
# to wrap them differently per device, which is incompatible with the
# generic device_stop tool's command="stop" routing.
SHAKER_REQUEST_MODELS: Dict[str, Tuple[str, type[BaseModel]]] = {
    "shake": ("request", ShakeRequest),
    "stop_shaking": ("request", StopShakingRequest),
    "lock_plate": ("request", LockPlateRequest),
    "unlock_plate": ("request", UnlockPlateRequest),
}


SHAKER_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "shake": EmptyCommandResponse,
    "stop_shaking": EmptyCommandResponse,
    "lock_plate": EmptyCommandResponse,
    "unlock_plate": EmptyCommandResponse,
}


def reject_unknown_shaker_fields(
    payload: Dict[str, Any],
    model_cls: type[BaseModel],
) -> None:
    """Raise ValueError if payload has fields the Request model does not declare."""
    unknown = set(payload.keys()) - set(model_cls.model_fields.keys())
    if unknown:
        raise ValueError(
            f"Unknown fields for {model_cls.__name__}: {sorted(unknown)}. "
            f"Update MCP/REST/caller to align with the cheshire-drivers Pydantic model."
        )


def validate_shaker_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Validate flat-dict params against the Request model for a shaker command.

    Returns the params dict unchanged. Pass-through for commands not in
    SHAKER_REQUEST_MODELS (no validation performed).

    Raises:
        ValueError: unknown fields.
        pydantic.ValidationError: payload does not match the Request model shape.
    """
    if command not in SHAKER_REQUEST_MODELS:
        return params
    _, model_cls = SHAKER_REQUEST_MODELS[command]
    reject_unknown_shaker_fields(params, model_cls)
    model_cls.model_validate(params)
    return params


def wrap_shaker_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and wrap a flat-dict payload into the kwarg dict the driver method expects.

    For commands in SHAKER_REQUEST_MODELS, returns
    `{kwarg_name: <Pydantic instance>}` suitable for `method(**result)`
    dispatch. For other commands, returns params unchanged.
    """
    if command not in SHAKER_REQUEST_MODELS:
        return params
    kwarg_name, model_cls = SHAKER_REQUEST_MODELS[command]
    reject_unknown_shaker_fields(params, model_cls)
    instance = model_cls.model_validate(params)
    return {kwarg_name: instance}
