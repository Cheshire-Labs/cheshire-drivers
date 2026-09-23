"""Opentrons slot occupancy: reconcile, add_deck_labware, and move_plate onto slots.

Regression cover for three carrier-first wrapper methods that hardcoded the
Hamilton carrier shape and so rejected or double-occupied Opentrons slots. A
Flex/OT-2 slot is a ``ResourceHolder`` directly under the deck (not a carrier
site), addressed by the ``"<slot>-slot"`` deck-site label the runtime sends:

  * ``reconcile_deck_occupancy`` twice used to raise "Slot ... is already
    occupied" because the clear step only walked carrier sites, never slots.
  * ``add_deck_labware`` / ``move_plate`` used to reject the ``"<slot>-slot"``
    site form because they only parsed the Hamilton ``"<carrier>-<index>"`` form.

move_plate onto a slot is a gripper move, which only the Flex has; the OT-2 has
no gripper, so an OT-2 slot move is rejected with a physical-reality message.
"""

import pytest

from cheshire_drivers.liquid_handler_models import (
    AddDeckLabwareRequest,
    DeckLayoutConfig,
    DeckResourceConfig,
    GetDeckStateRequest,
    MovePlateRequest,
    ReconcileDeckOccupancyRequest,
    RemoveDeckLabwareRequest,
)
from cheshire_drivers.plr.liquid_handler import ChatterboxLiquidHandlerDriver

PLATE = "Cor_96_wellplate_360ul_Fb"


async def _driver(deck_type: str) -> ChatterboxLiquidHandlerDriver:
    """An Opentrons slot deck: no carriers, built-in slots addressed by name."""
    driver = ChatterboxLiquidHandlerDriver()
    await driver.configure_deck(DeckLayoutConfig(deck_type=deck_type, resources=[]))
    await driver.initialize()
    return driver


def _slot_plate(name: str, slot: str) -> DeckResourceConfig:
    return DeckResourceConfig(name=name, catalog_ref=PLATE, parent_id=slot, site_index=0)


def _occupant_name(driver: ChatterboxLiquidHandlerDriver, slot_key):
    assert driver._lh is not None
    occupant = driver._lh.deck.slots[slot_key]
    return occupant.name if occupant is not None else None


class TestReconcileSlotDeckDoesNotDoubleOccupy:
    """reconcile clears prior slot occupancy before re-placing, so a repeated
    reconcile of the same slot set is idempotent rather than "already occupied"."""

    @pytest.mark.asyncio
    async def test_flex_reconcile_twice_same_slot_is_idempotent(self) -> None:
        driver = await _driver("FlexDeck")
        spec = ReconcileDeckOccupancyRequest(resources=[_slot_plate("p1", "C2")])
        await driver.reconcile_deck_occupancy(spec)
        await driver.reconcile_deck_occupancy(spec)
        assert _occupant_name(driver, "C2") == "p1"

    @pytest.mark.asyncio
    async def test_ot2_reconcile_twice_same_slot_is_idempotent(self) -> None:
        driver = await _driver("OTDeck")
        spec = ReconcileDeckOccupancyRequest(resources=[_slot_plate("p1", "7")])
        await driver.reconcile_deck_occupancy(spec)
        await driver.reconcile_deck_occupancy(spec)
        assert _occupant_name(driver, 6) == "p1"  # OT-2 slot 7 is 0-indexed list position 6

    @pytest.mark.asyncio
    async def test_flex_reconcile_clears_a_vacated_slot(self) -> None:
        driver = await _driver("FlexDeck")
        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_slot_plate("p1", "C2")])
        )
        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_slot_plate("p1", "A1")])
        )
        assert _occupant_name(driver, "C2") is None
        assert _occupant_name(driver, "A1") == "p1"

    @pytest.mark.asyncio
    async def test_flex_reconcile_leaves_the_trash_bin_in_place(self) -> None:
        """The trash bin is a Container occupying a slot holder; the clear step must exclude it so a
        reconcile never strips the deck of its trash."""
        driver = await _driver("FlexDeck")
        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_slot_plate("p1", "C2")])
        )
        assert _occupant_name(driver, "A3") == "trash"

    @pytest.mark.asyncio
    async def test_flex_reconcile_leaves_a_non_labware_fixture_in_place(self) -> None:
        """A holder occupant that is not materialized labware (a module/fixture) is deck structure,
        so the clear step leaves it in place even though it sits in a slot holder like labware does."""
        from pylabrobot.resources import Resource

        driver = await _driver("FlexDeck")
        assert driver._lh is not None
        fixture = Resource(name="temp_module", size_x=127.0, size_y=85.0, size_z=40.0)
        driver._lh.deck.get_slot_holder("C1").assign_child_resource(fixture)
        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_slot_plate("p1", "A1")])
        )
        assert _occupant_name(driver, "C1") == "temp_module"
        assert _occupant_name(driver, "A1") == "p1"


class TestAddDeckLabwareOntoSlot:
    """add_deck_labware accepts the ``"<slot>-slot"`` deck-site label for both
    Opentrons decks (an external transporter or operator drops labware onto a slot)."""

    @pytest.mark.asyncio
    async def test_flex_add_deck_labware_at_named_slot(self) -> None:
        driver = await _driver("FlexDeck")
        await driver.add_deck_labware(
            AddDeckLabwareRequest(name="p1", catalog_ref=PLATE, at="C2-slot")
        )
        assert _occupant_name(driver, "C2") == "p1"

    @pytest.mark.asyncio
    async def test_ot2_add_deck_labware_at_numbered_slot(self) -> None:
        driver = await _driver("OTDeck")
        await driver.add_deck_labware(
            AddDeckLabwareRequest(name="p1", catalog_ref=PLATE, at="7-slot")
        )
        assert _occupant_name(driver, 6) == "p1"


class TestMovePlateOntoSlot:
    """A Flex gripper move targets a slot by its ``"<slot>-slot"`` label; the
    driver resolves it to the slot holder. The OT-2 has no gripper, so the same
    move on an OT-2 slot is rejected with a physical-reality message."""

    @pytest.mark.asyncio
    async def test_flex_move_plate_between_slots(self) -> None:
        driver = await _driver("FlexDeck")
        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_slot_plate("p1", "A1")])
        )
        await driver.move_plate(
            MovePlateRequest(plate="p1", to_position="C2-slot", from_position="A1-slot")
        )
        assert _occupant_name(driver, "A1") is None
        assert _occupant_name(driver, "C2") == "p1"

    @pytest.mark.asyncio
    async def test_flex_move_reparents_when_model_disagrees_with_from_position(self) -> None:
        """move_plate corrects PLR's model to the claimed from_position before the gripper move.
        With the plate actually at C2 but a move declared from A1, it re-homes to A1 (resolved via
        the slot label) then lands at B2 -- so the slot-aware resolution feeds that correction too."""
        driver = await _driver("FlexDeck")
        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_slot_plate("p1", "C2")])
        )
        await driver.move_plate(
            MovePlateRequest(plate="p1", to_position="B2-slot", from_position="A1-slot")
        )
        assert _occupant_name(driver, "C2") is None
        assert _occupant_name(driver, "A1") is None
        assert _occupant_name(driver, "B2") == "p1"

    @pytest.mark.asyncio
    async def test_ot2_slot_move_is_rejected_no_gripper_and_leaves_plate_put(self) -> None:
        driver = await _driver("OTDeck")
        await driver.add_deck_labware(
            AddDeckLabwareRequest(name="p1", catalog_ref=PLATE, at="1-slot")
        )
        with pytest.raises(ValueError, match="no gripper"):
            await driver.move_plate(
                MovePlateRequest(plate="p1", to_position="7-slot", from_position="1-slot")
            )
        # The refusal fires before any mutation, so the plate stays on its origin slot.
        assert _occupant_name(driver, 0) == "p1"


class TestStagingPadsReportLikeAnyDeckSite:
    """The Flex staging pads (column 4) are deck sites: the arm hands off there and
    the deck gripper relays inward. The runtime learns what the driver world holds
    ONLY through get_deck_state, and un-materializes a departing labware only if
    that read reports it. A pad resident hidden from the read is therefore never
    removed on pickup, so it stays in the PLR tree and the next drop onto that pad
    is refused ("already has a resource"). Both read surfaces report the pads."""

    @pytest.mark.asyncio
    async def test_labware_on_a_staging_pad_is_reported_by_both_read_surfaces(self) -> None:
        driver = await _driver("FlexDeck")
        await driver.add_deck_labware(
            AddDeckLabwareRequest(name="staged", catalog_ref=PLATE, at="B4-slot")
        )
        await driver.add_deck_labware(
            AddDeckLabwareRequest(name="addressable", catalog_ref=PLATE, at="C1-slot")
        )

        state = await driver.get_deck_state(GetDeckStateRequest())
        labware_state = driver._get_labware_state()

        by_name = {item.name: item for item in state.labware}
        assert "staged" in by_name and by_name["staged"].site == "B4-slot"
        assert "addressable" in by_name and by_name["addressable"].site == "C1-slot"
        assert "staged" in labware_state
        assert "addressable" in labware_state

    @pytest.mark.asyncio
    async def test_pad_holders_themselves_are_not_reported_as_labware(self) -> None:
        driver = await _driver("FlexDeck")
        state = await driver.get_deck_state(GetDeckStateRequest())
        assert not any("slot_" in item.name for item in state.labware)

    @pytest.mark.asyncio
    async def test_a_pad_picked_clean_by_the_arm_accepts_the_next_gripper_drop(self) -> None:
        """The bench sequence: rack 1 leaves through D4 (arm pick = remove_deck_labware),
        rack 2 is relayed to D4 by the deck gripper. Only works if the arm's pick
        actually emptied the pad in the driver world."""
        driver = await _driver("FlexDeck")
        await driver.add_deck_labware(
            AddDeckLabwareRequest(name="rack_1", catalog_ref=PLATE, at="D4-slot")
        )
        await driver.add_deck_labware(
            AddDeckLabwareRequest(name="rack_2", catalog_ref=PLATE, at="B2-slot")
        )
        assert "rack_1" in [i.name for i in (await driver.get_deck_state(GetDeckStateRequest())).labware]

        await driver.remove_deck_labware(RemoveDeckLabwareRequest(name="rack_1"))
        await driver.move_plate(
            MovePlateRequest(plate="rack_2", from_position="B2-slot", to_position="D4-slot")
        )

        assert _occupant_name(driver, "D4") == "rack_2"
