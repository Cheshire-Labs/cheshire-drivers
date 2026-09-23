"""Tests for SimStrategy duration handling.

The 2026-05-03 refactor added typed `duration` to `SimStrategy.sim` plus a
`scale_factor` knob on `SleepSim`. Defaults preserve the historical
fast-test behavior; lab simulators bump `scale_factor` to honor caller
durations.
"""

import pytest

from cheshire_drivers import SleepSim
from cheshire_drivers.shaker_models import ShakeRequest
from cheshire_drivers.sims import SimShakerDriver


@pytest.mark.asyncio
async def test_sleep_sim_default_ignores_duration(recorded_sleeps: list[float]) -> None:
    """Default SleepSim (sim_time=0.2, scale_factor=0.0) ignores duration.

    A 7200-second duration must NOT extend the wait; it stays the flat
    sim_time, not 7200s scaled.
    """
    await SleepSim().sim("shake", duration=7200.0)
    assert recorded_sleeps == [pytest.approx(0.2)]


@pytest.mark.asyncio
async def test_sleep_sim_scale_factor_honors_duration(recorded_sleeps: list[float]) -> None:
    """scale_factor>0 with a duration scales the wait: 100s * 0.01 -> 1.0s.

    This is the accelerated-simulation path (real time would be scale=1.0).
    """
    await SleepSim(sim_time=0.0, scale_factor=0.01).sim("shake", duration=100.0)
    assert recorded_sleeps == [pytest.approx(1.0)]


@pytest.mark.asyncio
async def test_sleep_sim_no_duration_falls_back_to_sim_time(recorded_sleeps: list[float]) -> None:
    """duration=None waits sim_time regardless of scale_factor.

    All non-shake/seal/centrifuge call sites pass `duration=None`.
    """
    await SleepSim(sim_time=0.05, scale_factor=1.0).sim("initialize", duration=None)
    assert recorded_sleeps == [pytest.approx(0.05)]


@pytest.mark.asyncio
async def test_sim_shaker_driver_passes_duration_through_strategy() -> None:
    """SimShakerDriver.shake forwards `request.duration` to the strategy.

    End-to-end check: the pre-refactor code formatted duration into a log
    message but never let the strategy act on it. Now lab-sim configurations
    of SleepSim see the typed duration on every shake call.
    """
    captured: list[float | None] = []

    class CapturingStrategy(SleepSim):
        async def sim(self, prompt: str, duration: float | None = None) -> None:
            captured.append(duration)
            # Don't actually sleep; just capture.

    driver = SimShakerDriver(name="shaker_1", sim_strategy=CapturingStrategy())
    await driver.shake(ShakeRequest(speed=500, duration=10.0))
    assert captured == [10.0]


@pytest.mark.asyncio
async def test_sleep_sim_negative_or_zero_duration_falls_back_to_sim_time(recorded_sleeps: list[float]) -> None:
    """A zero duration must not scale the wait; it falls back to sim_time."""
    await SleepSim(sim_time=0.05, scale_factor=1.0).sim("noop", duration=0.0)
    assert recorded_sleeps == [pytest.approx(0.05)]
