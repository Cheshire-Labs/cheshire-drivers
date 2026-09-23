"""Pydantic models for the thermocycler driver interface.

`Step`/`Stage`/`Protocol` mirror PLR's `pylabrobot.thermocycling.standard`
dataclasses field-for-field so `_to_plr_protocol` converts 1:1 (PLR's
`Protocol.deserialize` does `Step(**step)`, so the field names must match).

Getter return shapes: the wire wrap accepts only None / BaseModel / dict /
single scalar. A raw `list[float]` or a raw enum would raise, so the four
temperature getters return `TemperatureListResponse` and the two enum getters
return `ThermocyclerStatusResponse`; the drivers RETURN those models. The
scalar getters (`get_lid_open`, `get_hold_time`, the four counters) return the
raw scalar and the wire wrap projects it onto the matching single-field
Response model registered in `THERMOCYCLER_RESPONSE_MODELS` (mirror of the
transporter `get_speed` -> `SpeedResponse` pattern).
"""

from typing import List, Optional

from pydantic import Field

from cheshire_drivers.command_responses import CommandResponse
from cheshire_drivers.liquid_handler_models import _StrictModel


class Step(_StrictModel):
    """One thermal step. Fields mirror PLR `standard.Step` exactly."""

    temperature: List[float]
    hold_seconds: float
    rate: Optional[float] = None


class Stage(_StrictModel):
    """One thermal stage (a step list run `repeats` times). Mirrors PLR `standard.Stage`."""

    steps: List[Step]
    repeats: int


class Protocol(_StrictModel):
    """A thermocycler protocol (ordered stages). Mirrors PLR `standard.Protocol`."""

    stages: List[Stage]


# --- Request models ---


class RunProtocolRequest(_StrictModel):
    protocol: Protocol
    block_max_volume: float = Field(..., gt=0)


class SetBlockTemperatureRequest(_StrictModel):
    temperature: List[float]


class SetLidTemperatureRequest(_StrictModel):
    temperature: List[float]


# --- Empty marker requests (param-less commands still take a Pydantic Request) ---


class OpenLidRequest(_StrictModel):
    pass


class CloseLidRequest(_StrictModel):
    pass


class DeactivateBlockRequest(_StrictModel):
    pass


class DeactivateLidRequest(_StrictModel):
    pass


class GetBlockCurrentTemperatureRequest(_StrictModel):
    pass


class GetBlockTargetTemperatureRequest(_StrictModel):
    pass


class GetLidCurrentTemperatureRequest(_StrictModel):
    pass


class GetLidTargetTemperatureRequest(_StrictModel):
    pass


class GetLidOpenRequest(_StrictModel):
    pass


class GetLidStatusRequest(_StrictModel):
    pass


class GetBlockStatusRequest(_StrictModel):
    pass


class GetHoldTimeRequest(_StrictModel):
    pass


class GetCurrentCycleIndexRequest(_StrictModel):
    pass


class GetTotalCycleCountRequest(_StrictModel):
    pass


class GetCurrentStepIndexRequest(_StrictModel):
    pass


class GetTotalStepCountRequest(_StrictModel):
    pass


# --- Response models ---


class TemperatureListResponse(CommandResponse):
    """Per-zone temperatures for the four temperature getters."""

    temperatures: List[float]


class ThermocyclerStatusResponse(CommandResponse):
    """Lid/block status carried as the enum `.value` string."""

    status: str


class LidOpenResponse(CommandResponse):
    """Response payload for ``get_lid_open``."""

    open: bool


class HoldTimeResponse(CommandResponse):
    """Response payload for ``get_hold_time`` (remaining hold seconds)."""

    seconds: float


class CycleIndexResponse(CommandResponse):
    """Response payload for ``get_current_cycle_index``."""

    index: int


class CycleCountResponse(CommandResponse):
    """Response payload for ``get_total_cycle_count``."""

    count: int


class StepIndexResponse(CommandResponse):
    """Response payload for ``get_current_step_index``."""

    index: int


class StepCountResponse(CommandResponse):
    """Response payload for ``get_total_step_count``."""

    count: int
