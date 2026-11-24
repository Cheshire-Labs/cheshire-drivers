from cheshire_drivers.deck_site_units import DeckSlotUnit, RailCarrierUnit, deck_site_units
from cheshire_drivers.device_sites import DEFAULT_SITE
from cheshire_drivers.labware_seed import get_carrier_site_identifiers
from cheshire_drivers.liquid_handler_models import DeckLayoutConfig, DeckResourceConfig
from cheshire_drivers.plr_wrappers import slot_names_for_deck_type


def _carrier(name: str, rail: int) -> DeckResourceConfig:
    return DeckResourceConfig(name=name, catalog_ref="PLT_CAR_L5AC_A00", rail=rail)


def test_rail_carrier_unit_takes_its_sites_from_the_carrier_catalog() -> None:
    unit = RailCarrierUnit(_carrier("carrier-7", 7))
    assert unit.name == "carrier-7"
    assert list(unit.site_identifiers()) == list(get_carrier_site_identifiers("PLT_CAR_L5AC_A00"))


def test_deck_slot_unit_has_one_default_site() -> None:
    unit = DeckSlotUnit("C2")
    assert unit.name == "C2"
    assert list(unit.site_identifiers()) == [DEFAULT_SITE]


def test_a_layout_with_rail_carriers_yields_one_unit_per_carrier() -> None:
    layout = DeckLayoutConfig(deck_type="STARlet", resources=[_carrier("a", 3), _carrier("b", 9)])
    units = list(deck_site_units(layout))
    assert [type(u) for u in units] == [RailCarrierUnit, RailCarrierUnit]
    assert [u.name for u in units] == ["a", "b"]


def test_a_slot_deck_without_carriers_yields_its_built_in_slots() -> None:
    layout = DeckLayoutConfig(deck_type="FlexDeck", resources=[])
    expected = slot_names_for_deck_type("FlexDeck")
    assert expected
    units = list(deck_site_units(layout))
    assert [type(u) for u in units] == [DeckSlotUnit] * len(expected)
    assert [u.name for u in units] == expected


def test_a_rail_deck_without_carriers_yields_no_units() -> None:
    assert list(deck_site_units(DeckLayoutConfig(deck_type="STARlet", resources=[]))) == []
