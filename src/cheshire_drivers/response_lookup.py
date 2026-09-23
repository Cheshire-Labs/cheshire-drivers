"""Cross-category lookup for the typed Response model paired with a command.

A gateway server's service layer calls :func:`lookup_response_model` with
a command name and the device's advertised interface set; the helper picks
the right per-category ``*_RESPONSE_MODELS`` dict and returns the typed
Response class. This is the return-side counterpart to the existing
``validate_*_payload`` chain in ``DeviceController.execute_command`` -- one
entry point that knows the wire shape on the way out.

Mirrors the interface-gating pattern already established in
:mod:`src.devices.controller.controller`: ``ILiquidHandler`` advertised
means LH commands resolve through ``LH_RESPONSE_MODELS``, ``ITransporter``
through ``TRANSPORTER_RESPONSE_MODELS``, etc. Cross-category commands
(``stop``) and BaseDriver methods (``initialize``, ``open``, ``close``,
``is_initialized``, ``set_temperature``, ``get_temperature``) are handled
by the ``BASE_RESPONSE_MODELS`` fallback.
"""

from typing import Dict

from pydantic import BaseModel

from cheshire_drivers.centrifuge_request_validation import CENTRIFUGE_RESPONSE_MODELS
from cheshire_drivers.command_responses import (
    ConnectedResponse,
    EmptyCommandResponse,
    InitializedResponse,
    TemperatureResponse,
)
from cheshire_drivers.delidder_request_validation import DELIDDER_RESPONSE_MODELS
from cheshire_drivers.lh_request_validation import (
    LH_RESPONSE_MODELS,
    LIQUID_PROBE_RESPONSE_MODELS,
)
from cheshire_drivers.protocol_runner_request_validation import (
    PROTOCOL_RUNNER_RESPONSE_MODELS,
)
from cheshire_drivers.lh_motion_request_validation import (
    FORCE_GRIPPER_JAW_RESPONSE_MODELS,
    GANTRY_PARKING_RESPONSE_MODELS,
    GRIPPER_MOTION_RESPONSE_MODELS,
    GRIPPER_POSITION_RESPONSE_MODELS,
    GRIPPER_ROTATION_RESPONSE_MODELS,
    PIPETTE_MOTION_RESPONSE_MODELS,
    WIDTH_GRIPPER_JAW_RESPONSE_MODELS,
)
from cheshire_drivers.reader_request_validation import READER_RESPONSE_MODELS
from cheshire_drivers.sealer_request_validation import SEALER_RESPONSE_MODELS
from cheshire_drivers.shaker_request_validation import SHAKER_RESPONSE_MODELS
from cheshire_drivers.thermocycler_request_validation import THERMOCYCLER_RESPONSE_MODELS
from cheshire_drivers.transporter_request_validation import TRANSPORTER_RESPONSE_MODELS


# Cross-category and BaseDriver-inherited commands that don't fit any
# single category map. ``stop`` is the cross-device emergency-stop hook;
# ``initialize`` / ``open`` / ``close`` are BaseDriver members on every
# driver; ``is_initialized`` / ``is_connected`` / ``connect`` / ``disconnect`` are
# BaseDriver members too; ``set_temperature`` / ``get_temperature`` come from
# cross-cutting marker interfaces. Contract members are listed explicitly
# rather than left to the unknown-command fallback, which exists for vendor
# extras and cannot carry a typed payload.
BASE_RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    "stop": EmptyCommandResponse,
    "initialize": EmptyCommandResponse,
    "open": EmptyCommandResponse,
    "close": EmptyCommandResponse,
    "is_initialized": InitializedResponse,
    "connect": EmptyCommandResponse,
    "disconnect": EmptyCommandResponse,
    "is_connected": ConnectedResponse,
    "set_temperature": EmptyCommandResponse,
    "get_temperature": TemperatureResponse,
}


_CATEGORY_DICTS: Dict[str, Dict[str, type[BaseModel]]] = {
    "ILiquidHandler": LH_RESPONSE_MODELS,
    "ILiquidProbe": LIQUID_PROBE_RESPONSE_MODELS,
    "ITransporter": TRANSPORTER_RESPONSE_MODELS,
    "IShaker": SHAKER_RESPONSE_MODELS,
    "ICentrifuge": CENTRIFUGE_RESPONSE_MODELS,
    "ISealer": SEALER_RESPONSE_MODELS,
    "IReader": READER_RESPONSE_MODELS,
    "IDelidder": DELIDDER_RESPONSE_MODELS,
    "IThermocycler": THERMOCYCLER_RESPONSE_MODELS,
    "IProtocolRunner": PROTOCOL_RUNNER_RESPONSE_MODELS,
    "IPipetteMotion": PIPETTE_MOTION_RESPONSE_MODELS,
    "IGantryParking": GANTRY_PARKING_RESPONSE_MODELS,
    "IGripperMotion": GRIPPER_MOTION_RESPONSE_MODELS,
    "IGripperPosition": GRIPPER_POSITION_RESPONSE_MODELS,
    "IForceGripperJaw": FORCE_GRIPPER_JAW_RESPONSE_MODELS,
    "IWidthGripperJaw": WIDTH_GRIPPER_JAW_RESPONSE_MODELS,
    "IGripperRotation": GRIPPER_ROTATION_RESPONSE_MODELS,
}


# Properties an operator can READ over the wire. Everything else on an
# interface that is a `@property` is engine-read metadata (`name`,
# `single_carriage`) and stays unreadable.
#
# One constant, three consumers: orca-client's executor uses it to decide what
# to fetch as an attribute instead of calling, the capability gate uses it to
# admit a read the command set deliberately excludes, and the guard test below
# it checks each name still has a typed response model to land in. They drifted
# before -- the gate rejected `is_initialized` while the client happily served
# it -- and a shared constant is what stops that recurring.
WIRE_READABLE_PROPERTIES: frozenset[str] = frozenset({
    "is_initialized",
    "is_connected",
})


def lookup_response_model(
    command: str, interfaces: frozenset[str],
) -> type[BaseModel]:
    """Return the typed Response model for a command on a device.

    Resolution order:

    1. Per-category map gated on the device's advertised interface
       (``ILiquidHandler``/``ITransporter``/etc.). The first interface
       that owns the command wins.
    2. Cross-category / BaseDriver fallback (``BASE_RESPONSE_MODELS``).
    3. ``EmptyCommandResponse`` if the command name is unknown -- this
       lets vendor-specific extras (auto-derived ``capabilities``)
       pass through with the uniform envelope shape rather than failing
       lookup. Vendors that need a typed payload override this by
       extending the per-category map.
    """
    for interface, models in _CATEGORY_DICTS.items():
        if interface in interfaces and command in models:
            return models[command]
    if command in BASE_RESPONSE_MODELS:
        return BASE_RESPONSE_MODELS[command]
    return EmptyCommandResponse
