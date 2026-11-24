"""Response base classes and the scalar responses every device kind shares.

orca-client wraps a driver's return value in one of these before sending it back.
The command table (``command_table.py``) says which one each command returns.
"""

from pydantic import BaseModel, ConfigDict


class CommandResponse(BaseModel):
    """Abstract base for every per-operation response payload.

    Subclasses set ``model_config`` if they need to relax ``extra="forbid"``
    for vendor-specific shapes; the default is strict so typos in driver
    return wrapping fail loud at validation time rather than silently
    propagating to clients.
    """

    model_config = ConfigDict(extra="forbid")


class EmptyCommandResponse(CommandResponse):
    """Marker payload for ack-only operations.

    Use this concrete class -- not a bare ``CommandResponse`` -- when an
    operation completes with no data to return. ``SuccessEnvelope[T]``
    will then render ``"result": {}`` rather than omitting the field, so
    the envelope shape stays uniform.
    """


class InitializedResponse(CommandResponse):
    """Response payload for ``is_initialized`` queries on any driver."""

    is_initialized: bool


class ConnectedResponse(CommandResponse):
    """Response payload for ``is_connected`` queries on any driver.

    The DEVICE's link, not the on-prem client's: those are two different
    connections and the client one never travels this wire.
    """

    is_connected: bool


class TemperatureResponse(CommandResponse):
    """Response payload for ``get_temperature`` queries (sealer, etc.)."""

    temperature: float
