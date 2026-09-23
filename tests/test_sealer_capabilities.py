"""Walk-style guard tests for the sealer wire-validation contract.

Mirrors `test_shaker_capabilities.py` for the sealer category. seal is the
only category-specific method given a typed request; set_temperature and
get_temperature are duplicately declared on ITempSettable / ITempGettable
(left out of that uplift) and stay plain-kwargs.
"""

import inspect

from cheshire_drivers.interfaces import BaseDriver, ISealerDriver
from cheshire_drivers.liquid_handler_models import _StrictModel
from cheshire_drivers.sealer_request_validation import SEALER_REQUEST_MODELS


_NON_REGISTRY_MEMBERS = (
    set(BaseDriver.__abstractmethods__)
    | {"set_temperature", "get_temperature"}
)


class TestSealerRequestModelsAlignWithInterface:
    def test_every_sealer_request_model_command_is_abstract_on_interface(self) -> None:
        for command in SEALER_REQUEST_MODELS:
            assert command in ISealerDriver.__abstractmethods__

    def test_sealer_request_model_kwarg_name_matches_method_signature(self) -> None:
        for command, (kwarg_name, model_cls) in SEALER_REQUEST_MODELS.items():
            method = getattr(ISealerDriver, command, None)
            assert method is not None
            sig = inspect.signature(method)
            params = [name for name in sig.parameters if name != "self"]
            assert kwarg_name in params

    def test_sealer_request_model_type_matches_method_annotation(self) -> None:
        for command, (kwarg_name, model_cls) in SEALER_REQUEST_MODELS.items():
            method = getattr(ISealerDriver, command)
            sig = inspect.signature(method)
            param = sig.parameters.get(kwarg_name)
            assert param is not None
            assert param.annotation is model_cls

    def test_every_atomic_sealer_abstract_method_has_an_entry(self) -> None:
        atomic_abstracts = (
            ISealerDriver.__abstractmethods__ - _NON_REGISTRY_MEMBERS
        )
        registered = set(SEALER_REQUEST_MODELS)
        missing = atomic_abstracts - registered
        assert not missing, (
            f"Sealer atomic methods on ISealerDriver missing from "
            f"SEALER_REQUEST_MODELS: {sorted(missing)}"
        )

    def test_sealer_request_model_is_strict_mode(self) -> None:
        for command, (_, model_cls) in SEALER_REQUEST_MODELS.items():
            assert issubclass(model_cls, _StrictModel)
            assert model_cls.model_config.get("extra") == "forbid"
