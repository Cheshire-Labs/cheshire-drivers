"""Pydantic models for sealer driver interface.

Mirrors the per-category pattern. All models reject unknown fields.
"""

from pydantic import Field

from cheshire_drivers.liquid_handler_models import _StrictModel


class SealRequest(_StrictModel):
    # Permissive bounds to match the older lax contract; physical caps belong
    # in the REST/MCP layer or the driver implementation.
    temperature: int = Field(..., ge=0, description="Sealing temperature in Celsius")
    duration: float = Field(..., ge=0, description="Sealing duration in seconds")
