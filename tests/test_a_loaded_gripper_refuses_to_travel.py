"""A handler asked to step aside says no while a plate may be in its jaws.

From the bench, 2026-09-01. A gripper move stalled part-way and left the plate
clamped in the jaws. The device went straight back to ready, the next thread
asked the Flex to park, and the gantry traversed with the plate still held. The
park's own busy check looked only at pipette tips, so nothing refused.

One gantry carries the pipette mounts and the gripper together, so what stops a
travel is anything hanging off it, not tips specifically.
"""

from typing import List, Optional

import pytest
from pylabrobot.resources import Resource

from cheshire_drivers.gantry_models import GantryBusyError, ParkGantryRequest
from cheshire_drivers.gripper_models import (
    GripWithForceRequest,
    MoveGripperToRequest,
    ReleaseJawRequest,
)
from cheshire_drivers.liquid_handler_models import (
    GetDeckStateRequest,
    MovePlateRequest,
)
from cheshire_drivers.plr.opentrons_flex import FlexLiquidHandlerDriver

from tests.test_opentrons_flex_pipetting import _EIGHT_CHANNEL, _bench

pytestmark = pytest.mark.asyncio


def _record_retractions(driver: FlexLiquidHandlerDriver) -> List[str]:
    flex = driver._require_flex("test")
    retracted: List[str] = []

    async def _retract(axis: str) -> None:
        retracted.append(axis)

    flex.retract_axis = _retract  # type: ignore[method-assign]
    return retracted


async def _bench_with_gripper() -> FlexLiquidHandlerDriver:
    driver, _ = await _bench(_EIGHT_CHANNEL, gripper=True)
    return driver


def _break_the_gripper(driver: FlexLiquidHandlerDriver) -> None:
    """Raise where an e-stop does: at the point the move reaches the machine."""
    gripper = driver._require_gripper("test")

    async def _stall(
        resource: Resource,
        to_slot: str,
        grip_distance_from_top: Optional[float] = None,
    ) -> None:
        raise RuntimeError("Stall or Collision Detected")

    gripper.move_labware = _stall  # type: ignore[method-assign]


async def _fail_a_gripper_move(driver: FlexLiquidHandlerDriver) -> None:
    _break_the_gripper(driver)
    with pytest.raises(RuntimeError):
        await driver.move_plate(
            MovePlateRequest(plate="plate_1", to_position="B2-slot")
        )


class TestAStalledMoveStopsTheNextTravel:
    async def test_park_refuses_after_a_gripper_move_died_part_way(self) -> None:
        driver = await _bench_with_gripper()
        retracted = _record_retractions(driver)
        await _fail_a_gripper_move(driver)

        with pytest.raises(GantryBusyError) as raised:
            await driver.park_gantry(ParkGantryRequest())

        assert "jaws" in str(raised.value)
        assert retracted == [], "refused, so nothing should have moved"

    async def test_a_gripper_jog_refuses_on_the_same_grounds(self) -> None:
        driver = await _bench_with_gripper()
        retracted = _record_retractions(driver)
        await _fail_a_gripper_move(driver)

        with pytest.raises(GantryBusyError):
            await driver.move_gripper_to(
                MoveGripperToRequest(x=100.0, y=100.0, z=100.0)
            )

        assert retracted == []

    async def test_opening_the_jaws_lets_the_gantry_travel_again(self) -> None:
        driver = await _bench_with_gripper()
        await _fail_a_gripper_move(driver)
        retracted = _record_retractions(driver)

        await driver.release_jaw(ReleaseJawRequest())
        await driver.park_gantry(ParkGantryRequest())

        assert "y" in retracted, "the park should travel once the jaws are open"

    async def test_opening_the_jaws_does_not_say_where_the_plate_went(self) -> None:
        """The plate lands wherever the gripper was, which is not the site the
        deck still names, so the record has to outlive the release."""
        driver = await _bench_with_gripper()
        await _fail_a_gripper_move(driver)

        await driver.release_jaw(ReleaseJawRequest())

        state = await driver.get_deck_state(GetDeckStateRequest())
        assert state.interrupted_move is not None
        assert state.interrupted_move.labware == "plate_1"


class TestClosingTheJawsByHandCountsToo:
    async def test_park_refuses_after_an_operator_grips(self) -> None:
        driver = await _bench_with_gripper()
        retracted = _record_retractions(driver)

        await driver.grip_with_force(GripWithForceRequest(force=15.0))

        with pytest.raises(GantryBusyError):
            await driver.park_gantry(ParkGantryRequest())
        assert retracted == []

    async def test_park_refuses_after_a_grip_dies_part_way(self) -> None:
        """A grip that raised may still have closed on a plate.

        The jaw actuates during the call, so a stall or an e-stop part-way
        through leaves it clamped with nothing reporting that.
        """
        driver = await _bench_with_gripper()
        retracted = _record_retractions(driver)
        gripper = driver._require_gripper("test")

        async def _stall(force: float = 15.0) -> None:
            raise RuntimeError("Stall or Collision Detected")

        gripper.grip = _stall  # type: ignore[method-assign]

        with pytest.raises(RuntimeError):
            await driver.grip_with_force(GripWithForceRequest(force=15.0))

        with pytest.raises(GantryBusyError):
            await driver.park_gantry(ParkGantryRequest())
        assert retracted == []

    async def test_releasing_lets_it_travel(self) -> None:
        driver = await _bench_with_gripper()
        await driver.grip_with_force(GripWithForceRequest(force=15.0))
        retracted = _record_retractions(driver)

        await driver.release_jaw(ReleaseJawRequest())
        await driver.park_gantry(ParkGantryRequest())

        assert "y" in retracted


class TestNothingHeldStillParks:
    async def test_an_idle_handler_parks(self) -> None:
        driver = await _bench_with_gripper()
        retracted = _record_retractions(driver)

        await driver.park_gantry(ParkGantryRequest())

        assert "y" in retracted

    async def test_a_move_that_finishes_leaves_the_jaws_clear(self) -> None:
        driver = await _bench_with_gripper()
        await driver.move_plate(
            MovePlateRequest(plate="plate_1", to_position="B2-slot")
        )
        retracted = _record_retractions(driver)

        await driver.park_gantry(ParkGantryRequest())

        assert "y" in retracted
