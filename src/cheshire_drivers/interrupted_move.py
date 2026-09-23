"""Recording a gripper move that started and did not finish.

A driver's deck model addresses labware by SITE, so a move in flight has nowhere
to be recorded in it: the re-parent runs only after the wire command returns, and
a command that raises leaves the deck naming the site the move began at. Nothing
in that model separates "sitting there" from "picked up off there and lost", so a
reader takes the stale site for the present location.

Every liquid handler that moves labware with its own gripper mixes this in and
reports it on ``get_deck_state``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from cheshire_drivers.liquid_handler_models import InterruptedMove


class InterruptedMoveTracking:
    """Remembers the gripper move that raised, until something settles where
    that labware is: the same labware moving, or anything that restates the deck
    wholesale. A successful move of DIFFERENT labware does not settle it, since
    a plate stranded in the jaws is still stranded.

    The FIRST unsettled failure is the one kept. Jaws already holding something
    refuse the next move too, so a later failure is usually that one's echo, and
    overwriting would rename the record off the plate actually stranded.
    """

    # Deliberately not a public property: `derive_capabilities` reads public
    # members off a concrete driver as invokable vendor extras, and this is
    # state to report, not a command.
    _interrupted_move: InterruptedMove | None = None
    _jaws_may_be_loaded: bool = False
    """Whether something may be clamped in the jaws right now.

    Kept apart from ``_interrupted_move`` because they answer different
    questions and stop being true at different moments. This one asks whether
    anything is dangling off the gantry, so opening the jaw settles it. The
    record asks whether the deck model is lying about where a labware is, and
    opening the jaw does not settle that: the plate lands wherever the gripper
    happened to be, which is not the site the deck still names.

    Per-process, and nothing rebuilds it. The Flex has no jaw readback, so after
    a client restart the jaws read clear whether or not a plate is still in them.
    """

    @asynccontextmanager
    async def _tracking_gripper_move(
        self, labware: str, to_site: str, from_site: str | None = None,
    ) -> AsyncIterator[None]:
        try:
            yield
        except BaseException as exc:
            # Not `Exception`: an abort or a timeout CANCELS the move, and a
            # cancelled move strands labware like any other.
            self._jaws_may_be_loaded = True
            if self._interrupted_move is None:
                self._interrupted_move = InterruptedMove(
                    labware=labware,
                    to_site=to_site,
                    from_site=from_site,
                    error=f"{type(exc).__name__}: {exc}",
                )
            raise
        # A move that finished put the labware down and let go of it.
        self._jaws_may_be_loaded = False
        self._resolve_interrupted_move(labware)

    def _note_jaws_opened(self) -> None:
        """The jaws are open, so nothing is hanging off the gantry.

        Says nothing about where what they held has ended up, which is why it
        leaves ``_interrupted_move`` alone.
        """
        self._jaws_may_be_loaded = False

    def _resolve_interrupted_move(self, labware: str | None = None) -> None:
        """Drop the record because its labware has been located.

        ``labware=None`` drops it whatever it names, for the deck-wide restates.
        """
        if self._interrupted_move is None:
            return
        if labware is None or self._interrupted_move.labware == labware:
            self._interrupted_move = None
