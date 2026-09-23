"""Walk-style guard tests for the protocol runner wire-validation contract."""

import inspect

from cheshire_drivers.interfaces import (
    BaseDriver,
    ILiquidHandlerWithProtocolDriver,
    IProtocolRunnerDriver,
)
from cheshire_drivers.liquid_handler_models import _StrictModel
from cheshire_drivers.protocol_runner_request_validation import (
    PROTOCOL_RUNNER_REQUEST_MODELS,
)
from cheshire_drivers.sims import SimLiquidHandlerWithProtocolDriver


_NON_REGISTRY_MEMBERS = set(BaseDriver.__abstractmethods__)


class TestProtocolRunnerRequestModelsAlignWithInterface:
    def test_every_protocol_runner_request_model_command_is_abstract_on_interface(self) -> None:
        for command in PROTOCOL_RUNNER_REQUEST_MODELS:
            assert command in IProtocolRunnerDriver.__abstractmethods__

    def test_protocol_runner_request_model_kwarg_name_matches_method_signature(self) -> None:
        for command, (kwarg_name, model_cls) in PROTOCOL_RUNNER_REQUEST_MODELS.items():
            method = getattr(IProtocolRunnerDriver, command, None)
            assert method is not None
            sig = inspect.signature(method)
            params = [name for name in sig.parameters if name != "self"]
            assert kwarg_name in params

    def test_protocol_runner_request_model_type_matches_method_annotation(self) -> None:
        for command, (kwarg_name, model_cls) in PROTOCOL_RUNNER_REQUEST_MODELS.items():
            method = getattr(IProtocolRunnerDriver, command)
            sig = inspect.signature(method)
            param = sig.parameters.get(kwarg_name)
            assert param is not None
            assert param.annotation is model_cls

    def test_every_atomic_protocol_runner_abstract_method_has_an_entry(self) -> None:
        atomic_abstracts = (
            IProtocolRunnerDriver.__abstractmethods__ - _NON_REGISTRY_MEMBERS
        )
        registered = set(PROTOCOL_RUNNER_REQUEST_MODELS)
        missing = atomic_abstracts - registered
        assert not missing

    def test_protocol_runner_request_model_is_strict_mode(self) -> None:
        for command, (_, model_cls) in PROTOCOL_RUNNER_REQUEST_MODELS.items():
            assert issubclass(model_cls, _StrictModel)
            assert model_cls.model_config.get("extra") == "forbid"


class TestLiquidHandlerWithProtocolComposite:
    """Composite LH drivers nominally implement the union interface.

    orca-core types its liquid-handler devices on `ILiquidHandlerWithProtocolDriver`
    (well-level verbs AND run_protocol) and assigns these concrete classes to that
    type on the pure-sim resolve path, so the nominal subclass relationship must
    hold or the typing breaks silently.
    """

    def test_sim_composite_implements_union_interface(self) -> None:
        assert issubclass(
            SimLiquidHandlerWithProtocolDriver, ILiquidHandlerWithProtocolDriver
        )

    def test_chatterbox_composite_implements_union_interface(self) -> None:
        from cheshire_drivers.plr.liquid_handler import (
            ChatterboxLiquidHandlerWithProtocolDriver,
        )

        assert issubclass(
            ChatterboxLiquidHandlerWithProtocolDriver, ILiquidHandlerWithProtocolDriver
        )
