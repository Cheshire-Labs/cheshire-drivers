"""Trough (single-pool container) liquid handling on the PLR driver.

A trough is one PLR ``Container``: ``positions=None`` on the wire means every
channel draws from / dispenses into the one pool, which the driver hands to PLR
as ``[trough] * n`` and PLR spreads across the bath. Volume is tracked as a
single pool keyed by ``TROUGH_WELL_ID``.
"""

import pytest
from pylabrobot.resources.resource import Coordinate
from pylabrobot.resources.trough import Trough
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well, WellBottomType
from pylabrobot.resources.tip_rack import TipRack, TipSpot, Tip
from pylabrobot.resources.utils import create_ordered_items_2d

from cheshire_drivers.liquid_handler_models import (
    AspirateRequest, AspirateTarget, Aspirate96Request,
    DeckLayoutConfig,
    DispenseRequest, DispenseTarget, Dispense96Request,
    MixRequest,
    PickUpTipsRequest, PickUpTips96Request, TipPick,
    TROUGH_WELL_ID,
)
from cheshire_drivers.plr_tracker_seeding import (
    swap_container_to_lenient,
    swap_container_to_replenishing,
    swap_container_to_strict,
    swap_to_lenient_tracker,
)
from cheshire_drivers.plr.liquid_handler import ChatterboxLiquidHandlerDriver


def _make_trough(name: str = "reagent_trough") -> Trough:
    return Trough(name=name, size_x=37.0, size_y=118.0, size_z=95.0, max_volume=200_000.0)


def _make_wide_trough(name: str = "reagent_trough") -> Trough:
    # Wide enough to seat the 96 head (its ~108x72 mm footprint).
    return Trough(name=name, size_x=120.0, size_y=80.0, size_z=40.0, max_volume=100_000.0)


def _make_plate(name: str = "test_plate") -> Plate:
    ordered = create_ordered_items_2d(
        Well, num_items_x=12, num_items_y=8, dx=9.0, dy=9.0, dz=0.0,
        item_dx=9.0, item_dy=9.0, size_x=9.0, size_y=9.0, size_z=10.0,
        bottom_type=WellBottomType.FLAT,
    )
    return Plate(name=name, size_x=127.76, size_y=85.48, size_z=14.5, ordered_items=ordered)


def _make_tip_rack(name: str = "test_tips") -> TipRack:
    def _make_tip(name: str = "tip", maximal_volume: float = 1000.0) -> Tip:
        return Tip(has_filter=False, total_tip_length=50.0, maximal_volume=maximal_volume, fitting_depth=8.0)

    ordered = create_ordered_items_2d(
        TipSpot, num_items_x=12, num_items_y=8, dx=9.0, dy=9.0, dz=0.0,
        item_dx=9.0, item_dy=9.0, size_x=9.0, size_y=9.0, size_z=10.0, make_tip=_make_tip,
    )
    return TipRack(name=name, size_x=127.76, size_y=85.48, size_z=20.0, ordered_items=ordered)


async def _setup(driver: ChatterboxLiquidHandlerDriver, *, trough: Trough | None = None,
                 plate: Plate | None = None, rack: TipRack | None = None) -> None:
    await driver.configure_deck(DeckLayoutConfig(deck_type="STARLet", resources=[]))
    if trough is not None:
        driver._lh.deck.assign_child_resource(trough, location=Coordinate(100, 100, 100))
        swap_container_to_lenient(trough)
    if plate is not None:
        # No test places both a trough and a plate, so the (100,100) slot is free.
        driver._lh.deck.assign_child_resource(plate, location=Coordinate(100, 100, 100))
        for well in plate.get_all_items():
            swap_to_lenient_tracker(well)
    if rack is not None:
        driver._lh.deck.assign_child_resource(rack, location=Coordinate(300, 100, 100))


_COL1 = ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1"]


@pytest.mark.asyncio
async def test_aspirate_from_trough_single_pool_accounting() -> None:
    driver = ChatterboxLiquidHandlerDriver(num_channels=8)
    trough, rack = _make_trough(), _make_tip_rack()
    await _setup(driver, trough=trough, rack=rack)
    swap_container_to_strict(trough, 1000.0)
    await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=_COL1)]))

    resp = await driver.aspirate(AspirateRequest(aspirations=[
        AspirateTarget(labware="reagent_trough", positions=None, volumes=[25.0] * 8)
    ]))

    # 8 channels x 25 uL all drawn from the one pool: 1000 - 200 = 800.
    assert resp.labware_state["reagent_trough"].volumes[TROUGH_WELL_ID] == pytest.approx(800.0)


@pytest.mark.asyncio
async def test_dispense_into_trough_credits_pool() -> None:
    driver = ChatterboxLiquidHandlerDriver(num_channels=8)
    trough, rack = _make_trough(), _make_tip_rack()
    await _setup(driver, trough=trough, rack=rack)
    swap_container_to_strict(trough, 1000.0)
    await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=_COL1)]))

    await driver.aspirate(AspirateRequest(aspirations=[
        AspirateTarget(labware="reagent_trough", positions=None, volumes=[25.0] * 8)
    ]))
    resp = await driver.dispense(DispenseRequest(dispenses=[
        DispenseTarget(labware="reagent_trough", positions=None, volumes=[25.0] * 8)
    ]))

    assert resp.labware_state["reagent_trough"].volumes[TROUGH_WELL_ID] == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_aspirate_from_empty_trough_does_not_raise() -> None:
    driver = ChatterboxLiquidHandlerDriver(num_channels=8)
    trough, rack = _make_trough(), _make_tip_rack()
    await _setup(driver, trough=trough, rack=rack)  # lenient, starts empty
    await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=_COL1)]))

    # Lenient tracker: drawing from an unseeded trough is audited, not refused.
    await driver.aspirate(AspirateRequest(aspirations=[
        AspirateTarget(labware="reagent_trough", positions=None, volumes=[25.0] * 8)
    ]))


@pytest.mark.asyncio
async def test_mix_in_trough_uses_channel_count_from_use_channels() -> None:
    driver = ChatterboxLiquidHandlerDriver(num_channels=8)
    trough, rack = _make_trough(), _make_tip_rack()
    await _setup(driver, trough=trough, rack=rack)
    swap_container_to_strict(trough, 1000.0)
    await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1", "B1", "C1", "D1"])]))

    resp = await driver.mix(MixRequest(
        labware="reagent_trough", positions=None, volume=50.0, repetitions=3,
        use_channels=[0, 1, 2, 3],
    ))

    # Mix is aspirate+dispense in place: the pool nets to zero, left unchanged.
    assert resp.labware_state["reagent_trough"].volumes[TROUGH_WELL_ID] == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_aspirate96_and_dispense96_from_trough() -> None:
    driver = ChatterboxLiquidHandlerDriver(num_channels=8)
    trough, rack = _make_wide_trough(), _make_tip_rack()
    await _setup(driver, trough=trough, rack=rack)
    swap_container_to_strict(trough, 50_000.0)
    await driver.pick_up_tips96(PickUpTips96Request(tip_rack="test_tips"))

    asp = await driver.aspirate96(Aspirate96Request(labware="reagent_trough", volume=10.0))
    # The 96 head draws 10 uL through every channel from the one pool: the driver
    # removes head_size x volume, so 50000 - 96*10 = 49040 (not 50000 - 10).
    assert asp.labware_state["reagent_trough"].volumes[TROUGH_WELL_ID] == pytest.approx(49_040.0)

    disp = await driver.dispense96(Dispense96Request(labware="reagent_trough", volume=10.0))
    # Credited back through every channel: 49040 + 96*10 = 50000.
    assert disp.labware_state["reagent_trough"].volumes[TROUGH_WELL_ID] == pytest.approx(50_000.0)


@pytest.mark.asyncio
async def test_aspirate_from_replenished_trough_does_not_deplete() -> None:
    driver = ChatterboxLiquidHandlerDriver(num_channels=8)
    trough, rack = _make_trough(), _make_tip_rack()
    await _setup(driver, trough=trough, rack=rack)
    swap_container_to_replenishing(trough, 1000.0)
    await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=_COL1)]))

    # Ten rounds of 8x25 uL = 2000 uL, twice the 1000 uL seed: a strict pool
    # raises once the cumulative draw passes 1000; replenished stays pinned.
    for _ in range(10):
        resp = await driver.aspirate(AspirateRequest(aspirations=[
            AspirateTarget(labware="reagent_trough", positions=None, volumes=[25.0] * 8)
        ]))
        assert resp.labware_state["reagent_trough"].volumes[TROUGH_WELL_ID] == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_aspirate96_from_replenished_trough_stays_pinned() -> None:
    driver = ChatterboxLiquidHandlerDriver(num_channels=8)
    trough, rack = _make_wide_trough(), _make_tip_rack()
    await _setup(driver, trough=trough, rack=rack)
    swap_container_to_replenishing(trough, 500.0)
    await driver.pick_up_tips96(PickUpTips96Request(tip_rack="test_tips"))

    # 96 channels x 10 uL = 960 uL in one shot from a 500 uL seed: strict would
    # raise (960 > 500), replenished leaves the pool at its seed.
    asp = await driver.aspirate96(Aspirate96Request(labware="reagent_trough", volume=10.0))
    assert asp.labware_state["reagent_trough"].volumes[TROUGH_WELL_ID] == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_positions_on_trough_is_rejected() -> None:
    driver = ChatterboxLiquidHandlerDriver(num_channels=8)
    trough, rack = _make_trough(), _make_tip_rack()
    await _setup(driver, trough=trough, rack=rack)
    await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))

    with pytest.raises(TypeError, match="single container"):
        await driver.aspirate(AspirateRequest(aspirations=[
            AspirateTarget(labware="reagent_trough", positions=["A1"], volumes=[25.0])
        ]))


@pytest.mark.asyncio
async def test_omitted_positions_on_plate_is_rejected() -> None:
    driver = ChatterboxLiquidHandlerDriver(num_channels=8)
    plate, rack = _make_plate(), _make_tip_rack()
    await _setup(driver, plate=plate, rack=rack)
    await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))

    with pytest.raises(TypeError, match="itemized"):
        await driver.aspirate(AspirateRequest(aspirations=[
            AspirateTarget(labware="test_plate", positions=None, volumes=[25.0])
        ]))


def test_mix_without_positions_requires_use_channels() -> None:
    with pytest.raises(ValueError, match="use_channels"):
        MixRequest(labware="reagent_trough", positions=None, volume=50.0, repetitions=3)


def test_mix_without_positions_rejects_empty_use_channels() -> None:
    # An empty list would resolve to zero channels and run a silent no-op mix.
    with pytest.raises(ValueError, match="use_channels"):
        MixRequest(
            labware="reagent_trough", positions=None, volume=50.0, repetitions=3,
            use_channels=[],
        )
