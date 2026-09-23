"""Pydantic models for delidder driver interface.

Single category-specific method (delid). Empty marker preserves uniform wire
shape; mirrors the precedent set by HomeRequest (homing)/MoveToSafeRequest (transporter)
and ReturnTips96Request/GetDeckStateRequest (LH).
"""

from cheshire_drivers.liquid_handler_models import _StrictModel


class DelidRequest(_StrictModel):
    pass
