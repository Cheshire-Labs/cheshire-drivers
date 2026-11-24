"""A pickup or a drop that fails on some channels says which ones, instead of failing the whole command.

PyLabRobot keeps the tips on the channels that worked and raises. Raising here
too would tell the record that nothing moved, while the head holds tips.
"""

import pytest
import pytest_asyncio
from pylabrobot.legacy.liquid_handling.errors import ChannelizedError
from pylabrobot.legacy.liquid_handling.standard import Drop, Pickup
from pylabrobot.resources.errors import HasTipError

from cheshire_drivers.liquid_handler_models import (
    ChannelError,
    DiscardTipsRequest,
    DropTipsRequest,
    PickUpTipsRequest,
    TipPick,
)
from cheshire_drivers.plr import ChatterboxLiquidHandlerDriver
from tests.test_volume_tracking_lenient import _configure_with_occupancy, _reset_plr_tracking_flags

__all__ = ["_reset_plr_tracking_flags"]

_PICK = PickUpTipsRequest(picks=[TipPick(tip_rack="tips", positions=["A1", "B1", "C1"])])


@pytest_asyncio.fixture
async def driver() -> ChatterboxLiquidHandlerDriver:
    driver = ChatterboxLiquidHandlerDriver(num_channels=8)
    await _configure_with_occupancy(driver)
    return driver


def _fail_channels(
    driver: ChatterboxLiquidHandlerDriver, command: str, channels: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert driver._lh is not None
    backend = driver._lh.backend

    async def fails(ops: list[Pickup] | list[Drop], use_channels: list[int]) -> None:
        raise ChannelizedError({channel: RuntimeError("no tip") for channel in channels})

    async def presence_unknown() -> list[bool | None]:
        raise NotImplementedError

    monkeypatch.setattr(backend, command, fails)
    monkeypatch.setattr(backend, "request_tip_presence", presence_unknown)


def _held(driver: ChatterboxLiquidHandlerDriver) -> list[int]:
    assert driver._lh is not None
    return [channel for channel, tracker in driver._lh.head.items() if tracker.has_tip]


@pytest.mark.asyncio
async def test_a_pickup_that_misses_one_channel_names_it(
    driver: ChatterboxLiquidHandlerDriver, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fail_channels(driver, "pick_up_tips", [1], monkeypatch)

    response = await driver.pick_up_tips(_PICK)

    assert response.success is False
    assert response.per_channel_errors == [ChannelError(
        channel_id=1, well_position="B1", labware="tips", attempted_volume=0.0,
        error_code="RuntimeError", error_message="no tip",
    )]
    assert _held(driver) == [0, 2]


@pytest.mark.asyncio
async def test_a_pickup_that_takes_nothing_still_raises(
    driver: ChatterboxLiquidHandlerDriver, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fail_channels(driver, "pick_up_tips", [0, 1, 2], monkeypatch)

    with pytest.raises(ChannelizedError):
        await driver.pick_up_tips(_PICK)
    assert _held(driver) == []


@pytest.mark.asyncio
async def test_a_return_that_leaves_one_tip_on_names_it(
    driver: ChatterboxLiquidHandlerDriver, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await driver.pick_up_tips(_PICK)
    _fail_channels(driver, "drop_tips", [2], monkeypatch)

    response = await driver.drop_tips(DropTipsRequest(
        to_waste=False, drops=[TipPick(tip_rack="tips", positions=["A1", "B1", "C1"])],
    ))

    assert [(e.channel_id, e.well_position) for e in response.per_channel_errors] == [(2, "C1")]
    assert _held(driver) == [2]


@pytest.mark.asyncio
async def test_a_waste_drop_on_named_channels_names_the_one_that_kept_its_tip(
    driver: ChatterboxLiquidHandlerDriver, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await driver.pick_up_tips(_PICK)
    _fail_channels(driver, "drop_tips", [0], monkeypatch)

    response = await driver.drop_tips(DropTipsRequest(to_waste=True, use_channels=[0, 1, 2]))

    assert [(e.channel_id, e.well_position) for e in response.per_channel_errors] == [(0, None)]
    assert _held(driver) == [0]


@pytest.mark.asyncio
async def test_a_pickup_onto_a_channel_already_holding_a_tip_raises_and_claims_no_channel_went(
    driver: ChatterboxLiquidHandlerDriver,
) -> None:
    await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="tips", positions=["D1"])], use_channels=[1]))

    with pytest.raises(HasTipError):
        await driver.pick_up_tips(_PICK)


@pytest.mark.asyncio
async def test_a_discard_that_leaves_one_tip_on_names_it(
    driver: ChatterboxLiquidHandlerDriver, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await driver.pick_up_tips(_PICK)
    _fail_channels(driver, "drop_tips", [1], monkeypatch)

    response = await driver.discard_tips(DiscardTipsRequest(use_channels=[0, 1, 2]))

    assert [(e.channel_id, e.well_position) for e in response.per_channel_errors] == [(1, None)]
    assert _held(driver) == [1]
