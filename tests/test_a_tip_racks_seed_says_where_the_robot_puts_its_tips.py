"""A tip rack's catalog row says where the robot puts its tips, from the definition the robot reads."""

import pytest

from cheshire_drivers.labware_seed import TipRackSeedEntry, load_labware_seed
from cheshire_drivers.plr.opentrons_flex import _create_resource
from cheshire_drivers.plr.opentrons_tip_racks import TIP_TOP_MM, opentrons_load_name
from cheshire_drivers.liquid_handler_models import DeckResourceConfig


def _racks() -> dict[str, TipRackSeedEntry]:
    return {e.labware_type: e for e in load_labware_seed() if isinstance(e, TipRackSeedEntry)}


def test_a_flex_rack_the_robot_loads_by_name_has_its_tips_at_opentrons_rack_top() -> None:
    assert _racks()["flex_96_tiprack_200ul"].tip_top_z == 99.0


def test_a_rack_the_robot_reads_from_plr_geometry_names_no_tip_top() -> None:
    hamilton = [e for name, e in _racks().items() if name.startswith("hamilton_")]
    assert hamilton and all(e.tip_top_z is None for e in hamilton)


@pytest.mark.parametrize("labware_type", sorted(_racks()))
def test_every_rack_the_live_flex_loads_by_name_has_its_tip_top_in_the_seed(labware_type: str) -> None:
    # The live Flex decides the load name in _create_resource; the seed must read the same one.
    resource = _create_resource(DeckResourceConfig(name="rack", catalog_ref=labware_type))
    load_name = opentrons_load_name(resource, labware_type)
    assert getattr(resource, "ot_load_name", None) == load_name
    if load_name is None:
        assert _racks()[labware_type].tip_top_z is None
    else:
        assert load_name in TIP_TOP_MM, f"{load_name} is loaded by name but has no tip top"
        assert _racks()[labware_type].tip_top_z == TIP_TOP_MM[load_name]
