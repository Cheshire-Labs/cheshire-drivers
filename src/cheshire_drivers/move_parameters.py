"""The scalars a transporter needs for one pick or place, and the patch layers use.

`MoveParameters` is total: every field is present and every field is a number the
arm can act on. Nothing labware-shaped crosses here, so a driver cannot tell a
plate from a tip box from this object. Field names are the arm's own argument
names, so a wrapper passes them through without renaming anything.

`MoveParameterPatch` is the sparse counterpart. Each layer that narrows a move
(the labware, the site, the labware at that site, how the labware is being
carried right now) contributes one patch, and `None` means "inherit". That is
what lets a narrow adjustment set one field without restating the other nine.

Who fills these in is not this module's business: it holds the vocabulary, and the
ordering of the layers lives with the engine that knows about sites, protocols and
what is being carried. A driver receives numbers.
"""

from collections.abc import Iterable
from typing import Literal, Optional

from typing_extensions import Self

from cheshire_drivers.layered_parameters import merge_patch_over
from cheshire_drivers.liquid_handler_models import _StrictModel

AccessType = Literal["vertical", "horizontal"]
"""How a site is entered and left: from above, or from the side."""

MoveParameterField = Literal[
    "access_type",
    "clearance",
    "z_above",
    "grasp_offset",
    "resource_width",
    "resource_height",
    "travel_margin",
    "jaw_opening",
    "plate_present_margin",
    "z_offset",
    "grip_distance_from_top",
    "speed",
]
"""The fields a layer can name, for callers that pass a field name rather than a value.

Putting a field back to inheriting is the one thing a patch cannot express, since
None there means inherit already. A caller names the field instead.

Annotate the incoming field list with this so the request body or tool argument is
validated on the way in. Nothing downstream re-checks a name: `without` drops what
it recognises and ignores the rest, so a typo that gets past the edge clears
nothing and reports success.
"""


class MoveParameters(_StrictModel):
    """Resolved pick/place scalars for one move. Total: every field present."""

    access_type: AccessType
    clearance: float
    """Distance backed off from the labware when approaching and departing.

    On a vertical site this is a FLOOR on the entry height, not the entry height: the
    arm works out how high it has to be from what is standing at the site and takes
    this only if it is higher. Raise it when the site needs more room than its
    contents do, never to make room for a tall labware."""
    z_above: float
    """Lift after a horizontal retract. Zero for a vertical approach."""
    grasp_offset: float
    """How deep the site is: the lip of the pad or the wall of the pocket the labware
    sits in, which is what a carried leg has to rise clear of. A property of the site
    and not of the labware, so it is the same for a tip box and a microplate in the
    same nest."""
    resource_width: float
    """Jaw separation for the grip, measured on the skirt rather than the footprint."""
    resource_height: float
    """How tall the labware is. Used to pass over the one standing at a site on the
    way in to pick it, and for nothing else: what a move rises by on the way out is
    the depth of the nest, not the height of what came out of it."""
    travel_margin: float
    """Safety margin added to the rise."""
    jaw_opening: float
    """How far the jaws open to release, or to clear the labware on approach."""
    plate_present_margin: float
    """How far a held labware must hold the jaws above the grip position for the grip
    to count as holding something. Measured on whichever face the jaws close on, so a
    labware gripped on its short side declares a different one."""
    z_offset: float
    """Grip height relative to what this teachpoint was taught with. Zero when
    the labware being moved is the labware the position was taught on."""
    grip_distance_from_top: Optional[float] = None
    """How far below the labware's own top the jaws close, for a gripper that
    has no teachpoint to measure from.

    The same physical decision as `z_offset` read off a different datum. An arm
    taught at a site knows the height it was taught at and adjusts from there;
    a handler's own deck gripper is told a slot and works the height out from
    the labware, so it needs the answer in the labware's own terms.

    None is a value, not an absence: it means nothing here has an opinion and
    the robot uses whatever its labware definition states. That is worth
    knowing, because a definition stating none leaves the Opentrons Flex
    gripping at the labware's MID-HEIGHT, which is around 5 mm too low on a
    standard flat-bottom microplate. Grip the skirt: Opentrons' own definitions
    put it 1-2.5 mm below the top on a flat plate, ~6 mm on a PCR plate with a
    raised well block, and ~19 mm on a deep-well block."""
    speed: Optional[float] = None
    """Percent of full speed, or None to leave the arm at its current setting.
    None is a value here, not an absence: most moves do not want a speed change."""


class MoveParameterPatch(_StrictModel):
    """One layer's contribution to a move. Every field optional; None inherits."""

    access_type: Optional[AccessType] = None
    clearance: Optional[float] = None
    z_above: Optional[float] = None
    grasp_offset: Optional[float] = None
    resource_width: Optional[float] = None
    resource_height: Optional[float] = None
    travel_margin: Optional[float] = None
    jaw_opening: Optional[float] = None
    plate_present_margin: Optional[float] = None
    z_offset: Optional[float] = None
    grip_distance_from_top: Optional[float] = None
    speed: Optional[float] = None

    def apply_to(self, parameters: MoveParameters) -> MoveParameters:
        """This layer merged over an already-resolved record. See
        `merge_patch_over` for what a None field means: layering a
        deployment-wide slow speed slows everything under it, and a narrower
        layer that wants full speed says so with a number."""
        return merge_patch_over(self, parameters)

    def over(self, base: Self) -> Self:
        """This layer merged onto an earlier one, still sparse.

        Unlike `apply_to` the result stays a patch, so a field neither layer named
        keeps inheriting rather than being pinned to whatever it resolves to today.
        """
        merged = {
            **base.model_dump(exclude_none=True),
            **self.model_dump(exclude_none=True),
        }
        return type(self).model_validate(merged)

    def without(self, fields: Iterable[MoveParameterField]) -> Self:
        """This patch with the named fields dropped back to inheriting.

        Apply an edit as `edit.over(stored).without(cleared)`. The other order
        drops the field from the edit and then takes it straight back off the
        stored patch, so the clear silently does nothing.
        """
        dropped = set(fields)
        kept = {
            name: value
            for name, value in self.model_dump(exclude_none=True).items()
            if name not in dropped
        }
        return type(self).model_validate(kept)


SEED_MOVE_PARAMETERS = MoveParameters(
    access_type="vertical",
    clearance=20.0,
    z_above=0.0,
    grasp_offset=20.0,
    resource_width=75.0,
    resource_height=14.35,
    travel_margin=10.0,
    jaw_opening=14.0,
    plate_present_margin=2.0,
    z_offset=0.0,
    grip_distance_from_top=None,
    speed=None,
)
"""What a deployment's editable defaults start out as, the same way the built-in
access configs are seeded.

They are a starting point and not a fallback: once seeded, the stored row is the
answer, and a driver never reaches for these.

Two are weak. `resource_width` is jaw separation on the skirt, which no catalog
carries and which differs per labware, so 75.0 is one plate's measurement
standing in for every labware until a per-labware grip profile supplies the real
one. `resource_height` is a standard-height microplate, and it is a placeholder in the
same way: the sender resolves the real height from the labware it is carrying and
sends it on the move, so the seed only stands for a move carrying nothing.

`grip_distance_from_top` is seeded at None on purpose rather than weakly. It
differs per labware by more than a factor of ten (1-2.5 mm below the top on a
flat plate, ~19 mm on a deep-well block), so there is no number that is merely
imprecise for everything; one that suits a flat plate grips a deep well by the
top of its wells. None hands the decision to the robot's own definition, and a
grip profile is where the measured answer goes.
"""
