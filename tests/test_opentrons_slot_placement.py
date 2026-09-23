from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends.chatterbox import LiquidHandlerChatterboxBackend
from pylabrobot.resources import Resource
from pylabrobot.resources.opentrons import FlexDeck, OTDeck

from cheshire_drivers import DeckResourceConfig
from cheshire_drivers.plr_wrappers import (
    PLRLiquidHandlerWrapper,
    _deck_factories,
    slot_names_for_deck_type,
)


def test_flex_and_ot2_decks_registered() -> None:
    factories = _deck_factories()
    assert "FlexDeck" in factories
    assert "OTDeck" in factories


def test_flex_slot_names_are_the_named_slots_minus_trash() -> None:
    # 12 base slots minus the A3 trash, plus the 4 staging pads: the pads are
    # the arm handoff lane, so routing must be able to address them as sites.
    slots = slot_names_for_deck_type("FlexDeck")
    assert slots is not None
    assert len(slots) == 15
    assert "C2" in slots
    assert "A3" not in slots
    for staging in ("A4", "B4", "C4", "D4"):
        assert staging in slots


def test_ot2_slot_names_are_1_based_strings_minus_trash() -> None:
    """The OT-2 deck keys slots by 1-based integer; they surface as strings "1".."11" through the
    same helper the Flex uses, with slot 12 (trash) excluded -- one model, both Opentrons decks."""
    slots = slot_names_for_deck_type("OTDeck")
    assert slots is not None
    assert len(slots) == 11
    assert slots[0] == "1"
    assert "7" in slots
    assert "12" not in slots


def test_rail_and_unknown_deck_types_have_no_slots() -> None:
    assert slot_names_for_deck_type("STARlet") is None
    assert slot_names_for_deck_type("nope") is None


def _wrapper_on_deck(deck: Resource) -> PLRLiquidHandlerWrapper:
    lh = LiquidHandler(backend=LiquidHandlerChatterboxBackend(num_channels=1), deck=deck)
    wrapper = PLRLiquidHandlerWrapper(LiquidHandlerChatterboxBackend(num_channels=1))
    wrapper._lh = lh
    return wrapper


def test_place_resource_puts_labware_in_the_named_flex_slot() -> None:
    """parent_id names a Flex slot, so _place_resource routes to the deck's slot holder rather than
    the carrier path (which fails, since a Flex slot is not a Carrier)."""
    deck = FlexDeck()
    wrapper = _wrapper_on_deck(deck)
    plate = Resource(name="plate", size_x=127.0, size_y=85.0, size_z=14.0)

    wrapper._place_resource(
        plate,
        DeckResourceConfig(name="plate", catalog_ref="ignored", parent_id="C2", site_index=0),
    )

    assert deck.slots["C2"] is plate


def test_non_numeric_ot2_slot_name_raises_a_clear_error() -> None:
    """The interface keeps slots as strings; only the OT-2 impl converts to int. A non-numeric name
    gives a helpful message rather than a raw 'invalid literal for int()'."""
    import pytest

    deck = OTDeck()
    wrapper = _wrapper_on_deck(deck)
    plate = Resource(name="plate", size_x=127.0, size_y=85.0, size_z=14.0)

    with pytest.raises(ValueError, match="OT-2 slot must be a 1-based integer name"):
        wrapper._place_resource(
            plate,
            DeckResourceConfig(name="plate", catalog_ref="ignored", parent_id="C2", site_index=0),
        )


def test_place_resource_puts_labware_in_the_named_ot2_slot() -> None:
    """parent_id "7" is an OT-2 slot: the same placement path converts the string name to the deck's
    1-based integer slot, so the Flex and OT-2 share one _place_resource arm."""
    deck = OTDeck()
    wrapper = _wrapper_on_deck(deck)
    plate = Resource(name="plate", size_x=127.0, size_y=85.0, size_z=14.0)

    wrapper._place_resource(
        plate,
        DeckResourceConfig(name="plate", catalog_ref="ignored", parent_id="7", site_index=0),
    )

    assert deck.slots[6] is plate
