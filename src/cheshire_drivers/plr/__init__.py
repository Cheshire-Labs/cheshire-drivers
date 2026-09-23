"""Pre-built drivers and labware adapters wrapping PyLabRobot backends.

Users import from this module instead of importing PLR backends directly.

Drivers:
    from cheshire_drivers.plr import A4SSealerDriver
    sealer = Sealer("sealer", A4SSealerDriver(port="/dev/tty.usb"))

Labware:
    from cheshire_drivers.plr.labware import plr_plate_factory
    plate = PlateTemplate("plate", plr_plate_factory(Cor_Falcon_96_wellplate))

PLR concrete-type re-exports:
    Consumers that need PLR's concrete `Plate`/`TipRack`/`Trough`/`Resource`
    types import them from this module instead of `pylabrobot.resources.*`,
    keeping PLR a transitive dependency of cheshire-drivers only.

PLR backend lookup:
    Consumers that need to dynamically resolve a PLR backend class by name
    call `get_plr_backend_class` instead of doing their own
    `__import__('pylabrobot.<sub>')` dispatch.
"""

from cheshire_drivers._plr_compat import ensure_plr_stubs

ensure_plr_stubs()

from pylabrobot import resources as plr_resources
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.resources.trough import Trough

from cheshire_drivers.plr.backends import get_plr_backend_class
from cheshire_drivers.plr.sealer import A4SSealerDriver
from cheshire_drivers.plr.shaker import ChatterboxShakerDriver
from cheshire_drivers.plr.centrifuge import (
    ChatterboxCentrifugeDriver,
    VSpinCentrifugeDriver,
)
from cheshire_drivers.plr.thermocycler import ChatterboxThermocyclerDriver
from cheshire_drivers.plr.heating_shaker import ChatterboxHeatingShakerDriver
from cheshire_drivers.plr.liquid_handler import (
    ChatterboxLiquidHandlerDriver,
    ChatterboxLiquidHandlerWithProtocolDriver,
    OT2LiquidHandlerDriver,
    STARLiquidHandlerDriver,
)
from cheshire_drivers.plr.opentrons_flex import FlexLiquidHandlerDriver
from cheshire_drivers.plr.reader import ChatterboxReaderDriver
from cheshire_drivers.plr.storage import ChatterboxStorageDriver
from cheshire_drivers.plr.transporter import PreciseFlexTransporterDriver
from cheshire_drivers.plr.transporter_wrapper import (
    PLRArmBackend,
    PLRTransporterBackendWrapper,

    convert_cartesian_to_plr_coord,
    convert_joint_to_plr_dict,
    transporter_driver,
)
from cheshire_drivers.plr.labware import (
    PLRPlateAdapter,
    PLRWellAdapter,
    PLRTipRackAdapter,
    PLRTipSpotAdapter,
    PLRTroughAdapter,
    plr_plate_factory,
    plr_tip_rack_factory,
    plr_trough_factory,
)
from cheshire_drivers.plr.labware_converter import PLRLabwareConverter

__all__ = [
    # PLR concrete-type re-exports
    "Plate",
    "Resource",
    "TipRack",
    "Trough",
    # PLR namespace re-export for by-name factory dispatch (the envelope resolver)
    "plr_resources",
    # PLR backend lookup
    "get_plr_backend_class",
    # Drivers
    "A4SSealerDriver",
    "ChatterboxCentrifugeDriver",
    "ChatterboxHeatingShakerDriver",
    "ChatterboxLiquidHandlerDriver",
    "ChatterboxLiquidHandlerWithProtocolDriver",
    "FlexLiquidHandlerDriver",
    "OT2LiquidHandlerDriver",
    "STARLiquidHandlerDriver",
    "ChatterboxReaderDriver",
    "ChatterboxShakerDriver",
    "ChatterboxStorageDriver",
    "ChatterboxThermocyclerDriver",
    "VSpinCentrifugeDriver",
    "PreciseFlexTransporterDriver",
    # Transporter/arm wrapper + factory (need pylabrobot.arms)
    "PLRArmBackend",
    "PLRTransporterBackendWrapper",

    "convert_cartesian_to_plr_coord",
    "convert_joint_to_plr_dict",
    "transporter_driver",
    # Labware adapters
    "PLRPlateAdapter",
    "PLRWellAdapter",
    "PLRTipRackAdapter",
    "PLRTipSpotAdapter",
    "PLRTroughAdapter",
    # Labware factory wrappers
    "plr_plate_factory",
    "plr_tip_rack_factory",
    "plr_trough_factory",
    # Labware converter
    "PLRLabwareConverter",
]
