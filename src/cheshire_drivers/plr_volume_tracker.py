"""Lenient volume tracker for cumulative-delta audit without enforcement.

PyLabRobot's stock `VolumeTracker` raises `TooLittleLiquidError` when an
aspirate would underflow a well, and `TooLittleVolumeError` when a
dispense would overflow it. That enforcement is correct for real
hardware (operators have entered initial volumes and want safety
guards), but it falls apart for sim-only deployments where operators
have not bothered to seed initial volumes for every plate on the deck.

This subclass disables the two enforcement checks while preserving the
rest of `VolumeTracker`'s behavior: pending/committed accumulation,
`commit()` / `rollback()`, `set_volume()`, `disable()` / `enable()`,
callbacks, serialization, `is_disabled` semantics. The cumulative
`pending_volume` (returned by `get_used_volume()`) then becomes a net
movement counter: aspirate makes it more negative, dispense makes it
more positive, both with no clamping. A well seeded with 0 will go
negative on aspirate; a well dispensed into beyond `max_volume` will
exceed its physical capacity. Both are valid AUDIT values.

This is per-well, not per-process: the wrapper installs this subclass
only on wells the operator did not explicitly seed. Seeded volumes ride
in with the labware, as `DeckResourceConfig.well_state` on
`reconcile_deck_occupancy` / `add_deck_labware` or as the labware
template's initial state; a deck layout declares carriers, not labware,
so it is not where a well volume comes from. Seeded wells keep the stock
`VolumeTracker` (initialised to the seeded value) so subsequent
operations on those wells still raise on physical-bound violations.
That gives operators both modes simultaneously: strict where they care
to declare it, lenient (audit-only) everywhere else.

Layer note: this module lives at the same level as `plr_wrappers.py`,
NOT under `cheshire_drivers/plr/`. The `plr/` package is the leaf
namespace for concrete drivers that USE `plr_wrappers.py`, so anything
that `plr_wrappers.py` itself depends on must sit at or above its
layer. Locating `LenientVolumeTracker` here lets `plr_wrappers.py`
import it at module scope without triggering the
`plr/__init__.py` -> `plr/sealer.py` -> `plr_wrappers.py` cycle.
"""

from cheshire_drivers._plr_compat import ensure_plr_stubs

ensure_plr_stubs()

from pylabrobot.resources.volume_tracker import VolumeTracker


class LenientVolumeTracker(VolumeTracker):
    """Volume tracker that accumulates deltas without raising on bounds.

    See module docstring for motivation. The only difference from
    `VolumeTracker` is that `remove_liquid` and `add_liquid` skip the
    pre-flight TooLittleLiquidError / TooLittleVolumeError checks.
    """

    def remove_liquid(self, volume: float) -> None:
        self.pending_volume -= volume
        if self._callback is not None:
            self._callback()

    def add_liquid(self, volume: float) -> None:
        self.pending_volume += volume
        if self._callback is not None:
            self._callback()


class ReplenishingVolumeTracker(VolumeTracker):
    """Bottomless reagent source for sim: level stays at its seeded fill.

    Models an operator-replenished reagent reservoir. Seeded to `initial_volume`,
    it never moves: `remove_liquid` does not lower it (an aspirate can never
    underflow) and `add_liquid` does not raise (a mix dispense-back or top-up
    can never overflow), so `get_used_volume()` keeps reading the seeded level.
    Installed only for labware the caller flags replenished; real hardware runs
    the actual finite reagent and never installs this tracker.
    """

    def remove_liquid(self, volume: float) -> None:
        if self._callback is not None:
            self._callback()

    def add_liquid(self, volume: float) -> None:
        if self._callback is not None:
            self._callback()
