"""Tests for the per-command logical-duration metadata on driver interfaces.

The decorator pattern means orphan dict-key drift is impossible by
construction: timings are method attributes, set by
``@command_timing(typical=..., max=...)``. The tests guard:

  1. CommandTiming model validation (range, ordering, immutability).
  2. Every driver interface decorates every async abstract method.
  3. The MRO merge in ``collect_command_timings`` so concrete drivers
     inherit interface defaults and can override individual entries by
     re-decorating the method.
"""

import inspect

import pytest
from pydantic import ValidationError

from cheshire_drivers.command_timings import (
    CommandTiming,
    collect_command_timings,
    command_timing,
)
from cheshire_drivers.interfaces import (
    BaseDriver,
    ICentrifugeDriver,
    IDelidderDriver,
    IForceGripperJawDriver,
    IGripperMotionDriver,
    IGripperPositionDriver,
    IGripperRotationDriver,
    ILiquidHandlerDriver,
    ILiquidProbeDriver,
    IPipetteMotionDriver,
    IPlateWasherDriver,
    IProtocolRunnerDriver,
    IReaderDriver,
    ISealerDriver,
    IShakerDriver,
    IStorageDriver,
    ITempGettableDriver,
    ITempSettableDriver,
    ITransporterDriver,
    IWasteDriver,
    IWidthGripperJawDriver,
)
from cheshire_drivers.shaker_models import ShakeRequest
from cheshire_drivers import sims, plr_wrappers


ALL_INTERFACES: list[type] = [
    ILiquidProbeDriver,
    BaseDriver,
    IShakerDriver,
    ISealerDriver,
    ITempSettableDriver,
    ITempGettableDriver,
    IProtocolRunnerDriver,
    ICentrifugeDriver,
    IReaderDriver,
    IDelidderDriver,
    ITransporterDriver,
    IStorageDriver,
    IPlateWasherDriver,
    ILiquidHandlerDriver,
    IWasteDriver,
    IPipetteMotionDriver,
    IGripperMotionDriver,
    IGripperPositionDriver,
    IForceGripperJawDriver,
    IWidthGripperJawDriver,
    IGripperRotationDriver,
]


class TestCommandTimingModel:
    def test_typical_and_max_round_trip(self) -> None:
        ct = CommandTiming(typical_seconds=10.0, max_seconds=60.0)
        assert ct.typical_seconds == 10.0
        assert ct.max_seconds == 60.0

    def test_max_below_typical_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CommandTiming(typical_seconds=60.0, max_seconds=10.0)

    def test_negative_typical_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CommandTiming(typical_seconds=-1.0, max_seconds=10.0)

    def test_negative_max_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CommandTiming(typical_seconds=0.0, max_seconds=-1.0)

    def test_equal_typical_and_max_accepted(self) -> None:
        ct = CommandTiming(typical_seconds=5.0, max_seconds=5.0)
        assert ct.typical_seconds == ct.max_seconds == 5.0

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            CommandTiming(typical_seconds=1.0, max_seconds=2.0, units="seconds")  # type: ignore[call-arg]

    def test_frozen_blocks_mutation(self) -> None:
        ct = CommandTiming(typical_seconds=1.0, max_seconds=2.0)
        with pytest.raises(ValidationError):
            ct.typical_seconds = 99.0  # type: ignore[misc]


class TestCommandTimingDecorator:
    def test_attaches_timing_attribute(self) -> None:
        @command_timing(typical=1.0, max=2.0)
        async def fn() -> None: ...

        attached = getattr(fn, "__command_timing__", None)
        assert isinstance(attached, CommandTiming)
        assert attached.typical_seconds == 1.0
        assert attached.max_seconds == 2.0

    def test_validation_propagates(self) -> None:
        with pytest.raises(ValidationError):
            command_timing(typical=10.0, max=1.0)


class TestInterfacesDecorateEveryAsyncAbstract:
    @pytest.mark.parametrize("iface", ALL_INTERFACES)
    def test_every_async_abstract_has_timing(self, iface: type) -> None:
        """Every async abstract method on the interface (or inherited via the
        MRO) must carry a ``@command_timing`` decorator.

        Properties (sync attribute reads) are exempt; they do not dispatch
        through the timeout-aware command path. Underscore-prefixed privates
        are exempt as well.
        """
        timings = collect_command_timings(iface)
        for name in iface.__abstractmethods__:
            if name.startswith("_"):
                continue
            try:
                member = inspect.getattr_static(iface, name)
            except AttributeError:
                continue
            if isinstance(member, property):
                continue
            assert name in timings, (
                f"{iface.__name__} abstract method {name!r} is missing "
                f"@command_timing(...)"
            )

    @pytest.mark.parametrize("iface", ALL_INTERFACES)
    def test_interface_has_non_empty_timing_set(self, iface: type) -> None:
        """Each interface contributes at least one timed command through its
        MRO. IPlateWasherDriver / IWasteDriver inherit their full contract
        from a single parent; the inherited timings are the whole contract."""
        timings = collect_command_timings(iface)
        assert len(timings) > 0


class TestCollectCommandTimingsMroMerge:
    def test_concrete_inherits_base_and_interface_timings(self) -> None:
        timings = collect_command_timings(sims.SimShakerDriver)
        # BaseDriver-decorated method.
        assert "initialize" in timings
        # IShakerDriver-decorated method.
        assert "shake" in timings
        assert timings["shake"].typical_seconds == 60.0

    def test_concrete_subclass_override_wins(self) -> None:
        class _FastShake(sims.SimShakerDriver):
            @command_timing(typical=1.0, max=2.0)
            async def shake(self, request: ShakeRequest) -> None:
                return await super().shake(request)

        merged = collect_command_timings(_FastShake)
        # Override wins.
        assert merged["shake"].max_seconds == 2.0
        # Other inherited entries preserved.
        assert "initialize" in merged
        assert merged["stop"].max_seconds == 10.0

    def test_subclass_without_redecoration_inherits(self) -> None:
        merged = collect_command_timings(plr_wrappers.PLRShakerBackendWrapper)
        assert "shake" in merged
        assert merged["shake"].max_seconds == 7200.0
        assert "initialize" in merged

    def test_multi_interface_class_merges_both_sources(self) -> None:
        """A concrete driver that implements two interfaces (e.g. an LH that
        also runs vendor protocols) gets the union of both timing tables
        via the MRO walk."""

        class _MultiIface(ILiquidHandlerDriver, IProtocolRunnerDriver):
            pass

        merged = collect_command_timings(_MultiIface)
        assert "aspirate" in merged
        assert "run_protocol" in merged
        assert "initialize" in merged

    def test_underscore_methods_skipped(self) -> None:
        class _DriverWithPrivate:
            @command_timing(typical=1.0, max=2.0)
            async def _private(self) -> None: ...

        merged = collect_command_timings(_DriverWithPrivate)
        assert "_private" not in merged

    def test_properties_skipped(self) -> None:
        merged = collect_command_timings(BaseDriver)
        # is_initialized is an abstract property; never carries a timing.
        assert "is_initialized" not in merged
