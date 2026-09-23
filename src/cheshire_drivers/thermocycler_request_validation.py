"""Wire-shape validation for thermocycler commands.

Mirrors `cheshire_drivers.transporter_request_validation` for the thermocycler
category: every wire-callable command (mutations AND getters) takes a Pydantic
Request, so param-less commands carry an empty marker Request. The paired
RESPONSE_MODELS declares each command's typed return shape; the four
temperature getters and the two status getters return a typed model directly,
while the scalar getters return a raw value that the wire wrap projects onto a
single-field Response model.
"""

from typing import Any, Dict, Tuple

from pydantic import BaseModel

from cheshire_drivers.command_responses import EmptyCommandResponse
from cheshire_drivers.thermocycler_models import (
    CloseLidRequest,
    CycleCountResponse,
    CycleIndexResponse,
    DeactivateBlockRequest,
    DeactivateLidRequest,
    GetBlockCurrentTemperatureRequest,
    GetBlockStatusRequest,
    GetBlockTargetTemperatureRequest,
    GetCurrentCycleIndexRequest,
    GetCurrentStepIndexRequest,
    GetHoldTimeRequest,
    GetLidCurrentTemperatureRequest,
    GetLidOpenRequest,
    GetLidStatusRequest,
    GetLidTargetTemperatureRequest,
    GetTotalCycleCountRequest,
    GetTotalStepCountRequest,
    HoldTimeResponse,
    LidOpenResponse,
    OpenLidRequest,
    RunProtocolRequest,
    SetBlockTemperatureRequest,
    SetLidTemperatureRequest,
    StepCountResponse,
    StepIndexResponse,
    TemperatureListResponse,
    ThermocyclerStatusResponse,
)


THERMOCYCLER_REQUEST_MODELS: Dict[str, Tuple[str, type[BaseModel]]] = {
    "open_lid": ("request", OpenLidRequest),
    "close_lid": ("request", CloseLidRequest),
    "set_block_temperature": ("request", SetBlockTemperatureRequest),
    "set_lid_temperature": ("request", SetLidTemperatureRequest),
    "deactivate_block": ("request", DeactivateBlockRequest),
    "deactivate_lid": ("request", DeactivateLidRequest),
    "run_protocol": ("request", RunProtocolRequest),
    "get_block_current_temperature": ("request", GetBlockCurrentTemperatureRequest),
    "get_block_target_temperature": ("request", GetBlockTargetTemperatureRequest),
    "get_lid_current_temperature": ("request", GetLidCurrentTemperatureRequest),
    "get_lid_target_temperature": ("request", GetLidTargetTemperatureRequest),
    "get_lid_open": ("request", GetLidOpenRequest),
    "get_lid_status": ("request", GetLidStatusRequest),
    "get_block_status": ("request", GetBlockStatusRequest),
    "get_hold_time": ("request", GetHoldTimeRequest),
    "get_current_cycle_index": ("request", GetCurrentCycleIndexRequest),
    "get_total_cycle_count": ("request", GetTotalCycleCountRequest),
    "get_current_step_index": ("request", GetCurrentStepIndexRequest),
    "get_total_step_count": ("request", GetTotalStepCountRequest),
}


THERMOCYCLER_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "open_lid": EmptyCommandResponse,
    "close_lid": EmptyCommandResponse,
    "set_block_temperature": EmptyCommandResponse,
    "set_lid_temperature": EmptyCommandResponse,
    "deactivate_block": EmptyCommandResponse,
    "deactivate_lid": EmptyCommandResponse,
    "run_protocol": EmptyCommandResponse,
    "get_block_current_temperature": TemperatureListResponse,
    "get_block_target_temperature": TemperatureListResponse,
    "get_lid_current_temperature": TemperatureListResponse,
    "get_lid_target_temperature": TemperatureListResponse,
    "get_lid_open": LidOpenResponse,
    "get_lid_status": ThermocyclerStatusResponse,
    "get_block_status": ThermocyclerStatusResponse,
    "get_hold_time": HoldTimeResponse,
    "get_current_cycle_index": CycleIndexResponse,
    "get_total_cycle_count": CycleCountResponse,
    "get_current_step_index": StepIndexResponse,
    "get_total_step_count": StepCountResponse,
}


def reject_unknown_thermocycler_fields(
    payload: Dict[str, Any],
    model_cls: type[BaseModel],
) -> None:
    unknown = set(payload.keys()) - set(model_cls.model_fields.keys())
    if unknown:
        raise ValueError(
            f"Unknown fields for {model_cls.__name__}: {sorted(unknown)}. "
            f"Update MCP/REST/caller to align with the cheshire-drivers Pydantic model."
        )


def validate_thermocycler_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if command not in THERMOCYCLER_REQUEST_MODELS:
        return params
    _, model_cls = THERMOCYCLER_REQUEST_MODELS[command]
    reject_unknown_thermocycler_fields(params, model_cls)
    model_cls.model_validate(params)
    return params


def wrap_thermocycler_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if command not in THERMOCYCLER_REQUEST_MODELS:
        return params
    kwarg_name, model_cls = THERMOCYCLER_REQUEST_MODELS[command]
    reject_unknown_thermocycler_fields(params, model_cls)
    instance = model_cls.model_validate(params)
    return {kwarg_name: instance}
