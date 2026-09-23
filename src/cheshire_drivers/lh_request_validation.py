"""Wire-shape validation for liquid-handler commands.

Single source of truth for which liquid-handler command takes which Pydantic
Request model and the flow_rate scalar-to-list expansion that MCP/REST clients
depend on. Imported by:

- the gateway server: pre-flight validation in the device controller before the
  WebSocket round-trip.
- orca-client: dispatch-time wrapping in CommandExecutor._deserialize_params
  before invoking the driver method.

Both layers must agree on shape; sharing the module guarantees they do.
"""

from typing import Dict, Tuple

from pydantic import BaseModel, JsonValue

from cheshire_drivers.command_responses import EmptyCommandResponse
from cheshire_drivers.homing_models import HomeRequest
from cheshire_drivers.liquid_handler_models import (
    Aspirate96Request,
    AspirateRequest,
    DeckLayoutConfig,
    DeckStateResponse,
    DiscardTipsRequest,
    Dispense96Request,
    DispenseRequest,
    DropTips96Request,
    DropTipsRequest,
    GetDeckStateRequest,
    GetHeadConfigurationRequest,
    HeadConfigurationResponse,
    LabwareStateResponse,
    LiquidProbeRequest,
    LiquidProbeResponse,
    MixRequest,
    MovePlateRequest,
    PickUpTips96Request,
    PickUpTipsRequest,
    AddDeckLabwareRequest,
    DiscardStrandedTipsRequest,
    ReconcileDeckOccupancyRequest,
    ReconcileHardwareStateRequest,
    ReconcileHardwareStateResponse,
    ResetDeckLabwareRequest,
    ReturnTips96Request,
    RemoveDeckLabwareRequest,
)


# Every atomic LH command on the driver takes a Pydantic Request. Empty
# marker models (e.g. ReturnTips96Request, GetDeckStateRequest) are valid
# entries; they preserve the uniform shape across the wire while declaring
# no input fields.
LH_REQUEST_MODELS: Dict[str, Tuple[str, type[BaseModel]]] = {
    "configure_deck": ("config", DeckLayoutConfig),
    "aspirate": ("request", AspirateRequest),
    "dispense": ("request", DispenseRequest),
    "pick_up_tips": ("request", PickUpTipsRequest),
    "drop_tips": ("request", DropTipsRequest),
    "discard_tips": ("request", DiscardTipsRequest),
    "mix": ("request", MixRequest),
    "move_plate": ("request", MovePlateRequest),
    "add_deck_labware": ("request", AddDeckLabwareRequest),
    "remove_deck_labware": ("request", RemoveDeckLabwareRequest),
    "aspirate96": ("request", Aspirate96Request),
    "dispense96": ("request", Dispense96Request),
    "pick_up_tips96": ("request", PickUpTips96Request),
    "drop_tips96": ("request", DropTips96Request),
    "return_tips96": ("request", ReturnTips96Request),
    "get_deck_state": ("request", GetDeckStateRequest),
    "home": ("request", HomeRequest),
    "get_head_configuration": ("request", GetHeadConfigurationRequest),
    "reset_deck_labware": ("request", ResetDeckLabwareRequest),
    "reconcile_deck_occupancy": ("request", ReconcileDeckOccupancyRequest),
    "reconcile_hardware_state": ("request", ReconcileHardwareStateRequest),
    "discard_stranded_tips": ("request", DiscardStrandedTipsRequest),
}


# Every key in LH_REQUEST_MODELS must be paired here; the test_response_lookup
# drift guard fails CI if they fall out of sync. Ack-only commands (driver returns None) pair with EmptyCommandResponse.
LH_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "configure_deck": LabwareStateResponse,
    "aspirate": LabwareStateResponse,
    "dispense": LabwareStateResponse,
    "pick_up_tips": LabwareStateResponse,
    "drop_tips": LabwareStateResponse,
    "discard_tips": LabwareStateResponse,
    "mix": LabwareStateResponse,
    "move_plate": EmptyCommandResponse,
    "add_deck_labware": EmptyCommandResponse,
    "remove_deck_labware": EmptyCommandResponse,
    "aspirate96": LabwareStateResponse,
    "dispense96": LabwareStateResponse,
    "pick_up_tips96": LabwareStateResponse,
    "drop_tips96": LabwareStateResponse,
    "return_tips96": LabwareStateResponse,
    "get_deck_state": DeckStateResponse,
    "home": EmptyCommandResponse,
    "get_head_configuration": HeadConfigurationResponse,
    "reset_deck_labware": LabwareStateResponse,
    "reconcile_deck_occupancy": LabwareStateResponse,
    "reconcile_hardware_state": ReconcileHardwareStateResponse,
    "discard_stranded_tips": ReconcileHardwareStateResponse,
}

# ``liquid_probe`` is its own interface, not an ILiquidHandler command: sensing the
# liquid surface needs a pressure sensor the head either has or does not. The refusal
# for a head without one comes from the interface it does not declare; this registry
# only says how the payload is shaped once the command is allowed through.
LIQUID_PROBE_REQUEST_MODELS: Dict[str, Tuple[str, type[BaseModel]]] = {
    "liquid_probe": ("request", LiquidProbeRequest),
}


LIQUID_PROBE_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "liquid_probe": LiquidProbeResponse,
}


LH_LIST_FLOW_RATE_MODELS: frozenset[type[BaseModel]] = frozenset(
    {AspirateRequest, DispenseRequest}
)


# Maps each LH command that carries per-channel flow_rates to the request-level
# field that holds the per-target slices. Used by ``normalize_lh_flow_rate`` to
# count total positions when expanding a scalar ``flow_rate`` ergonomic into the
# per-channel ``flow_rates`` list. Adding a new per-channel command means adding
# its targets-field name here.
LH_TARGETS_FIELD: Dict[type[BaseModel], str] = {
    AspirateRequest: "aspirations",
    DispenseRequest: "dispenses",
}


def normalize_lh_flow_rate(
    params: Dict[str, JsonValue],
    model_cls: type[BaseModel],
) -> Dict[str, JsonValue]:
    """Expand scalar `flow_rate` to N-element `flow_rates` for per-channel models.

    AspirateRequest and DispenseRequest carry per-channel `flow_rates: list[float]`
    where N is the total channel count across all target slices. The channel count
    is ``len(volumes)`` per slice (always present), NOT ``len(positions)`` -- a
    single-container (trough) slice has volumes but no positions.
    MCP/REST clients send a scalar `flow_rate` for ergonomics; this helper expands
    to a list of length ``sum(len(target.volumes) for target in targets)``.

    Raises ValueError if both `flow_rate` and `flow_rates` are present.
    """
    if model_cls not in LH_LIST_FLOW_RATE_MODELS:
        return params
    if "flow_rate" not in params:
        return params
    if "flow_rates" in params:
        raise ValueError(
            f"Conflicting flow_rate (scalar) and flow_rates (list) on "
            f"{model_cls.__name__}. Send one or the other."
        )
    flow_rate = _as_flow_rate(params["flow_rate"])
    targets_field = LH_TARGETS_FIELD[model_cls]
    targets = params.get(targets_field, [])
    if not isinstance(targets, list):
        raise ValueError(
            f"{targets_field} must be a list of targets for flow_rate to spread across, "
            f"got {type(targets).__name__}."
        )
    total_channels = 0
    for target in targets:
        if not isinstance(target, dict):
            continue
        volumes = target.get("volumes", [])
        if isinstance(volumes, list):
            total_channels += len(volumes)
    if total_channels == 0:
        return params
    rates: list[JsonValue] = [flow_rate] * total_channels
    expanded: Dict[str, JsonValue] = {**params}
    expanded.pop("flow_rate")
    expanded["flow_rates"] = rates
    return expanded


def _as_flow_rate(value: JsonValue) -> float:
    """The scalar `flow_rate` as a number, read the way the list it expands into is.

    `flow_rates` goes through pydantic, which reads "5" as 5.0, so the scalar has
    to accept the same string rather than being stricter than the field it
    becomes. A bool is not a rate, though Python will happily call it 1.0.
    """
    if isinstance(value, bool):
        raise ValueError("flow_rate must be a number, got bool.")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"flow_rate must be a number, got {value!r}.") from exc
    raise ValueError(f"flow_rate must be a number, got {type(value).__name__}.")


def reject_unknown_lh_fields(
    payload: Dict[str, JsonValue],
    model_cls: type[BaseModel],
) -> None:
    """Raise ValueError if payload has fields the Request model does not declare.

    Pydantic v2 default behavior is `extra="ignore"`, which would silently drop
    unknown fields. The driver layer is the source of truth; if a caller sends
    a field the model does not know, that is a wire-shape mismatch we want to
    catch loudly rather than silently lose data on every call.
    """
    unknown = set(payload.keys()) - set(model_cls.model_fields.keys())
    if unknown:
        raise ValueError(
            f"Unknown fields for {model_cls.__name__}: {sorted(unknown)}. "
            f"Update MCP/REST/caller to align with the cheshire-drivers Pydantic model."
        )


def validate_lh_payload(
    command: str, params: Dict[str, JsonValue]
) -> Dict[str, JsonValue]:
    """Validate flat-dict params against the Request model for an LH command.

    Returns the normalized flat-dict payload (with flow_rate expanded if needed).
    Pass-through for commands not in LH_REQUEST_MODELS (no validation performed).

    Raises:
        ValueError: conflicting flow_rate/flow_rates, or unknown fields.
        pydantic.ValidationError: payload does not match the Request model shape.
    """
    if command not in LH_REQUEST_MODELS:
        return params
    _, model_cls = LH_REQUEST_MODELS[command]
    payload = normalize_lh_flow_rate(params, model_cls)
    reject_unknown_lh_fields(payload, model_cls)
    model_cls.model_validate(payload)
    return payload


def wrap_lh_payload(
    command: str, params: Dict[str, JsonValue]
) -> Dict[str, JsonValue | BaseModel]:
    """Validate and wrap a flat-dict payload into the kwarg dict the driver method expects.

    For commands in LH_REQUEST_MODELS, returns `{kwarg_name: <Pydantic instance>}`
    suitable for `method(**result)` dispatch. For other commands, returns
    params unchanged.
    """
    if command not in LH_REQUEST_MODELS:
        return dict(params)
    kwarg_name, model_cls = LH_REQUEST_MODELS[command]
    payload = normalize_lh_flow_rate(params, model_cls)
    reject_unknown_lh_fields(payload, model_cls)
    instance = model_cls.model_validate(payload)
    return {kwarg_name: instance}


def validate_liquid_probe_payload(
    command: str, params: Dict[str, JsonValue]
) -> Dict[str, JsonValue]:
    """Validate flat-dict params against the Request model for a liquid-probe command.

    Returns the params unchanged. Pass-through for commands the probe interface
    does not own.

    Raises:
        ValueError: unknown fields.
        pydantic.ValidationError: payload does not match the Request model shape.
    """
    if command not in LIQUID_PROBE_REQUEST_MODELS:
        return params
    _, model_cls = LIQUID_PROBE_REQUEST_MODELS[command]
    reject_unknown_lh_fields(params, model_cls)
    model_cls.model_validate(params)
    return params


def wrap_liquid_probe_payload(
    command: str, params: Dict[str, JsonValue]
) -> Dict[str, JsonValue | BaseModel]:
    """Validate and wrap a flat-dict payload into the kwarg dict the driver method expects."""
    if command not in LIQUID_PROBE_REQUEST_MODELS:
        return dict(params)
    kwarg_name, model_cls = LIQUID_PROBE_REQUEST_MODELS[command]
    reject_unknown_lh_fields(params, model_cls)
    instance = model_cls.model_validate(params)
    return {kwarg_name: instance}
