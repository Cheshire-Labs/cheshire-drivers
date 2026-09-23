"""Characterization: the ``pylabrobot.opentrons`` Flex heads commit to the trackers we read.

The whole Flex adapter rests on one assumption -- that a head's tip and volume
bookkeeping lands on the same PLR trackers ``derive_labware_state`` walks. These
drive ``OpentronsFlex`` + ``ChatterboxTransport`` DIRECTLY (no adapter) and pin
the expected values after each command, including the sensor-verified pickup
rollback that must move no state at all.
"""

from typing import Iterator, NamedTuple

import pytest

from cheshire_drivers.liquid_handler_models import TROUGH_WELL_ID
from cheshire_drivers.plr.opentrons_flex import derive_labware_state
from pylabrobot.opentrons import ChatterboxTransport, OpentronsError, OpentronsFlex
from pylabrobot.opentrons.flex_head import FlexHead8
from pylabrobot.resources import (
    Container,
    Plate,
    TipRack,
    cor_96_wellplate_360uL_Fb,
    set_tip_tracking,
    set_volume_tracking,
)
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.opentrons.flex_tip_racks import flex_96_tiprack_50ul

_PLATE_SEED_UL = 100.0
_TROUGH_SEED_UL = 50_000.0
_COLUMN_0 = [f"{row}1" for row in "ABCDEFGH"]


class _Bench(NamedTuple):
    flex: OpentronsFlex
    transport: ChatterboxTransport
    head: FlexHead8
    rack: TipRack
    plate: Plate
    trough: Container


@pytest.fixture
def tracking() -> Iterator[None]:
    """PLR gates tip/volume tracking behind module globals that default OFF."""
    set_tip_tracking(True)
    set_volume_tracking(True)
    yield
    set_tip_tracking(False)
    set_volume_tracking(False)


def _make_trough(name: str = "trough_1") -> Container:
    trough = Container(name=name, size_x=107.0, size_y=71.0, size_z=25.0, max_volume=195_000.0)
    setattr(trough, "ot_load_name", "nest_1_reservoir_195ml")
    return trough


async def _bench(simulate_failed_pickup: bool = False) -> _Bench:
    """A set-up Flex with an 8-channel head, a tip rack, a seeded plate and a seeded trough."""
    transport = ChatterboxTransport(
        pipettes=[("p50_multi_flex", 8, 1.0, 50.0, "left")],
        simulate_failed_pickup=simulate_failed_pickup,
    )
    deck = FlexDeck()
    flex = OpentronsFlex(deck=deck, host="localhost", transport=transport)
    rack = flex_96_tiprack_50ul(name="rack_1")
    plate = cor_96_wellplate_360uL_Fb(name="plate_1")
    trough = _make_trough()
    deck.assign_child_at_slot(rack, "C1")
    deck.assign_child_at_slot(plate, "C2")
    deck.assign_child_at_slot(trough, "C3")
    for well in plate.get_all_items():
        well.tracker.set_volume(_PLATE_SEED_UL)
    trough.tracker.set_volume(_TROUGH_SEED_UL)
    await flex.setup()
    head = flex.left
    assert isinstance(head, FlexHead8)
    return _Bench(flex, transport, head, rack, plate, trough)


@pytest.mark.asyncio
async def test_a_fresh_deck_reports_full_rack_seeded_plate_and_seeded_trough(tracking) -> None:
    bench = await _bench()
    try:
        state = derive_labware_state(bench.flex.deck)
        assert set(state) == {"rack_1", "plate_1", "trough_1"}
        assert state["rack_1"].tips is not None and all(state["rack_1"].tips.values())
        assert state["plate_1"].volumes == {
            well.get_identifier(): _PLATE_SEED_UL for well in bench.plate.get_all_items()
        }
        assert state["trough_1"].volumes == {TROUGH_WELL_ID: _TROUGH_SEED_UL}
    finally:
        await bench.flex.stop()


@pytest.mark.asyncio
async def test_column_pickup_flips_only_that_columns_spots(tracking) -> None:
    bench = await _bench()
    try:
        await bench.head.pick_up_tips(bench.rack, column=0)

        state = derive_labware_state(bench.flex.deck)
        tips = state["rack_1"].tips
        assert tips is not None
        assert [tips[position] for position in _COLUMN_0] == [False] * 8
        assert all(present for spot, present in tips.items() if spot not in _COLUMN_0)
        # A pickup moves no liquid: the plate and trough pools are untouched.
        assert state["plate_1"].volumes == {
            well.get_identifier(): _PLATE_SEED_UL for well in bench.plate.get_all_items()
        }
        assert state["trough_1"].volumes == {TROUGH_WELL_ID: _TROUGH_SEED_UL}
    finally:
        await bench.flex.stop()


@pytest.mark.asyncio
async def test_column_aspirate_then_dispense_moves_only_the_addressed_wells(tracking) -> None:
    bench = await _bench()
    try:
        await bench.head.pick_up_tips(bench.rack, column=0)
        await bench.head.aspirate(bench.plate, column=0, volume=20.0)

        after_aspirate = derive_labware_state(bench.flex.deck).get("plate_1")
        assert after_aspirate is not None and after_aspirate.volumes is not None
        assert [after_aspirate.volumes[position] for position in _COLUMN_0] == [80.0] * 8
        assert after_aspirate.volumes["A2"] == _PLATE_SEED_UL

        await bench.head.dispense(bench.plate, column=1, volume=20.0)

        after_dispense = derive_labware_state(bench.flex.deck).get("plate_1")
        assert after_dispense is not None and after_dispense.volumes is not None
        assert [after_dispense.volumes[position] for position in _COLUMN_0] == [80.0] * 8
        assert after_dispense.volumes["A2"] == 120.0
        assert after_dispense.volumes["A3"] == _PLATE_SEED_UL
        # Plate work moves nothing on the other labware.
        state = derive_labware_state(bench.flex.deck)
        assert state["trough_1"].volumes == {TROUGH_WELL_ID: _TROUGH_SEED_UL}
        rack_tips = state["rack_1"].tips
        assert rack_tips is not None
        assert [rack_tips[position] for position in _COLUMN_0] == [False] * 8
    finally:
        await bench.flex.stop()


@pytest.mark.asyncio
async def test_container_aspirate_debits_the_single_pool_under_a1(tracking) -> None:
    """Eight channels drawing from one cavity share ONE tracker, so the pool
    falls by the summed volume and still reports under the single "A1" key."""
    bench = await _bench()
    try:
        await bench.head.pick_up_tips(bench.rack, column=0)
        await bench.head.aspirate_container(bench.trough, volume=10.0)

        state = derive_labware_state(bench.flex.deck)
        assert state["trough_1"].volumes == {TROUGH_WELL_ID: _TROUGH_SEED_UL - 80.0}
        # The draw comes out of the pool, not the plate the head is parked over.
        assert state["plate_1"].volumes == {
            well.get_identifier(): _PLATE_SEED_UL for well in bench.plate.get_all_items()
        }
    finally:
        await bench.flex.stop()


@pytest.mark.asyncio
async def test_a_pickup_the_tip_sensor_rejects_leaves_state_unchanged(tracking) -> None:
    """The head stages tip trackers, then rolls them back when the hardware
    tip-presence sensor reports nothing seated, so no state escapes the failure."""
    bench = await _bench(simulate_failed_pickup=True)
    try:
        before = derive_labware_state(bench.flex.deck)

        with pytest.raises(OpentronsError):
            await bench.head.pick_up_tips(bench.rack, column=0)

        assert derive_labware_state(bench.flex.deck) == before
        assert all(tip is None for tip in bench.head.get_mounted_tips())
    finally:
        await bench.flex.stop()


@pytest.mark.asyncio
async def test_dropping_tips_back_restores_the_rack_spots(tracking) -> None:
    bench = await _bench()
    try:
        await bench.head.pick_up_tips(bench.rack, column=0)
        await bench.head.drop_tips(bench.rack, column=0)

        state = derive_labware_state(bench.flex.deck)
        tips = state["rack_1"].tips
        assert tips is not None and all(tips.values())
        assert all(tip is None for tip in bench.head.get_mounted_tips())
    finally:
        await bench.flex.stop()


@pytest.mark.asyncio
async def test_labware_on_a_staging_pad_reports_its_state(tracking) -> None:
    """The pads are deck sites like any other; a plate handed off there keeps its
    well state visible so the runtime's ledger and the driver world agree."""
    bench = await _bench()
    try:
        staged = cor_96_wellplate_360uL_Fb(name="staged_plate")
        bench.flex.deck.assign_child_at_slot(staged, "B4")

        assert "staged_plate" in derive_labware_state(bench.flex.deck)
    finally:
        await bench.flex.stop()
