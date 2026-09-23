"""Wire-shape validation for centrifuge commands.

Mirrors `cheshire_drivers.shaker_request_validation` for the centrifuge category.
"""

from typing import Any, Dict, Tuple

from pydantic import BaseModel

from cheshire_drivers.centrifuge_models import CentrifugeRequest
from cheshire_drivers.command_responses import EmptyCommandResponse


CENTRIFUGE_REQUEST_MODELS: Dict[str, Tuple[str, type[BaseModel]]] = {
    "centrifuge": ("request", CentrifugeRequest),
}


CENTRIFUGE_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "centrifuge": EmptyCommandResponse,
}


def reject_unknown_centrifuge_fields(
    payload: Dict[str, Any],
    model_cls: type[BaseModel],
) -> None:
    unknown = set(payload.keys()) - set(model_cls.model_fields.keys())
    if unknown:
        raise ValueError(
            f"Unknown fields for {model_cls.__name__}: {sorted(unknown)}. "
            f"Update MCP/REST/caller to align with the cheshire-drivers Pydantic model."
        )


def validate_centrifuge_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if command not in CENTRIFUGE_REQUEST_MODELS:
        return params
    _, model_cls = CENTRIFUGE_REQUEST_MODELS[command]
    reject_unknown_centrifuge_fields(params, model_cls)
    model_cls.model_validate(params)
    return params


def wrap_centrifuge_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if command not in CENTRIFUGE_REQUEST_MODELS:
        return params
    kwarg_name, model_cls = CENTRIFUGE_REQUEST_MODELS[command]
    reject_unknown_centrifuge_fields(params, model_cls)
    instance = model_cls.model_validate(params)
    return {kwarg_name: instance}
