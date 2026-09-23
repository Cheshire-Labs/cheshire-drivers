import asyncio
from unittest.mock import patch

import pytest

from cheshire_drivers.move_parameters import SEED_MOVE_PARAMETERS
from cheshire_drivers.sims import HumanSim
from cheshire_drivers.human_transporter_driver import HumanTransporterDriver
from cheshire_drivers.teachpoints import CartesianCoordinates, Teachpoint
from cheshire_drivers.transporter_models import (
    PickAtCoordsRequest,
    PlaceAtCoordsRequest,
)


def _tp(name: str) -> Teachpoint:
    return Teachpoint(
        name,
        CartesianCoordinates(x=100.0, y=0.0, z=50.0, yaw=180.0, pitch=90.0, roll=0.0),
        orientation="right",
        access_type="vertical",
    )


class TestHumanSimAsync:
    @pytest.mark.asyncio
    async def test_sim_does_not_block_event_loop(self) -> None:
        """HumanSim.sim() must yield the event loop while waiting for input."""
        sim = HumanSim()
        concurrent_ran = False

        async def concurrent_task() -> None:
            nonlocal concurrent_ran
            concurrent_ran = True

        with patch("cheshire_drivers.sims.input", return_value=""):
            # Schedule both tasks; if sim blocks, concurrent_task never runs
            await asyncio.gather(sim.sim("test"), concurrent_task())

        assert concurrent_ran


class TestHumanTransporterDriver:
    @pytest.mark.asyncio
    async def test_pick_prompt_format(self) -> None:
        """pick_at_coords() should produce an imperative PICK UP instruction
        keyed off the resolved teachpoint name."""
        prompts: list[str] = []

        async def capture(prompt: str) -> None:
            prompts.append(prompt)

        driver = HumanTransporterDriver("test_human")
        driver._sim_strategy.sim = capture  # type: ignore[assignment]

        await driver.pick_at_coords(
            PickAtCoordsRequest(teachpoint=_tp("pad_1"), labware_type="Nunc_96", handling=SEED_MOVE_PARAMETERS)
        )
        assert len(prompts) == 1
        assert "PICK UP" in prompts[0]
        assert "pad_1" in prompts[0]
        assert "Nunc_96" in prompts[0]

    @pytest.mark.asyncio
    async def test_place_prompt_format(self) -> None:
        """place_at_coords() should produce an imperative PLACE instruction
        keyed off the resolved teachpoint name."""
        prompts: list[str] = []

        async def capture(prompt: str) -> None:
            prompts.append(prompt)

        driver = HumanTransporterDriver("test_human")
        driver._sim_strategy.sim = capture  # type: ignore[assignment]

        await driver.place_at_coords(
            PlaceAtCoordsRequest(teachpoint=_tp("shaker_1"), labware_type="Nunc_96", handling=SEED_MOVE_PARAMETERS)
        )
        assert len(prompts) == 1
        assert "PLACE" in prompts[0]
        assert "shaker_1" in prompts[0]
        assert "Nunc_96" in prompts[0]

    def test_default_strategy_is_human_sim(self) -> None:
        """Default sim strategy should be HumanSim, not SleepSim."""
        driver = HumanTransporterDriver("test_human")
        assert isinstance(driver._sim_strategy, HumanSim)
