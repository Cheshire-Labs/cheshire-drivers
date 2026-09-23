"""Wire-shape validation for sealer commands.

Mirrors `cheshire_drivers.shaker_request_validation` for the sealer category.
seal is the only category-specific method we uplift here; set_temperature
and get_temperature are duplicately declared on ITempSettable / ITempGettable
(outside the typed-request uplift) so they stay plain-kwargs to keep
multi-mixin sims working.
"""

from typing import Any, Dict, Tuple

from pydantic import BaseModel

from cheshire_drivers.command_responses import EmptyCommandResponse
from cheshire_drivers.sealer_models import SealRequest


SEALER_REQUEST_MODELS: Dict[str, Tuple[str, type[BaseModel]]] = {
    "seal": ("request", SealRequest),
}


SEALER_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "seal": EmptyCommandResponse,
}


def reject_unknown_sealer_fields(
    payload: Dict[str, Any],
    model_cls: type[BaseModel],
) -> None:
    unknown = set(payload.keys()) - set(model_cls.model_fields.keys())
    if unknown:
        raise ValueError(
            f"Unknown fields for {model_cls.__name__}: {sorted(unknown)}. "
            f"Update MCP/REST/caller to align with the cheshire-drivers Pydantic model."
        )


def validate_sealer_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if command not in SEALER_REQUEST_MODELS:
        return params
    _, model_cls = SEALER_REQUEST_MODELS[command]
    reject_unknown_sealer_fields(params, model_cls)
    model_cls.model_validate(params)
    return params


def wrap_sealer_payload(command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if command not in SEALER_REQUEST_MODELS:
        return params
    kwarg_name, model_cls = SEALER_REQUEST_MODELS[command]
    reject_unknown_sealer_fields(params, model_cls)
    instance = model_cls.model_validate(params)
    return {kwarg_name: instance}
