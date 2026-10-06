"""Cheshire Drivers - Shared driver layer for lab automation.

This package provides driver interfaces, PLR wrappers, and simulation drivers
for lab automation equipment. It is shared between orca-core and orca-client.

Nothing is imported at module level. Every name below is fetched on first
access, because importing one wire model used to load the PLR wrappers, and
through them pylabrobot and matplotlib. `__init__.pyi` holds the real import
statements, so `from cheshire_drivers import X` stays fully typed.
"""

import importlib
from typing import Any

# Name -> the module that defines it. Serves `from cheshire_drivers import X`
# without importing anything else in the package.
_EXPORTS: dict[str, str] = {
    "AccessConfig": "cheshire_drivers.teachpoints",
    "AddDeckLabwareRequest": "cheshire_drivers.liquid_handler_models",
    "Aspirate96Request": "cheshire_drivers.liquid_handler_models",
    "AspirateRequest": "cheshire_drivers.liquid_handler_models",
    "AspirateTarget": "cheshire_drivers.liquid_handler_models",
    "BaseDriver": "cheshire_drivers.interfaces",
    "BaseSimDriver": "cheshire_drivers.sims",
    "CartesianCoordinates": "cheshire_drivers.teachpoints",
    "CentrifugeSimMixin": "cheshire_drivers.sims",
    "DeckLayoutConfig": "cheshire_drivers.liquid_handler_models",
    "DeckResourceConfig": "cheshire_drivers.liquid_handler_models",
    "DelidderSimMixin": "cheshire_drivers.sims",
    "Dispense96Request": "cheshire_drivers.liquid_handler_models",
    "DispenseRequest": "cheshire_drivers.liquid_handler_models",
    "DispenseTarget": "cheshire_drivers.liquid_handler_models",
    "DropTips96Request": "cheshire_drivers.liquid_handler_models",
    "DropTipsRequest": "cheshire_drivers.liquid_handler_models",
    "FaultRegistry": "cheshire_drivers.faults",
    "FaultSpec": "cheshire_drivers.faults",
    "HangFault": "cheshire_drivers.faults",
    "HumanSim": "cheshire_drivers.sims",
    "HumanTransporterDriver": "cheshire_drivers.human_transporter_driver",
    "ICentrifugeDriver": "cheshire_drivers.interfaces",
    "IContainer": "cheshire_drivers.labware_interfaces",
    "IDelidderDriver": "cheshire_drivers.interfaces",
    "IForceGripperJawDriver": "cheshire_drivers.interfaces",
    "IGripperMotionDriver": "cheshire_drivers.interfaces",
    "IGripperPositionDriver": "cheshire_drivers.interfaces",
    "IGripperRotationDriver": "cheshire_drivers.interfaces",
    "ILiquidHandlerDriver": "cheshire_drivers.interfaces",
    "ILiquidHandlerWithProtocolDriver": "cheshire_drivers.interfaces",
    "ILiquidProbeDriver": "cheshire_drivers.interfaces",
    "IPipetteMotionDriver": "cheshire_drivers.interfaces",
    "IPlate": "cheshire_drivers.labware_interfaces",
    "IPlateWasherDriver": "cheshire_drivers.interfaces",
    "IProtocolRunnerDriver": "cheshire_drivers.interfaces",
    "IgnoresLabwareHandoff": "cheshire_drivers.labware_handoff",
    "IReaderDriver": "cheshire_drivers.interfaces",
    "ISealerDriver": "cheshire_drivers.interfaces",
    "IShakerDriver": "cheshire_drivers.interfaces",
    "IStorageDriver": "cheshire_drivers.interfaces",
    "ITeachpointStore": "cheshire_drivers.teachpoints",
    "ITempGettableDriver": "cheshire_drivers.interfaces",
    "ITempSettableDriver": "cheshire_drivers.interfaces",
    "IThermocyclerDriver": "cheshire_drivers.interfaces",
    "ITipRack": "cheshire_drivers.labware_interfaces",
    "ITipSpot": "cheshire_drivers.labware_interfaces",
    "ITransporterDriver": "cheshire_drivers.interfaces",
    "ITrough": "cheshire_drivers.labware_interfaces",
    "IWasteDriver": "cheshire_drivers.interfaces",
    "IWell": "cheshire_drivers.labware_interfaces",
    "IWidthGripperJawDriver": "cheshire_drivers.interfaces",
    "InMemoryTeachpointStore": "cheshire_drivers.teachpoints",
    "JointCoordinates": "cheshire_drivers.teachpoints",
    "LabwareIdentity": "cheshire_drivers.labware_models",
    "LabwareStateResponse": "cheshire_drivers.liquid_handler_models",
    "LabwareWellState": "cheshire_drivers.liquid_handler_models",
    "LiquidHandlerSimMixin": "cheshire_drivers.sims",
    "LiquidProbeRequest": "cheshire_drivers.liquid_handler_models",
    "LiquidProbeResponse": "cheshire_drivers.liquid_handler_models",
    "LiquidProbeSimMixin": "cheshire_drivers.sims",
    "MixParams": "cheshire_drivers.pipetting",
    "MixParamsModel": "cheshire_drivers.liquid_handler_models",
    "MixRequest": "cheshire_drivers.liquid_handler_models",
    "MovePlateRequest": "cheshire_drivers.liquid_handler_models",
    "NullPlatePadDriver": "cheshire_drivers.null_plate_pad",
    "NullTeachpointStore": "cheshire_drivers.teachpoints",
    "PLRArmBackend": "cheshire_drivers.plr.transporter_wrapper",
    "PLRCentrifugeBackend": "cheshire_drivers.plr_wrappers",
    "PLRCentrifugeBackendWrapper": "cheshire_drivers.plr_wrappers",
    "PLRLiquidHandlerWrapper": "cheshire_drivers.plr_wrappers",
    "PLRSealerBackend": "cheshire_drivers.plr_wrappers",
    "PLRSealerBackendWrapper": "cheshire_drivers.plr_wrappers",
    "PLRShakerBackend": "cheshire_drivers.plr_wrappers",
    "PLRShakerBackendWrapper": "cheshire_drivers.plr_wrappers",
    "PLRThermocyclerBackend": "cheshire_drivers.plr_wrappers",
    "PLRThermocyclerBackendWrapper": "cheshire_drivers.plr_wrappers",
    "PLRTransporterBackendWrapper": "cheshire_drivers.plr.transporter_wrapper",
    "PartialFault": "cheshire_drivers.faults",
    "PickUpTips96Request": "cheshire_drivers.liquid_handler_models",
    "PickUpTipsRequest": "cheshire_drivers.liquid_handler_models",
    "PipettingParameters": "cheshire_drivers.liquid_handler_models",
    "PipettingPatch": "cheshire_drivers.liquid_handler_models",
    "PipettingProfile": "cheshire_drivers.pipetting",
    "PlateWasherSimMixin": "cheshire_drivers.sims",
    "ProtocolRunnerSimMixin": "cheshire_drivers.sims",
    "RaiseFault": "cheshire_drivers.faults",
    "ReaderSimMixin": "cheshire_drivers.sims",
    "RecordedCall": "cheshire_drivers.sims",
    "RecordingLiquidHandlerDriver": "cheshire_drivers.sims",
    "RecordingShakerDriver": "cheshire_drivers.sims",
    "RemoveDeckLabwareRequest": "cheshire_drivers.liquid_handler_models",
    "SEED_PIPETTING_PARAMETERS": "cheshire_drivers.liquid_handler_models",
    "SealerSimMixin": "cheshire_drivers.sims",
    "ShakerSimMixin": "cheshire_drivers.sims",
    "SimCentrifugeDriver": "cheshire_drivers.sims",
    "SimDelidderDriver": "cheshire_drivers.sims",
    "SimDriver": "cheshire_drivers.sims",
    "SimLiquidHandlerDriver": "cheshire_drivers.sims",
    "SimLiquidHandlerWithProtocolDriver": "cheshire_drivers.sims",
    "SimPlateWasherDriver": "cheshire_drivers.sims",
    "SimReaderDriver": "cheshire_drivers.sims",
    "SimSealerDriver": "cheshire_drivers.sims",
    "SimShakerDriver": "cheshire_drivers.sims",
    "SimStorageDriver": "cheshire_drivers.sims",
    "SimStrategy": "cheshire_drivers.sims",
    "SimThermocyclerDriver": "cheshire_drivers.sims",
    "SimTranslatorDriver": "cheshire_drivers.translator_driver",
    "SimTransporterDriver": "cheshire_drivers.sims",
    "SimTransporterValidationError": "cheshire_drivers.sims",
    "SimWasteDriver": "cheshire_drivers.sims",
    "SimulationVenusProtocolDriver": "cheshire_drivers.venus_driver",
    "SleepSim": "cheshire_drivers.sims",
    "StorageSimMixin": "cheshire_drivers.sims",
    "Teachpoint": "cheshire_drivers.teachpoints",
    "TeachpointsRegistry": "cheshire_drivers.teachpoints",
    "TempGettableSimMixin": "cheshire_drivers.sims",
    "TempSettableSimMixin": "cheshire_drivers.sims",
    "ThermocyclerSimMixin": "cheshire_drivers.sims",
    "TipPick": "cheshire_drivers.liquid_handler_models",
    "VenusProtocolDriver": "cheshire_drivers.venus_driver",
    "WasteSimMixin": "cheshire_drivers.sims",
    "centrifuge_driver": "cheshire_drivers.driver_factories",
    "convert_cartesian_to_plr_coord": "cheshire_drivers.plr.transporter_wrapper",
    "convert_joint_to_plr_dict": "cheshire_drivers.plr.transporter_wrapper",
    "resolve_exception_class": "cheshire_drivers.faults",
    "sealer_driver": "cheshire_drivers.driver_factories",
    "shaker_driver": "cheshire_drivers.driver_factories",
    "thermocycler_driver": "cheshire_drivers.driver_factories",
    "transporter_driver": "cheshire_drivers.plr.transporter_wrapper",
}

# Derived, not repeated: a literal list here would be a second copy of the map
# above, and pyright would flag every name as absent from the module.
__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Import the owning module on first access and cache the result.

    Typed `Any` because the exported names share no useful supertype. The real
    signatures live in `__init__.pyi`, which is what type checkers read.
    """
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(_EXPORTS)
