"""The labware a gripper takes when no plate is on record: its default grip."""

from collections.abc import Callable
from dataclasses import dataclass

from cheshire_drivers._plr_compat import ensure_plr_stubs

ensure_plr_stubs()

from pylabrobot.resources import Plate, corning_96_wellplate_360ul_flat

from cheshire_drivers.liquid_handler_models import DEFAULT_GRIP_LABWARE


@dataclass(frozen=True)
class StandInLabware:
    """What a gripper carries when no plate is on record: the name it has on the driver's deck, and the plate it is gripped as."""

    name: str
    plate: Callable[[str], Plate]


DEFAULT_STAND_IN = StandInLabware(name=DEFAULT_GRIP_LABWARE, plate=corning_96_wellplate_360ul_flat)


def default_grip_labware(stand_in: StandInLabware) -> Plate:
    """The stand-in's plate, carried for one move and taken off the deck after it, so the deck record gains nothing."""
    return stand_in.plate(stand_in.name)


class GripsAStandIn:
    """A driver whose own gripper moves a stand-in when no plate is on record."""

    # Not public: `derive_capabilities` reads a driver's public members as vendor commands.
    _stand_in: StandInLabware = DEFAULT_STAND_IN
