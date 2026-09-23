"""Pydantic models for centrifuge driver interface.

Single category-specific method (centrifuge). PLR vendor extras such as
stop and set_acceleration stay as concrete-class methods on the PLR wrapper
(auto-derived into the capabilities set) rather than promoted to abstract;
not every centrifuge implementation supports them.
"""

from pydantic import Field

from cheshire_drivers.liquid_handler_models import _StrictModel


class CentrifugeRequest(_StrictModel):
    # ge=0 preserves the older lax contract; physical caps belong in the
    # REST/MCP layer or the driver implementation, not the wire model.
    g: float = Field(..., ge=0, description="Centrifugal force in units of g (gravity)")
    duration: float = Field(..., ge=0, description="Duration in seconds")
