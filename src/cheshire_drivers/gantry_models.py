"""Models and refusals for the liquid-handler gantry-parking interface.

Separate from gripper_models because the gantry is not the gripper: parking moves
whatever the handler has over its deck (the gripper rides along, but so do the
pipette mounts), and a handler with no gripper at all can still have a gantry to
move out of an arm's way.
"""

from abc import ABC, abstractmethod

from cheshire_drivers.driver_errors import DriverRefusedError
from cheshire_drivers.liquid_handler_models import _StrictModel


class GantryParkPosition(_StrictModel):
    """A deck-frame spot (mm) a bench wants this handler's gripper parked at."""

    x: float
    y: float
    z: float


class ParkGantryRequest(_StrictModel):
    """Move the handler's moving parts clear of its own deck.

    ``at`` is optional because where "clear" is depends on the machine, and the
    handler is the only thing that knows. Supply it when a bench has a spot it
    would rather the gripper waited at.

    A specific spot rides on THIS request rather than going out as a plain
    IGripperMotion jog, because parking is the one gantry move that carries a
    safety judgement (see IGantryParkingDriver.park_gantry). A caller that sent
    the jog instead would get the motion without the judgement.
    """

    at: GantryParkPosition | None = None


class GantryJogRequest(_StrictModel, ABC):
    """A jog of one head, which crosses the deck only when it moves in X or Y."""

    @abstractmethod
    def moves_in_xy(self) -> bool: ...


class GantryBusyError(DriverRefusedError):
    """The handler will not move its gantry right now.

    Raised when something is still hanging off the gantry. A plate that may be
    in the jaws refuses every travel: it goes with the gantry and hits whatever
    it reaches, which the bench found out. Tips on a head refuse a park or a
    gripper move, because no transfer needs one mid-way. Either way the work in
    progress wins and the caller waits rather than interrupting it.

    Distinct from a failure, which is what ``DriverRefusedError`` carries
    upstream. Nothing is wrong, nothing was moved, and the same request will
    succeed once the handler is clear. What clears it depends on what is held:
    dropping the tips, or opening the jaws with release_jaw.
    """
