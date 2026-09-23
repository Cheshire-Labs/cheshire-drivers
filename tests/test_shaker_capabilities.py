"""Walk-style guard tests for the shaker wire-validation contract.

Mirrors `TestTransporterRequestModelsAlignWithInterface` for the shaker
category. Catches drift between the `shaker_request_validation` registry
and `IShakerDriver`'s actual abstract method signatures.
"""

import inspect

from cheshire_drivers.interfaces import BaseDriver, IShakerDriver
from cheshire_drivers.liquid_handler_models import _StrictModel
from cheshire_drivers.shaker_request_validation import SHAKER_REQUEST_MODELS


# Methods that stay parameterless on IShakerDriver intentionally:
# - BaseDriver members (initialize, open, close, is_initialized): inherited
#   contract; per-category typing would force per-driver wrapping for any
#   command name that BaseDriver also exposes.
# - Cross-category commands (stop): the generic device_stop tool routes
#   command="stop" to any device type; per-category Request typing would
#   crash non-shaker devices.
# - Properties (supports_locking): not wire-callable methods.
_NON_REGISTRY_MEMBERS = (
    set(BaseDriver.__abstractmethods__)
    | {"stop", "supports_locking"}
)


class TestShakerRequestModelsAlignWithInterface:
    """The shaker_request_validation registry must match IShakerDriver's
    actual abstract method signatures."""

    def test_every_shaker_request_model_command_is_abstract_on_interface(self) -> None:
        for command in SHAKER_REQUEST_MODELS:
            assert command in IShakerDriver.__abstractmethods__, (
                f"SHAKER_REQUEST_MODELS lists {command!r} but it is not an "
                f"abstractmethod on IShakerDriver"
            )

    def test_shaker_request_model_kwarg_name_matches_method_signature(self) -> None:
        for command, (kwarg_name, model_cls) in SHAKER_REQUEST_MODELS.items():
            method = getattr(IShakerDriver, command, None)
            assert method is not None, f"IShakerDriver has no method {command!r}"
            sig = inspect.signature(method)
            params = [name for name in sig.parameters if name != "self"]
            assert kwarg_name in params, (
                f"SHAKER_REQUEST_MODELS[{command!r}] declares kwarg name "
                f"{kwarg_name!r} but IShakerDriver.{command} signature "
                f"has params {params}"
            )

    def test_shaker_request_model_type_matches_method_annotation(self) -> None:
        for command, (kwarg_name, model_cls) in SHAKER_REQUEST_MODELS.items():
            method = getattr(IShakerDriver, command)
            sig = inspect.signature(method)
            param = sig.parameters.get(kwarg_name)
            assert param is not None
            assert param.annotation is model_cls, (
                f"SHAKER_REQUEST_MODELS[{command!r}] = {model_cls.__name__} "
                f"but IShakerDriver.{command}({kwarg_name}: ...) is "
                f"annotated as {param.annotation}"
            )

    def test_every_atomic_shaker_abstract_method_has_an_entry(self) -> None:
        atomic_abstracts = (
            IShakerDriver.__abstractmethods__ - _NON_REGISTRY_MEMBERS
        )
        registered = set(SHAKER_REQUEST_MODELS)
        missing = atomic_abstracts - registered
        assert not missing, (
            f"Shaker atomic methods on IShakerDriver missing from "
            f"SHAKER_REQUEST_MODELS: {sorted(missing)}"
        )

    def test_shaker_request_model_is_strict_mode(self) -> None:
        for command, (_, model_cls) in SHAKER_REQUEST_MODELS.items():
            assert issubclass(model_cls, _StrictModel), (
                f"{model_cls.__name__} for command {command!r} must inherit _StrictModel"
            )
            cfg = model_cls.model_config
            assert cfg.get("extra") == "forbid", (
                f"{model_cls.__name__} (command {command!r}) must declare "
                f"extra='forbid'; got {cfg!r}"
            )
