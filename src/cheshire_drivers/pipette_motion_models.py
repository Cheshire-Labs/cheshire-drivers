"""Pydantic models for the pipette-motion driver interface (IPipetteMotion).

Low-level per-channel Cartesian motion: place a channel at a known deck point,
read where it is, and jog it. This is the direct-positioning / troubleshooting
surface, distinct from the labware-referenced aspirate/dispense on ILiquidHandler.

All models reject unknown fields (`extra='forbid'` via `_StrictModel`); a
silent-drop on the wire is a bug we do not tolerate.
"""

from typing import Literal, Optional

from typing_extensions import Self

from pydantic import model_validator

from cheshire_drivers.liquid_handler_models import _StrictModel

ZReference = Literal["tip_end", "stop_disk"]
"""Which datum on the channel the z coordinate refers to. `tip_end` is the end of
the mounted tip (or the nozzle when no tip is on), `stop_disk` is the pipette's
stop-disk reference. STAR firmware reinterprets z by tip state, so a taught z is
ambiguous without this; the field makes it explicit. A backend that exposes only
one datum honors that value and rejects the other rather than moving to the wrong
plane."""



def require_tip_end_datum(z_reference: ZReference) -> None:
    """Opentrons robots report one z datum: the mounted tip's end, or the nozzle when no tip is on.

    `stop_disk` is a Hamilton datum with no Opentrons equivalent, so accepting it would silently
    move to a different plane than the caller meant.
    """
    if z_reference != "tip_end":
        raise ValueError(
            f"Opentrons channels reference the tip end, not '{z_reference}'. "
            "Re-issue the command with z_reference='tip_end'."
        )

class MoveChannelToRequest(_StrictModel):
    """Move one channel to an absolute deck-frame position (mm).

    Only the axes supplied are commanded; the rest hold their current value. So
    `MoveChannelToRequest(channel=0, z=0.0)` drops the tip to the deck-frame z
    without touching x or y.
    """

    channel: int
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    z_reference: ZReference = "tip_end"

    @model_validator(mode="after")
    def _at_least_one_axis(self) -> Self:
        if self.x is None and self.y is None and self.z is None:
            raise ValueError("MoveChannelToRequest: supply at least one of x, y, z to move.")
        return self


class MoveChannelRelativeRequest(_StrictModel):
    """Jog one channel by a delta on each supplied axis, from its current position (mm).

    No z_reference: a relative delta is datum-independent (tip_end and stop_disk are the same
    carriage offset by a constant, so a +dz displacement is identical in either datum)."""

    channel: int
    dx: Optional[float] = None
    dy: Optional[float] = None
    dz: Optional[float] = None

    @model_validator(mode="after")
    def _at_least_one_delta(self) -> Self:
        if self.dx is None and self.dy is None and self.dz is None:
            raise ValueError("MoveChannelRelativeRequest: supply at least one of dx, dy, dz to jog.")
        return self


class GetChannelPositionRequest(_StrictModel):
    """Read one channel's current deck-frame position."""

    channel: int
    z_reference: ZReference = "tip_end"


class ChannelPosition(_StrictModel):
    """A channel's current deck-frame position (mm). ``z_reference`` echoes the datum the read
    used, so a stored position stays unambiguous when it is later re-commanded."""

    channel: int
    x: float
    y: float
    z: float
    z_reference: ZReference
