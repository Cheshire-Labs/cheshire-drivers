"""Wire-shape validation for transporter commands.

Mirrors `cheshire_drivers.lh_request_validation` for the transporter
category. Imported by:

- the gateway server: pre-flight validation in the device controller before the
  WebSocket round-trip.
- orca-client: dispatch-time wrapping in CommandExecutor._deserialize_params
  before invoking the driver method.

Both layers must agree on shape; sharing the module guarantees they do.

Teachpoint-coordinate Requests carry the legacy `Teachpoint` dataclass via a
field validator that converts wire dicts on the way in. Both `validate` and
`wrap` exercise the same Request `model_validate`, so the wire-shape guard
runs identically on each side.
"""

from typing import Any, Dict, Tuple

from pydantic import BaseModel

from cheshire_drivers.command_responses import EmptyCommandResponse
from cheshire_drivers.homing_models import HomeRequest
from cheshire_drivers.teachpoints import CartesianCoordinates, JointCoordinates
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
    SpeedResponse,
    UnseedPositionRequest,
)


# Every wire-callable transporter command takes a Pydantic Request. Empty
# marker models (e.g. HomeRequest, GetSpeedRequest) preserve uniform wire shape
# and let the pre-flight + executor wrap pipeline stay registry-driven without
# special cases. Mirrors cheshire-drivers `da17814` (LH retired LH_NO_REQUEST
# in favour of GetDeckStateRequest empty marker).
TRANSPORTER_REQUEST_MODELS: Dict[str, Tuple[str, type[BaseModel]]] = {
    "initialize": ("request", InitializeRequest),
    "home": ("request", HomeRequest),
    "move_to_safe": ("request", MoveToSafeRequest),
    "open_gripper": ("request", OpenGripperRequest),
    "close_gripper": ("request", CloseGripperRequest),
    "halt": ("request", HaltRequest),
    "pick_at_coords": ("request", PickAtCoordsRequest),
    "place_at_coords": ("request", PlaceAtCoordsRequest),
    "move_to_coords": ("request", MoveToCoordsRequest),
    "move_single_axis": ("request", MoveSingleAxisRequest),
    "move_single_axis_relative": ("request", MoveSingleAxisRelativeRequest),
    "set_free_mode": ("request", SetFreeModeRequest),
    "set_speed": ("request", SetSpeedRequest),
    "get_joint_position": ("request", GetJointPositionRequest),
    "get_cartesian_position": ("request", GetCartesianPositionRequest),
    "get_speed": ("request", GetSpeedRequest),
    "seed_position": ("request", SeedPositionRequest),
    "ensure_seeded": ("request", EnsureSeededRequest),
    "unseed_position": ("request", UnseedPositionRequest),
    "reset_world": ("request", ResetWorldRequest),
}


# Pairs each transporter command with its typed Response model. Most
# commands ack-only via EmptyCommandResponse; the three position / speed
# queries return typed payloads.
TRANSPORTER_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "initialize": EmptyCommandResponse,
    "home": EmptyCommandResponse,
    "move_to_safe": EmptyCommandResponse,
    "open_gripper": EmptyCommandResponse,
    "close_gripper": EmptyCommandResponse,
    "halt": EmptyCommandResponse,
    "pick_at_coords": EmptyCommandResponse,
    "place_at_coords": EmptyCommandResponse,
    "move_to_coords": EmptyCommandResponse,
    "move_single_axis": EmptyCommandResponse,
    "move_single_axis_relative": EmptyCommandResponse,
    "set_free_mode": EmptyCommandResponse,
    "set_speed": EmptyCommandResponse,
    "get_joint_position": JointCoordinates,
    "get_cartesian_position": CartesianCoordinates,
    "get_speed": SpeedResponse,
    "seed_position": EmptyCommandResponse,
    "ensure_seeded": EmptyCommandResponse,
    "unseed_position": EmptyCommandResponse,
    "reset_world": EmptyCommandResponse,
}


def reject_unknown_transporter_fields(
    payload: Dict[str, Any],
    model_cls: type[BaseModel],
) -> None:
    """Raise ValueError if payload has fields the Request model does not declare.

    Pydantic v2 default behavior is `extra='ignore'`, but `_StrictModel`
    declares `extra='forbid'` so unknown fields raise `ValidationError`. This
    helper produces the same shape of error message the LH path uses so
    callers see a consistent diagnostic across categories.
    """
    unknown = set(payload.keys()) - set(model_cls.model_fields.keys())
    if unknown:
        raise ValueError(
            f"Unknown fields for {model_cls.__name__}: {sorted(unknown)}. "
            f"Update MCP/REST/caller to align with the cheshire-drivers Pydantic model."
        )


def validate_transporter_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Validate flat-dict params against the Request model for a transporter command.

    Returns the params dict unchanged. Pass-through for commands not in
    TRANSPORTER_REQUEST_MODELS (no validation performed).

    Raises:
        ValueError: unknown fields.
        pydantic.ValidationError: payload does not match the Request model shape.
    """
    if command not in TRANSPORTER_REQUEST_MODELS:
        return params
    _, model_cls = TRANSPORTER_REQUEST_MODELS[command]
    reject_unknown_transporter_fields(params, model_cls)
    model_cls.model_validate(params)
    return params


def wrap_transporter_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and wrap a flat-dict payload into the kwarg dict the driver method expects.

    For commands in TRANSPORTER_REQUEST_MODELS, returns
    `{kwarg_name: <Pydantic instance>}` suitable for `method(**result)`
    dispatch. For other commands, returns params unchanged.

    For teachpoint-carrying Requests, the field validator inside the Request
    converts an incoming `params['teachpoint']` dict to a `Teachpoint`
    dataclass; the driver method receives a fully-constructed Teachpoint.
    """
    if command not in TRANSPORTER_REQUEST_MODELS:
        return params
    kwarg_name, model_cls = TRANSPORTER_REQUEST_MODELS[command]
    reject_unknown_transporter_fields(params, model_cls)
    instance = model_cls.model_validate(params)
    return {kwarg_name: instance}
