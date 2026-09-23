"""Per-operation Response payload base classes + cross-cutting scalars.

These are the typed return-side counterpart to the existing per-domain
Request models in ``*_request_validation.py`` modules. orca-client
wraps a driver's return value into a concrete subclass before sending
it back over the gateway WebSocket; the gateway server builds a
``SuccessEnvelope[ConcreteResponse]`` around it for MCP and REST.

Hierarchy:

  * :class:`CommandResponse` -- abstract base. Marker so the wire
    contract is identifiable in the SuccessEnvelope generic.
  * :class:`EmptyCommandResponse` -- ack-only marker. Used for
    operations that complete without a meaningful payload (initialize,
    open, close, shake, stop, lock_plate, move_plate, etc.). Keeps
    ``result`` always populated as ``{}`` rather than omitted.
  * :class:`InitializedResponse` -- single-field bool wrapper for
    ``is_initialized`` queries on any driver.
  * :class:`TemperatureResponse` -- single-field float wrapper for
    ``get_temperature`` queries on sealers, etc.

Per-category response shapes (LabwareStateResponse, DeckStateResponse,
SpeedResponse, JointCoordinates, CartesianCoordinates) live alongside
their matching Request models in the per-category model files
(``liquid_handler_models.py``, ``transporter_models.py``,
``teachpoints.py``). The mapping ``command -> ResponseClass`` lives in
each category's ``*_request_validation.py:*_RESPONSE_MODELS`` dict;
:func:`cheshire_drivers.response_lookup.lookup_response_model` is the
single entry point that resolves to the right class.
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
