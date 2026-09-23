"""Drift guard between *_REQUEST_MODELS and *_RESPONSE_MODELS.

Every device-category command surface is paired across two dicts:
``*_REQUEST_MODELS`` declares the input shape, ``*_RESPONSE_MODELS``
declares the output shape. orca-client wraps the driver return into
the response shape; the gateway service validates the wire payload
against the same response_cls. If the two dicts fall out of sync,
orca-client wraps with one model while the gateway tries to validate
with another, and we see ``driver_failure`` envelopes on what should
be successful commands.

This test fails CI if a new command lands in REQUEST_MODELS but not
RESPONSE_MODELS (or vice versa), which is what would happen if a
contributor adds a method on an interface, registers the request, and
forgets the response. Lookup is the single resolution point so we
guard it with a single test module.
"""

import inspect
from typing import Mapping

import pytest
from pydantic import BaseModel

from cheshire_drivers.centrifuge_request_validation import (
    CENTRIFUGE_REQUEST_MODELS,
    CENTRIFUGE_RESPONSE_MODELS,
)
from cheshire_drivers.command_responses import (
    CommandResponse,
    EmptyCommandResponse,
    InitializedResponse,
    TemperatureResponse,
)
from cheshire_drivers.delidder_request_validation import (
    DELIDDER_REQUEST_MODELS,
    DELIDDER_RESPONSE_MODELS,
)
from cheshire_drivers.lh_request_validation import (
    LH_REQUEST_MODELS,
    LH_RESPONSE_MODELS,
    LIQUID_PROBE_REQUEST_MODELS,
    LIQUID_PROBE_RESPONSE_MODELS,
)
from cheshire_drivers.protocol_runner_request_validation import (
    PROTOCOL_RUNNER_REQUEST_MODELS,
    PROTOCOL_RUNNER_RESPONSE_MODELS,
)
from cheshire_drivers.reader_request_validation import (
    READER_REQUEST_MODELS,
    READER_RESPONSE_MODELS,
)
from cheshire_drivers.response_lookup import (
    BASE_RESPONSE_MODELS,
    lookup_response_model,
)
from cheshire_drivers.sealer_request_validation import (
    SEALER_REQUEST_MODELS,
    SEALER_RESPONSE_MODELS,
)
from cheshire_drivers.interfaces import ILiquidProbeDriver
from cheshire_drivers.shaker_request_validation import (
    SHAKER_REQUEST_MODELS,
    SHAKER_RESPONSE_MODELS,
)
from cheshire_drivers.thermocycler_request_validation import (
    THERMOCYCLER_REQUEST_MODELS,
    THERMOCYCLER_RESPONSE_MODELS,
)
from cheshire_drivers.transporter_request_validation import (
    TRANSPORTER_REQUEST_MODELS,
    TRANSPORTER_RESPONSE_MODELS,
)


_CATEGORIES: list[tuple[str, Mapping[str, object], Mapping[str, type[BaseModel]]]] = [
    ("LH", LH_REQUEST_MODELS, LH_RESPONSE_MODELS),
    ("LiquidProbe", LIQUID_PROBE_REQUEST_MODELS, LIQUID_PROBE_RESPONSE_MODELS),
    ("Transporter", TRANSPORTER_REQUEST_MODELS, TRANSPORTER_RESPONSE_MODELS),
    ("Shaker", SHAKER_REQUEST_MODELS, SHAKER_RESPONSE_MODELS),
    ("Sealer", SEALER_REQUEST_MODELS, SEALER_RESPONSE_MODELS),
    ("Centrifuge", CENTRIFUGE_REQUEST_MODELS, CENTRIFUGE_RESPONSE_MODELS),
    ("Thermocycler", THERMOCYCLER_REQUEST_MODELS, THERMOCYCLER_RESPONSE_MODELS),
    ("Reader", READER_REQUEST_MODELS, READER_RESPONSE_MODELS),
    ("Delidder", DELIDDER_REQUEST_MODELS, DELIDDER_RESPONSE_MODELS),
    ("ProtocolRunner", PROTOCOL_RUNNER_REQUEST_MODELS, PROTOCOL_RUNNER_RESPONSE_MODELS),
]


@pytest.mark.parametrize(
    "category, request_models, response_models",
    _CATEGORIES,
    ids=lambda c: c if isinstance(c, str) else "",
)
def test_request_response_model_keys_align(
    category: str,
    request_models: Mapping[str, object],
    response_models: Mapping[str, type[BaseModel]],
) -> None:
    """Every command in REQUEST_MODELS must have a paired RESPONSE_MODELS entry.

    Drift here means orca-client wraps with one model while the
    gateway validates with another, surfacing as silent ``driver_failure``
    envelopes on otherwise-correct commands.
    """
    request_keys = set(request_models.keys())
    response_keys = set(response_models.keys())
    missing_response = request_keys - response_keys
    missing_request = response_keys - request_keys
    assert not missing_response, (
        f"{category}: commands in REQUEST_MODELS but not in RESPONSE_MODELS: "
        f"{sorted(missing_response)}. Pair them in the per-category "
        f"_request_validation.py module."
    )
    assert not missing_request, (
        f"{category}: commands in RESPONSE_MODELS but not in REQUEST_MODELS: "
        f"{sorted(missing_request)}. Either remove the spurious entry or "
        f"add the matching request mapping."
    )


@pytest.mark.parametrize(
    "category, response_models",
    [(name, resp) for name, _req, resp in _CATEGORIES],
    ids=lambda c: c if isinstance(c, str) else "",
)
def test_response_models_subclass_basemodel(
    category: str,
    response_models: Mapping[str, type[BaseModel]],
) -> None:
    """Every value in *_RESPONSE_MODELS must be a BaseModel subclass.

    The gateway's ``response_cls.model_validate`` and orca-client's
    return-wrapping both rely on this. A non-BaseModel value would
    surface as a runtime AttributeError on the first command of the
    category to land at the gateway.
    """
    for command, model_cls in response_models.items():
        assert isinstance(model_cls, type) and issubclass(model_cls, BaseModel), (
            f"{category}: {command!r} -> {model_cls!r} is not a BaseModel "
            f"subclass."
        )


def test_base_response_models_subclass_command_response() -> None:
    """BASE_RESPONSE_MODELS values must be CommandResponse subclasses.

    The base map covers cross-category and BaseDriver methods (stop,
    initialize, open, close, is_initialized, set_temperature,
    get_temperature). Its values are the cross-cutting CommandResponse
    subclasses defined in command_responses.py.
    """
    for command, model_cls in BASE_RESPONSE_MODELS.items():
        assert issubclass(model_cls, CommandResponse), (
            f"BASE_RESPONSE_MODELS[{command!r}] -> {model_cls!r} must "
            f"extend CommandResponse."
        )


def test_lookup_response_model_resolves_lh_command_via_interface_gate() -> None:
    """ILiquidHandler advertised resolves an LH command to the LH map."""
    cls = lookup_response_model("aspirate", frozenset({"ILiquidHandler"}))
    assert cls is LH_RESPONSE_MODELS["aspirate"]


def test_lookup_response_model_resolves_transporter_via_interface_gate() -> None:
    """ITransporter advertised resolves a transporter command to its map."""
    cls = lookup_response_model("get_speed", frozenset({"ITransporter"}))
    assert cls is TRANSPORTER_RESPONSE_MODELS["get_speed"]


def test_lookup_response_model_resolves_thermocycler_via_interface_gate() -> None:
    """IThermocycler advertised resolves a thermocycler getter to its map."""
    cls = lookup_response_model(
        "get_block_current_temperature", frozenset({"IThermocycler"})
    )
    assert cls is THERMOCYCLER_RESPONSE_MODELS["get_block_current_temperature"]


def test_lookup_response_model_falls_through_to_base_for_basedriver_methods() -> None:
    """``initialize`` on any device resolves to EmptyCommandResponse."""
    cls = lookup_response_model("initialize", frozenset({"IShaker"}))
    assert cls is EmptyCommandResponse


def test_lookup_response_model_resolves_is_initialized_to_typed_bool_wrapper() -> None:
    """``is_initialized`` resolves to InitializedResponse regardless of category."""
    cls = lookup_response_model("is_initialized", frozenset({"ITransporter"}))
    assert cls is InitializedResponse


def test_lookup_response_model_resolves_get_temperature_via_base() -> None:
    """``get_temperature`` resolves to TemperatureResponse via the base fallback."""
    cls = lookup_response_model("get_temperature", frozenset({"ISealer"}))
    assert cls is TemperatureResponse


def test_lookup_response_model_unknown_command_falls_through_to_empty() -> None:
    """Unknown commands surface as EmptyCommandResponse so vendor extras work.

    The fall-through is intentional for vendor-specific extras that
    aren't typed in the per-category map. orca-client's _wrap_result
    raises ValueError if a primitive return cannot project onto
    EmptyCommandResponse, so the silent-fallback risk is on the
    orca-client side, not here.
    """
    cls = lookup_response_model("vendor_specific_extra", frozenset({"ILiquidHandler"}))
    assert cls is EmptyCommandResponse


def test_liquid_probe_resolves_only_for_a_device_that_advertises_the_probe_interface() -> None:
    """A liquid handler with no pressure sensor never declares ILiquidProbe, so the
    command does not resolve a typed payload on it -- and the capability gate above
    this refuses it outright rather than sending it to the deck."""
    from cheshire_drivers.command_responses import EmptyCommandResponse as _Empty
    from cheshire_drivers.liquid_handler_models import LiquidProbeResponse

    assert lookup_response_model("liquid_probe", frozenset({"ILiquidProbe"})) is LiquidProbeResponse
    assert lookup_response_model("liquid_probe", frozenset({"ILiquidHandler"})) is _Empty


class TestProbeRegistryMatchesTheInterface:
    """The probe registry and ILiquidProbeDriver must name the same commands.

    A method added to the interface with no Request model reaches the driver
    unvalidated, and a model registered for a command the interface does not
    declare can never be dispatched. Both go unnoticed without this pair.
    """

    def test_every_registered_command_is_abstract_on_the_interface(self) -> None:
        for command in LIQUID_PROBE_REQUEST_MODELS:
            assert command in ILiquidProbeDriver.__abstractmethods__, (
                f"LIQUID_PROBE_REQUEST_MODELS lists {command!r}, "
                f"which ILiquidProbeDriver does not declare"
            )

    def test_every_abstract_command_is_registered(self) -> None:
        for command in ILiquidProbeDriver.__abstractmethods__:
            assert command in LIQUID_PROBE_REQUEST_MODELS, (
                f"{command!r} is abstract on ILiquidProbeDriver but has no Request model"
            )

    def test_registered_kwarg_name_matches_the_method_signature(self) -> None:
        for command, (kwarg_name, _model) in LIQUID_PROBE_REQUEST_MODELS.items():
            params = [
                p
                for p in inspect.signature(getattr(ILiquidProbeDriver, command)).parameters
                if p != "self"
            ]
            assert kwarg_name in params, (
                f"LIQUID_PROBE_REQUEST_MODELS[{command!r}] declares kwarg {kwarg_name!r} "
                f"but the signature has {params}"
            )
