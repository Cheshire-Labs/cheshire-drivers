"""The lenient trackers are installed, and stay installed.

The class-level tests next door prove the trackers behave. They do not prove
anything ever puts one on a real handler, and a review found that deleting the
installation left the whole suite green. A fix nothing asserts is a fix that
gets refactored away.

The reconnect case is the one that actually bit: `LiquidHandler.setup()` builds
fresh stock head trackers, so installing only at configure time meant an
operator running disconnect then initialize -- the documented way back from a
backend that has latched a refusal -- re-armed every refusal this change exists
to remove.
"""

import pytest
from pylabrobot.resources.tip_tracker import TipTracker

from cheshire_drivers.liquid_handler_models import (
    AddDeckLabwareRequest,
    DeckLayoutConfig,
    DeckResourceConfig,
    DiscardTipsRequest,
    PickUpTipsRequest,
    TipPick,
)
from cheshire_drivers.plr import ChatterboxLiquidHandlerDriver
from cheshire_drivers.plr_tip_tracker import LenientTipSpotTracker, LenientTipTracker

pytestmark = pytest.mark.asyncio

_RACK = "tips"
_TIP_CARRIER = DeckResourceConfig(
    name="carrier-15", catalog_ref="TIP_CAR_480_A00", rail=15,
)


async def _configured(with_rack: bool = True) -> ChatterboxLiquidHandlerDriver:
    driver = ChatterboxLiquidHandlerDriver(num_channels=8)
    await driver.configure_deck(
        DeckLayoutConfig(deck_type="STARlet", resources=[_TIP_CARRIER]),
    )
    if with_rack:
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name=_RACK,
            catalog_ref="hamilton_96_tiprack_1000uL_filter",
            at="carrier-15-0",
        ))
    return driver


def _head_tracker_types(driver: ChatterboxLiquidHandlerDriver) -> set[str]:
    assert driver._lh is not None
    return {type(t).__name__ for t in driver._lh.head.values()}


class TestTheHeadCarriesLenientTrackers:
    async def test_after_configure_deck(self) -> None:
        driver = await _configured(with_rack=False)
        assert _head_tracker_types(driver) == {LenientTipTracker.__name__}

    async def test_still_after_a_reconnect(self) -> None:
        """setup() rebuilds the head. An operator's way back from a latched
        backend must not re-arm the refusals."""
        driver = await _configured(with_rack=False)
        await driver.initialize()
        await driver.disconnect()
        await driver.initialize()
        assert _head_tracker_types(driver) == {LenientTipTracker.__name__}, (
            "a reconnect left the head carrying stock trackers"
        )

    async def test_stock_is_a_different_type(self) -> None:
        """Negative control: the assertions above would be vacuous if the stock
        tracker and the lenient one shared a name."""
        assert TipTracker.__name__ != LenientTipTracker.__name__


class TestTipSpotsCarryLenientTrackers:
    async def test_a_rack_added_to_the_deck_gets_them(self) -> None:
        driver = await _configured()
        assert driver._lh is not None
        rack = driver._lh.deck.get_resource(_RACK)
        types = {type(spot.tracker).__name__ for spot in rack.get_all_items()}
        assert types == {LenientTipSpotTracker.__name__}


class TestASpotAskedTwiceHandsOutTwoTips:
    async def test_a_spot_does_not_hand_out_the_same_tip_object_twice(self) -> None:
        """A spot that believes it is empty still has to answer what a pick from
        it yields, and PyLabRobot reads that before any tracking gate.

        Answering with the tip it handed out last time is the trap: two channels
        then hold ONE Tip and therefore ONE volume tracker, and the second pick
        silently rewrites the first channel's liquid. A refusal turned into
        wrong numbers is worse than the refusal.
        """
        driver = await _configured()
        assert driver._lh is not None
        spot = driver._lh.deck.get_resource(_RACK).get_item("A1")
        spot.tracker.remove_tip(commit=True)

        first, second = spot.tracker.get_tip(), spot.tracker.get_tip()

        assert first is not second, "the spot handed out the same Tip twice"
        assert first.tracker is not second.tracker, (
            "the two tips share a volume tracker, so liquid in one is liquid "
            "in the other"
        )
        assert first.maximal_volume == second.maximal_volume, (
            "a minted tip must carry the spot's own geometry"
        )


class TestATipWithLiquidStillComesOff:
    async def test_discarding_a_wet_tip_is_not_refused(self) -> None:
        """The residual-volume refusal is measured against volumes we projected
        onto the driver, and a wet tip physically comes off either way."""
        driver = await _configured()
        await driver.initialize()
        await driver.pick_up_tips(
            PickUpTipsRequest(picks=[TipPick(tip_rack=_RACK, positions=["A1"])]),
        )
        assert driver._lh is not None
        driver._lh.head[0].get_tip().tracker.add_liquid(100.0)
        await driver.discard_tips(DiscardTipsRequest(use_channels=[0]))
