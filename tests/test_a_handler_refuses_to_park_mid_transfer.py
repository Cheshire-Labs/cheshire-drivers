"""A handler asked to step aside says no while it is still holding tips.

Parking travels, and the gantry carries every mount at once, so a park with tips
on drags whatever liquid is aboard across the deck. The transfer in progress wins
and the arm waits for it, which is why the refusal is the handler's to make: it
is the only thing that knows what it is holding.
"""

from typing import List

import pytest
from cheshire_drivers.gantry_models import (
    GantryBusyError,
    GantryParkPosition,
    ParkGantryRequest,
)
from cheshire_drivers.gripper_models import MoveGripperToRequest
from cheshire_drivers.liquid_handler_models import DiscardTipsRequest
from cheshire_drivers.plr.opentrons_flex import FlexLiquidHandlerDriver
from cheshire_drivers.sims import (
    SimLiquidHandlerDriver,
    SimLiquidHandlerWithProtocolDriver,
)

from tests.test_opentrons_flex_pipetting import (
    _EIGHT_CHANNEL,
    _SINGLE_CHANNEL,
    _bench,
    _pick_up_column,
    _pick_up_column_on,
)

pytestmark = pytest.mark.asyncio


def _record_retractions(driver: FlexLiquidHandlerDriver) -> List[str]:
    flex = driver._require_flex("test")
    retracted: List[str] = []

    async def _retract(axis: str) -> None:
        retracted.append(axis)

    flex.retract_axis = _retract  # type: ignore[method-assign]
    return retracted


def _record_gripper_moves(driver: FlexLiquidHandlerDriver) -> List[tuple]:
    gripper = driver._require_gripper("test")
    moves: List[tuple] = []

    async def _move_to(x: float, y: float, z: float, speed=None) -> None:
        moves.append((x, y, z))

    gripper.move_to = _move_to  # type: ignore[method-assign]
    return moves


async def test_a_handler_holding_tips_refuses_to_park() -> None:
    driver, _ = await _bench()
    retracted = _record_retractions(driver)
    await _pick_up_column(driver)

    with pytest.raises(GantryBusyError):
        await driver.park_gantry(ParkGantryRequest())

    assert retracted == [], "refused, so nothing should have moved"


async def test_the_refusal_survives_the_tips_coming_off() -> None:
    """The same request succeeds once the transfer is done, which is what makes
    waiting the right response to it rather than failing the move."""
    driver, _ = await _bench()
    await _pick_up_column(driver)
    retracted = _record_retractions(driver)
    with pytest.raises(GantryBusyError):
        await driver.park_gantry(ParkGantryRequest())

    await driver.discard_tips(DiscardTipsRequest())

    await driver.park_gantry(ParkGantryRequest())
    assert retracted == ["leftZ", "y"]


async def test_a_bench_can_name_the_spot_to_park_at() -> None:
    driver, _ = await _bench(gripper=True)
    retracted = _record_retractions(driver)
    moves = _record_gripper_moves(driver)

    await driver.park_gantry(
        ParkGantryRequest(at=GantryParkPosition(x=10.0, y=20.0, z=30.0))
    )

    assert moves == [(10.0, 20.0, 30.0)]
    assert retracted == ["leftZ", "extensionZ"], (
        "a named spot replaces the y traverse, not the lift that has to precede it"
    )


async def test_a_gripper_jog_is_refused_while_holding_tips_too() -> None:
    """A jog moves the same gantry a park does, so it needs the same refusal.
    Without one it is simply the way around asking the handler at all."""
    driver, _ = await _bench(gripper=True)
    moves = _record_gripper_moves(driver)
    await _pick_up_column(driver)

    with pytest.raises(GantryBusyError):
        await driver.move_gripper_to(MoveGripperToRequest(x=10.0, y=20.0, z=30.0))

    assert moves == [], "refused, so nothing should have moved"


async def test_a_named_spot_is_refused_while_holding_tips_too() -> None:
    """The spot rides on this request precisely so it cannot skip the refusal;
    sent as a plain gripper jog it would have moved."""
    driver, _ = await _bench(gripper=True)
    moves = _record_gripper_moves(driver)
    await _pick_up_column(driver)

    with pytest.raises(GantryBusyError):
        await driver.park_gantry(
            ParkGantryRequest(at=GantryParkPosition(x=10.0, y=20.0, z=30.0))
        )

    assert moves == []


@pytest.mark.parametrize(
    "sim_driver", [SimLiquidHandlerDriver, SimLiquidHandlerWithProtocolDriver],
)
async def test_a_sim_handler_can_be_stepped_aside(sim_driver: type) -> None:
    """Otherwise a workflow that steps a handler aside needs hardware to run.

    Both, because the composite is what a plain sim liquid handler resolves to;
    covering only the atomic one leaves the case that actually runs uncovered.
    """
    driver = sim_driver("sim_lh")

    await driver.park_gantry(ParkGantryRequest())

    assert "IGantryParking" in sim_driver.interfaces


async def test_the_refusal_names_every_head_that_is_holding() -> None:
    """An operator reading it has to know which head to clear."""
    driver, _ = await _bench(_EIGHT_CHANNEL, _SINGLE_CHANNEL)
    await _pick_up_column(driver)
    await _pick_up_column_on(driver, [8], ["A2"])

    with pytest.raises(GantryBusyError, match="hold tips"):
        await driver.park_gantry(ParkGantryRequest())
