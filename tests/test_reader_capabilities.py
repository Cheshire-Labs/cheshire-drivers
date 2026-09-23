"""Walk-style guard tests for the reader wire-validation contract."""

import inspect

from cheshire_drivers.interfaces import BaseDriver, IReaderDriver
from cheshire_drivers.liquid_handler_models import _StrictModel
from cheshire_drivers.reader_request_validation import READER_REQUEST_MODELS


_NON_REGISTRY_MEMBERS = set(BaseDriver.__abstractmethods__)


class TestReaderRequestModelsAlignWithInterface:
    def test_every_reader_request_model_command_is_abstract_on_interface(self) -> None:
        for command in READER_REQUEST_MODELS:
            assert command in IReaderDriver.__abstractmethods__

    def test_reader_request_model_kwarg_name_matches_method_signature(self) -> None:
        for command, (kwarg_name, model_cls) in READER_REQUEST_MODELS.items():
            method = getattr(IReaderDriver, command, None)
            assert method is not None
            sig = inspect.signature(method)
            params = [name for name in sig.parameters if name != "self"]
            assert kwarg_name in params

    def test_reader_request_model_type_matches_method_annotation(self) -> None:
        for command, (kwarg_name, model_cls) in READER_REQUEST_MODELS.items():
            method = getattr(IReaderDriver, command)
            sig = inspect.signature(method)
            param = sig.parameters.get(kwarg_name)
            assert param is not None
            assert param.annotation is model_cls

    def test_every_atomic_reader_abstract_method_has_an_entry(self) -> None:
        atomic_abstracts = (
            IReaderDriver.__abstractmethods__ - _NON_REGISTRY_MEMBERS
        )
        registered = set(READER_REQUEST_MODELS)
        missing = atomic_abstracts - registered
        assert not missing

    def test_reader_request_model_is_strict_mode(self) -> None:
        for command, (_, model_cls) in READER_REQUEST_MODELS.items():
            assert issubclass(model_cls, _StrictModel)
            assert model_cls.model_config.get("extra") == "forbid"
