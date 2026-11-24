"""Pydantic models for sealer driver interface.

Mirrors the per-category pattern. All models reject unknown fields.
"""

from pydantic import Field

from cheshire_drivers.liquid_handler_models import _StrictModel


class SealRequest(_StrictModel):
    temperature: int = Field(..., ge=0, le=200, description="Sealing temperature in Celsius")
    duration: float = Field(..., gt=0, description="Sealing duration in seconds")
