"""Tip trackers that record what happened instead of refusing it.

PyLabRobot's stock `TipTracker` raises `HasTipError` when a tip arrives somewhere
it believes is occupied, and `NoTipError` when one leaves somewhere it believes
is bare. Those beliefs came from what we projected onto the driver, so enforcing
against them is our own guess checking itself, and a wrong guess stops the run at
the instrument rather than at a surface an operator can reach.

`disable()` is not the way out: a disabled stock tracker raises `RuntimeError`
from the same calls, so it refuses harder. These subclasses keep every tracker
mechanic -- pending/committed state, commit/rollback, callbacks, serialization --
and drop only the refusals that are ours.

**Not every raise here is ours, and the two roles differ.**

- A tip *leaving* somewhere the tracker thought was bare is always our belief
  being wrong. The tip physically moved. Both roles record it.
- A tip *arriving* somewhere the tracker thinks is occupied means different
  things per role. On a **channel** it is a stale belief: if the channel really
  held a tip the pick would have failed physically, so the new tip replaces the
  old belief. On a **tip spot** it is a collision -- two tips cannot occupy one
  spot -- so the spot keeps refusing, and a partial column return still rolls
  back and reports the spots it started with.

What both still refuse: reading a tip off a channel that has never held one.
There is no honest answer, and inventing a tip to satisfy the read would be
manufacturing the measurement this exists to stop. The ledger is what should
answer that; until it models mounted tips, refusing and putting the channel in
front of an operator is the right outcome.

Sits beside `plr_volume_tracker.py`, at the same layer and for the same reason:
`plr_wrappers.py` imports it at module scope and must not reach into the `plr/`
leaf package.
"""

from typing import Callable, Optional

from cheshire_drivers._plr_compat import ensure_plr_stubs

ensure_plr_stubs()

from pylabrobot.resources.errors import HasTipError, NoTipError
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipSpot
from pylabrobot.resources.tip_tracker import TipTracker


class LenientTipTracker(TipTracker):
    """A channel's tip tracker: records pickups and drops, refuses neither."""

    def __init__(self, thing: str) -> None:
        super().__init__(thing=thing)
        self._last_known_tip: Optional[Tip] = None

    def add_tip(
        self, tip: Tip, origin: Optional[TipSpot] = None, commit: bool = True,
    ) -> None:
        """Take the tip, whatever this channel believed it was already holding.

        The pick physically happened. Had the channel really been loaded it
        would have failed at the instrument, so a belief that says otherwise is
        stale and is replaced.
        """
        self._pending_tip = tip
        self._tip_origin = origin
        self._last_known_tip = tip
        if commit:
            self.commit()

    def remove_tip(self, commit: bool = False) -> None:
        """Let the tip go, even from somewhere this tracker thought was bare."""
        if self._pending_tip is not None:
            self._last_known_tip = self._pending_tip
        self._pending_tip = None
        if commit:
            self.commit()

    def commit(self) -> None:
        """Once a removal lands, the channel is bare and stays bare.

        The fallback below only has to survive until here: PyLabRobot reads the
        tip to build the drop and clears the channel afterwards. Letting it
        outlive the commit meant a second drop on the same channel succeeded and
        put the SAME tip object into another spot, so two spots shared one
        volume tracker and a rack reported a tip nobody put there.
        """
        super().commit()
        if self._tip is None:
            self._last_known_tip = None

    def get_tip(self) -> Tip:
        """The tip here, or the one being taken off it right now.

        PyLabRobot reads the tip off the channel to build the drop operation,
        before clearing the channel. Answering with the tip this channel really
        held is an observation, not a default, and it expires when the removal
        commits.
        """
        if self._tip is not None:
            return self._tip
        if self._last_known_tip is not None:
            return self._last_known_tip
        raise NoTipError(
            f"{self.thing} has never held a tip, so what it carries is unknown. "
            f"Say what is mounted rather than guessing."
        )

    def rollback(self) -> None:
        self._pending_tip = self._tip


class LenientTipSpotTracker(LenientTipTracker):
    """A tip spot's tracker: records a pick it did not expect, refuses a drop
    onto a spot already holding a tip, because that one is a collision and not
    a stale belief.

    Takes a factory for the spot's own tip. A spot the record thinks is empty
    still has to answer what a pick from it yields, and it must be a NEW tip: the
    channel fallback answers with the last tip this thing held, which for a spot
    is a tip already mounted somewhere. Handing it out twice makes two channels
    share one Tip and therefore one volume tracker, and the second pick silently
    wipes the first channel's liquid. Tip geometry is the spot's declaration,
    not a claim about what is in it, so minting one invents nothing.
    """

    def __init__(self, thing: str, make_tip: Callable[[], Tip]) -> None:
        super().__init__(thing=thing)
        self._make_tip = make_tip

    def add_tip(
        self, tip: Tip, origin: Optional[TipSpot] = None, commit: bool = True,
    ) -> None:
        if self._pending_tip is not None:
            raise HasTipError(f"{self.thing} already has a tip.")
        super().add_tip(tip, origin=origin, commit=commit)

    def get_tip(self) -> Tip:
        if self._tip is not None:
            return self._tip
        return self._make_tip()
