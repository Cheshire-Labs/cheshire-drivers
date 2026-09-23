"""Transporter driver that prompts operators with clear pick/place instructions."""

from typing import Optional

from cheshire_drivers.sims import HumanSim, SimStrategy, SimTransporterDriver
from cheshire_drivers.transporter_models import PickAtCoordsRequest, PlaceAtCoordsRequest


class HumanTransporterDriver(SimTransporterDriver):
    """SimTransporterDriver subclass with operator-friendly pick/place prompts.

    Overrides pick_at_coords / place_at_coords to show imperative
    instructions. The Teachpoint (including its position_id) flows through
    the request payload (the system layer pre-resolved it plus any gateway
    chain), so the human-prompt UX is unchanged from before the rename.
    Other inherited methods (initialize, home, move_to_safe) keep their
    SimTransporterDriver behavior.
    """

    def __init__(self, name: str, sim_strategy: Optional[SimStrategy] = None) -> None:
        super().__init__(name, sim_strategy or HumanSim())

    async def pick_at_coords(self, request: PickAtCoordsRequest) -> None:
        await self._sim(
            f"PICK UP {request.labware_type} from '{request.teachpoint.position_id}'."
        )

    async def place_at_coords(self, request: PlaceAtCoordsRequest) -> None:
        await self._sim(
            f"PLACE {request.labware_type} at '{request.teachpoint.position_id}'."
        )
