"""Tests for ``cheshire_drivers.faults`` pre-armed fault injection.

Exercises raise/hang/recovery/selective-on_calls semantics on
``SimShakerDriver`` and validates boot-time rejection of typo'd method
names + unresolvable error_type strings.
"""

import asyncio

import pytest

from cheshire_drivers import (
    FaultRegistry,
    HangFault,
    RaiseFault,
    SimShakerDriver,
    SimTransporterDriver,
    SleepSim,
    resolve_exception_class,
)
from cheshire_drivers.shaker_models import ShakeRequest
from cheshire_drivers.sims import SimTransporterValidationError


def _fast_sim() -> SleepSim:
    """Sleep-zero strategy so tests don't burn wall-clock."""
    return SleepSim(sim_time=0.0)


def test_resolve_exception_class_builtins() -> None:
    assert resolve_exception_class("RuntimeError") is RuntimeError
    assert resolve_exception_class("ValueError") is ValueError
    assert resolve_exception_class("TimeoutError") is TimeoutError


def test_resolve_exception_class_cheshire_drivers() -> None:
    assert resolve_exception_class("SimTransporterValidationError") is SimTransporterValidationError


def test_resolve_exception_class_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="does not resolve"):
        resolve_exception_class("NotARealError")


def test_resolve_exception_class_rejects_non_exception() -> None:
    # `dict` is a builtin but not an exception subclass -> reject
    with pytest.raises(ValueError, match="does not resolve"):
        resolve_exception_class("dict")


def test_unknown_method_rejected_at_boot() -> None:
    with pytest.raises(ValueError, match="not a faultable method"):
        SimShakerDriver(
            "s", sim_strategy=_fast_sim(),
            faults=[RaiseFault(method="shak", error_type="RuntimeError")],
        )


def test_invalid_error_type_rejected_at_boot() -> None:
    with pytest.raises(ValueError, match="does not resolve"):
        SimShakerDriver(
            "s", sim_strategy=_fast_sim(),
            faults=[RaiseFault(method="shake", error_type="NotARealError")],
        )


@pytest.mark.asyncio
async def test_raise_fault_fires_on_first_call() -> None:
    d = SimShakerDriver(
        "s", sim_strategy=_fast_sim(),
        faults=[RaiseFault(method="shake", error_type="RuntimeError", message="boom")],
    )
    with pytest.raises(RuntimeError, match="boom"):
        await d.shake(ShakeRequest(speed=500, duration=0.1))


@pytest.mark.asyncio
async def test_raise_fault_recoverable_after_first_call() -> None:
    """on_calls=[1] means call 1 raises; call 2 onwards is clean."""
    d = SimShakerDriver(
        "s", sim_strategy=_fast_sim(),
        faults=[RaiseFault(method="shake", error_type="ValueError", on_calls=[1])],
    )
    with pytest.raises(ValueError):
        await d.shake(ShakeRequest(speed=500, duration=0.1))
    # call 2 must succeed - sim state is not corrupted by a fault
    await d.shake(ShakeRequest(speed=500, duration=0.1))
    await d.shake(ShakeRequest(speed=500, duration=0.1))


@pytest.mark.asyncio
async def test_raise_fault_selective_on_calls() -> None:
    d = SimShakerDriver(
        "s", sim_strategy=_fast_sim(),
        faults=[RaiseFault(method="shake", error_type="RuntimeError", on_calls=[2, 4])],
    )
    outcomes: list[str] = []
    for _ in range(5):
        try:
            await d.shake(ShakeRequest(speed=500, duration=0.1))
            outcomes.append("ok")
        except RuntimeError:
            outcomes.append("raised")
    assert outcomes == ["ok", "raised", "ok", "raised", "ok"]


@pytest.mark.asyncio
async def test_hang_fault_delays_then_continues(recorded_sleeps: list[float]) -> None:
    d = SimShakerDriver(
        "s", sim_strategy=_fast_sim(),
        faults=[HangFault(method="shake", extra_seconds=0.3, on_calls=[1])],
    )
    await d.shake(ShakeRequest(speed=500, duration=0.1))
    assert pytest.approx(0.3) in recorded_sleeps  # the hang fired on call 1

    recorded_sleeps.clear()
    await d.shake(ShakeRequest(speed=500, duration=0.1))
    assert pytest.approx(0.3) not in recorded_sleeps  # call 2 not hung


@pytest.mark.asyncio
async def test_hang_then_raise_same_call(recorded_sleeps: list[float]) -> None:
    """Hang runs first, then raise - the same call incurs the hang before raising."""
    d = SimShakerDriver(
        "s", sim_strategy=_fast_sim(),
        faults=[
            HangFault(method="shake", extra_seconds=0.2, on_calls=[1]),
            RaiseFault(method="shake", error_type="RuntimeError", on_calls=[1]),
        ],
    )
    with pytest.raises(RuntimeError):
        await d.shake(ShakeRequest(speed=500, duration=0.1))
    assert pytest.approx(0.2) in recorded_sleeps  # hang happened before the raise


@pytest.mark.asyncio
async def test_no_faults_means_no_overhead() -> None:
    """Driver constructed without faults - registry has nothing to fire."""
    d = SimShakerDriver("s", sim_strategy=_fast_sim())
    for _ in range(3):
        await d.shake(ShakeRequest(speed=500, duration=0.1))


def test_fault_registry_isolation_between_drivers() -> None:
    """Two drivers with the same fault don't share counters."""
    d1 = SimShakerDriver(
        "s1", sim_strategy=_fast_sim(),
        faults=[RaiseFault(method="shake", error_type="RuntimeError", on_calls=[1])],
    )
    d2 = SimShakerDriver(
        "s2", sim_strategy=_fast_sim(),
        faults=[RaiseFault(method="shake", error_type="RuntimeError", on_calls=[1])],
    )

    async def _exhaust(d: SimShakerDriver) -> None:
        with pytest.raises(RuntimeError):
            await d.shake(ShakeRequest(speed=500, duration=0.1))

    asyncio.run(_exhaust(d1))
    # d2's counter is still 0; its fault should still fire on its first call
    asyncio.run(_exhaust(d2))


def test_registry_validates_error_type_on_construction() -> None:
    """A bad error_type baked into FaultRegistry directly is rejected."""
    with pytest.raises(ValueError, match="does not resolve"):
        FaultRegistry([RaiseFault(method="shake", error_type="NotARealError")])


@pytest.mark.asyncio
async def test_transporter_fault_recoverable() -> None:
    """SimTransporterDriver's separate __init__ also wires faults correctly."""
    t = SimTransporterDriver(
        "t", sim_strategy=_fast_sim(),
        faults=[RaiseFault(method="home", error_type="RuntimeError", on_calls=[1])],
    )
    from cheshire_drivers.homing_models import HomeRequest
    with pytest.raises(RuntimeError):
        await t.home(HomeRequest())
    # Recovers on call 2
    await t.home(HomeRequest())


def test_raise_fault_forbids_unknown_kwargs() -> None:
    """A typo in a FaultSpec kwarg surfaces at construction, not later.

    Without ``extra="forbid"``, ``metohd="shake"`` would silently
    construct with ``method=""`` and the failure would only surface
    when the registry rejects it as "not a faultable method" -- a
    confusing error pointing at the wrong root cause.
    """
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as exc_info:
        RaiseFault(metohd="shake", error_type="RuntimeError")  # type: ignore[call-arg]
    # The error mentions the unknown field by name so the typo is obvious.
    assert "metohd" in str(exc_info.value).lower()


def test_hang_fault_forbids_unknown_kwargs() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        HangFault(method="shake", extra_secnds=1.0)  # type: ignore[call-arg]


def test_empty_subclass_does_not_shadow_parent_faultable_methods() -> None:
    """``WasteSimMixin(StorageSimMixin): pass`` must keep StorageSimMixin's
    methods reachable via ``_collect_faultable_methods``. Setting an empty
    frozenset on the subclass would shadow the parent's set; the MRO walk
    would rescue correctness but the shadowed attribute is misleading.
    """
    from cheshire_drivers.sims import (
        StorageSimMixin,
        WasteSimMixin,
        _collect_faultable_methods,
    )
    storage_methods = _collect_faultable_methods(StorageSimMixin)
    waste_methods = _collect_faultable_methods(WasteSimMixin)
    # Waste inherits Storage; the union must hold.
    assert storage_methods.issubset(waste_methods)
    # And the subclass should not have set an own _faultable_methods
    # attribute at all (vs. setting it to frozenset() which would shadow).
    assert "_faultable_methods" not in WasteSimMixin.__dict__
