"""What get_deck_state calls a deck occupant, on both deck shapes.

``DeckStateResponse.labware`` describes what sits on the deck: carriers and the
labware on their sites. A PLR deck registers its descendants recursively, so
reading its registry straight back hands out every well and tip spot inside that
labware too, which describe no deck position and swamp the answer (a Flex
holding two labware reported 207 entries). The two shapes are pinned together
because the filter has to drop the items inside labware without dropping the
Hamilton carriers, which sit on the deck in their own right.
"""

import pytest

from cheshire_drivers.liquid_handler_models import (
    AddDeckLabwareRequest,
    DeckLayoutConfig,
    DeckResourceConfig,
    GetDeckStateRequest,
    ReconcileDeckOccupancyRequest,
)
from cheshire_drivers.plr.liquid_handler import ChatterboxLiquidHandlerDriver

PLATE = "Cor_96_wellplate_360ul_Fb"
TIP_RACK = "flex_96_tiprack_50ul"


@pytest.mark.asyncio
async def test_a_slot_deck_reports_its_labware_and_not_their_wells_or_tip_spots() -> None:
    driver = ChatterboxLiquidHandlerDriver()
    await driver.configure_deck(DeckLayoutConfig(deck_type="FlexDeck", resources=[]))
    await driver.initialize()
    await driver.add_deck_labware(
        AddDeckLabwareRequest(name="plate_1", catalog_ref=PLATE, at="C1-slot")
    )
    await driver.add_deck_labware(
        AddDeckLabwareRequest(name="tips_1", catalog_ref=TIP_RACK, at="C2-slot")
    )

    state = await driver.get_deck_state(GetDeckStateRequest())

    assert {item.name for item in state.labware} == {"trash", "plate_1", "tips_1"}
    assert {item.type for item in state.labware}.isdisjoint({"Well", "TipSpot"})
    assert [(r.name, r.tips_remaining, r.total_tips) for r in state.tip_racks] == [
        ("tips_1", 96, 96)
    ]


@pytest.mark.asyncio
async def test_a_rail_deck_keeps_its_carriers_and_the_labware_on_them() -> None:
    driver = ChatterboxLiquidHandlerDriver()
    await driver.configure_deck(
        DeckLayoutConfig(
            deck_type="STARLet",
            resources=[
                DeckResourceConfig(name="plt_car", catalog_ref="PLT_CAR_L5AC_A00", rail=25)
            ],
        )
    )
    await driver.reconcile_deck_occupancy(
        ReconcileDeckOccupancyRequest(
            resources=[
                DeckResourceConfig(
                    name="plate_1", catalog_ref=PLATE, parent_id="plt_car", site_index=0
                )
            ]
        )
    )

    state = await driver.get_deck_state(GetDeckStateRequest())

    assert {item.name for item in state.labware} == {
        "trash_core96",
        "waste_block",
        "trash",
        "plt_car",
        "plate_1",
    }
    assert {item.type for item in state.labware}.isdisjoint({"Well", "TipSpot"})
