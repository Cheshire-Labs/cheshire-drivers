"""Pydantic models for reader (plate reader) driver interface.

Single category-specific method (read).
"""

from pydantic import Field

from cheshire_drivers.liquid_handler_models import _StrictModel


class ReadRequest(_StrictModel):
    protocol_filepath: str = Field(..., description="Path to the protocol file to execute")
    output_filepath: str = Field(..., description="Path where the read results will be written")
