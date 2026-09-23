from abc import ABC, abstractmethod
from typing import ClassVar, Literal

from cheshire_drivers.command_timings import command_timing
from cheshire_drivers.driver_introspection import external
from cheshire_drivers.liquid_handler_models import (
    AspirateRequest, Aspirate96Request,
    DeckLayoutConfig,
    DeckStateResponse,
    DiscardTipsRequest,
    DispenseRequest, Dispense96Request,
    DropTipsRequest, DropTips96Request,
    GetDeckStateRequest,
    GetHeadConfigurationRequest,
    HeadConfigurationResponse,
    LabwareStateResponse,
    LiquidProbeRequest,
    LiquidProbeResponse,
    MixRequest, MovePlateRequest,
    PickUpTipsRequest, PickUpTips96Request,
    AddDeckLabwareRequest,
    DiscardStrandedTipsRequest,
    ReconcileDeckOccupancyRequest,
    ReconcileHardwareStateRequest,
    ReconcileHardwareStateResponse,
    ResetDeckLabwareRequest,
    ReturnTips96Request,
    RemoveDeckLabwareRequest,
)
from cheshire_drivers.centrifuge_models import CentrifugeRequest
from cheshire_drivers.thermocycler_models import (
    CycleCountResponse,
    CycleIndexResponse,
    CloseLidRequest,
    DeactivateBlockRequest,
    DeactivateLidRequest,
    GetBlockCurrentTemperatureRequest,
    GetBlockStatusRequest,
    GetBlockTargetTemperatureRequest,
    GetCurrentCycleIndexRequest,
    GetCurrentStepIndexRequest,
    GetHoldTimeRequest,
    GetLidCurrentTemperatureRequest,
    GetLidOpenRequest,
    GetLidStatusRequest,
    GetLidTargetTemperatureRequest,
    GetTotalCycleCountRequest,
    GetTotalStepCountRequest,
    HoldTimeResponse,
    LidOpenResponse,
    OpenLidRequest,
    RunProtocolRequest as ThermocyclerRunProtocolRequest,
    SetBlockTemperatureRequest,
    SetLidTemperatureRequest,
    StepCountResponse,
    StepIndexResponse,
    TemperatureListResponse,
    ThermocyclerStatusResponse,
)
from cheshire_drivers.delidder_models import DelidRequest
from cheshire_drivers.gantry_models import ParkGantryRequest
from cheshire_drivers.protocol_runner_models import RunProtocolRequest
from cheshire_drivers.reader_models import ReadRequest
from cheshire_drivers.sealer_models import SealRequest
from cheshire_drivers.shaker_models import (
    LockPlateRequest,
    ShakeRequest,
    StopShakingRequest,
    UnlockPlateRequest,
)
from cheshire_drivers.homing_models import HomeRequest
from cheshire_drivers.teachpoints import (
    CartesianCoordinates,
    JointCoordinates,
    Teachpoint,
)
from cheshire_drivers.transporter_models import (
    CloseGripperRequest,
    EnsureSeededRequest,
    GetCartesianPositionRequest,
    GetJointPositionRequest,
    GetSpeedRequest,
    HaltRequest,
    InitializeRequest,
    MoveSingleAxisRelativeRequest,
    MoveSingleAxisRequest,
    MoveToCoordsRequest,
    MoveToSafeRequest,
    OpenGripperRequest,
    PickAtCoordsRequest,
    PlaceAtCoordsRequest,
    ResetWorldRequest,
    SeedPositionRequest,
    SetFreeModeRequest,
    SetSpeedRequest,
    UnseedPositionRequest,
)
from cheshire_drivers.pipette_motion_models import (
    ChannelPosition,
    GetChannelPositionRequest,
    MoveChannelRelativeRequest,
    MoveChannelToRequest,
)
from cheshire_drivers.gripper_models import (
    GetGripperPositionRequest,
    GetGripperRotationRequest,
    GetJawWidthRequest,
    GripperPosition,
    GripWithForceRequest,
    MoveGripperRelativeRequest,
    MoveGripperToRequest,
    ReleaseJawRequest,
    RotateGripperRequest,
    SetJawWidthRequest,
)

# Axis names for single-axis movement and free mode control
AxisName = Literal["rail", "base", "shoulder", "elbow", "wrist", "gripper"]


class BaseDriver(ABC):
    """Base contract for all device drivers.

    Cancellation contract: every command method is a cancellable coroutine.
    When the caller cancels it (the engine's recoverable-timeout abort/
    mark-complete, or a transport-level cancel), ``asyncio.CancelledError`` is
    raised at the current ``await`` point. Implementations must let it
    propagate and leave the device in a safe state -- a real-hardware driver
    catches it to issue a physical stop/halt, then re-raises; a sim driver
    simply lets its ``await asyncio.sleep`` unwind. Drivers must NOT swallow
    ``CancelledError``. There is no command-id on driver methods: the timeout
    bound and command<->response correlation live in the engine and transport,
    not the driver.
    """

    @external
    @abstractmethod
    @command_timing(typical=30.0, max=120.0)
    async def initialize(self) -> None:
        """Bring the device up so it accepts commands.

        Whether this moves the device depends on the driver: one whose vendor
        library separates bring-up from homing must keep them separate here and
        leave homing to the device's own home verb, while one that has only a
        compound bring-up moves as part of it.
        """
        ...

    @property
    @external
    @abstractmethod
    def is_initialized(self) -> bool:
        """Returns whether the driver is initialized or not."""
        ...

    @property
    @external
    def is_connected(self) -> bool:
        """Whether THIS DEVICE's link to its hardware is open.

        Distinct from whether the on-prem client is reachable. Those are two
        different connections and both matter: the client can be alive and
        heartbeating while this particular device sits released, and a surface
        that reads only the client's state will offer commands the instrument
        cannot take.

        Defaults to tracking `is_initialized`, which is the truth for backends
        whose link is opened by bring-up and has no separate existence. Drivers
        that hold a real per-connection session override this so `connect` and
        `disconnect` are observable on their own.
        """
        return self.is_initialized

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=30.0)
    async def open(self) -> None:
        """Open driver door."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=30.0)
    async def close(self) -> None:
        """Close driver door."""
        ...

    @external
    @command_timing(typical=5.0, max=30.0)
    async def connect(self) -> None:
        """Open the link to the device and confirm it answers. Moves nothing.

        The half of bring-up that costs nothing physically, so an operator can
        check a device is reachable without committing to `initialize`, which
        on most hardware ends in motion. Calling it on a device that is already
        linked must be harmless: several surfaces drive the same device, and a
        re-link that swaps the socket orphans whatever session the old one held.

        Defaults to doing nothing, because most backends PyLabRobot gives us
        have no link separate from `setup()` and the honest answer for them is
        that there is nothing to open. Drivers whose hardware holds a real
        per-connection session override this.
        """
        return None

    @external
    @command_timing(typical=2.0, max=15.0)
    async def disconnect(self) -> None:
        """Hand the device back to its own controls, without moving it.

        For devices that hold an exclusive session while connected (an Opentrons
        robot refuses its touchscreen for as long as its run is open), this is
        how an operator gets the device back without power-cycling it.

        Defaults to doing nothing, and deliberately does not fall back to the
        backend's `stop`: `stop` homes on its way out and on some hardware
        re-runs initialization, which is the opposite of what this promises.
        """
        return None

    async def _shutdown(self) -> None:
        """Local disposal hook (``DeviceRegistry.cleanup_all`` calls it once when
        releasing the driver). Distinct from ``close`` (the per-op 'close door'
        wire command): the underscore keeps ``_shutdown`` off the wire and
        capability surface. Default no-op; drivers owning out-of-process
        resources (a device-sim vendor server) override it to stop them."""



class IShakerDriver(BaseDriver, ABC):
    interfaces: ClassVar[frozenset[str]] = frozenset({"IShaker"})

    @external
    @abstractmethod
    @command_timing(typical=2.0, max=10.0)
    async def stop(self) -> None:
        """Stop the shaker backend.

        Cross-category command (also called via the generic device_stop MCP tool
        and REST endpoint). Stays parameterless across all device categories so
        the wire dispatch can route a single command name to any device type
        without per-driver wrapping.
        """
        ...

    @property
    @external
    @abstractmethod
    def supports_locking(self) -> bool:
        """Check if the shaker supports locking the plate"""
        ...

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=30.0)
    async def unlock_plate(self, request: UnlockPlateRequest) -> None:
        """Unlock the plate"""
        ...

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=30.0)
    async def lock_plate(self, request: LockPlateRequest) -> None:
        """Lock the plate"""
        ...

    @external
    @abstractmethod
    @command_timing(typical=60.0, max=7200.0)
    async def shake(self, request: ShakeRequest) -> None:
        """Shake the shaker at the given speed and duration."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=2.0, max=10.0)
    async def stop_shaking(self, request: StopShakingRequest) -> None:
        """Stop shaking"""
        ...


class ISealerDriver(BaseDriver, ABC):
    interfaces: ClassVar[frozenset[str]] = frozenset({"ISealer"})

    @external
    @abstractmethod
    @command_timing(typical=90.0, max=600.0)
    async def seal(self, request: SealRequest) -> None:
        """Seal at specified temperature and duration."""
        ...

    # set_temperature / get_temperature are duplicately declared on
    # ITempSettableDriver / ITempGettableDriver. Those interfaces are out of
    # the typed-request uplift; leaving these signatures plain keeps SimSealerDriver's
    # multi-mixin MRO consistent until a future stream uplifts the temp
    # interfaces.
    @external
    @abstractmethod
    @command_timing(typical=2.0, max=10.0)
    async def set_temperature(self, temperature: float) -> None:
        """Set the temperature."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_temperature(self) -> float:
        """Get the current temperature."""
        ...



class ITempSettableDriver(ABC):
    interfaces: ClassVar[frozenset[str]] = frozenset({"ITempSettable"})

    @external
    @abstractmethod
    @command_timing(typical=2.0, max=10.0)
    async def set_temperature(self, temperature: float) -> None:
        """Set the temperature of the device."""
        ...


class ITempGettableDriver(ABC):
    interfaces: ClassVar[frozenset[str]] = frozenset({"ITempGettable"})

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_temperature(self) -> float:
        """Get the current temperature of the device."""
        ...


class IProtocolRunnerDriver(BaseDriver, ABC):
    interfaces: ClassVar[frozenset[str]] = frozenset({"IProtocolRunner"})

    @external
    @abstractmethod
    @command_timing(typical=600.0, max=14400.0)
    async def run_protocol(self, request: RunProtocolRequest) -> None:
        """Execute a protocol run command."""
        ...


class ICentrifugeDriver(BaseDriver, ABC):
    interfaces: ClassVar[frozenset[str]] = frozenset({"ICentrifuge"})

    @external
    @abstractmethod
    @command_timing(typical=300.0, max=10800.0)
    async def centrifuge(self, request: CentrifugeRequest) -> None:
        """Spin the centrifuge at a specified speed for a specified duration."""
        ...


class IThermocyclerDriver(BaseDriver, ABC):
    interfaces: ClassVar[frozenset[str]] = frozenset({"IThermocycler"})

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=30.0)
    async def open_lid(self, request: OpenLidRequest) -> None:
        """Open the thermocycler lid."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=30.0)
    async def close_lid(self, request: CloseLidRequest) -> None:
        """Close the thermocycler lid."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=120.0)
    async def set_block_temperature(self, request: SetBlockTemperatureRequest) -> None:
        """Set the block temperature (one value per zone)."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=120.0)
    async def set_lid_temperature(self, request: SetLidTemperatureRequest) -> None:
        """Set the lid temperature (one value per zone)."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=2.0, max=10.0)
    async def deactivate_block(self, request: DeactivateBlockRequest) -> None:
        """Deactivate block temperature control."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=2.0, max=10.0)
    async def deactivate_lid(self, request: DeactivateLidRequest) -> None:
        """Deactivate lid temperature control."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=600.0, max=14400.0)
    async def run_protocol(self, request: ThermocyclerRunProtocolRequest) -> None:
        """Run a thermal protocol (stages of temperature steps)."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_block_current_temperature(
        self, request: GetBlockCurrentTemperatureRequest,
    ) -> TemperatureListResponse:
        """Get the current block temperature per zone."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_block_target_temperature(
        self, request: GetBlockTargetTemperatureRequest,
    ) -> TemperatureListResponse:
        """Get the block target temperature per zone."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_lid_current_temperature(
        self, request: GetLidCurrentTemperatureRequest,
    ) -> TemperatureListResponse:
        """Get the current lid temperature per zone."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_lid_target_temperature(
        self, request: GetLidTargetTemperatureRequest,
    ) -> TemperatureListResponse:
        """Get the lid target temperature per zone."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_lid_open(self, request: GetLidOpenRequest) -> LidOpenResponse:
        """Return whether the lid is open."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_lid_status(self, request: GetLidStatusRequest) -> ThermocyclerStatusResponse:
        """Get the lid temperature status."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_block_status(self, request: GetBlockStatusRequest) -> ThermocyclerStatusResponse:
        """Get the block temperature status."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_hold_time(self, request: GetHoldTimeRequest) -> HoldTimeResponse:
        """Get the remaining hold time in seconds."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_current_cycle_index(
        self, request: GetCurrentCycleIndexRequest,
    ) -> CycleIndexResponse:
        """Get the zero-based index of the current cycle."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_total_cycle_count(
        self, request: GetTotalCycleCountRequest,
    ) -> CycleCountResponse:
        """Get the total cycle count."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_current_step_index(
        self, request: GetCurrentStepIndexRequest,
    ) -> StepIndexResponse:
        """Get the zero-based index of the current step within the cycle."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_total_step_count(
        self, request: GetTotalStepCountRequest,
    ) -> StepCountResponse:
        """Get the total number of steps in the current cycle."""
        ...


class IReaderDriver(BaseDriver, ABC):
    interfaces: ClassVar[frozenset[str]] = frozenset({"IReader"})

    @external
    @abstractmethod
    @command_timing(typical=120.0, max=1800.0)
    async def read(self, request: ReadRequest) -> None:
        """Read data using the specified protocol and save results to a file."""
        ...


class IDelidderDriver(BaseDriver, ABC):
    interfaces: ClassVar[frozenset[str]] = frozenset({"IDelidder"})

    @external
    @abstractmethod
    @command_timing(typical=15.0, max=60.0)
    async def delid(self, request: DelidRequest) -> None:
        """Remove the lid from the specified labware."""
        ...


class IHomeableDriver(ABC):
    """A device that can be told to drive its axes back to a known reference.

    Its own capability rather than part of any device kind, because which
    devices home does not follow from what they are for: an arm and a liquid
    handler both do, a shaker has nothing to home. Bring-up never homes, so a
    device whose axes lose their reference at power-off has to be able to say
    it can be homed, or an operator has no way to make its first move safe.
    """

    interfaces: ClassVar[frozenset[str]] = frozenset({"IHomeable"})

    @external
    @abstractmethod
    @command_timing(typical=30.0, max=120.0)
    async def home(self, request: HomeRequest) -> None:
        """Drive every axis to its reference position. MOVES THE DEVICE.

        Sweeps the full envelope, through anything in the way and anything the
        device is holding. Always an operator's deliberate request.
        """
        ...


class ITransporterDriver(IHomeableDriver, ABC):
    # Inheritance shadows `interfaces` rather than unioning it, so an arm
    # restates the homing capability it takes from IHomeableDriver.
    interfaces: ClassVar[frozenset[str]] = frozenset({"ITransporter", "IHomeable"})

    @property
    @external
    @abstractmethod
    def name(self) -> str:
        """Returns the name of the transporter."""
        ...

    @property
    def single_carriage(self) -> bool:
        """True when ALL taught positions are stations of ONE physical
        carriage (translator/shuttle): at most one labware may occupy the
        whole position set at any time. False for arms, whose taught
        positions are independent pads -- an arm gripping one plate at a
        time is NOT this. Engine-read scheduling metadata, not a command."""
        return False

    @external
    @abstractmethod
    @command_timing(typical=30.0, max=120.0)
    async def initialize(self, request: InitializeRequest) -> None:
        """Bring the arm up so it accepts commands.

        Homing sweeps the arm through whatever it is holding, so a driver whose
        vendor library offers the pieces separately must not fold a home in
        here; homing is `home`. A driver that has only a compound bring-up homes
        as part of it, and its docstring has to say so.
        """
        ...

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=30.0)
    async def move_to_safe(self, request: MoveToSafeRequest) -> None:
        """Moves the transporter to a safe position."""
        ...

    @external
    @command_timing(typical=5.0, max=30.0)
    async def connect(self) -> None:
        """Open the link to the arm and confirm it answers. Moves nothing.

        Same contract as `BaseDriver.connect`, which an arm does not inherit.
        """
        return None

    @external
    @command_timing(typical=2.0, max=15.0)
    async def disconnect(self) -> None:
        """Hand the arm back, without moving it. See `BaseDriver.disconnect`.

        An arm holding a real link refuses rather than pretending: an operator
        who believes a released arm is safe may reach into its envelope.
        """
        return None

    @property
    @external
    @abstractmethod
    def is_initialized(self) -> bool:
        """Returns whether the driver is initialized or not."""
        ...

    @property
    @external
    def is_connected(self) -> bool:
        """Whether the arm's own link is open. See `BaseDriver.is_connected`."""
        return self.is_initialized

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=60.0)
    async def pick_at_coords(self, request: PickAtCoordsRequest) -> None:
        """Pick plate at coordinates specified by teachpoint.

        The system layer (orca-core's `Transporter.pick`) resolves the
        destination Teachpoint via its bound teachpoint store (keyed on
        `position_id`) and walks any gateway chain before dispatching here.
        The driver receives a fully-resolved Teachpoint plus the optional
        gateway path; no position_id lookup happens on the dispatch path.
        """
        ...

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=60.0)
    async def place_at_coords(self, request: PlaceAtCoordsRequest) -> None:
        """Place plate at coordinates specified by teachpoint.

        Symmetric with `pick_at_coords`; receives a fully-resolved
        Teachpoint and the optional pre-walked gateway path.
        """
        ...

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=60.0)
    async def move_to_coords(self, request: MoveToCoordsRequest) -> None:
        """Move to coordinates specified by teachpoint."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_joint_position(self, request: GetJointPositionRequest) -> JointCoordinates:
        """Get current joint positions.

        Returns:
            JointCoordinates with current joint values (rail, base, shoulder, elbow, wrist).
        """
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_cartesian_position(self, request: GetCartesianPositionRequest) -> CartesianCoordinates:
        """Get current position in Cartesian coordinates.

        Returns:
            CartesianCoordinates with x, y, z, roll, pitch, yaw values.
        """
        ...

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=60.0)
    async def move_single_axis(self, request: MoveSingleAxisRequest) -> None:
        """Move a single axis to absolute position."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=60.0)
    async def move_single_axis_relative(self, request: MoveSingleAxisRelativeRequest) -> None:
        """Move a single axis by relative distance from current position."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=2.0, max=10.0)
    async def set_free_mode(self, request: SetFreeModeRequest) -> None:
        """Enable/disable free mode (freedrive) for specified axes.

        Examples:
            set_free_mode(SetFreeModeRequest(axes="all"))            # Enable on all axes
            set_free_mode(SetFreeModeRequest(axes="none"))           # Disable on all axes
            set_free_mode(SetFreeModeRequest(axes=["rail"]))         # Only rail is free
            set_free_mode(SetFreeModeRequest(axes=["base", "elbow"])) # Multiple axes free
        """
        ...

    @external
    @abstractmethod
    @command_timing(typical=2.0, max=10.0)
    async def open_gripper(self, request: OpenGripperRequest) -> None:
        """Open the jaws past the grip position, by a stated opening or the arm's own."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=2.0, max=10.0)
    async def close_gripper(self, request: CloseGripperRequest) -> None:
        """Close the jaws to the calibrated grip position, or to a named position."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def set_speed(self, request: SetSpeedRequest) -> None:
        """Set movement speed as percentage of maximum (0.0 to 1.0)."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_speed(self, request: GetSpeedRequest) -> float:
        """Get current movement speed setting.

        Returns:
            Current speed as percentage (0.0 to 1.0)
        """
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def halt(self, request: HaltRequest) -> None:
        """Emergency stop - immediately halt all movement.

        The transporter may need to be re-initialized after a halt.
        """
        ...


    # -- World-state sync (workflow-internal; not operator-facing) --

    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def seed_position(self, request: SeedPositionRequest) -> None:
        """Declare that `request.labware` is staged at `request.position_id`.

        Used by orca-core to bring orca-client's transporter sim graph into
        alignment with the cloud-side labware ledger when a workflow starts.
        Idempotency is NOT guaranteed -- raises on conflict (position
        already occupied). For idempotent re-sync use `ensure_seeded`.

        Real-hardware drivers may treat this as a no-op since physical
        state is the source of truth.
        """
        ...

    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def ensure_seeded(self, request: EnsureSeededRequest) -> None:
        """Idempotent variant of `seed_position`.

        No-op if `request.labware` is already at `request.position_id`
        (or held by the gripper). If a DIFFERENT labware occupies the
        position, RECONCILES to the engine: the engine ledger is the source
        of truth, so the stale occupant is evicted and the requested labware
        seeded. The projection obeys the ledger; it does not veto it.

        Real-hardware drivers may treat this as a no-op.
        """
        ...

    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def unseed_position(self, request: UnseedPositionRequest) -> None:
        """Idempotent removal of `request.labware` from `request.position_id`.

        No-op if the position does not currently hold this labware (the
        caller is likely the actor who already moved it via `pick`).

        Real-hardware drivers may treat this as a no-op.
        """
        ...

    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def reset_world(self, request: ResetWorldRequest) -> None:
        """Wipe ALL labware occupancy from the driver's world projection.

        Clears every seeded position and the gripper, leaving position
        structure intact. Used by the engine's authoritative
        `clear_all_labware` so the sim projection obeys a ledger that now
        holds no labware. Unlike `unseed_position` (per-labware, needs the
        caller to know the identity), this is a blanket reset that reaches
        orphaned seeds the engine has lost track of.

        Real-hardware drivers may treat this as a no-op.
        """
        ...


class IStorageDriver(BaseDriver, ABC):
    interfaces: ClassVar[frozenset[str]] = frozenset({"IStorage"})

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=60.0)
    async def dispense(self) -> None:
        """Release one plate from the source's queue to the output position.

        Wire-level command for a stacker or hotel that releases one plate on
        demand. The driver knows only how to advance the source's internal
        queue. What the caller does with the plate that comes out, including
        naming and tracking it, happens above this call.

        Returns None. Per-source extensibility (returning a barcode, accepting
        a labware-type hint) is deferred until a real use case asks for it.
        """
        ...


class IPlateWasherDriver(IProtocolRunnerDriver, ABC):
    interfaces: ClassVar[frozenset[str]] = frozenset({"IPlateWasher", "IProtocolRunner"})


class ILiquidHandlerDriver(BaseDriver, ABC):
    """Serializable interface for liquid handler control.

    Atomic operations (aspirate/dispense/tip ops) with Pydantic request
    models. Works identically for in-process and remote transport.

    Protocol execution lives on :class:`IProtocolRunnerDriver`. Drivers
    that support BOTH atomic ops and vendor protocol files (e.g. a
    Hamilton MLSTAR + Venus driver) declare both base classes
    explicitly and merge ``"IProtocolRunner"`` into their ``interfaces``
    frozenset. PLR-backed drivers (Chatterbox, real PLR backends)
    declare only ILiquidHandlerDriver -- PLR has no external protocol
    file concept and previously was forced to stub ``run_protocol`` to
    raise NotImplementedError.

    Capability: ``provides_state`` advertises whether this driver returns
    reliable per-call labware state in its LabwareStateResponse. PLR sims
    (Chatterbox) and PLR backends with introspection set this True;
    disconnected/no-device sims set it False. The orca-core LiquidHandler
    bridge consults this flag together with the user-side
    ``trust_driver_state`` opt-in to decide whether to emit
    DRIVER_OBSERVED tracking records from the response.
    """

    interfaces: ClassVar[frozenset[str]] = frozenset({"ILiquidHandler"})

    # Class-level capability flag. Subclasses override with True when the
    # underlying backend reliably reports labware state per call.
    provides_state: ClassVar[bool] = False

    # --- Deck management ---

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=30.0)
    async def configure_deck(self, config: DeckLayoutConfig) -> LabwareStateResponse:
        """Configure the deck layout with labware positions.

        Workflow: configure_deck() then initialize().

        Args:
            config: Deck layout with deck type, carriers, and labware positions.
        """
        ...

    @external
    @abstractmethod
    @command_timing(typical=2.0, max=10.0)
    async def get_deck_state(self, request: GetDeckStateRequest) -> DeckStateResponse:
        """The driver's own account of its deck: tips, labware, and tip racks.

        Occupancy is addressed by SITE, so labware that is not at one has nowhere
        to be reported and its entry keeps naming the site it left. A driver that
        moves labware with its own gripper therefore reports `interrupted_move`
        when a move raised partway, so a reader can tell a stale site from a
        current one instead of taking every site at face value.
        """
        ...

    @external
    @abstractmethod
    @command_timing(typical=2.0, max=10.0)
    async def get_head_configuration(
        self, request: GetHeadConfigurationRequest
    ) -> HeadConfigurationResponse:
        """Report the mounted nozzles and which of them share a plunger.

        Ganging is a property of the pipette in the mount, not of the driver, so this cannot be a
        capability the class declares: the same driver is ganged with a multi-channel pipette and
        independent with a single-channel one. Callers planning per-channel volumes read this
        first, and drivers enforce it per command against what is actually mounted.
        """
        ...

    @abstractmethod
    @command_timing(typical=2.0, max=10.0)
    async def reset_deck_labware(self, request: ResetDeckLabwareRequest) -> LabwareStateResponse:
        """Wipe deck occupancy (plates / tip racks / troughs), preserve carriers.

        The driver deck is a projection of the engine ledger; this clears stale
        labware so a panic clear-all leaves no occupancy the ledger no longer
        knows about. Carriers (deck structure) are not touched.
        """
        ...

    @abstractmethod
    @command_timing(typical=2.0, max=10.0)
    async def reconcile_deck_occupancy(
        self, request: ReconcileDeckOccupancyRequest,
    ) -> LabwareStateResponse:
        """Set deck occupancy to exactly ``request.resources`` (carriers preserved).

        Wipe current occupancy, then place each resource. Idempotent: the deck
        projection ends matching the authoritative occupancy the engine derives
        from the ledger.
        """
        ...

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=60.0)
    async def reconcile_hardware_state(
        self, request: ReconcileHardwareStateRequest,
    ) -> ReconcileHardwareStateResponse:
        """Re-read hardware ground truth and repair what is unambiguous. Moves nothing.

        The mirror of ``reconcile_deck_occupancy``: there the engine ledger is
        the truth and the deck projection obeys it; here the HARDWARE is the
        truth (control-session liveness, tip-presence sensors) and the driver's
        cached state obeys it. An operator who recovers the device at the
        instrument invalidates the session and can leave cached tip state
        describing tips that are gone; this is the operator's and the engine's
        way to find out and repair it. A dead instrument-side session is
        rebuilt without motion; tip bookkeeping is cleared only where a sensor
        is definitive. ``requires_intervention`` reports a physically-present
        tip the model does not know: resolve with ``discard_stranded_tips`` or
        at the instrument, never by asserting state into the driver.
        """
        ...

    @external
    @abstractmethod
    @command_timing(typical=20.0, max=120.0)
    async def discard_stranded_tips(
        self, request: DiscardStrandedTipsRequest,
    ) -> ReconcileHardwareStateResponse:
        """Trash tips only the hardware knows about. MOVES the robot.

        The escape for reconcile's ``requires_intervention`` outcome. Only
        mounts whose sensor reads present while the model tracks nothing are
        touched; tips the model does know go through ``discard_tips``.
        Returns the post-discard reconcile report.
        """
        ...

    # --- 8-channel operations ---

    @external
    @abstractmethod
    @command_timing(typical=15.0, max=180.0)
    async def aspirate(self, request: AspirateRequest) -> LabwareStateResponse:
        """Aspirate liquid from wells."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=15.0, max=180.0)
    async def dispense(self, request: DispenseRequest) -> LabwareStateResponse:
        """Dispense liquid into wells."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=60.0)
    async def pick_up_tips(self, request: PickUpTipsRequest) -> LabwareStateResponse:
        """Pick up tips from a tip rack."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=60.0)
    async def drop_tips(self, request: DropTipsRequest) -> LabwareStateResponse:
        """Drop tips to waste or back to a tip rack."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=60.0)
    async def discard_tips(self, request: DiscardTipsRequest) -> LabwareStateResponse:
        """Drop currently held tips to waste."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=60.0)
    async def move_plate(self, request: MovePlateRequest) -> None:
        """Move a plate to a named position using the internal gripper."""
        ...

    @abstractmethod
    @command_timing(typical=10.0, max=60.0)
    async def add_deck_labware(self, request: AddDeckLabwareRequest) -> None:
        """Materialize one labware (plate, tip rack, or trough) on the deck at a site from catalog_ref; for plates this is create-on-arrival before the internal-gripper move."""
        ...

    @abstractmethod
    @command_timing(typical=10.0, max=60.0)
    async def remove_deck_labware(self, request: RemoveDeckLabwareRequest) -> None:
        """Un-materialize one named labware from the deck (departure); unlike reset_deck_labware it removes exactly one."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=30.0, max=600.0)
    async def mix(self, request: MixRequest) -> LabwareStateResponse:
        """Mix in wells (aspirate/dispense cycles in place)."""
        ...

    # --- 96-head operations ---

    @external
    @abstractmethod
    @command_timing(typical=20.0, max=180.0)
    async def aspirate96(self, request: Aspirate96Request) -> LabwareStateResponse:
        """Aspirate from all wells simultaneously using the 96-head."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=20.0, max=180.0)
    async def dispense96(self, request: Dispense96Request) -> LabwareStateResponse:
        """Dispense to all wells simultaneously using the 96-head."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=15.0, max=120.0)
    async def pick_up_tips96(self, request: PickUpTips96Request) -> LabwareStateResponse:
        """Pick up 96 tips at once using the 96-head."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=15.0, max=120.0)
    async def drop_tips96(self, request: DropTips96Request) -> LabwareStateResponse:
        """Drop 96 tips to waste or back to a tip rack."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=15.0, max=120.0)
    async def return_tips96(self, request: ReturnTips96Request) -> LabwareStateResponse:
        """Return 96-head tips to the rack they were picked up from."""
        ...


class ILiquidHandlerWithProtocolDriver(ILiquidHandlerDriver, IProtocolRunnerDriver, ABC):
    """Liquid handler that runs both atomic ops and vendor protocol files.

    Names the union (Hamilton MLSTAR + Venus, Bravo + VWorks) so a consumer
    can be typed against "well-level verbs AND run_protocol" -- Python has no
    intersection type. Concrete drivers in this category already inherit both
    bases; this just gives the combination a name.
    """

    interfaces: ClassVar[frozenset[str]] = frozenset(
        {"ILiquidHandler", "IProtocolRunner"}
    )


class IWasteDriver(IStorageDriver, ABC):
    interfaces: ClassVar[frozenset[str]] = frozenset({"IWaste", "IStorage"})


class ILiquidProbeDriver(ABC):
    """Find the liquid surface in a well, for a head that can sense it.

    Separate from ILiquidHandler because sensing is hardware, not software: a
    Flex and a Hamilton STAR read pressure as a tip descends, an OT-2 has no
    sensor at all. A driver that declares this interface can answer where the
    liquid is; one that does not is refused the command at the gateway rather
    than failing at the deck.
    """

    interfaces: ClassVar[frozenset[str]] = frozenset({"ILiquidProbe"})

    @external
    @abstractmethod
    @command_timing(typical=10.0, max=60.0)
    async def liquid_probe(self, request: LiquidProbeRequest) -> LiquidProbeResponse:
        """Descend a mounted tip until it senses liquid, and report the height it found."""
        ...


class IPipetteMotionDriver(ABC):
    """Direct per-channel Cartesian motion: place a channel at a known deck point,
    read where it is, and jog it. The troubleshooting / teaching surface, distinct
    from the labware-referenced aspirate/dispense on ILiquidHandler."""

    interfaces: ClassVar[frozenset[str]] = frozenset({"IPipetteMotion"})

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=30.0)
    async def move_channel_to(self, request: MoveChannelToRequest) -> None:
        """Move one channel to an absolute deck-frame position (only the supplied axes)."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=30.0)
    async def move_channel_relative(self, request: MoveChannelRelativeRequest) -> None:
        """Jog one channel by a delta on each supplied axis, from its current position."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_channel_position(self, request: GetChannelPositionRequest) -> ChannelPosition:
        """Read one channel's current deck-frame position."""
        ...


class IGripperMotionDriver(ABC):
    """Place the liquid handler's plate gripper at an absolute deck-frame point.

    The motion floor every gripper honors. Reading the position and jogging by a delta both
    need position feedback, so they live on IGripperPosition instead; a gripper without it
    (the Opentrons Flex, whose API has no gripper position query at all) declares only this."""

    interfaces: ClassVar[frozenset[str]] = frozenset({"IGripperMotion"})

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=30.0)
    async def move_gripper_to(self, request: MoveGripperToRequest) -> None:
        """Move the gripper to an absolute deck-frame position. MOVES THE DEVICE.

        Where the gripper shares a carriage with the pipette mounts, this drives
        them too, so it owes what a park owes: refuse mid-transfer, and lift every
        populated mount first (see IGantryParkingDriver). Where the gripper is an
        arm of its own, neither applies and this is a plain move.
        """
        ...


class IGantryParkingDriver(ABC):
    """Move the handler's own moving parts clear of its deck, on its own terms.

    A gantry stays wherever its last operation left it, and an arm reaching into a
    shared site meets it there. This is how the handler is asked to get out of the
    way without the caller having to know the machine's geometry.

    Rare, and declared rather than assumed: most liquid handlers have no way to be
    told this, and a handler whose deck no other mover reaches never needs it.

    A bench that wants the gripper at a particular spot puts it on the request
    rather than sending an IGripperMotion jog: parking carries a refusal and a
    lift, and a bench spot should not be the way around either.

    Two duties, and they belong to whatever traverses a SHARED gantry, this method
    included. Refuse while a transfer is in progress, because only the handler
    knows what it is carrying: tips on a head, or a plate in its jaws. Lift every
    populated mount before travelling,
    because that one carriage holds them all and a mount left low is dragged
    across whatever stands in the way. A handler whose gripper moves on its own
    arm carries neither duty here."""

    interfaces: ClassVar[frozenset[str]] = frozenset({"IGantryParking"})

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=30.0)
    async def park_gantry(self, request: ParkGantryRequest) -> None:
        """Move clear of the deck. MOVES THE DEVICE.

        The handler picks the moment, because it is the only thing that knows
        what it is holding. Work in progress wins: a handler carrying tips or
        holding a plate in its jaws raises GantryBusyError rather than dragging
        either across the deck, and the caller waits and asks again.
        """
        ...


class IGripperPositionDriver(ABC):
    """Report where the gripper is and jog it from there. Hamilton iSWAP, Brooks PreciseFlex.

    Split from IGripperMotion (see there): a jog is computed from the current position, so both
    methods rest on the same feedback. A gripper that cannot report where it is must not offer a
    jog either, because jogging from an assumed origin drives the arm somewhere unintended."""

    interfaces: ClassVar[frozenset[str]] = frozenset({"IGripperPosition"})

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_gripper_position(self, request: GetGripperPositionRequest) -> GripperPosition:
        """Read the gripper's current deck-frame position."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=30.0)
    async def move_gripper_relative(self, request: MoveGripperRelativeRequest) -> None:
        """Jog the gripper by a delta on each supplied axis."""
        ...


class IForceGripperJawDriver(ABC):
    """Force-controlled gripper jaw: close until a grip force is reached. Opentrons Flex.

    Split from IWidthGripperJaw so a force gripper never exposes a width command it
    cannot honor. The operator/AI learns the control model from the declared
    interface, not from a runtime rejection. A force gripper reports no force feedback,
    so there is deliberately no jaw readback here (contrast IWidthGripperJaw.get_jaw_width)."""

    interfaces: ClassVar[frozenset[str]] = frozenset({"IForceGripperJaw"})

    @external
    @abstractmethod
    @command_timing(typical=3.0, max=15.0)
    async def grip_with_force(self, request: GripWithForceRequest) -> None:
        """Close the jaw until it grips at the requested force (newtons)."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=3.0, max=15.0)
    async def release_jaw(self, request: ReleaseJawRequest) -> None:
        """Open the jaw fully."""
        ...


class IWidthGripperJawDriver(ABC):
    """Width-controlled gripper jaw: drive the jaw to an opening width and read it.
    Hamilton iSWAP, Brooks PreciseFlex.

    Split from IForceGripperJaw (see there): the declared interface, not a runtime
    error, tells the caller this gripper is width-controlled."""

    interfaces: ClassVar[frozenset[str]] = frozenset({"IWidthGripperJaw"})

    @external
    @abstractmethod
    @command_timing(typical=3.0, max=15.0)
    async def set_jaw_width(self, request: SetJawWidthRequest) -> None:
        """Drive the jaw to the requested opening width (mm)."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_jaw_width(self, request: GetJawWidthRequest) -> float:
        """Read the current jaw opening (mm)."""
        ...


class IGripperRotationDriver(ABC):
    """Rotate the gripper about its vertical axis. Hamilton iSWAP.

    Only for grippers with a rotation drive; a Cartesian gripper (Opentrons Flex)
    does not declare this interface."""

    interfaces: ClassVar[frozenset[str]] = frozenset({"IGripperRotation"})

    @external
    @abstractmethod
    @command_timing(typical=5.0, max=30.0)
    async def rotate_gripper(self, request: RotateGripperRequest) -> None:
        """Rotate the gripper to the requested angle (degrees)."""
        ...

    @external
    @abstractmethod
    @command_timing(typical=1.0, max=5.0)
    async def get_gripper_rotation(self, request: GetGripperRotationRequest) -> float:
        """Read the gripper's current rotation (degrees)."""
        ...
