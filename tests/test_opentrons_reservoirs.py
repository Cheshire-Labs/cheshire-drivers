"""Opentrons reservoirs load the robot's own definition, and build a correct
one if they ever have to.

The first cut of these carried neither `ot_load_name` nor
`material_z_thickness`, so a Flex could not load them at all: with no load
name the driver builds a definition from geometry, and that build refuses a
container whose cavity floor height is undeclared. PURE_SIM never builds one,
which is why it looked fine.
"""

import pytest
from pylabrobot.opentrons.flex import OpentronsFlex

from cheshire_drivers.plr.opentrons_troughs import (
    agilent_1_reservoir_290ml,
    axygen_1_reservoir_90ml,
    nest_1_reservoir_195ml,
)


# (factory, load name, version, body height, cavity floor, cavity depth, uL)
RESERVOIRS = [
    (axygen_1_reservoir_90ml, "axygen_1_reservoir_90ml", 3, 19.15, 6.73, 12.42, 90_000),
    (nest_1_reservoir_195ml, "nest_1_reservoir_195ml", 5, 31.4, 4.55, 26.85, 195_000),
    (agilent_1_reservoir_290ml, "agilent_1_reservoir_290ml", 5, 44.04, 4.81, 39.23, 290_000),
]


@pytest.mark.parametrize(
    "factory,load_name,version,height,floor_z,depth,volume",
    RESERVOIRS,
    ids=[r[1] for r in RESERVOIRS],
)
def test_reservoir_matches_the_vendor_definition(
    factory, load_name, version, height, floor_z, depth, volume,
) -> None:
    """The load name and revision the robot resolves, and geometry that agrees
    with the definition it will resolve them to."""
    reservoir = factory("r")

    assert reservoir.ot_load_name == load_name
    assert reservoir.ot_version == version
    assert reservoir.get_size_z() == pytest.approx(height)
    assert reservoir.max_volume == pytest.approx(volume)

    # Every Opentrons reservoir is SBS; the multi-channel bounds check reads
    # this to decide whether a nozzle array fits over the cavity.
    assert reservoir.get_size_x() == pytest.approx(127.76)
    assert reservoir.get_size_y() == pytest.approx(85.48, abs=0.02)

    built = OpentronsFlex._build_labware_definition(reservoir)
    well = built["wells"]["A1"]
    assert well["z"] == pytest.approx(floor_z)
    assert well["depth"] == pytest.approx(depth)


@pytest.mark.parametrize("factory", [r[0] for r in RESERVOIRS], ids=[r[1] for r in RESERVOIRS])
def test_a_full_reservoir_fills_its_cavity(factory) -> None:
    """Volume and height convert through the cavity opening, so a full vessel
    reads as a liquid column inside the cavity rather than above the rim."""
    reservoir = factory("r")
    depth = reservoir.get_size_z() - reservoir.material_z_thickness
    full_height = reservoir.compute_height_from_volume(reservoir.max_volume)
    assert 0 < full_height <= depth
