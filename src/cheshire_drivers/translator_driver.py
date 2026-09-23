"""Sim driver for translators/shuttles: one carriage serves every taught position."""

from cheshire_drivers.sims import (
    SimTransporterDriver,
    SimTransporterValidationError,
)
from cheshire_drivers.transporter_models import PlaceAtCoordsRequest


class SimTranslatorDriver(SimTransporterDriver):
    """Sim driver for a translator/shuttle: ONE carriage serves every taught
    position, so the whole position set holds at most one labware.

    ``single_carriage`` is the declaration the engine's reservation layer
    reads -- that layer is the real scheduling gate (it must refuse to
    PROMISE a spot the carriage cannot accept; a driver-time rejection
    would fire after the grant, mid-move). The guard here enforces the
    same invariant on this driver's OWN place ops as defense-in-depth:
    the world mirrors both endpoints via the engine's location-observer
    seed/unseed sync, so a stale occupant cannot false-positive it.
    """

    @property
    def single_carriage(self) -> bool:
        return True

    async def place_at_coords(self, request: PlaceAtCoordsRequest) -> None:
        if request.external_control:
            # An external-control place is a world no-op in the base; the
            # occupancy guard must not turn that into a rejection.
            await super().place_at_coords(request)
            return
        target = request.teachpoint.position_id
        occupied = sorted(
            position_id
            for position_id in self._positions
            if position_id != target and self._resource_at(position_id) is not None
        )
        if occupied:
            raise SimTransporterValidationError(
                f"place rejected: single-carriage translator '{self.name}' "
                f"already holds labware at {occupied}"
            )
        await super().place_at_coords(request)
