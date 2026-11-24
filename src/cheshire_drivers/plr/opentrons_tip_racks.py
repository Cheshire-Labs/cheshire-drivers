"""Where a tip rack the Flex loads by its Opentrons name puts its tips: the robot reads Opentrons' definition, not PLR's geometry."""

from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip_rack import TipRack

OPENTRONS_PREFIX = "opentrons_"

# Opentrons shared-data/labware/definitions/2/<load name>/1.json at b3c5c90: well A1's z plus its depth, the top
# of a seated tip above the rack's base, in mm. Version 1 is the revision a Flex loads a tip rack by.
TIP_TOP_MM: dict[str, float] = {
    "opentrons_flex_96_filtertiprack_50ul": 99.0,
    "opentrons_flex_96_tiprack_1000ul": 99.0,
    "opentrons_flex_96_tiprack_200ul": 99.0,
    "opentrons_flex_96_tiprack_50ul": 99.0,
    "opentrons_96_filtertiprack_1000ul": 97.47,
    "opentrons_96_filtertiprack_10ul": 64.69,
    "opentrons_96_filtertiprack_200ul": 64.69,
    "opentrons_96_filtertiprack_20ul": 64.69,
    "opentrons_96_tiprack_1000ul": 97.47,
    "opentrons_96_tiprack_10ul": 64.69,
    "opentrons_96_tiprack_20ul": 64.69,
    "opentrons_96_tiprack_300ul": 64.69,
}


def opentrons_load_name(rack: Resource, catalog_ref: str) -> str | None:
    """The Opentrons name a Flex loads this rack by: the one it declares, else a catalog ref that is one."""
    declared = getattr(rack, "ot_load_name", None)
    if isinstance(declared, str):
        return declared
    return catalog_ref if catalog_ref.startswith(OPENTRONS_PREFIX) else None


def opentrons_tip_top_mm(rack: TipRack, catalog_ref: str) -> float | None:
    """The tip top Opentrons' definition gives, for a rack the Flex loads by name; None for one it builds from PLR."""
    name = opentrons_load_name(rack, catalog_ref)
    return None if name is None else TIP_TOP_MM.get(name)
