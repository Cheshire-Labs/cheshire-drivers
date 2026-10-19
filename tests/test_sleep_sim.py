"""SleepSim stands in for a driver call with one flat sleep; the engine paces the step."""

import pytest

from cheshire_drivers.sims import SleepSim


@pytest.mark.asyncio
async def test_every_call_sleeps_the_configured_time(recorded_sleeps: list[float]) -> None:
    sim = SleepSim(sim_time=0.7)
    await sim.sim("shake")
    await sim.sim("seal")
    assert recorded_sleeps == pytest.approx([0.7, 0.7])


@pytest.mark.asyncio
async def test_the_default_is_a_fifth_of_a_second(recorded_sleeps: list[float]) -> None:
    await SleepSim().sim("shake")
    assert recorded_sleeps == pytest.approx([0.2])
