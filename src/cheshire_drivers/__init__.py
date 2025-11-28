"""Cheshire Drivers - Shared driver layer for lab automation.

This package provides driver interfaces, PLR wrappers, and simulation drivers
for lab automation equipment. It is shared between orca-core and client-driver.
"""

# Interfaces
from cheshire_drivers.interfaces import (
    BaseDriver,
    IShakerDriver,
    ISealerDriver,
    ITempSettableDriver,
    ITempGettableDriver,
    IProtocolRunnerDriver,
    ICentrifugeDriver,
    IReaderDriver,
    IDelidderDriver,
    ITransporterDriver,
    IStorageDriver,
    IPlateWasherDriver,
    ILiquidHandlerDriver,
    IWasteDriver,
)

# PLR Wrappers
from cheshire_drivers.plr_wrappers import (
    PLRTransporterBackendWrapper,
    PLRSealerBackendWrapper,
    PLRShakerBackendWrapper,
    PLRCentrifugeBackendWrapper,
    convert_teachpoint_to_plr_coord,
)

# Simulation Drivers
from cheshire_drivers.sims import (
    SimStrategy,
    SleepSim,
    HumanSim,
    BaseSimDriver,
    SimDriver,
    SimShakerDriver,
    SimSealerDriver,
    SimCentrifugeDriver,
    SimTransporterDriver,
    SimStorageDriver,
    SimPlateWasherDriver,
    SimLiquidHandlerDriver,
    SimDelidderDriver,
    SimWasteDriver,
    SimReaderDriver,
    # Mixins for composing custom drivers
    ShakerSimMixin,
    CentrifugeSimMixin,
    ReaderSimMixin,
    DelidderSimMixin,
    ProtocolRunnerSimMixin,
)

# Teachpoints
from cheshire_drivers.teachpoints import (
    Teachpoint,
    CartesianCoordinates,
    TeachpointsRegistry,
    AccessConfig,
)

# Specialized Drivers
from cheshire_drivers.venus_driver import (
    VenusProtocolDriver,
    SimulationVenusProtocolDriver,
)
from cheshire_drivers.null_plate_pad import NullPlatePadDriver

__all__ = [
    # Interfaces
    "BaseDriver",
    "IShakerDriver",
    "ISealerDriver",
    "ITempSettableDriver",
    "ITempGettableDriver",
    "IProtocolRunnerDriver",
    "ICentrifugeDriver",
    "IReaderDriver",
    "IDelidderDriver",
    "ITransporterDriver",
    "IStorageDriver",
    "IPlateWasherDriver",
    "ILiquidHandlerDriver",
    "IWasteDriver",
    # PLR Wrappers
    "PLRTransporterBackendWrapper",
    "PLRSealerBackendWrapper",
    "PLRShakerBackendWrapper",
    "PLRCentrifugeBackendWrapper",
    "convert_teachpoint_to_plr_coord",
    # Simulation Drivers
    "SimStrategy",
    "SleepSim",
    "HumanSim",
    "BaseSimDriver",
    "SimDriver",
    "SimShakerDriver",
    "SimSealerDriver",
    "SimCentrifugeDriver",
    "SimTransporterDriver",
    "SimStorageDriver",
    "SimPlateWasherDriver",
    "SimLiquidHandlerDriver",
    "SimDelidderDriver",
    "SimWasteDriver",
    "SimReaderDriver",
    # Mixins
    "ShakerSimMixin",
    "CentrifugeSimMixin",
    "ReaderSimMixin",
    "DelidderSimMixin",
    "ProtocolRunnerSimMixin",
    # Teachpoints
    "Teachpoint",
    "CartesianCoordinates",
    "TeachpointsRegistry",
    "AccessConfig",
    # Specialized Drivers
    "VenusProtocolDriver",
    "SimulationVenusProtocolDriver",
    "NullPlatePadDriver",
]
