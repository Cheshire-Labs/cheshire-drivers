"""Walk-style guard tests for the capability-advertisement contract.

Driver capability is advertised via two layers on the wire:
1. `interfaces: ClassVar[frozenset[str]]` — manual on each interface class.
2. `capabilities` — auto-derived at handshake by orca-client via
   `cheshire_drivers.driver_introspection.derive_capabilities`. Operators get
   full method-level access for troubleshooting (per user direction); every
   public callable (sync or async) or @property reachable on the concrete
   class is wire-callable. The introspection endpoint surfaces signatures +
   docstrings so operators see what they can call and how.
"""

import inspect
from typing import ClassVar, get_args, get_origin

import pytest

from cheshire_drivers import sims, plr_wrappers, venus_driver, null_plate_pad
from cheshire_drivers import plr as plr_pkg
from cheshire_drivers.command_timings import CommandTiming, command_timing
from cheshire_drivers.driver_introspection import (
    MethodInfo,
    _is_external_interface_member,
    derive_capabilities,
    describe_driver,
    describe_method,
    interface_command_names,
    interface_member_names,
)
from cheshire_drivers.shaker_models import ShakeRequest
from cheshire_drivers import interfaces as interfaces_module
from cheshire_drivers.interfaces import (
    BaseDriver,
    ICentrifugeDriver,
    ILiquidHandlerDriver,
    ILiquidProbeDriver,
    IProtocolRunnerDriver,
    IShakerDriver,
    ITempSettableDriver,
    ITransporterDriver,
)
from cheshire_drivers.lh_request_validation import LH_REQUEST_MODELS


ALL_INTERFACES: list[type] = sorted(
    (
        member
        for member in vars(interfaces_module).values()
        if isinstance(member, type)
        and member.__module__ == interfaces_module.__name__
        and getattr(member, "interfaces", None) is not None
    ),
    key=lambda cls: cls.__name__,
)
"""Every interface the module defines, read off the module itself.

Hand-listing them meant a new interface was covered only if someone remembered
to add it, which is how ILiquidProbeDriver went uncovered until a review caught
it. The `interfaces` ClassVar is what makes a class one of these; BaseDriver has
none, so it is not in the list.
"""


class TestInterfaceClassVars:
    """Every interface advertises its identity ClassVar."""

    @pytest.mark.parametrize("iface", ALL_INTERFACES)
    def test_interface_has_interfaces_classvar(self, iface: type) -> None:
        assert hasattr(iface, "interfaces"), f"{iface.__name__} missing 'interfaces' ClassVar"
        assert isinstance(iface.interfaces, frozenset), (
            f"{iface.__name__}.interfaces must be frozenset[str], got {type(iface.interfaces)}"
        )
        assert len(iface.interfaces) > 0, f"{iface.__name__}.interfaces must be non-empty"
        for name in iface.interfaces:
            assert isinstance(name, str), f"{iface.__name__}.interfaces members must be str"

    def test_lh_provides_state_default_false(self) -> None:
        assert ILiquidHandlerDriver.provides_state is False

    def test_lh_provides_state_is_classvar(self) -> None:
        annotations = ILiquidHandlerDriver.__annotations__
        assert "provides_state" in annotations
        anno = annotations["provides_state"]
        assert get_origin(anno) is ClassVar
        assert get_args(anno) == (bool,)


class TestNoOrphanInterfaces:
    """Every interface keeps a non-empty contract through MRO."""

    @pytest.mark.parametrize("iface", ALL_INTERFACES)
    def test_abstract_set_is_non_empty(self, iface: type) -> None:
        assert len(iface.__abstractmethods__) > 0, (
            f"{iface.__name__} has no abstract methods (lost contract through MRO?)"
        )


class TestConcreteDriverInterfacesAdvertisement:
    """Concrete drivers inherit `interfaces` ClassVar through MRO."""

    @pytest.mark.parametrize(
        "cls,expected_iface_name",
        [
            (sims.SimShakerDriver, "IShaker"),
            (sims.SimSealerDriver, "ISealer"),
            (sims.SimCentrifugeDriver, "ICentrifuge"),
            (sims.SimReaderDriver, "IReader"),
            (sims.SimDelidderDriver, "IDelidder"),
            (sims.SimTransporterDriver, "ITransporter"),
            (sims.SimLiquidHandlerDriver, "ILiquidHandler"),
            (plr_pkg.PLRTransporterBackendWrapper, "ITransporter"),
            (plr_wrappers.PLRSealerBackendWrapper, "ISealer"),
            (plr_wrappers.PLRShakerBackendWrapper, "IShaker"),
            (plr_wrappers.PLRCentrifugeBackendWrapper, "ICentrifuge"),
            (plr_wrappers.PLRLiquidHandlerWrapper, "ILiquidHandler"),
        ],
    )
    def test_concrete_driver_advertises_expected_interface(
        self, cls: type, expected_iface_name: str
    ) -> None:
        assert hasattr(cls, "interfaces")
        assert expected_iface_name in cls.interfaces, (
            f"{cls.__name__}.interfaces ({cls.interfaces}) does not include "
            f"{expected_iface_name!r}"
        )

    def test_plr_lh_provides_state_true(self) -> None:
        assert plr_wrappers.PLRLiquidHandlerWrapper.provides_state is True


class TestDeriveCapabilities:
    """Auto-derive returns vendor extras: public methods + properties on the
    concrete class minus members on its declared abstract interfaces."""

    def test_plr_centrifuge_extras(self) -> None:
        # PLRCentrifugeBackendWrapper.stop and .set_acceleration are concrete
        # extras NOT declared on ICentrifugeDriver.
        caps = derive_capabilities(plr_wrappers.PLRCentrifugeBackendWrapper)
        assert caps == frozenset({"stop", "set_acceleration"})

    def test_plr_liquid_handler_no_extras(self) -> None:
        # All methods are on ILiquidHandlerDriver / BaseDriver.
        caps = derive_capabilities(plr_wrappers.PLRLiquidHandlerWrapper)
        assert caps == frozenset()

    def test_sim_shaker_lifecycle_is_contract_not_extras(self) -> None:
        """The whole lifecycle surface is contract, so none of it is an extra.

        The sim shaker used to contribute connect/disconnect/is_connected from
        its mixins, which made them extras. They are on the contract now, so the
        extras set is empty and the same names resolve through the interface.
        That is the stronger guarantee: the gate admits a contract member on
        EVERY device, whereas an extra exists only where some concrete class
        happened to define it.
        """
        caps = derive_capabilities(sims.SimShakerDriver)
        assert not ({"connect", "disconnect", "is_connected"} & set(caps))
        # IShakerDriver-declared methods must NOT be in extras either.
        assert "shake" not in caps
        assert "lock_plate" not in caps

        assert {"connect", "disconnect", "is_connected"} <= interface_member_names(IShakerDriver)
        # is_connected is a @property: engine-read state, never a wire command.
        commands = interface_command_names(IShakerDriver)
        assert {"connect", "disconnect"} <= commands
        assert "is_connected" not in commands

    def test_venus_handoff_callbacks_are_reachable_over_the_wire(self) -> None:
        # The handoff callbacks are wire-exposed so operators can invoke them
        # mid-debug. They are on the protocol-runner contract, so not extras.
        caps = derive_capabilities(venus_driver.VenusProtocolDriver)
        assert "execute" in caps, f"Venus is missing expected extra 'execute': caps={sorted(caps)}"
        handoff = {"prepare_for_pick", "prepare_for_place", "notify_picked", "notify_placed"}
        assert handoff <= interface_command_names(IProtocolRunnerDriver)
        assert not (handoff & set(caps))
        # IProtocolRunner-declared method must NOT be in extras.
        assert "run_protocol" not in caps
        # Lifecycle moved onto the contract, so it leaves the extras and has to
        # be reachable through the interface instead.
        assert not ({"connect", "disconnect"} & set(caps))
        assert {"connect", "disconnect"} <= interface_command_names(IProtocolRunnerDriver)

    def test_underscore_prefixed_members_filtered(self) -> None:
        # _sim is a private abstract on the Sim mixin — must not leak into
        # auto-derived capabilities even though it is declared abstract.
        caps = derive_capabilities(sims.SimShakerDriver)
        for name in caps:
            assert not name.startswith("_"), f"underscore-prefixed leaked: {name!r}"

    def test_sync_vendor_extras_are_picked_up(self) -> None:
        """Sync public methods on a concrete driver must surface as vendor extras.

        Vendor drivers expose synchronous troubleshooting helpers (diagnostic
        readers, status snapshots) that operators must be able to invoke.
        derive_capabilities counts public callables regardless of sync/async.
        """

        class _FakeShakerWithSyncExtra(sims.SimShakerDriver):
            def diagnostic_snapshot(self) -> dict:
                return {}

        caps = derive_capabilities(_FakeShakerWithSyncExtra)
        assert "diagnostic_snapshot" in caps


class TestDescribeMethod:
    """describe_method produces structured introspection metadata."""

    def test_pydantic_request_method_uses_json_schema(self) -> None:
        from cheshire_drivers.liquid_handler_models import AspirateRequest
        mi = describe_method(ILiquidHandlerDriver, "aspirate")
        assert mi.kind == "method"
        assert "request" in mi.params
        # JSON Schema for AspirateRequest carries Pydantic field definitions
        schema = mi.params["request"]
        assert schema == AspirateRequest.model_json_schema()
        assert mi.returns == "LabwareStateResponse"

    def test_plain_kwargs_method_signature_derived(self) -> None:
        # ITempSettableDriver.set_temperature(temperature: float) is the
        # canonical plain-kwargs example: it was left out of the typed-request
        # uplift the shaker, sealer and centrifuge categories got.
        mi = describe_method(ITempSettableDriver, "set_temperature")
        assert mi.kind == "method"
        assert set(mi.params.keys()) == {"temperature"}
        assert mi.params["temperature"]["type"] == "float"
        assert mi.params["temperature"]["required"] is True

    def test_property_kind_marked(self) -> None:
        mi = describe_method(BaseDriver, "is_initialized")
        assert mi.kind == "property"
        assert mi.params == {}
        assert mi.returns == "bool"

    def test_typing_constructs_render_with_args(self) -> None:
        # Optional[List[int]] must not collapse to bare "Optional" — would
        # destroy operator usability of the introspection endpoint.
        # Witnessed via a test-local class so the assertion is independent of
        # any production interface's evolving signatures.
        from typing import List, Optional

        class _Witness:
            async def witness(self, items: Optional[List[int]] = None) -> None: ...

        mi = describe_method(_Witness, "witness")
        rendered = mi.params["items"]["type"]
        assert rendered is not None
        assert "Optional" in rendered or "None" in rendered
        assert "List" in rendered or "list" in rendered

    def test_docstring_propagated_when_present(self) -> None:
        mi = describe_method(IShakerDriver, "shake")
        assert mi.docstring is not None
        assert "Shake" in mi.docstring

    def test_duration_populated_for_known_command(self) -> None:
        mi = describe_method(IShakerDriver, "shake")
        assert mi.duration is not None
        assert isinstance(mi.duration, CommandTiming)
        assert mi.duration.typical_seconds == 60.0
        assert mi.duration.max_seconds == 7200.0

    def test_duration_inherited_from_base_driver(self) -> None:
        # IShakerDriver inherits BaseDriver's initialize timing through MRO merge.
        mi = describe_method(IShakerDriver, "initialize")
        assert mi.duration is not None
        assert mi.duration.typical_seconds == 30.0
        assert mi.duration.max_seconds == 120.0

    def test_duration_none_for_property(self) -> None:
        # is_initialized is a property; no timing entry expected.
        mi = describe_method(BaseDriver, "is_initialized")
        assert mi.duration is None

    def test_duration_none_for_unregistered_method(self) -> None:
        class _Witness:
            async def some_ad_hoc_method(self) -> None: ...

        mi = describe_method(_Witness, "some_ad_hoc_method")
        assert mi.duration is None

    def test_duration_picks_concrete_override(self) -> None:
        class _FastSimShake(sims.SimShakerDriver):
            @command_timing(typical=1.0, max=2.0)
            async def shake(self, request: ShakeRequest) -> None:
                return await super().shake(request)

        mi = describe_method(_FastSimShake, "shake")
        assert mi.duration is not None
        assert mi.duration.max_seconds == 2.0


class TestDescribeDriver:
    """describe_driver yields the full callable surface (interface + extras)."""

    def test_plr_centrifuge_includes_extras_with_descriptions(self) -> None:
        # Construct a wrapper without invoking the backend (we only introspect
        # class-level metadata; no method bodies executed).
        wrapper = plr_wrappers.PLRCentrifugeBackendWrapper.__new__(
            plr_wrappers.PLRCentrifugeBackendWrapper
        )
        out = describe_driver(wrapper)
        # Interface contract: centrifuge plus inherited BaseDriver members
        for required in {"centrifuge", "open", "close", "initialize", "is_initialized"}:
            assert required in out, f"missing interface member {required!r}"
        # Auto-derived extras
        for extra in {"stop", "set_acceleration"}:
            assert extra in out, f"missing vendor extra {extra!r}"
        # MethodInfo invariants
        for name, mi in out.items():
            assert isinstance(mi, MethodInfo)
            assert mi.kind in {"method", "property"}


# Internal plumbing: interface-declared but not @external, so describe_driver
# must hide them from the operator/AI introspection projection.
_HIDDEN_LH_PLUMBING = {
    "add_deck_labware", "remove_deck_labware",
    "reconcile_deck_occupancy", "reset_deck_labware",
}
_HIDDEN_TRANSPORTER_PLUMBING = {
    "seed_position", "ensure_seeded", "unseed_position", "reset_world",
}


class TestExternalIntrospectionProjection:
    """describe_driver shows only @external-marked interface methods, hides
    unmarked interface plumbing, and keeps concrete-only vendor extras
    (Decision A two-case projection)."""

    def test_lh_plumbing_hidden_operator_methods_shown(self) -> None:
        wrapper = plr_wrappers.PLRLiquidHandlerWrapper.__new__(
            plr_wrappers.PLRLiquidHandlerWrapper
        )
        out = describe_driver(wrapper)
        for hidden in _HIDDEN_LH_PLUMBING:
            assert hidden not in out, f"internal plumbing {hidden!r} leaked into introspection"
        for shown in {
            "aspirate", "dispense", "pick_up_tips", "drop_tips", "discard_tips",
            "move_plate", "mix", "aspirate96", "dispense96", "configure_deck",
            "get_deck_state", "initialize", "open", "close", "is_initialized",
            "reconcile_hardware_state", "discard_stranded_tips",
        }:
            assert shown in out, f"operator method {shown!r} missing from introspection"

    def test_transporter_world_sync_hidden_operator_verbs_shown(self) -> None:
        driver = sims.SimTransporterDriver("robot1")
        out = describe_driver(driver)
        for hidden in _HIDDEN_TRANSPORTER_PLUMBING:
            assert hidden not in out, f"internal plumbing {hidden!r} leaked into introspection"
        for shown in {
            "home", "move_to_safe", "pick_at_coords", "place_at_coords",
            "halt", "open_gripper", "close_gripper", "name", "is_initialized",
        }:
            assert shown in out, f"operator verb {shown!r} missing from introspection"

    def test_concrete_vendor_extra_still_shown(self) -> None:
        class _FakeLHWithVendorExtra(sims.SimLiquidHandlerDriver):
            def vendor_diag(self) -> dict:
                return {}

        driver = _FakeLHWithVendorExtra("lh1")
        out = describe_driver(driver)
        assert "vendor_diag" in out, "concrete-only vendor extra must stay auto-derived"
        for hidden in _HIDDEN_LH_PLUMBING:
            assert hidden not in out

    def test_external_marker_resolves_through_concrete_override(self) -> None:
        # A concrete override does not re-carry the marker; MRO resolution must
        # still find it on the interface declaration.
        assert _is_external_interface_member(plr_wrappers.PLRLiquidHandlerWrapper, "aspirate")
        assert not _is_external_interface_member(
            plr_wrappers.PLRLiquidHandlerWrapper, "add_deck_labware"
        )

    def test_external_composes_with_abstractmethod(self) -> None:
        # __abstractmethods__ is computed from each member's __isabstractmethod__,
        # so membership proves @external did not strip abstractness.
        assert "aspirate" in ILiquidHandlerDriver.__abstractmethods__
        assert "add_deck_labware" in ILiquidHandlerDriver.__abstractmethods__


def _interface_declaring(command: str) -> type | None:
    """The interface class that makes `command` abstract, or None.

    Not every liquid-handler wire command comes from ILiquidHandlerDriver. A
    capability a liquid handler shares with other device kinds lives on its own
    interface -- `home` is on IHomeableDriver, which an arm carries too -- so
    the registry is checked against whichever interface declares each command.
    """
    for name in dir(interfaces_module):
        cls = getattr(interfaces_module, name)
        if not inspect.isclass(cls) or not name.startswith("I"):
            continue
        if command in getattr(cls, "__abstractmethods__", frozenset()):
            return cls
    return None


class TestLhRequestModelsAlignWithInterface:
    """The lh_request_validation registry must match the abstract method
    signatures of whichever interface declares each command."""

    def test_every_lh_request_model_command_is_abstract_on_an_interface(self) -> None:
        for command in LH_REQUEST_MODELS:
            assert _interface_declaring(command) is not None, (
                f"LH_REQUEST_MODELS lists {command!r} but no interface declares "
                f"it abstract, so nothing pins its wire shape"
            )

    def test_lh_request_model_kwarg_name_matches_method_signature(self) -> None:
        for command, (kwarg_name, model_cls) in LH_REQUEST_MODELS.items():
            declaring = _interface_declaring(command)
            assert declaring is not None, f"no interface declares {command!r}"
            sig = inspect.signature(getattr(declaring, command))
            params = [name for name in sig.parameters if name != "self"]
            assert kwarg_name in params, (
                f"LH_REQUEST_MODELS[{command!r}] declares kwarg name {kwarg_name!r} "
                f"but {declaring.__name__}.{command} signature has params {params}"
            )

    def test_lh_request_model_type_matches_method_annotation(self) -> None:
        for command, (kwarg_name, model_cls) in LH_REQUEST_MODELS.items():
            declaring = _interface_declaring(command)
            assert declaring is not None, f"no interface declares {command!r}"
            sig = inspect.signature(getattr(declaring, command))
            param = sig.parameters.get(kwarg_name)
            assert param is not None
            assert param.annotation is model_cls, (
                f"LH_REQUEST_MODELS[{command!r}] = {model_cls.__name__} but "
                f"{declaring.__name__}.{command}({kwarg_name}: ...) is annotated "
                f"as {param.annotation}"
            )

    def test_every_atomic_lh_abstract_method_has_an_entry(self) -> None:
        non_atomic = {"open", "close", "initialize", "is_initialized"}
        atomic_abstracts = ILiquidHandlerDriver.__abstractmethods__ - non_atomic
        registered = set(LH_REQUEST_MODELS)
        missing = atomic_abstracts - registered
        assert not missing, (
            f"LH atomic methods on ILiquidHandlerDriver missing from "
            f"lh_request_validation registry: {sorted(missing)}"
        )
