"""Pick and place on a SCARA arm, built out of the arm driver's own moves.

The driver exposes primitives: go to a pose, open or close the jaws by so much,
read where the joints are, read the reachable envelope. It does not know what a
nest is, how far a skirt has to rise to clear one, or what counts as having
caught something. Those are this layer's, and every number they need arrives
already resolved on a :class:`MoveParameters`.

The controller's own PickPlate is not used. It reads a taught station index,
which is persistent controller state with a geometry of its own, and it runs the
approach out of reach of the caller, so nothing above it can vary the route per
labware. Composing the legs here puts the route and the geometry in the same
place as the parameters that describe them.
"""

from contextlib import AbstractAsyncContextManager
from math import cos, hypot, radians, sin
from typing import Awaitable, Dict, Protocol

from pylabrobot.brooks.precise_flex import Axis, ElbowOrientation, WorkEnvelope
from pylabrobot.resources import Coordinate

from cheshire_drivers.move_parameters import MoveParameters


class ArmMotion(Protocol):
    """The arm primitives a composed pick or place is built from."""

    closed_gripper_position: float

    @property
    def gripper_joint_range(self) -> tuple[float | None, float | None]: ...

    def at_speed(self, speed_pct: float | None) -> AbstractAsyncContextManager[None]: ...

    async def set_grasp_data(
        self, plate_width: float, finger_speed_pct: float, grasp_force: float
    ) -> None: ...

    # These two return an awaitable rather than being `async def`: the arm decorates
    # them for its event bus, which erases the coroutine type an `async def` would want.
    def move_to_location(
        self,
        location: Coordinate,
        direction: float,
        *,
        orientation: ElbowOrientation | None = None,
    ) -> Awaitable[None]: ...

    def move_gripper_joint_position(
        self, position: float, force_sensing: bool = False
    ) -> Awaitable[None]: ...

    async def request_joint_position(self) -> Dict[int, float]: ...


class UnreachableTargetError(RuntimeError):
    """A pick or place the arm cannot complete, refused before it starts moving."""


class EmptyGripError(RuntimeError):
    """The jaws closed on nothing, so there is no labware to carry."""


def lift(location: Coordinate, mm: float) -> Coordinate:
    """The same point, raised."""
    return Coordinate(location.x, location.y, location.z + mm)


def back_off(location: Coordinate, direction: float, mm: float) -> Coordinate:
    """Straight back along the approach direction, the way a shelf is entered and left."""
    yaw = radians(direction)
    return Coordinate(location.x - mm * cos(yaw), location.y - mm * sin(yaw), location.z)


def out_of_the_nest(handling: MoveParameters) -> float:
    """How far a leg with the labware IN the jaws travels off the grip point.

    What it has to clear is the site: the lip of the pad or the wall of the pocket
    the labware sits in. `grasp_offset` is how deep that is, and the labware's own
    height has no part in it, because the labware is held above the lip either way.
    The same pad has the same lip whether a tip box or a microplate is on it.

    A pick rises by this carrying the labware out; a place descends from it
    carrying the labware in.
    """
    return handling.grasp_offset + handling.travel_margin


def over_the_labware(handling: MoveParameters) -> float:
    """How far a leg with EMPTY jaws travels off the grip point.

    What it has to clear is the labware standing at the site, because empty jaws
    are down at the grip point beside it and the next thing they do is travel
    sideways. That is true arriving to pick one up and true leaving after setting
    one down: the obstacle is the same object either way, and it is the same height
    whether the arm is about to grip it or has just let go of it.

    Leaving after a place by the nest's depth instead is how the arm clears the pad
    and then drives through the box it just put on it.
    """
    return handling.resource_height + handling.travel_margin


def highest_lift(handling: MoveParameters) -> float:
    """The most a pick or place rises above the grip point, for the pre-flight.

    A shelf only ever rises by `z_above`, on both legs.
    """
    if handling.access_type == "vertical":
        return max(
            handling.clearance, over_the_labware(handling), out_of_the_nest(handling)
        )
    return handling.z_above


def assert_reachable(
    envelope: WorkEnvelope,
    location: Coordinate,
    rise: float,
) -> None:
    """Refuse an unreachable target before the controller starts moving toward it.

    Reach is checked at the grip point against the yaw-free tool annulus, so this is
    a cheap refusal and the arm's per-leg kinematics still have the final say. The
    rise is checked too, because a retreat that leaves the Z travel would fail with
    the labware already in the jaws.

    Measured from the arm's own origin, so it holds only for an arm whose origin does
    not move. See `ArmPickPlace` on railed arms.
    """
    reach = hypot(location.x, location.y)
    if not envelope.inner <= reach <= envelope.outer:
        raise UnreachableTargetError(
            f"target at reach {reach:.1f} mm is outside the arm's annulus "
            f"[{envelope.inner:.1f}, {envelope.outer:.1f}]"
        )
    if not envelope.zmin <= location.z <= envelope.zmax:
        raise UnreachableTargetError(
            f"target z={location.z} is outside the arm's Z travel "
            f"[{envelope.zmin}, {envelope.zmax}]"
        )
    if location.z + rise > envelope.zmax:
        raise UnreachableTargetError(
            f"rising {rise} mm above z={location.z} reaches {location.z + rise}, past the "
            f"top of the arm's Z travel {envelope.zmax}"
        )


class ArmPickPlace:
    """One arm's pick and place, sequenced from its moves.

    Single station frame only. A railed arm's reach origin travels with the rail, so
    every coordinate here, and the annulus `assert_reachable` checks against, would
    have to carry the rail position too. Railed arms are refused at the wrapper that
    builds this, and adding one means carrying the rail through both.
    """

    def __init__(self, backend: ArmMotion) -> None:
        self._arm = backend

    async def pick(
        self,
        location: Coordinate,
        direction: float,
        handling: MoveParameters,
        orientation: ElbowOrientation | None = None,
        envelope: WorkEnvelope | None = None,
        finger_speed_pct: float = 50.0,
        grasp_force: float = 10.0,
    ) -> None:
        """Open, reach in, close on the labware, and lift it clear of what held it.

        `finger_speed_pct` and `grasp_force` go out verbatim as the controller's
        GraspData, which the vendor documents as feeding its own PickPlate. This
        sequence does not use PickPlate, and the close commands the calibrated grip
        position either way, so neither changes where the fingers stop.

        Raises:
            UnreachableTargetError: if the target or the retreat leaves the envelope.
            EmptyGripError: if the jaws close without the labware holding them apart.
                The arm is backed out of the site first, so it is not left in the nest.
        """
        if envelope is not None:
            assert_reachable(envelope, location, highest_lift(handling))
        async with self._arm.at_speed(handling.speed):
            await self._arm.set_grasp_data(
                plate_width=handling.resource_width,
                finger_speed_pct=finger_speed_pct,
                grasp_force=grasp_force,
            )
            await self._open_to(self._arm.closed_gripper_position + handling.jaw_opening)
            await self._reach_in(
                location, direction, handling, orientation, over_the_labware(handling)
            )
            try:
                await self._grip(handling.plate_present_margin)
            except EmptyGripError:
                await self._back_out(
                    location, direction, handling, orientation, over_the_labware(handling)
                )
                raise
            await self._back_out(
                location, direction, handling, orientation, out_of_the_nest(handling)
            )

    async def place(
        self,
        location: Coordinate,
        direction: float,
        handling: MoveParameters,
        orientation: ElbowOrientation | None = None,
        envelope: WorkEnvelope | None = None,
    ) -> None:
        """Reach in with the labware, let go, and lift the fingers off it.

        Takes no grip width: the jaws are already around the labware, and the release
        opens off wherever the labware is holding them.
        """
        if envelope is not None:
            assert_reachable(envelope, location, highest_lift(handling))
        async with self._arm.at_speed(handling.speed):
            await self._reach_in(
                location, direction, handling, orientation, out_of_the_nest(handling)
            )
            await self._release(handling.jaw_opening)
            await self._back_out(
                location, direction, handling, orientation, over_the_labware(handling)
            )

    async def _move_to(
        self,
        location: Coordinate,
        direction: float,
        orientation: ElbowOrientation | None,
    ) -> None:
        await self._arm.move_to_location(location, direction, orientation=orientation)

    async def _reach_in(
        self,
        location: Coordinate,
        direction: float,
        handling: MoveParameters,
        orientation: ElbowOrientation | None,
        entry_lift: float,
    ) -> None:
        """Travel from clear air onto the labware, by the route the site allows.

        A vertical site is entered from directly above, no lower than `entry_lift`:
        the arm cannot descend onto a labware from under the top of it, whatever the
        site says, so `clearance` is the site's floor rather than the answer. A tight
        nest raising it still wins. A horizontal site is a shelf, where `clearance` is
        a standoff along the approach and not a height: stand off, come in level, then
        close the gap.
        """
        if handling.access_type == "vertical":
            await self._move_to(
                lift(location, max(handling.clearance, entry_lift)), direction, orientation
            )
            await self._move_to(location, direction, orientation)
            return
        standoff = back_off(location, direction, handling.clearance)
        await self._move_to(lift(standoff, handling.z_above), direction, orientation)
        await self._move_to(standoff, direction, orientation)
        await self._move_to(location, direction, orientation)

    async def _back_out(
        self,
        location: Coordinate,
        direction: float,
        handling: MoveParameters,
        orientation: ElbowOrientation | None,
        rise: float,
    ) -> None:
        """Travel from the labware back to clear air, retracing `_reach_in`.

        A vertical site is left by rising clear of it, no lower than `clearance`,
        the same floor `_reach_in` puts under the way down: the site's floor is a
        property of the site, not of which direction the arm happens to be going.
        Honouring it on the way in and not on the way out would also put the
        pre-flight `highest_lift` check above a Z the retreat never reaches.

        A shelf is left by rising only the little the site declares, enough for the
        lip, and then withdrawing level: there is nothing to traverse over inside a
        hotel, and a full lift would meet the shelf above.
        """
        if handling.access_type == "vertical":
            await self._move_to(
                lift(location, max(handling.clearance, rise)), direction, orientation
            )
            return
        raised = lift(location, handling.z_above)
        await self._move_to(raised, direction, orientation)
        await self._move_to(
            back_off(raised, direction, handling.clearance), direction, orientation
        )

    async def _grip(self, plate_present_margin: float) -> None:
        """Close to the calibrated grip position and check something is in the jaws.

        The commanded position is where a held labware wants the fingers. Commanding
        tighter and letting the labware stop them holds a standing position error,
        which the controller reads as an overheating motor rather than as a grip.
        """
        closed = self._arm.closed_gripper_position
        await self._arm.move_gripper_joint_position(closed, force_sensing=True)
        joints = await self._arm.request_joint_position()
        if joints[Axis.GRIPPER] < closed + plate_present_margin:
            raise EmptyGripError(
                f"the gripper closed to {joints[Axis.GRIPPER]:.1f} with nothing in it: a held "
                f"resource keeps the jaws more than {plate_present_margin:.1f} above the grip "
                f"position ({closed:.1f}). If something IS in the jaws, that pair is wrong for "
                f"this labware - the width of the face being gripped is what decides both."
            )

    async def _release(self, jaw_opening: float) -> None:
        """Open a step off the labware, rather than sweeping the fingers to the stop."""
        joints = await self._arm.request_joint_position()
        await self._open_to(joints[Axis.GRIPPER] + jaw_opening)

    async def _open_to(self, position: float) -> None:
        """Open the jaws, never past the end of the gripper axis.

        A release opens off wherever a held labware left the jaws, which is already
        part of the way to the stop, so the step off it can reach the end. The driver
        holds its own targets inside the axis as well; sending one it can see is out
        of range would be asking it to correct a mistake this layer could avoid.
        """
        _, ceiling = self._arm.gripper_joint_range
        if ceiling is not None:
            position = min(position, ceiling)
        await self._arm.move_gripper_joint_position(position, force_sensing=False)
