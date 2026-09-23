"""Walk-style guard tests for the transporter wire-validation contract.

Mirrors `TestLhRequestModelsAlignWithInterface` from `test_driver_capabilities.py`
for the transporter category. Catches drift between the
`transporter_request_validation` registry and `ITransporterDriver`'s actual
abstract method signatures: the next `77d445a`-style regression on transporter
fails CI loudly here instead of breaking on first wire call.
"""

import inspect

from pydantic import ConfigDict

from cheshire_drivers.interfaces import ITransporterDriver
from cheshire_drivers.liquid_handler_models import _StrictModel
from cheshire_drivers.transporter_models import TeachpointModel
from cheshire_drivers.transporter_request_validation import (
    TRANSPORTER_REQUEST_MODELS,
)


class TestTransporterRequestModelsAlignWithInterface:
    """The transporter_request_validation registry must match
    ITransporterDriver's actual abstract method signatures."""

    def test_every_transporter_request_model_command_is_abstract_on_itr(self) -> None:
        for command in TRANSPORTER_REQUEST_MODELS:
            assert command in ITransporterDriver.__abstractmethods__, (
                f"TRANSPORTER_REQUEST_MODELS lists {command!r} but it is not an "
                f"abstractmethod on ITransporterDriver"
            )

    def test_transporter_request_model_kwarg_name_matches_method_signature(self) -> None:
        for command, (kwarg_name, model_cls) in TRANSPORTER_REQUEST_MODELS.items():
            method = getattr(ITransporterDriver, command, None)
            assert method is not None, (
                f"ITransporterDriver has no method {command!r}"
            )
            sig = inspect.signature(method)
            params = [name for name in sig.parameters if name != "self"]
            assert kwarg_name in params, (
                f"TRANSPORTER_REQUEST_MODELS[{command!r}] declares kwarg name "
                f"{kwarg_name!r} but ITransporterDriver.{command} signature "
                f"has params {params}"
            )

    def test_transporter_request_model_type_matches_method_annotation(self) -> None:
        for command, (kwarg_name, model_cls) in TRANSPORTER_REQUEST_MODELS.items():
            method = getattr(ITransporterDriver, command)
            sig = inspect.signature(method)
            param = sig.parameters.get(kwarg_name)
            assert param is not None
            assert param.annotation is model_cls, (
                f"TRANSPORTER_REQUEST_MODELS[{command!r}] = {model_cls.__name__} "
                f"but ITransporterDriver.{command}({kwarg_name}: ...) is "
                f"annotated as {param.annotation}"
            )

    def test_every_atomic_transporter_abstract_method_has_an_entry(self) -> None:
        # Properties (`name`, `is_initialized`) and sync helpers are not
        # wire-callable; the orca-client executor rejects non-coroutine
        # commands. Every other abstractmethod must be in TRANSPORTER_REQUEST_MODELS
        # (no NO_REQUEST set; mirrors LH precedent after da17814 retirement).
        non_atomic_or_sync = {
            "name",
            "is_initialized",
        }
        atomic_abstracts = (
            ITransporterDriver.__abstractmethods__ - non_atomic_or_sync
        )
        registered = set(TRANSPORTER_REQUEST_MODELS)
        missing = atomic_abstracts - registered
        assert not missing, (
            f"Transporter atomic methods on ITransporterDriver missing from "
            f"TRANSPORTER_REQUEST_MODELS: {sorted(missing)}"
        )

    def test_transporter_request_model_is_strict_mode(self) -> None:
        """Every Request model rejects unknown fields (`extra='forbid'`).

        Inheriting `_StrictModel` enforces this; the test makes the contract
        explicit so a future model that overrides config without preserving
        `extra='forbid'` fails the suite immediately.
        """
        for command, (_, model_cls) in TRANSPORTER_REQUEST_MODELS.items():
            assert issubclass(model_cls, _StrictModel), (
                f"{model_cls.__name__} for command {command!r} must inherit _StrictModel"
            )
            cfg = model_cls.model_config
            assert cfg.get("extra") == "forbid", (
                f"{model_cls.__name__} (command {command!r}) must declare "
                f"extra='forbid'; got {cfg!r}"
            )

    def test_teachpoint_model_is_strict_mode(self) -> None:
        """The wire-only TeachpointModel is also strict."""
        assert issubclass(TeachpointModel, _StrictModel)
        assert TeachpointModel.model_config.get("extra") == "forbid"
