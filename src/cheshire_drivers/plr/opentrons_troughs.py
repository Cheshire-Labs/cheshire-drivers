"""Opentrons reservoirs, defined here because PyLabRobot carries none.

PLR's Opentrons support covers plates, tip racks and tube racks; reservoirs
have no factory upstream, so a deployment that names one gets no geometry at
all. Dimensions come from the vendor definition shipped in the Opentrons
repository under shared-data/labware/definitions/2.

Each one declares ``ot_load_name``, so on a Flex the robot loads its OWN
definition and the numbers here stay nominal. They still have to be right:
``material_z_thickness`` is what an Opentrons definition anchors liquid ops
to when one has to be built from geometry, and the footprint is what the
multi-channel bounds check reads before it lets a nozzle array over the
cavity.
"""

from pylabrobot.resources.trough import Trough, TroughBottomType


def _opentrons_reservoir(
    name: str,
    load_name: str,
    version: int,
    size_z: float,
    cavity_floor_z: float,
    cavity_x: float,
    cavity_y: float,
    max_volume: float,
) -> Trough:
    """One SBS-footprint, flat-bottom, single-cavity Opentrons reservoir."""
    cavity_area = cavity_x * cavity_y
    reservoir = Trough(
        name=name,
        size_x=127.76,
        size_y=85.48,
        size_z=size_z,
        material_z_thickness=cavity_floor_z,
        max_volume=max_volume,
        model=load_name,
        bottom_type=TroughBottomType.FLAT,
        compute_height_from_volume=lambda volume: volume / cavity_area,
        compute_volume_from_height=lambda height: height * cavity_area,
    )
    reservoir.ot_load_name = load_name
    reservoir.ot_version = version
    return reservoir


def axygen_1_reservoir_90ml(name: str) -> Trough:
    """Axygen 1 Well Reservoir 90 mL, Opentrons load name axygen_1_reservoir_90ml.

    The shallowest of the three: a 19.15 mm body, which is the easiest way to
    tell it apart from the NEST and Agilent reservoirs on a bench.
    """
    return _opentrons_reservoir(
        name,
        load_name="axygen_1_reservoir_90ml",
        version=3,
        size_z=19.15,
        cavity_floor_z=6.73,
        cavity_x=106.8,
        cavity_y=70.5,
        max_volume=90_000,  # units: uL
    )


def nest_1_reservoir_195ml(name: str) -> Trough:
    """NEST 1 Well Reservoir 195 mL, Opentrons load name nest_1_reservoir_195ml.

    31.4 mm body, 26.85 mm deep cavity.
    """
    return _opentrons_reservoir(
        name,
        load_name="nest_1_reservoir_195ml",
        version=5,
        size_z=31.4,
        cavity_floor_z=4.55,
        cavity_x=107.3,
        cavity_y=71.3,
        max_volume=195_000,  # units: uL
    )


def agilent_1_reservoir_290ml(name: str) -> Trough:
    """Agilent 1 Well Reservoir 290 mL, Opentrons load name agilent_1_reservoir_290ml.

    The tallest of the three: a 44.04 mm body.
    """
    return _opentrons_reservoir(
        name,
        load_name="agilent_1_reservoir_290ml",
        version=5,
        size_z=44.04,
        cavity_floor_z=4.81,
        cavity_x=107.5,
        cavity_y=71.25,
        max_volume=290_000,  # units: uL
    )
