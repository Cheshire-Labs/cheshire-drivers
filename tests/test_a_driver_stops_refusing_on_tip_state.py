"""PyLabRobot's tip trackers stop refusing operations on state we gave them.

A driver's tip numbers came from what we projected onto it. Enforcing against
them is our own guess checking itself and reporting back as independent
evidence, and when the guess is wrong it stops a run at the instrument instead
of at a surface where somebody can fix it.

Wells were given this treatment long ago -- `LenientVolumeTracker` strips the
two bound raises and keeps the accounting. Tips never were, and the refusals
they carry are the ones that stop runs:

- the tip SPOT refuses a pick from a spot it believes is empty. Gated behind
  `does_tip_tracking() and not tracker.is_disabled`, so it can be turned off
  per spot.
- the HEAD refuses a pick onto a channel it believes is loaded, and a drop from
  a channel it believes is bare. Neither is gated, and `disable()` makes the
  head worse rather than better: a disabled tracker raises RuntimeError on the
  same calls.

Two things stay refused on purpose.

Reading a tip off a channel that has never held one: nobody knows what is on it,
and inventing a tip to get past the check would be manufacturing the measurement
this whole effort exists to stop. That is UNKNOWN, and UNKNOWN refuses.

And a drop onto a tip spot that already holds a tip. Leniency is about beliefs we
gave the driver, and a tip arriving where a tip already is means different things
per role: on a channel it is a stale belief, because a channel that really held a
tip would have failed the pick physically; on a spot it is a collision, and two
tips cannot share one. The spot keeps that refusal, which is what makes a partial
column return roll back and report the spots it started with.
"""

import pytest
from pylabrobot.resources.errors import HasTipError, NoTipError
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_tracker import TipTracker

from cheshire_drivers.plr_tip_tracker import (
    LenientTipSpotTracker,
    LenientTipTracker,
)


def _tip() -> Tip:
    return Tip(
        has_filter=False, total_tip_length=50.0, maximal_volume=300.0, fitting_depth=8.0,
    )


class TestTheStockTrackerStillRefuses:
    """The control. Without these, the tests below would pass on a broken
    subclass that inherited the stock behaviour unchanged."""

    def test_stock_refuses_a_second_tip(self) -> None:
        tracker = TipTracker(thing="channel_0")
        tracker.add_tip(_tip())
        with pytest.raises(HasTipError):
            tracker.add_tip(_tip())

    def test_stock_refuses_to_remove_from_a_bare_channel(self) -> None:
        tracker = TipTracker(thing="channel_0")
        with pytest.raises(NoTipError):
            tracker.remove_tip()


class TestAPickOntoAChannelItThinksIsLoaded:
    def test_the_new_tip_replaces_the_believed_one(self) -> None:
        """The head believing a channel is loaded is a belief we gave it. The
        pick physically happened, so the channel holds the tip that was picked."""
        tracker = LenientTipTracker(thing="channel_0")
        stale = _tip()
        tracker.add_tip(stale)
        fresh = _tip()
        tracker.add_tip(fresh)
        assert tracker.get_tip() is fresh


class TestADropFromAChannelItThinksIsBare:
    def test_removing_a_tip_it_does_not_believe_in_is_not_an_error(self) -> None:
        tracker = LenientTipTracker(thing="channel_0")
        tracker.remove_tip()
        assert not tracker.has_tip

    def test_the_channel_reads_bare_afterwards(self) -> None:
        tracker = LenientTipTracker(thing="channel_0")
        tracker.add_tip(_tip())
        tracker.remove_tip(commit=True)
        tracker.remove_tip(commit=True)
        assert not tracker.has_tip


class TestReadingTheTipBack:
    def test_the_tip_is_readable_while_the_drop_is_being_built(self) -> None:
        """PyLabRobot reads the tip off the channel to build the Drop op, and it
        does that BEFORE clearing the channel."""
        tracker = LenientTipTracker(thing="channel_0")
        picked = _tip()
        tracker.add_tip(picked)
        tracker.remove_tip()
        assert tracker.get_tip() is picked

    def test_the_tip_stops_being_readable_once_the_drop_commits(self) -> None:
        """Answering after the commit let a second drop on the same channel put
        the SAME tip into another spot: two spots sharing one volume tracker,
        and a rack reporting a tip nobody put there."""
        tracker = LenientTipTracker(thing="channel_0")
        tracker.add_tip(_tip())
        tracker.remove_tip(commit=True)
        with pytest.raises(NoTipError):
            tracker.get_tip()

    def test_a_second_drop_on_a_bare_channel_yields_no_tip_to_place(self) -> None:
        tracker = LenientTipTracker(thing="channel_0")
        tracker.add_tip(_tip())
        tracker.remove_tip(commit=True)
        tracker.remove_tip(commit=True)
        with pytest.raises(NoTipError):
            tracker.get_tip()

    def test_a_channel_that_never_held_a_tip_still_refuses(self) -> None:
        """Deliberately not lenient. Nothing has ever said what is on this
        channel, and a fabricated tip would be an invented measurement."""
        tracker = LenientTipTracker(thing="channel_0")
        with pytest.raises(NoTipError):
            tracker.get_tip()


class TestItIsStillATipTracker:
    def test_disable_does_not_turn_the_raises_back_on(self) -> None:
        """The stock tracker raises RuntimeError from add/remove once disabled,
        so disabling the head is worse than leaving it alone. Leniency must not
        depend on it."""
        tracker = LenientTipTracker(thing="channel_0")
        tracker.disable()
        tracker.add_tip(_tip())
        tracker.remove_tip()

    def test_commit_and_rollback_still_work(self) -> None:
        tracker = LenientTipTracker(thing="channel_0")
        tip = _tip()
        tracker.add_tip(tip, commit=False)
        tracker.rollback()
        assert not tracker.has_tip


class TestATipSpotIsNotAChannel:
    """The spot is lenient in one direction only."""

    def test_a_spot_records_a_pick_it_thought_had_nothing_to_give(self) -> None:
        """The refusal that stops recovery: a rack whose baseline was never
        written reads empty, and every pick from it would be refused."""
        tracker = LenientTipSpotTracker(thing="rack_1_A1", make_tip=_tip)
        tracker.remove_tip()
        assert not tracker.has_tip

    def test_a_spot_still_refuses_a_tip_arriving_where_one_already_is(self) -> None:
        """Two tips cannot share a spot. This one is physics, not a belief."""
        tracker = LenientTipSpotTracker(thing="rack_1_A1", make_tip=_tip)
        tracker.add_tip(_tip())
        with pytest.raises(HasTipError):
            tracker.add_tip(_tip())

    def test_a_channel_does_not_refuse_the_same_arrival(self) -> None:
        """The same call, the other role, the opposite answer -- which is the
        whole reason these are two classes."""
        tracker = LenientTipTracker(thing="channel_0")
        tracker.add_tip(_tip())
        tracker.add_tip(_tip())
        assert tracker.has_tip
