"""The Flex moves clear of its own deck when an arm needs the space.

A gantry stays wherever its last operation left it. On the bench a PF400 came in
to pick from a staging slot the Flex had just placed into, and met the gripper
still hanging over it.

Parking is ``retractAxis``, not a homing routine: each axis travels to its own
home position, so nothing is re-referenced and mounted tips do not refuse it.

One gantry carries both pipette mounts and the gripper, so every populated mount
has to be up before it traverses. Measured on the bench 2026-08-26: a park moved
260 mm in y with the pipette z unchanged at 100 mm, which does not clear a 99 mm
tip rack, let alone one with a tip on.
"""

from typing import List, Tuple

import pytest
from cheshire_drivers.gantry_models import GantryParkPosition, ParkGantryRequest
from cheshire_drivers.gripper_models import MoveGripperToRequest
from cheshire_drivers.plr.opentrons_flex import FlexLiquidHandlerDriver

from tests.test_opentrons_flex_pipetting import (
    _EIGHT_CHANNEL,
    _NINETY_SIX,
    _SINGLE_CHANNEL,
    _bench,
)

pytestmark = pytest.mark.asyncio


def _record_motion(driver: FlexLiquidHandlerDriver) -> List[str]:
    """Every axis retraction and gripper move, in the order they are issued."""
    flex = driver._require_flex("test")
    log: List[str] = []

    async def _retract(axis: str) -> None:
        log.append(axis)

    flex.retract_axis = _retract  # type: ignore[method-assign]
    if flex.gripper is not None:

        async def _move_to(x: float, y: float, z: float, speed=None) -> None:
            log.append("gripper.move_to")

        flex.gripper.move_to = _move_to  # type: ignore[method-assign]
    return log


async def test_parking_lifts_every_mount_before_it_travels() -> None:
    """Order is the whole point: travelling first drags whichever mount is down
    across everything standing between here and the back of the deck."""
    driver, _ = await _bench(_EIGHT_CHANNEL, _SINGLE_CHANNEL, gripper=True)
    log = _record_motion(driver)

    await driver.park_gantry(ParkGantryRequest())

    assert log == ["leftZ", "rightZ", "extensionZ", "y"]


async def test_the_traverse_is_the_last_thing_a_park_does() -> None:
    """Whatever else a park retracts, y moves after all of it."""
    driver, _ = await _bench(_EIGHT_CHANNEL, _SINGLE_CHANNEL, gripper=True)
    log = _record_motion(driver)

    await driver.park_gantry(ParkGantryRequest())

    assert log[-1] == "y"
    assert "y" not in log[:-1]


async def test_an_empty_mount_is_not_retracted() -> None:
    """The robot rejects an axis it has no instrument for, and a bare carriage
    is not what collides."""
    driver, _ = await _bench(_EIGHT_CHANNEL)
    log = _record_motion(driver)

    await driver.park_gantry(ParkGantryRequest())

    assert log == ["leftZ", "y"]


async def test_a_ninety_six_head_is_retracted_like_any_other_mount() -> None:
    """The 96-channel head hangs off the same gantry and is the largest thing on
    it. It reports one mount, so one z covers it."""
    driver, _ = await _bench(_NINETY_SIX)
    log = _record_motion(driver)

    await driver.park_gantry(ParkGantryRequest())

    assert log == ["leftZ", "y"]


async def test_a_bench_park_spot_still_lifts_first() -> None:
    """A named spot says where the gripper ends up, not that it may travel low:
    robot/moveTo drives straight there and takes the pipette mounts with it."""
    driver, _ = await _bench(_EIGHT_CHANNEL, _SINGLE_CHANNEL, gripper=True)
    log = _record_motion(driver)

    await driver.park_gantry(ParkGantryRequest(at=GantryParkPosition(x=1.0, y=2.0, z=3.0)))

    assert log == ["leftZ", "rightZ", "extensionZ", "gripper.move_to"]


async def test_a_bench_park_spot_does_not_traverse_in_y_as_well() -> None:
    """The named spot IS the destination; retracting y afterwards would undo it."""
    driver, _ = await _bench(_EIGHT_CHANNEL, gripper=True)
    log = _record_motion(driver)

    await driver.park_gantry(ParkGantryRequest(at=GantryParkPosition(x=1.0, y=2.0, z=3.0)))

    assert "y" not in log


async def test_a_gripper_jog_lifts_every_mount_before_it_travels() -> None:
    """A jog crosses the deck exactly as a park does, so it owes the same lift."""
    driver, _ = await _bench(_EIGHT_CHANNEL, _SINGLE_CHANNEL, gripper=True)
    log = _record_motion(driver)

    await driver.move_gripper_to(MoveGripperToRequest(x=1.0, y=2.0, z=3.0))

    assert log == ["leftZ", "rightZ", "extensionZ", "gripper.move_to"]


async def test_the_flex_declares_the_capability() -> None:
    """Parking is rare, so a caller has to be able to ask whether this handler
    can do it rather than assume every liquid handler can."""
    assert "IGantryParking" in FlexLiquidHandlerDriver.interfaces
