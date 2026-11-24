"""Every liquid handler lifts all of its heads before its gantry moves in X or Y.

One gantry carries every pipette mount, the 96 head and the gripper, so a head
left low is dragged across the deck by any move of any of them. The step runs in
the command hook, which an operator command and a run step both pass through, and
so do vendor commands reached through the driver.
"""

import inspect
from abc import abstractmethod
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from cheshire_drivers.driver_hooks import DriverHooks, current_call_id
from cheshire_drivers.driver_introspection import vendor_surfaces
from cheshire_drivers.gantry_models import GantryJogRequest

TRAVELLING_COMMANDS: frozenset[str] = frozenset({
    "aspirate",
    "dispense",
    "mix",
    "liquid_probe",
    "pick_up_tips",
    "drop_tips",
    "discard_tips",
    "aspirate96",
    "dispense96",
    "pick_up_tips96",
    "drop_tips96",
    "return_tips96",
    "move_plate",
    "move_gripper_to",
    "park_gantry",
})
"""Interface commands that always move the gantry in X or Y."""


def command_travels(driver_cls: type, command: str, request: BaseModel | None) -> bool:
    """True when `command` moves the gantry of a `driver_cls` in X or Y."""
    if command in TRAVELLING_COMMANDS:
        return True
    if isinstance(request, GantryJogRequest):
        return request.moves_in_xy()
    return any(
        command in {surface.wire_name(member) for member in surface.travels}
        for surface in vendor_surfaces(driver_cls)
    )


def every_command_of(vendor_type: type) -> frozenset[str]:
    """Every public coroutine of a vendor class, for a driver whose wire decides which of them move."""
    return frozenset(
        name for name, member in inspect.getmembers(vendor_type)
        if not name.startswith("_") and inspect.iscoroutinefunction(member)
    )


class FirstMoveGate:
    """Holds a lift until the running command sends its first move.

    A driver arms it in `_retract_heads` and opens it at its wire, so a request the
    driver or its vendor library refuses sends nothing to the robot.
    """

    def __init__(self) -> None:
        self._armed_for: str | None = None
        self._lift: Callable[[], Awaitable[None]] | None = None

    def arm(self, lift: Callable[[], Awaitable[None]]) -> None:
        """Run `lift` before the first move the running command sends."""
        self._armed_for = current_call_id()
        self._lift = lift

    async def before(self, moves: bool) -> None:
        """Called by the wire before each command it sends; `moves` says whether that command moves the gantry."""
        lift = self._lift
        if lift is None or not moves or self._armed_for != current_call_id():
            return
        self._lift = None
        await lift()


class RetractsHeadsBeforeTravel(DriverHooks):
    """A liquid handler: before a command moves its gantry in X or Y, every head goes up."""

    @abstractmethod
    async def _retract_heads(self, command: str) -> None:
        """Lift every head to its safe height before `command` travels.

        Raise GantryBusyError when the heads cannot be lifted safely, for example
        while the gripper may still hold a plate. A handler whose own moves already
        lift every head does nothing here.
        """

    async def _before_command(self, command: str, request: BaseModel | None) -> None:
        await super()._before_command(command, request)
        if command_travels(type(self), command, request):
            await self._retract_heads(command)
