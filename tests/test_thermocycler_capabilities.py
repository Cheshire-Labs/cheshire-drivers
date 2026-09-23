"""Walk-style guard tests for the thermocycler wire-validation contract.

Mirrors `test_centrifuge_capabilities.py` for the thermocycler category. Every
abstract op (including every getter) must have a `THERMOCYCLER_REQUEST_MODELS`
entry whose kwarg name and model type match the interface method signature.
"""

import inspect

from cheshire_drivers.interfaces import BaseDriver, IThermocyclerDriver
from cheshire_drivers.liquid_handler_models import _StrictModel
from cheshire_drivers.thermocycler_request_validation import THERMOCYCLER_REQUEST_MODELS


_NON_REGISTRY_MEMBERS = set(BaseDriver.__abstractmethods__)


class TestThermocyclerRequestModelsAlignWithInterface:
    def test_every_thermocycler_request_model_command_is_abstract_on_interface(self) -> None:
        for command in THERMOCYCLER_REQUEST_MODELS:
            assert command in IThermocyclerDriver.__abstractmethods__

    def test_thermocycler_request_model_kwarg_name_matches_method_signature(self) -> None:
        for command, (kwarg_name, model_cls) in THERMOCYCLER_REQUEST_MODELS.items():
            method = getattr(IThermocyclerDriver, command, None)
            assert method is not None
            sig = inspect.signature(method)
            params = [name for name in sig.parameters if name != "self"]
            assert kwarg_name in params

    def test_thermocycler_request_model_type_matches_method_annotation(self) -> None:
        for command, (kwarg_name, model_cls) in THERMOCYCLER_REQUEST_MODELS.items():
            method = getattr(IThermocyclerDriver, command)
            sig = inspect.signature(method)
            param = sig.parameters.get(kwarg_name)
            assert param is not None
            assert param.annotation is model_cls

    def test_every_atomic_thermocycler_abstract_method_has_an_entry(self) -> None:
        atomic_abstracts = (
            IThermocyclerDriver.__abstractmethods__ - _NON_REGISTRY_MEMBERS
        )
        registered = set(THERMOCYCLER_REQUEST_MODELS)
        missing = atomic_abstracts - registered
        assert not missing

    def test_thermocycler_request_model_is_strict_mode(self) -> None:
        for command, (_, model_cls) in THERMOCYCLER_REQUEST_MODELS.items():
            assert issubclass(model_cls, _StrictModel)
            assert model_cls.model_config.get("extra") == "forbid"
