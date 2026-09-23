"""Pydantic models for protocol runner driver interface.

Single category-specific method (run_protocol). The `params` field accepts
dict[str, Any] because protocol files take vendor-specific shapes (Hamilton
Venus, Tecan EVOware, plate washer protocols) and the driver layer cannot
narrow the shape without breaking the universal protocol-runner contract.
This is the truly-dynamic external-API exception to the no-Any rule.

A future change may introduce per-protocol-type Request hierarchies if/when
specific protocols' params shapes are known and worth typing.
"""

from typing import Any, Dict

from pydantic import Field

from cheshire_drivers.liquid_handler_models import _StrictModel


class RunProtocolRequest(_StrictModel):
    protocol_filepath: str = Field(..., description="Path to the protocol file to execute")
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Protocol-specific parameters; vendor-defined shape",
    )
