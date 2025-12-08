import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Literal, Optional, Union

from cheshire_drivers.interfaces import AxisName, ICentrifugeDriver, IDelidderDriver, ILiquidHandlerDriver, IPlateWasherDriver, IProtocolRunnerDriver, IReaderDriver, ISealerDriver, IShakerDriver, IStorageDriver, ITempGettableDriver, ITempSettableDriver, ITransporterDriver, IWasteDriver
from cheshire_drivers.teachpoints import CartesianCoordinates, JointCoordinates, Teachpoint

logger = logging.getLogger("cheshire_drivers")


class Sim(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...
    @abstractmethod
    async def _sim(self, message: str) -> None: ...

class SimStrategy(ABC):
    @abstractmethod
    async def sim(self, prompt: str) -> None:
        ...

class SleepSim(SimStrategy):
    def __init__(self, sim_time: float = 0.2) -> None:
        self.sim_time = sim_time

    async def sim(self, prompt: str) -> None:
        # keep the prompt visible in logs for traceability
        prompt = f"{prompt} (simulated wait for {self.sim_time} seconds)"
        logger.info(prompt)
        await asyncio.sleep(self.sim_time)

class HumanSim(SimStrategy):
    """Waits for the user to press Enter without blocking the event loop."""
    def __init__(self, prompt_suffix: str = " Press Enter to continue.") -> None:
        self.prompt_suffix = prompt_suffix

    async def sim(self, prompt: str) -> None:
        full = f"{prompt}{self.prompt_suffix}"
        input(full)

class BaseSimDriver:
    """Base simulation driver with common functionality"""

    def __init__(self, name: str, sim_strategy: Optional[SimStrategy] = None):
        self._name = name
        self._sim_strategy = sim_strategy or SleepSim()
        self._is_initialized: bool = False
        self._is_connected: bool = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    async def initialize(self) -> None:
        await self._sim(f"Initialization of {self.name} in progress...")
        self._is_initialized = True
        logger.info(f"{self.name} initialized successfully.")

    async def connect(self) -> None:
        await self._sim(f"Connecting {self.name}...")
        self._is_connected = True
        logger.info(f"{self.name} connected successfully.")

    async def disconnect(self) -> None:
        await self._sim(f"Disconnecting {self.name}...")
        self._is_connected = False
        logger.info(f"{self.name} disconnected successfully.")

    async def stop(self) -> None:
        """Stop the driver."""
        await self._sim(f"Stopping {self.name}...")
        logger.info(f"{self.name} stopped successfully.")

    async def open(self) -> None:
        """Opens the door of the device"""
        await self._sim(f"Opening {self.name}...")
        logger.info(f"{self.name} open door successfully")

    async def close(self) -> None:
        """Closes the door of the device"""
        await self._sim(f"Closing {self.name}...")
        logger.info(f"{self.name} close door successfully")

    async def _sim(self, message: str) -> None:
        await self._sim_strategy.sim(message)


# Mixins for simulation functionality

class ShakerSimMixin(Sim, IShakerDriver):
    """Mixin for shaker simulation functionality"""
    _supports_locking = True

    async def shake(self, speed: float, duration: float) -> None:
        """Shake the device for a specified duration and speed."""
        await self._sim(f"Shaking at speed {speed} for {duration} seconds...")
        logger.info("Shaking completed successfully.")

    async def stop_shaking(self) -> None:
        """Stop shaking"""
        await self._sim("Stopping shaking...")
        logger.info("Shaking stopped.")

    @property
    def supports_locking(self) -> bool:
        """Check if the shaker supports locking the plate"""
        return self._supports_locking

    async def lock_plate(self) -> None:
        """Lock the plate"""
        await self._sim("Locking plate...")
        logger.info("Plate locked.")

    async def unlock_plate(self) -> None:
        """Unlock the plate"""
        await self._sim("Unlocking plate...")
        logger.info("Plate unlocked.")


class SealerSimMixin(Sim, ISealerDriver):
    """Mixin for sealer simulation functionality"""
    async def open(self) -> None:
        """Open the sealer."""
        await self._sim("Opening sealer...")
        logger.info("Sealer opened successfully.")

    async def close(self) -> None:
        """Close the sealer."""
        await self._sim("Closing sealer...")
        logger.info("Sealer closed successfully.")

    async def seal(self, temperature: int, duration: float) -> None:
        """Seal the plate at a specified temperature and duration."""
        await self._sim(f"Sealing at {temperature}°C for {duration} seconds...")
        logger.info("Sealing completed successfully.")


class TempSettableSimMixin(Sim, ITempSettableDriver):
    """Mixin for temperature settable functionality"""

    async def set_temperature(self, temperature: float) -> None:
        """Set the temperature of the device."""
        await self._sim(f"Setting temperature to {temperature}°C...")
        logger.info(f"Temperature set to {temperature}°C.")


class TempGettableSimMixin(Sim, ITempGettableDriver):
    """Mixin for temperature gettable functionality"""

    async def get_temperature(self) -> float:
        """Get the current temperature of the device."""
        logger.info("Getting current temperature.")
        return 25.0  # Mock temperature value

class ProtocolRunnerSimMixin(Sim, IProtocolRunnerDriver):
    """Mixin for protocol runner functionality"""

    async def run_protocol(self, protocol_filepath: str, params: Dict[str, Any]) -> None:
        """Run a protocol with given parameters."""
        await self._sim(f"Running protocol: {protocol_filepath} with params: {params}...")
        logger.info(f"Protocol {protocol_filepath} executed successfully.")


class CentrifugeSimMixin(Sim, ICentrifugeDriver):
    """Mixin for centrifuge functionality"""

    async def open(self) -> None:
        """Open the centrifuge."""
        await self._sim("Opening centrifuge...")
        logger.info("Centrifuge opened successfully.")

    async def close(self) -> None:
        """Close the centrifuge."""
        await self._sim("Closing centrifuge...")
        logger.info("Centrifuge closed successfully.")

    async def centrifuge(self, g: float, duration: float) -> None:
        """Spin the centrifuge at a specified speed for a specified duration."""
        await self._sim(f"Spinning centrifuge for {duration} seconds at speed {g}...")
        logger.info("Centrifuge spin completed successfully.")


class ReaderSimMixin(Sim, IReaderDriver):
    """Mixin for reader functionality"""

    async def read(self, protocol_filepath: str, output_filepath: str) -> None:
        """Read data from the specified protocol and output to a file."""
        await self._sim(f"Reading data from {protocol_filepath} and writing to {output_filepath}...")
        logger.info(f"Data read successfully from {protocol_filepath} and written to {output_filepath}.")


class DelidderSimMixin(Sim, IDelidderDriver):
    """Mixin for delidder functionality"""

    async def delid(self) -> None:
        """Delid the specified labware."""
        await self._sim("Delidding labware...")
        logger.info("Labware delidded successfully.")


class SimTransporterDriver(ITransporterDriver):
    """Simulation transporter driver using mixin"""

    def __init__(self, name: str, sim_strategy: Optional[SimStrategy] = None) -> None:
        self._name = name
        self._is_initialized = False
        self._sim_strategy = sim_strategy or SleepSim()
        self._teachpoints: Dict[str, Teachpoint] = {}

    @property
    def name(self) -> str:
        return self._name

    async def _sim(self, message: str) -> None:
        await self._sim_strategy.sim(message)

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def initialize(self) -> None:
        logger.info(f"Initializing transporter driver: {self.name}...")
        self._is_initialized = True
        logger.info(f"Transporter driver {self.name} initialized successfully.")

    async def home(self) -> None:
        """Homes the transporter."""
        await self._sim(f"Driver: {self.name} homing...")
        logger.info(f"Driver: {self.name} homed successfully.")

    async def move_to_safe(self) -> None:
        """Moves the transporter to a safe position."""
        await self._sim(f"Driver: {self.name} moving to safe position...")
        logger.info(f"Driver: {self.name} moved to safe position successfully.")

    async def pick(self, position_name: str, labware_type: str) -> None:
        """Pick labware from a position."""
        await self._sim(f"Driver: {self.name} picking from {position_name}, labware type: {labware_type}...")
        logger.info(f"Driver: {self.name} picked from {position_name}, labware type: {labware_type}")

    async def place(self, position_name: str, labware_type: str) -> None:
        """Place labware at a position."""
        await self._sim(f"Driver: {self.name} placing to {position_name}, labware type: {labware_type}...")
        logger.info(f"Driver: {self.name} placed to {position_name}, labware type: {labware_type}")

    async def move_to_position(self, position_name: str) -> None:
        """Move to position without picking/placing (for waypoints)."""
        await self._sim(f"Driver: {self.name} moving through waypoint {position_name}...")
        logger.info(f"Driver: {self.name} moved through waypoint {position_name}")

    def get_teachpoints(self) -> List[Teachpoint]:
        """Get list of taught positions."""
        return list(self._teachpoints.values())

    def load_teachpoints(self, teachpoints: List[Teachpoint]) -> None:
        """Load taught positions."""
        self._teachpoints = {t.name: t for t in teachpoints}

    async def pick_at_coords(self, teachpoint: Teachpoint) -> None:
        """Pick plate at coordinates specified by teachpoint."""
        coords = teachpoint.coordinates
        if isinstance(coords, CartesianCoordinates):
            await self._sim(f"Driver: {self.name} picking at coords ({coords.x}, {coords.y}, {coords.z})...")
            logger.info(f"Driver: {self.name} picked at coords ({coords.x}, {coords.y}, {coords.z})")
        else:
            assert isinstance(coords, JointCoordinates)
            await self._sim(f"Driver: {self.name} picking at joint coords (elbow={coords.elbow})...")
            logger.info(f"Driver: {self.name} picked at joint coords (elbow={coords.elbow})")

    async def place_at_coords(self, teachpoint: Teachpoint) -> None:
        """Place plate at coordinates specified by teachpoint."""
        coords = teachpoint.coordinates
        if isinstance(coords, CartesianCoordinates):
            await self._sim(f"Driver: {self.name} placing at coords ({coords.x}, {coords.y}, {coords.z})...")
            logger.info(f"Driver: {self.name} placed at coords ({coords.x}, {coords.y}, {coords.z})")
        else:
            assert isinstance(coords, JointCoordinates)
            await self._sim(f"Driver: {self.name} placing at joint coords (elbow={coords.elbow})...")
            logger.info(f"Driver: {self.name} placed at joint coords (elbow={coords.elbow})")

    async def move_to_coords(self, teachpoint: Teachpoint) -> None:
        """Move to coordinates specified by teachpoint."""
        coords = teachpoint.coordinates
        if isinstance(coords, CartesianCoordinates):
            await self._sim(f"Driver: {self.name} moving to coords ({coords.x}, {coords.y}, {coords.z})...")
            logger.info(f"Driver: {self.name} moved to coords ({coords.x}, {coords.y}, {coords.z})")
        else:
            assert isinstance(coords, JointCoordinates)
            await self._sim(f"Driver: {self.name} moving to joint coords (elbow={coords.elbow})...")
            logger.info(f"Driver: {self.name} moved to joint coords (elbow={coords.elbow})")

    async def get_joint_position(self) -> JointCoordinates:
        """Return simulated joint position (default safe position)."""
        return JointCoordinates(rail=0.0, base=170.0, shoulder=0.0, elbow=180.0, wrist=0.0, gripper=0.0)

    async def move_single_axis(self, axis: AxisName, position: float) -> None:
        """Move a single axis to absolute position."""
        await self._sim(f"Driver: {self.name} moving {axis} to {position}...")
        logger.info(f"Driver: {self.name} moved {axis} to {position}")

    async def move_single_axis_relative(self, axis: AxisName, distance: float) -> None:
        """Move a single axis by relative distance from current position."""
        await self._sim(f"Driver: {self.name} moving {axis} by {distance}...")
        logger.info(f"Driver: {self.name} moved {axis} by {distance}")

    async def set_free_mode(self, axes: Union[List[AxisName], Literal["all", "none"]]) -> None:
        """Enable/disable free mode (freedrive) for specified axes."""
        await self._sim(f"Driver: {self.name} setting free mode: {axes}...")
        logger.info(f"Driver: {self.name} free mode set to {axes}")


class StorageSimMixin(Sim, IStorageDriver):
    pass

class LiquidHandlerSimMixin(ProtocolRunnerSimMixin, ILiquidHandlerDriver):
    pass

class PlateWasherSimMixin(ProtocolRunnerSimMixin, IPlateWasherDriver):
    pass

class WasteSimMixin(StorageSimMixin, IWasteDriver):
    pass


### Sim Drivers ###
class SimDriver(BaseSimDriver):
    """
    A simulation device driver that extends BaseSimDriver.
    """
    pass

class SimShakerDriver(BaseSimDriver, ShakerSimMixin):
    """Simulation shaker driver using mixin"""
    pass

class SimSealerDriver(BaseSimDriver, SealerSimMixin, TempSettableSimMixin, TempGettableSimMixin):
    """Simulation sealer driver using mixins"""
    pass

class SimCentrifugeDriver(BaseSimDriver, CentrifugeSimMixin):
    """Simulation centrifuge driver using mixin"""
    pass

class SimStorageDriver(BaseSimDriver, StorageSimMixin):
    pass

class SimPlateWasherDriver(BaseSimDriver, PlateWasherSimMixin):
    pass

class SimLiquidHandlerDriver(BaseSimDriver, LiquidHandlerSimMixin):
    pass

class SimDelidderDriver(BaseSimDriver, DelidderSimMixin):
    pass

class SimWasteDriver(BaseSimDriver, WasteSimMixin):
    pass

class SimReaderDriver(BaseSimDriver, ReaderSimMixin):
    pass
