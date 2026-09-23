"""Install the trackers a freshly placed or freshly seeded labware should carry.

Placement defaults every cavity to audit-only volume tracking, because PLR's
stock tracker rejects the first aspirate from a deck nobody declared volumes for.
Seeding then swaps the declared cavities back: strict enforces physical bounds,
replenishing pins a reagent source that never drains.

Tip spots get the same treatment for the same reason. A spot's belief about
whether it holds a tip came from what we projected onto the driver, so refusing
a pick on the strength of it is our own guess checking itself, and a wrong guess
stops the run at the instrument instead of at a surface an operator can reach.

Both liquid-handler adapters seed through these, so the two report the same
state for the same declared deck.
"""

from typing import TypeGuard

from cheshire_drivers._plr_compat import ensure_plr_stubs

ensure_plr_stubs()

from pylabrobot.resources.container import Container as PLRContainer
from pylabrobot.resources.plate import Plate as PLRPlate
from pylabrobot.resources.resource import Resource as PLRResource
from pylabrobot.resources.trash import Trash as PLRTrash
from pylabrobot.resources.volume_tracker import VolumeTracker as PLRVolumeTracker
from pylabrobot.resources.tip_rack import TipRack as PLRTipRack, TipSpot as PLRTipSpot
from pylabrobot.resources.well import Well as PLRWell

from cheshire_drivers.plr_tip_tracker import LenientTipSpotTracker
from cheshire_drivers.plr_volume_tracker import (
    LenientVolumeTracker,
    ReplenishingVolumeTracker,
)


def is_standalone_container(resource: PLRResource) -> TypeGuard[PLRContainer]:
    """A single-cavity labware pool. Wells and the trash bin are Containers too."""
    return isinstance(resource, PLRContainer) and not isinstance(resource, (PLRWell, PLRTrash))


def swap_to_lenient_tracker(well: PLRWell) -> None:
    """Replace ``well.tracker`` with a LenientVolumeTracker, keeping its callback.

    Load-bearing private-attribute access: PLR's ``VolumeTracker`` stores ONE
    callback (overwrite semantics), and that one is always ``Well._state_updated``,
    set in ``Well.__init__`` and nowhere else. If PLR moves to a callback list this
    swap silently loses the others and downstream state updates stop firing.
    """
    new_tracker = LenientVolumeTracker(
        thing=well.tracker.thing, max_volume=well.tracker.max_volume,
    )
    new_tracker.register_callback(well._state_updated)
    well.tracker = new_tracker


def swap_to_strict_tracker(well: PLRWell, initial_volume: float) -> None:
    """Replace ``well.tracker`` with PLR's stock tracker at ``initial_volume``.

    Ops on a seeded well raise on physical-bound violations. See
    ``swap_to_lenient_tracker`` for the callback-wiring caveat.
    """
    new_tracker = PLRVolumeTracker(
        thing=well.tracker.thing,
        max_volume=well.tracker.max_volume,
        initial_volume=initial_volume,
    )
    new_tracker.register_callback(well._state_updated)
    well.tracker = new_tracker


def swap_to_replenishing_tracker(well: PLRWell, initial_volume: float) -> None:
    """Replace ``well.tracker`` with a ReplenishingVolumeTracker at ``initial_volume``.

    A reagent source keeps its level and never underflows. See
    ``swap_to_lenient_tracker`` for the callback-wiring caveat.
    """
    new_tracker = ReplenishingVolumeTracker(
        thing=well.tracker.thing,
        max_volume=well.tracker.max_volume,
        initial_volume=initial_volume,
    )
    new_tracker.register_callback(well._state_updated)
    well.tracker = new_tracker


def swap_container_to_lenient(container: PLRContainer) -> None:
    """Replace a standalone container's tracker with a LenientVolumeTracker.

    A bare Container registers no ``_state_updated`` callback (only ``Well`` does),
    so this is a plain attribute replacement.
    """
    container.tracker = LenientVolumeTracker(
        thing=container.tracker.thing, max_volume=container.tracker.max_volume,
    )


def swap_container_to_strict(container: PLRContainer, initial_volume: float) -> None:
    """Replace a standalone container's tracker with PLR's stock tracker at ``initial_volume``."""
    container.tracker = PLRVolumeTracker(
        thing=container.tracker.thing,
        max_volume=container.tracker.max_volume,
        initial_volume=initial_volume,
    )


def swap_container_to_replenishing(container: PLRContainer, initial_volume: float) -> None:
    """Replace a standalone container's tracker with a ReplenishingVolumeTracker."""
    container.tracker = ReplenishingVolumeTracker(
        thing=container.tracker.thing,
        max_volume=container.tracker.max_volume,
        initial_volume=initial_volume,
    )


def swap_tip_spot_to_lenient(spot: PLRTipSpot) -> None:
    """Replace a tip spot's tracker with one that records instead of refusing.

    Keeps whatever tip the spot currently believes it holds, so this is a change
    of enforcement and not of state. See ``swap_to_lenient_tracker`` for the
    callback-wiring caveat; a TipSpot registers ``_state_updated`` the same way.
    """
    old = spot.tracker
    new_tracker = LenientTipSpotTracker(thing=old.thing, make_tip=spot.make_tip)
    if old.has_tip:
        new_tracker.add_tip(old.get_tip(), origin=spot)
    new_tracker.register_callback(spot._state_updated)
    spot.tracker = new_tracker


def swap_to_lenient(resource: PLRResource) -> None:
    """Default a freshly placed labware to audit-only tracking.

    Unseeded strict volume trackers reject the first aspirate, which is the wrong
    shape for a deck orca has not declared volumes for; seeding swaps those back
    to strict. Tip spots have no strict counterpart to swap back to: the ledger
    is what says which spots hold tips, and a spot refusing on its own copy of
    that is the driver enforcing our guess back at us.
    """
    if isinstance(resource, PLRPlate):
        for well in resource.get_all_items():
            swap_to_lenient_tracker(well)
    elif isinstance(resource, PLRTipRack):
        for spot in resource.get_all_items():
            swap_tip_spot_to_lenient(spot)
    elif is_standalone_container(resource):
        swap_container_to_lenient(resource)
