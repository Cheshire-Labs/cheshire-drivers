"""Wire-shape validation for protocol runner commands."""

from typing import Any, Dict, Tuple

from pydantic import BaseModel

from cheshire_drivers.command_responses import EmptyCommandResponse
from cheshire_drivers.protocol_runner_models import RunProtocolRequest


PROTOCOL_RUNNER_REQUEST_MODELS: Dict[str, Tuple[str, type[BaseModel]]] = {
    "run_protocol": ("request", RunProtocolRequest),
}


PROTOCOL_RUNNER_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "run_protocol": EmptyCommandResponse,
}


def reject_unknown_protocol_runner_fields(
    payload: Dict[str, Any],
    model_cls: type[BaseModel],
) -> None:
    unknown = set(payload.keys()) - set(model_cls.model_fields.keys())
    if unknown:
        raise ValueError(
            f"Unknown fields for {model_cls.__name__}: {sorted(unknown)}. "
            f"Update MCP/REST/caller to align with the cheshire-drivers Pydantic model."
        )


def validate_protocol_runner_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if command not in PROTOCOL_RUNNER_REQUEST_MODELS:
        return params
    _, model_cls = PROTOCOL_RUNNER_REQUEST_MODELS[command]
    reject_unknown_protocol_runner_fields(params, model_cls)
    model_cls.model_validate(params)
    return params


def wrap_protocol_runner_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if command not in PROTOCOL_RUNNER_REQUEST_MODELS:
        return params
    kwarg_name, model_cls = PROTOCOL_RUNNER_REQUEST_MODELS[command]
    reject_unknown_protocol_runner_fields(params, model_cls)
    instance = model_cls.model_validate(params)
    return {kwarg_name: instance}
