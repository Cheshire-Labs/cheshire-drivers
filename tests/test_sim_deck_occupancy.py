"""The sim liquid handler models its deck, so a UI can be driven without hardware.

The sim used to log every deck op and report an empty deck forever. Anything
driving it (the diagnostics UI, a demo, a test) saw labware "placed"
successfully and then a deck with nothing on it, and could place onto the same
site over and over because nothing was tracked. The real driver rejects that:
PyLabRobot's ``assign_child_at_slot`` raises when a slot is occupied.
"""

import pytest

from cheshire_drivers.liquid_handler_models import (
    AddDeckLabwareRequest,
    DeckLayoutConfig,
    DeckResourceConfig,
    GetDeckStateRequest,
    ReconcileDeckOccupancyRequest,
    RemoveDeckLabwareRequest,
    ResetDeckLabwareRequest,
)
from cheshire_drivers.sims import SimLiquidHandlerDriver


def _driver() -> SimLiquidHandlerDriver:
    return SimLiquidHandlerDriver("lh_sim")


async def _state(driver: SimLiquidHandlerDriver):
    return await driver.get_deck_state(GetDeckStateRequest())


class TestPlacement:
    @pytest.mark.asyncio
    async def test_placed_labware_shows_up_on_the_deck(self) -> None:
        driver = _driver()

        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="C2-slot",
        ))

        state = await _state(driver)
        assert [(i.name, i.site) for i in state.labware] == [("plate_1", "C2-slot")]

    @pytest.mark.asyncio
    async def test_a_second_labware_cannot_take_an_occupied_site(self) -> None:
        driver = _driver()
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="C2-slot",
        ))

        with pytest.raises(ValueError, match="already occupied"):
            await driver.add_deck_labware(AddDeckLabwareRequest(
                name="plate_2", catalog_ref="Cor_96_wellplate_360ul_Fb", at="C2-slot",
            ))

        assert len((await _state(driver)).labware) == 1

    @pytest.mark.asyncio
    async def test_a_name_cannot_be_placed_twice(self) -> None:
        """Two deck items with one name would make every name-addressed command ambiguous."""
        driver = _driver()
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="C2-slot",
        ))

        with pytest.raises(ValueError, match="already on the deck"):
            await driver.add_deck_labware(AddDeckLabwareRequest(
                name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="D1-slot",
            ))

    @pytest.mark.asyncio
    async def test_removing_frees_the_site(self) -> None:
        driver = _driver()
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="C2-slot",
        ))

        await driver.remove_deck_labware(RemoveDeckLabwareRequest(name="plate_1"))

        assert (await _state(driver)).labware == []
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="plate_2", catalog_ref="Cor_96_wellplate_360ul_Fb", at="C2-slot",
        ))
        assert [i.name for i in (await _state(driver)).labware] == ["plate_2"]


class TestTipRacks:
    @pytest.mark.asyncio
    async def test_a_placed_tip_rack_reports_full_inventory(self) -> None:
        """A UI shows tip counts; a rack that reports none reads as unusable."""
        driver = _driver()

        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="tips_1", catalog_ref="flex_96_tiprack_50ul", at="C1-slot",
        ))

        state = await _state(driver)
        assert [(r.name, r.tips_remaining, r.total_tips) for r in state.tip_racks] == [
            ("tips_1", 96, 96)
        ]


class TestWholesaleOps:
    @pytest.mark.asyncio
    async def test_reset_clears_the_deck(self) -> None:
        driver = _driver()
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="C2-slot",
        ))

        await driver.reset_deck_labware(ResetDeckLabwareRequest())

        assert (await _state(driver)).labware == []

    @pytest.mark.asyncio
    async def test_reconcile_replaces_what_is_there(self) -> None:
        driver = _driver()
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="old_plate", catalog_ref="Cor_96_wellplate_360ul_Fb", at="C2-slot",
        ))

        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[
            DeckResourceConfig(
                name="new_plate", catalog_ref="Cor_96_wellplate_360ul_Fb",
                parent_id="D1", site_index=0,
            ),
        ]))

        state = await _state(driver)
        assert [(i.name, i.site) for i in state.labware] == [("new_plate", "D1-slot")]

    @pytest.mark.asyncio
    async def test_configuring_a_deck_starts_it_empty(self) -> None:
        driver = _driver()
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="C2-slot",
        ))

        await driver.configure_deck(DeckLayoutConfig(deck_type="FlexDeck", resources=[]))

        assert (await _state(driver)).labware == []
