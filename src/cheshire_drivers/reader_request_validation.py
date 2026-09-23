"""Wire-shape validation for reader commands."""

from typing import Any, Dict, Tuple

from pydantic import BaseModel

from cheshire_drivers.command_responses import EmptyCommandResponse
from cheshire_drivers.reader_models import ReadRequest


READER_REQUEST_MODELS: Dict[str, Tuple[str, type[BaseModel]]] = {
    "read": ("request", ReadRequest),
}


READER_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "read": EmptyCommandResponse,
}


def reject_unknown_reader_fields(
    payload: Dict[str, Any],
    model_cls: type[BaseModel],
) -> None:
    unknown = set(payload.keys()) - set(model_cls.model_fields.keys())
    if unknown:
        raise ValueError(
            f"Unknown fields for {model_cls.__name__}: {sorted(unknown)}. "
            f"Update MCP/REST/caller to align with the cheshire-drivers Pydantic model."
        )


def validate_reader_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if command not in READER_REQUEST_MODELS:
        return params
    _, model_cls = READER_REQUEST_MODELS[command]
    reject_unknown_reader_fields(params, model_cls)
    model_cls.model_validate(params)
    return params


def wrap_reader_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if command not in READER_REQUEST_MODELS:
        return params
    kwarg_name, model_cls = READER_REQUEST_MODELS[command]
    reject_unknown_reader_fields(params, model_cls)
    instance = model_cls.model_validate(params)
    return {kwarg_name: instance}
