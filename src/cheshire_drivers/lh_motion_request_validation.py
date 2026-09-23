"""Wire-shape validation for the liquid-handler motion commands.

Mirrors `cheshire_drivers.transporter_request_validation` for the pipette-motion and gripper
interfaces. Imported by:

- the gateway server: pre-flight validation before the WebSocket round-trip.
- orca-client: dispatch-time wrapping before invoking the driver method.

Both layers must agree on shape; sharing the module guarantees they do.

Response models are registered per INTERFACE rather than as one flat category map, because these
interfaces are split by what the hardware exposes: a device declaring only IGripperMotion must not
resolve a jaw or position readback it cannot perform.
"""

from typing import Dict, Tuple

from pydantic import BaseModel, JsonValue

from cheshire_drivers.command_responses import EmptyCommandResponse
from cheshire_drivers.gantry_models import ParkGantryRequest
from cheshire_drivers.gripper_models import (
    GetGripperPositionRequest,
    GetGripperRotationRequest,
    GetJawWidthRequest,
    GripperPosition,
    GripperRotationResponse,
    GripWithForceRequest,
    JawWidthResponse,
    MoveGripperRelativeRequest,
    MoveGripperToRequest,
    ReleaseJawRequest,
    RotateGripperRequest,
    SetJawWidthRequest,
)
from cheshire_drivers.pipette_motion_models import (
    ChannelPosition,
    GetChannelPositionRequest,
    MoveChannelRelativeRequest,
    MoveChannelToRequest,
)


# Empty marker models (ReleaseJawRequest, GetJawWidthRequest) keep the wire shape uniform so the
# pre-flight + executor wrap pipeline stays registry-driven with no special cases.
LH_MOTION_REQUEST_MODELS: Dict[str, Tuple[str, type[BaseModel]]] = {
    "move_channel_to": ("request", MoveChannelToRequest),
    "move_channel_relative": ("request", MoveChannelRelativeRequest),
    "get_channel_position": ("request", GetChannelPositionRequest),
    "move_gripper_to": ("request", MoveGripperToRequest),
    "move_gripper_relative": ("request", MoveGripperRelativeRequest),
    "get_gripper_position": ("request", GetGripperPositionRequest),
    "grip_with_force": ("request", GripWithForceRequest),
    "release_jaw": ("request", ReleaseJawRequest),
    "set_jaw_width": ("request", SetJawWidthRequest),
    "get_jaw_width": ("request", GetJawWidthRequest),
    "rotate_gripper": ("request", RotateGripperRequest),
    "get_gripper_rotation": ("request", GetGripperRotationRequest),
    "park_gantry": ("request", ParkGantryRequest),
}


PIPETTE_MOTION_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "move_channel_to": EmptyCommandResponse,
    "move_channel_relative": EmptyCommandResponse,
    "get_channel_position": ChannelPosition,
}

GRIPPER_MOTION_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "move_gripper_to": EmptyCommandResponse,
}

GRIPPER_POSITION_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "get_gripper_position": GripperPosition,
    "move_gripper_relative": EmptyCommandResponse,
}

FORCE_GRIPPER_JAW_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "grip_with_force": EmptyCommandResponse,
    "release_jaw": EmptyCommandResponse,
}

WIDTH_GRIPPER_JAW_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "set_jaw_width": EmptyCommandResponse,
    "get_jaw_width": JawWidthResponse,
}

GANTRY_PARKING_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "park_gantry": EmptyCommandResponse,
}


GRIPPER_ROTATION_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "rotate_gripper": EmptyCommandResponse,
    "get_gripper_rotation": GripperRotationResponse,
}


def reject_unknown_lh_motion_fields(
    payload: Dict[str, JsonValue],
    model_cls: type[BaseModel],
) -> None:
    """Raise ValueError if payload has fields the Request model does not declare.

    `_StrictModel` already declares `extra='forbid'`, so this exists to produce the same shape of
    diagnostic the other categories emit rather than a raw ValidationError.
    """
    unknown = set(payload.keys()) - set(model_cls.model_fields.keys())
    if unknown:
        raise ValueError(
            f"Unknown fields for {model_cls.__name__}: {sorted(unknown)}. "
            f"Update MCP/REST/caller to align with the cheshire-drivers Pydantic model."
        )


def validate_lh_motion_payload(command: str, params: Dict[str, JsonValue]) -> Dict[str, JsonValue]:
    """Validate flat-dict params against the Request model for a motion command.

    Returns the params dict unchanged. Pass-through for commands not in
    LH_MOTION_REQUEST_MODELS (no validation performed).

    Raises:
        ValueError: unknown fields.
        pydantic.ValidationError: payload does not match the Request model shape.
    """
    if command not in LH_MOTION_REQUEST_MODELS:
        return params
    _, model_cls = LH_MOTION_REQUEST_MODELS[command]
    reject_unknown_lh_motion_fields(params, model_cls)
    model_cls.model_validate(params)
    return params


def wrap_lh_motion_payload(
    command: str, params: Dict[str, JsonValue]
) -> Dict[str, JsonValue | BaseModel]:
    """Validate and wrap a flat-dict payload into the kwarg dict the driver method expects.

    For commands in LH_MOTION_REQUEST_MODELS, returns `{kwarg_name: <Pydantic instance>}` suitable
    for `method(**result)` dispatch. For other commands, returns params unchanged. The value type
    is a union because a wrapped payload carries a model instance, which is not JSON.
    """
    if command not in LH_MOTION_REQUEST_MODELS:
        passthrough: Dict[str, JsonValue | BaseModel] = dict(params)
        return passthrough
    kwarg_name, model_cls = LH_MOTION_REQUEST_MODELS[command]
    reject_unknown_lh_motion_fields(params, model_cls)
    instance = model_cls.model_validate(params)
    return {kwarg_name: instance}
