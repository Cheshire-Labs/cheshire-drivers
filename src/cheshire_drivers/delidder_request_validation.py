"""Wire-shape validation for delidder commands.

Mirrors `cheshire_drivers.shaker_request_validation` for the delidder
category. Single category-specific method.
"""

from typing import Any, Dict, Tuple

from pydantic import BaseModel

from cheshire_drivers.command_responses import EmptyCommandResponse
from cheshire_drivers.delidder_models import DelidRequest


DELIDDER_REQUEST_MODELS: Dict[str, Tuple[str, type[BaseModel]]] = {
    "delid": ("request", DelidRequest),
}


DELIDDER_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "delid": EmptyCommandResponse,
}


def reject_unknown_delidder_fields(
    payload: Dict[str, Any],
    model_cls: type[BaseModel],
) -> None:
    unknown = set(payload.keys()) - set(model_cls.model_fields.keys())
    if unknown:
        raise ValueError(
            f"Unknown fields for {model_cls.__name__}: {sorted(unknown)}. "
            f"Update MCP/REST/caller to align with the cheshire-drivers Pydantic model."
        )


def validate_delidder_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if command not in DELIDDER_REQUEST_MODELS:
        return params
    _, model_cls = DELIDDER_REQUEST_MODELS[command]
    reject_unknown_delidder_fields(params, model_cls)
    model_cls.model_validate(params)
    return params


def wrap_delidder_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if command not in DELIDDER_REQUEST_MODELS:
        return params
    kwarg_name, model_cls = DELIDDER_REQUEST_MODELS[command]
    reject_unknown_delidder_fields(params, model_cls)
    instance = model_cls.model_validate(params)
    return {kwarg_name: instance}
