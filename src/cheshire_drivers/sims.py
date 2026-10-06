import asyncio
import functools
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List, Literal, Optional, TypeVar, Union

from cheshire_drivers._plr_compat import ensure_plr_stubs

ensure_plr_stubs()

from pylabrobot.resources.resource import Resource as PLRResource

from cheshire_drivers.faults import FaultRegistry, FaultSpec
from cheshire_drivers.gantry_models import ParkGantryRequest
from cheshire_drivers.homing_models import HomeRequest
from cheshire_drivers.labware_seed import LabwareSeedEntry, TipRackSeedEntry, load_labware_seed
from cheshire_drivers.labware_handoff import IgnoresLabwareHandoff
from cheshire_drivers.interfaces import (
    IGantryParkingDriver,
    IHomeableDriver,
    AxisName, ICentrifugeDriver,
    IDelidderDriver, ILiquidHandlerDriver, ILiquidHandlerWithProtocolDriver,
    ILiquidProbeDriver,
    IPlateWasherDriver,
    IReaderDriver, ISealerDriver, IShakerDriver,
    IStorageDriver, ITempGettableDriver, ITempSettableDriver,
    IThermocyclerDriver, ITransporterDriver, IWasteDriver,
)
from cheshire_drivers.labware_interfaces import IPlate, ITipRack, ITipSpot, IWell
from cheshire_drivers.centrifuge_models import CentrifugeRequest
from cheshire_drivers.thermocycler_models import (
    CloseLidRequest,
    CycleCountResponse,
    CycleIndexResponse,
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
from cheshire_drivers.protocol_runner_models import RunProtocolRequest
from cheshire_drivers.reader_models import ReadRequest
from cheshire_drivers.sealer_models import SealRequest
from cheshire_drivers.shaker_models import (
    LockPlateRequest,
    ShakeRequest,
    StopShakingRequest,
    UnlockPlateRequest,
)
from cheshire_drivers.liquid_handler_models import (
    AspirateRequest, Aspirate96Request,
    DeckLayoutConfig,
    DeckResourceState,
    DeckStateResponse,
    DiscardTipsRequest,
    DispenseRequest, Dispense96Request,
    DropTipsRequest, DropTips96Request,
    GetDeckStateRequest,
    GetHeadConfigurationRequest,
    HeadConfigurationResponse,
    NozzleGroup,
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
    TipRackState,
    RemoveDeckLabwareRequest,
    build_aspirate_partial_failure,
    build_dispense_partial_failure,
)
from cheshire_drivers.teachpoints import (
    CartesianCoordinates,
    JointCoordinates,
    Teachpoint,
)
from cheshire_drivers.labware_models import LabwareIdentity
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

logger = logging.getLogger("cheshire_drivers")


class Sim(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...
    @abstractmethod
    async def _sim(self, message: str, duration: float | None = None) -> None: ...

    _faults: FaultRegistry
    _faultable_methods: ClassVar[frozenset[str]] = frozenset()


def _fault_hook(method: Any) -> Any:
    """Wrap a sim driver's async domain method to apply pre-armed faults.

    The wrapper increments the per-method call counter on entry and
    raises/hangs if any matching ``FaultSpec`` fires. The underlying
    method then runs unchanged. Sim state is never mutated by a fault,
    so a one-shot ``RaiseFault(on_calls=[1])`` does not brick subsequent
    calls.
    """
    @functools.wraps(method)
    async def wrapper(self: Sim, *args: Any, **kwargs: Any) -> Any:
        await self._faults.before(method.__name__)
        return await method(self, *args, **kwargs)
    return wrapper


_FaultHookC = TypeVar("_FaultHookC", bound=type)


def _apply_fault_hook(cls: _FaultHookC) -> _FaultHookC:
    """Class decorator: wrap each public async method to run fault checks.

    Walks ``vars(cls)`` (own methods only, not inherited) and replaces
    every async method that does not start with ``_`` with a
    fault-checking wrapper. Records the wrapped names on
    ``cls._faultable_methods`` (a ClassVar frozenset) so ``BaseSimDriver``
    + ``SimTransporterDriver`` can validate that every ``FaultSpec.method``
    actually targets a known method on the driver.
    """
    own_names: set[str] = set()
    for name, attr in list(vars(cls).items()):
        if name.startswith("_"):
            continue
        if not asyncio.iscoroutinefunction(attr):
            continue
        setattr(cls, name, _fault_hook(attr))
        own_names.add(name)
    # Don't set `_faultable_methods` on classes that define no own
    # methods (e.g. `WasteSimMixin(StorageSimMixin): pass`). Setting an
    # empty frozenset would shadow the parent's set; the MRO walk in
    # `_collect_faultable_methods` would still rescue correctness, but
    # the shadowed attribute is misleading to a reader.
    if own_names:
        setattr(cls, "_faultable_methods", frozenset(own_names))
    return cls


def _collect_faultable_methods(cls: type) -> frozenset[str]:
    """Union of ``_faultable_methods`` across ``cls.__mro__``.

    ``BaseSimDriver`` + ``SimTransporterDriver`` use this in ``__init__``
    to validate ``FaultSpec.method`` against the methods their concrete
    type actually exposes via inheritance.
    """
    names: set[str] = set()
    for klass in cls.__mro__:
        names |= getattr(klass, "_faultable_methods", frozenset())
    return frozenset(names)


def _validate_fault_specs(
    driver_cls: type, specs: list[FaultSpec],
) -> FaultRegistry:
    """Reject ``FaultSpec.method`` values that aren't defined on the driver.

    Catches typos (``"shak"`` → no-op) at construction rather than at
    runtime. The ``FaultRegistry`` itself only validates ``error_type``;
    the method-name allowlist is the driver's responsibility because
    only the driver class knows its own surface.
    """
    valid = _collect_faultable_methods(driver_cls)
    for spec in specs:
        if spec.method not in valid:
            raise ValueError(
                f"FaultSpec.method={spec.method!r} is not a faultable "
                f"method on {driver_cls.__name__}; valid methods: "
                f"{sorted(valid)}."
            )
    return FaultRegistry(specs)

class SimStrategy(ABC):
    @abstractmethod
    async def sim(self, prompt: str, duration: float | None = None) -> None:
        """Simulate an operation.

        `duration` is the typed duration (seconds) the caller knows about.
        Strategies that model real time consume it; strategies that model
        operator interaction may ignore it. Pass `None` when the call site
        has no duration to advertise.
        """


class SleepSim(SimStrategy):
    """Sleep-based sim strategy with optional duration scaling.

    `sim_time` is the unconditional sleep applied to every call. `scale_factor`
    multiplies the typed `duration` when provided; the actual wait is
    `sim_time + max(duration, 0) * scale_factor`.

    Defaults preserve the historical fast-test behavior: `sim_time=0.2`,
    `scale_factor=0.0` ignores duration entirely and waits a flat 0.2s. Lab
    simulators bump `scale_factor` (1.0 for real-time, 0.001 for compressed
    realism) to make sims honor caller-supplied durations.
    """

    def __init__(
        self, sim_time: float = 0.2, scale_factor: float = 0.0,
    ) -> None:
        self.sim_time = sim_time
        self.scale_factor = scale_factor

    async def sim(self, prompt: str, duration: float | None = None) -> None:
        wait = self.sim_time
        if duration is not None and self.scale_factor > 0.0 and duration > 0.0:
            wait += duration * self.scale_factor
        prompt = f"{prompt} (simulated wait for {wait} seconds)"
        logger.info(prompt)
        await asyncio.sleep(wait)


class HumanSim(SimStrategy):
    """Waits for the user to press Enter without blocking the event loop.

    Operator-driven strategy: the wait is bounded by human input, not by the
    caller's `duration`. The signature accepts `duration` to satisfy the
    `SimStrategy` contract; the value is ignored.
    """
    def __init__(self, prompt_suffix: str = " Press Enter to continue.") -> None:
        self.prompt_suffix = prompt_suffix

    async def sim(self, prompt: str, duration: float | None = None) -> None:
        del duration
        full = f"{prompt}{self.prompt_suffix}"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, input, full)

@_apply_fault_hook
class BaseSimDriver:
    """Base simulation driver with common functionality.

    Accepts an optional ``faults`` list at construction. Each
    ``FaultSpec`` is validated against the driver's combined faultable
    method surface (collected across the MRO) and rejected at boot if
    it targets an unknown method.
    """

    _faultable_methods: ClassVar[frozenset[str]] = frozenset()


    def __init__(
        self,
        name: str,
        sim_strategy: Optional[SimStrategy] = None,
        faults: Optional[List[FaultSpec]] = None,
    ):
        self._name = name
        self._sim_strategy = sim_strategy or SleepSim()
        self._is_initialized: bool = False
        self._faults = _validate_fault_specs(type(self), faults or [])

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def initialize(self) -> None:
        await self._sim(f"Initialization of {self.name} in progress...")
        self._is_initialized = True
        logger.info(f"{self.name} initialized successfully.")

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

    async def _sim(self, message: str, duration: float | None = None) -> None:
        await self._sim_strategy.sim(message, duration=duration)


# Mixins for simulation functionality

@_apply_fault_hook
class ShakerSimMixin(Sim, IShakerDriver):
    """Mixin for shaker simulation functionality"""
    _supports_locking = True

    async def shake(self, request: ShakeRequest) -> None:
        await self._sim(
            f"Shaking at speed {request.speed} for {request.duration} seconds...",
            duration=request.duration,
        )
        logger.info("Shaking completed successfully.")

    async def stop_shaking(self, request: StopShakingRequest) -> None:
        await self._sim("Stopping shaking...")
        logger.info("Shaking stopped.")

    @property
    def supports_locking(self) -> bool:
        return self._supports_locking

    async def lock_plate(self, request: LockPlateRequest) -> None:
        await self._sim("Locking plate...")
        logger.info("Plate locked.")

    async def unlock_plate(self, request: UnlockPlateRequest) -> None:
        await self._sim("Unlocking plate...")
        logger.info("Plate unlocked.")


@_apply_fault_hook
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

    async def seal(self, request: SealRequest) -> None:
        await self._sim(
            f"Sealing at {request.temperature}C for {request.duration} seconds...",
            duration=request.duration,
        )
        logger.info("Sealing completed successfully.")


@_apply_fault_hook
class TempSettableSimMixin(Sim, ITempSettableDriver):
    """Mixin for temperature settable functionality"""

    async def set_temperature(self, temperature: float) -> None:
        """Set the temperature of the device."""
        await self._sim(f"Setting temperature to {temperature}°C...")
        logger.info(f"Temperature set to {temperature}°C.")


@_apply_fault_hook
class TempGettableSimMixin(Sim, ITempGettableDriver):
    """Mixin for temperature gettable functionality"""

    async def get_temperature(self) -> float:
        """Get the current temperature of the device."""
        logger.info("Getting current temperature.")
        return 25.0  # Mock temperature value

@_apply_fault_hook
class ProtocolRunnerSimMixin(Sim, IgnoresLabwareHandoff):
    """Mixin for protocol runner functionality"""

    async def run_protocol(self, request: RunProtocolRequest) -> None:
        await self._sim(
            f"Running protocol: {request.protocol_filepath} with params: {request.params}..."
        )
        logger.info(f"Protocol {request.protocol_filepath} executed successfully.")


@_apply_fault_hook
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

    async def centrifuge(self, request: CentrifugeRequest) -> None:
        await self._sim(
            f"Spinning centrifuge for {request.duration} seconds at {request.g}g...",
            duration=request.duration,
        )
        logger.info("Centrifuge spin completed successfully.")


@_apply_fault_hook
class ThermocyclerSimMixin(Sim, IThermocyclerDriver):
    """Mixin for thermocycler functionality.

    Keeps single-zone block/lid target + current temperatures, a lid-open
    flag, and profile/cycle/step counters in memory so the getters return
    values coherent with the mutations that ran. Targets are None until set;
    status derives from whether a target is present, matching PLR's chatterbox
    sim.
    """

    _IDLE = "idle"
    _HOLDING = "holding at target"

    def _tc_init(self) -> None:
        """Lazily initialize thermocycler sim state on first access.

        The mixin has no __init__ (BaseSimDriver owns construction), so state
        is created on demand and stored on the instance.
        """
        if getattr(self, "_tc_ready", False):
            return
        self._block_temp: List[float] = [25.0]
        self._lid_temp: List[float] = [25.0]
        self._block_target: Optional[List[float]] = None
        self._lid_target: Optional[List[float]] = None
        self._lid_open: bool = True
        self._total_steps: int = 0
        self._current_step: int = 0
        self._tc_ready: bool = True

    def _require_single_zone(self, temperature: List[float]) -> None:
        """Reject wrong-zone temperature lists, matching PLR's single-zone backend."""
        if len(temperature) != 1:
            raise ValueError(
                f"Expected 1 temperature (single zone), got {len(temperature)}"
            )

    async def open_lid(self, request: OpenLidRequest) -> None:
        self._tc_init()
        await self._sim("Opening thermocycler lid...")
        self._lid_open = True

    async def close_lid(self, request: CloseLidRequest) -> None:
        self._tc_init()
        await self._sim("Closing thermocycler lid...")
        self._lid_open = False

    async def set_block_temperature(self, request: SetBlockTemperatureRequest) -> None:
        self._tc_init()
        self._require_single_zone(request.temperature)
        await self._sim(f"Setting block temperature to {request.temperature}C...")
        self._block_target = list(request.temperature)
        self._block_temp = list(request.temperature)

    async def set_lid_temperature(self, request: SetLidTemperatureRequest) -> None:
        self._tc_init()
        self._require_single_zone(request.temperature)
        await self._sim(f"Setting lid temperature to {request.temperature}C...")
        self._lid_target = list(request.temperature)
        self._lid_temp = list(request.temperature)

    async def deactivate_block(self, request: DeactivateBlockRequest) -> None:
        self._tc_init()
        await self._sim("Deactivating block...")
        self._block_target = None

    async def deactivate_lid(self, request: DeactivateLidRequest) -> None:
        self._tc_init()
        await self._sim("Deactivating lid...")
        self._lid_target = None

    async def run_protocol(self, request: ThermocyclerRunProtocolRequest) -> None:
        self._tc_init()
        total = sum(
            stage.repeats * len(stage.steps) for stage in request.protocol.stages
        )
        await self._sim(
            f"Running thermocycler protocol ({total} steps)...",
        )
        self._total_steps = total
        self._current_step = total
        if request.protocol.stages and request.protocol.stages[-1].steps:
            final = request.protocol.stages[-1].steps[-1].temperature
            self._block_target = list(final)
            self._block_temp = list(final)

    async def get_block_current_temperature(
        self, request: GetBlockCurrentTemperatureRequest,
    ) -> TemperatureListResponse:
        self._tc_init()
        return TemperatureListResponse(temperatures=list(self._block_temp))

    async def get_block_target_temperature(
        self, request: GetBlockTargetTemperatureRequest,
    ) -> TemperatureListResponse:
        self._tc_init()
        if self._block_target is None:
            raise RuntimeError("Block target temperature is not set. Is a cycle running?")
        return TemperatureListResponse(temperatures=list(self._block_target))

    async def get_lid_current_temperature(
        self, request: GetLidCurrentTemperatureRequest,
    ) -> TemperatureListResponse:
        self._tc_init()
        return TemperatureListResponse(temperatures=list(self._lid_temp))

    async def get_lid_target_temperature(
        self, request: GetLidTargetTemperatureRequest,
    ) -> TemperatureListResponse:
        self._tc_init()
        if self._lid_target is None:
            raise RuntimeError("Lid target temperature is not set. Is a cycle running?")
        return TemperatureListResponse(temperatures=list(self._lid_target))

    async def get_lid_open(self, request: GetLidOpenRequest) -> LidOpenResponse:
        self._tc_init()
        return LidOpenResponse(open=self._lid_open)

    async def get_lid_status(self, request: GetLidStatusRequest) -> ThermocyclerStatusResponse:
        self._tc_init()
        status = self._HOLDING if self._lid_target is not None else self._IDLE
        return ThermocyclerStatusResponse(status=status)

    async def get_block_status(self, request: GetBlockStatusRequest) -> ThermocyclerStatusResponse:
        self._tc_init()
        status = self._HOLDING if self._block_target is not None else self._IDLE
        return ThermocyclerStatusResponse(status=status)

    async def get_hold_time(self, request: GetHoldTimeRequest) -> HoldTimeResponse:
        self._tc_init()
        return HoldTimeResponse(seconds=0.0)

    async def get_current_cycle_index(
        self, request: GetCurrentCycleIndexRequest,
    ) -> CycleIndexResponse:
        self._tc_init()
        # Zero-based per this interface's docstring; PLR chatterbox returns 1
        # here, contradicting its own zero-based doc, so we diverge on purpose.
        return CycleIndexResponse(index=0)

    async def get_total_cycle_count(
        self, request: GetTotalCycleCountRequest,
    ) -> CycleCountResponse:
        self._tc_init()
        return CycleCountResponse(count=1)

    async def get_current_step_index(
        self, request: GetCurrentStepIndexRequest,
    ) -> StepIndexResponse:
        self._tc_init()
        return StepIndexResponse(index=self._current_step)

    async def get_total_step_count(
        self, request: GetTotalStepCountRequest,
    ) -> StepCountResponse:
        self._tc_init()
        return StepCountResponse(count=self._total_steps)


@_apply_fault_hook
class ReaderSimMixin(Sim, IReaderDriver):
    """Mixin for reader functionality"""

    async def read(self, request: ReadRequest) -> None:
        await self._sim(
            f"Reading data from {request.protocol_filepath} and writing to {request.output_filepath}..."
        )
        logger.info(
            f"Data read successfully from {request.protocol_filepath} and written to {request.output_filepath}."
        )


@_apply_fault_hook
class DelidderSimMixin(Sim, IDelidderDriver):
    """Mixin for delidder functionality"""

    async def delid(self, request: DelidRequest) -> None:
        await self._sim("Delidding labware...")
        logger.info("Labware delidded successfully.")


class SimTransporterValidationError(RuntimeError):
    """Raised when SimTransporterDriver detects a logically-invalid move.

    Workflow-author bugs caught at sim time: picking from empty positions,
    placing onto occupied positions, or moves that violate gripper state
    (already-full pick / already-empty place). Real-hardware drivers do
    not raise this; it is fiction-checking only.
    """


@_apply_fault_hook
class SimTransporterDriver(ITransporterDriver):
    """Simulation transporter driver.

    Validates three logical invariants on pick/place by reading parent/child
    state on a PLR Resource graph the sim owns: pick rejects if the position
    is empty or the gripper is full; place rejects if the position is
    occupied or the gripper is empty. Does not validate physics (collision,
    clearance, gripper-finger compatibility).

    The `pick_at_coords`/`place_at_coords` methods key validation by
    `teachpoint.position_id`. The sim does not walk `request.gateway_path` —
    only the production driver (`PLRTransporterBackendWrapper`)
    traverses gateway waypoints. The sim treats pick/place as a single
    atomic op against the destination position.

    Tests and sim setup seed labware at positions via `seed_position`. The
    transporter takes ownership of resources passed to `seed_position`
    (PLR's `assign_child_resource` reparents); do not pass live resources
    that another deck is also tracking.

    Accepts an optional ``faults`` list at construction; pre-armed faults
    apply to public async methods (``pick_at_coords``, ``place_at_coords``,
    ``home``, etc.) per the same mechanism as ``BaseSimDriver`` mixins.
    """

    _GRIPPER_SUFFIX = "__sim_transporter_gripper__"
    _WORLD_SUFFIX = "__sim_transporter_world__"
    _faultable_methods: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        name: str,
        sim_strategy: Optional[SimStrategy] = None,
        faults: Optional[List[FaultSpec]] = None,
    ) -> None:
        self._name = name
        self._is_initialized = False
        self._sim_strategy = sim_strategy or SleepSim()
        self._world: PLRResource = PLRResource(
            name=f"{name}{self._WORLD_SUFFIX}", size_x=0.0, size_y=0.0, size_z=0.0,
        )
        self._gripper: PLRResource = PLRResource(
            name=f"{name}{self._GRIPPER_SUFFIX}", size_x=0.0, size_y=0.0, size_z=0.0,
        )
        self._world.assign_child_resource(self._gripper, location=None)
        self._positions: Dict[str, PLRResource] = {}
        self._faults = _validate_fault_specs(type(self), faults or [])
        # Wire-shape `seed_position`/`ensure_seeded`/`unseed_position` accept
        # `LabwareIdentity` instead of a real PLR resource. We mint a
        # placeholder PLR resource per identity (keyed by `labware_id`) and
        # cache it here so subsequent ensure/unseed/pick ops resolve to the
        # same in-graph object. Real-hardware drivers do not maintain this
        # map; physical state is the source of truth.
        self._resources_by_id: Dict[str, PLRResource] = {}

    @property
    def name(self) -> str:
        return self._name

    async def _sim(self, message: str) -> None:
        await self._sim_strategy.sim(message)

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    def _get_or_create_position(self, position_id: str) -> PLRResource:
        if position_id in self._positions:
            return self._positions[position_id]
        position = PLRResource(
            name=f"{self._name}__pos__{position_id}",
            size_x=0.0, size_y=0.0, size_z=0.0,
        )
        self._world.assign_child_resource(position, location=None)
        self._positions[position_id] = position
        return position

    def _resource_at(self, position_id: str) -> Optional[PLRResource]:
        position = self._positions.get(position_id)
        if position is None or not position.children:
            return None
        return position.children[0]

    def _gripper_payload(self) -> Optional[PLRResource]:
        if not self._gripper.children:
            return None
        return self._gripper.children[0]

    def _seed_resource(self, position_id: str, labware: PLRResource) -> None:
        """Seed a PLR resource at the named position in the sim's world.

        In-process helper used by the wire-shape `seed_position` after
        identity translation, and directly by tests that want to control
        the PLR resource explicitly. The transporter takes ownership of
        `labware`: PLR's `assign_child_resource` reparents it, so do not
        pass a resource that is concurrently tracked by another deck graph.

        Raises `SimTransporterValidationError` if the position is already
        occupied (release the existing labware with `pick` first), or if
        the seed would violate PLR's tree-wide name-uniqueness rule (e.g.,
        seeding two labware with the same `name` into one driver's world).
        """
        position = self._get_or_create_position(position_id)
        if position.children:
            raise SimTransporterValidationError(
                f"seed_position rejected: '{position_id}' already occupied "
                f"by '{position.children[0].name}'"
            )
        labware.unassign()
        try:
            position.assign_child_resource(labware, location=None)
        except ValueError as exc:
            raise SimTransporterValidationError(
                f"seed_position rejected: cannot seed '{labware.name}' at "
                f"'{position_id}': {exc}"
            ) from exc

    def _ensure_resource_seeded(self, position_id: str, labware: PLRResource) -> None:
        """Reconcile the sim graph to the engine ledger's authoritative seed.

        Bridges parallel labware-state trackers (multiple transporters
        each owning a private PLR graph) into agreement: each transporter
        observes peers' moves and ensures its own graph reflects them.
        No-op if `labware` is already at `position_id`.

        The engine ledger is the single source of truth; this graph is a
        derived projection that obeys it. A DIFFERENT occupant at the
        position means the projection holds a stale seed (a prior execution
        that failed or aborted without unseeding). Evict the orphan and seed
        the authoritative labware rather than vetoing the engine's seed -- a
        veto here strands the orphan where no clear/recovery surface can
        reach it and wedges every future submission to this position.

        The gripper obeys the same rule. A ledger that says the labware is at
        a position while this driver still has it in the jaws means it left
        them by a route this driver did not perform -- an operator lifting a
        plate out after a failed place and recording where they put it. Seeding
        takes it out of the gripper, so the next pick is not refused as
        already-holding.
        """
        current = self._resource_at(position_id)
        if current is labware:
            return
        if current is not None:
            logger.warning(
                "ensure_seeded reconciling '%s': evicting stale '%s', seeding '%s'",
                position_id, current.name, labware.name,
            )
            self._evict_resource(current)
        self._seed_resource(position_id, labware)

    def _evict_resource(self, resource: PLRResource) -> None:
        """Detach a stale resource from the sim graph and drop its id cache.

        Removes the resource from its PLR parent and purges every
        `_resources_by_id` entry pointing at it, so a later op for the same
        identity mints a clean placeholder instead of resurrecting the orphan.
        """
        resource.unassign()
        stale_ids = [
            lid for lid, res in self._resources_by_id.items() if res is resource
        ]
        for lid in stale_ids:
            self._resources_by_id.pop(lid, None)

    def _unseed_resource(self, position_id: str, labware: PLRResource) -> None:
        """Idempotent removal of `labware` from `position_id` in the sim graph.

        In-process helper. The wire-shape `unseed_position` translates
        `LabwareIdentity` to its placeholder resource and delegates here.
        No-op if the position does not currently hold this `labware`
        (the caller is likely the actor who already moved it via `pick`).
        """
        current = self._resource_at(position_id)
        if current is labware:
            labware.unassign()


    def _resource_for_identity(self, identity: LabwareIdentity) -> PLRResource:
        """Mint or look up the placeholder PLR resource for a labware identity.

        Wire-shape ops carry only `LabwareIdentity` (id + barcode +
        labware_type); they do not carry the real PLR resource the
        cloud-side workflow tracks. The sim graph still needs a unique
        PLR object per identity to validate world state (PLR enforces
        tree-wide name uniqueness). We mint one here, keyed by id, and
        cache so ensure_seeded/unseed_position/pick all resolve to the
        same node.
        """
        existing = self._resources_by_id.get(identity.labware_id)
        if existing is not None:
            return existing
        placeholder = PLRResource(
            name=self._placeholder_name_for_id(identity.labware_id),
            size_x=0.0, size_y=0.0, size_z=0.0,
        )
        self._resources_by_id[identity.labware_id] = placeholder
        return placeholder


    def _placeholder_name_for_id(self, labware_id: str) -> str:
        """Mirror the naming convention used by `_resource_for_identity`.

        Centralized so `pick_at_coords` can compare placeholder names
        without owning the format string.
        """
        return f"{self._name}__id__{labware_id}"

    async def seed_position(self, request: SeedPositionRequest) -> None:
        """Wire op: seed `request.labware` at `request.position_id`.

        Mints a placeholder PLR resource for the identity and seeds it
        into the sim graph. Raises if the position is already occupied
        (use `ensure_seeded` for idempotent re-sync).

        Note on `_resource_for_identity` cache lifetime: when this method
        raises (position already occupied), the placeholder for
        `request.labware` is already cached in `_resources_by_id` and stays
        cached. This is intentional: a retry of `seed_position` (or
        `ensure_seeded`) for the same identity to ANY position must reuse
        the same placeholder so identity tracking via the
        `{driver}__id__{labware_id}` name stays consistent. Dropping the
        cache entry on raise would let a retry mint a fresh placeholder
        with the same encoded id, potentially colliding with whatever is
        still seeded elsewhere under that name. The cache is bounded by
        per-workflow labware count and goes away when the driver is
        dropped; survives-on-raise is the correct semantic.
        """
        resource = self._resource_for_identity(request.labware)
        self._seed_resource(request.position_id, resource)

    async def ensure_seeded(self, request: EnsureSeededRequest) -> None:
        """Wire op: idempotent seed of `request.labware` at `request.position_id`.

        Mints (or reuses cached) placeholder PLR resource, then delegates
        to the in-process `_ensure_resource_seeded` for the conflict /
        already-seeded checks against the PLR graph.
        """
        resource = self._resource_for_identity(request.labware)
        self._ensure_resource_seeded(request.position_id, resource)

    async def unseed_position(self, request: UnseedPositionRequest) -> None:
        """Wire op: remove `request.labware` from `request.position_id`.

        No-op if the position does not currently hold this labware (the
        caller is likely the actor who already moved it via `pick`).
        Drops the placeholder from the identity cache so subsequent ops
        for the same identity treat it as fresh.
        """
        resource = self._resource_for_identity(request.labware)
        self._unseed_resource(request.position_id, resource)
        # Free the cache slot once the placeholder is fully detached. Real
        # PLR semantics: an unassigned resource may still be referenced by
        # tests; we only drop the cache entry, not the object.
        if request.labware.labware_id in self._resources_by_id:
            current = self._resources_by_id[request.labware.labware_id]
            if current is resource and self._gripper_payload() is not resource:
                # Only drop if the resource is no longer in use (gripper or
                # any cached position).
                still_seeded = any(
                    self._resource_at(pos) is resource for pos in self._positions
                )
                if not still_seeded:
                    self._resources_by_id.pop(request.labware.labware_id, None)

    async def reset_world(self, request: ResetWorldRequest) -> None:
        """Wipe all labware occupancy from the sim graph (authoritative clear).

        Detaches every seeded labware from every position and the gripper and
        drops the identity cache. Position/gripper structure is preserved so
        the driver keeps validating moves; only occupancy is cleared. Used by
        the engine's `clear_all_labware` to make a "no labware anywhere"
        ledger authoritative over this projection -- reaching orphaned seeds
        that per-labware `unseed_position` cannot, because the engine no
        longer knows their identities.
        """
        for position in self._positions.values():
            for child in list(position.children):
                child.unassign()
        for child in list(self._gripper.children):
            child.unassign()
        self._resources_by_id.clear()

    async def initialize(self, request: InitializeRequest) -> None:
        logger.info(f"Initializing transporter driver: {self.name}...")
        self._is_initialized = True
        logger.info(f"Transporter driver {self.name} initialized successfully.")

    async def home(self, request: HomeRequest) -> None:
        """Homes the transporter."""
        await self._sim(f"Driver: {self.name} homing...")
        logger.info(f"Driver: {self.name} homed successfully.")

    async def move_to_safe(self, request: MoveToSafeRequest) -> None:
        """Moves the transporter to a safe position."""
        await self._sim(f"Driver: {self.name} moving to safe position...")
        logger.info(f"Driver: {self.name} moved to safe position successfully.")

    async def _validated_pick(self, position_id: str, sim_message: str, log_message: str) -> None:
        held = self._gripper_payload()
        if held is not None:
            raise SimTransporterValidationError(
                f"pick rejected: gripper already holds "
                f"'{held.name}'; cannot pick from '{position_id}'"
            )
        labware = self._resource_at(position_id)
        if labware is None:
            raise SimTransporterValidationError(
                f"pick rejected: position '{position_id}' is empty"
            )
        await self._sim(sim_message)
        labware.unassign()
        self._gripper.assign_child_resource(labware, location=None)
        logger.info(log_message)

    async def _validated_place(self, position_id: str, sim_message: str, log_message: str) -> None:
        payload = self._gripper_payload()
        if payload is None:
            raise SimTransporterValidationError(
                f"place rejected: gripper is empty; nothing to place at '{position_id}'"
            )
        position = self._get_or_create_position(position_id)
        if position.children:
            raise SimTransporterValidationError(
                f"place rejected: position '{position_id}' already occupied "
                f"by '{position.children[0].name}'"
            )
        await self._sim(sim_message)
        payload.unassign()
        position.assign_child_resource(payload, location=None)
        logger.info(log_message)

    async def _external_control_noop(self, gerund: str, past: str) -> None:
        """Ad-hoc/external-control move: orca tracks no occupancy here, so
        validate and mutate nothing; simulate motion only."""
        await self._sim(f"Driver: {self.name} {gerund} at coords (external control)...")
        logger.info(f"Driver: {self.name} {past} at coords (external control)")

    async def pick_at_coords(self, request: PickAtCoordsRequest) -> None:
        """Pick plate at coordinates specified by teachpoint.

        Validation keys off `teachpoint.position_id`. When
        `request.expected_labware` is set, additionally cross-checks that
        the resource at the named position carries the expected identity
        (placeholder PLR resource name encodes `labware_id`). Mismatch
        raises `SimTransporterValidationError`.
        """
        teachpoint = request.teachpoint
        if request.external_control:
            await self._external_control_noop("picking", "picked")
            return
        if request.expected_labware is not None:
            current = self._resource_at(teachpoint.position_id)
            if current is not None:
                expected_name = self._placeholder_name_for_id(
                    request.expected_labware.labware_id
                )
                if current.name != expected_name:
                    raise SimTransporterValidationError(
                        f"pick rejected: position '{teachpoint.position_id}' holds "
                        f"'{current.name}', expected labware id "
                        f"'{request.expected_labware.labware_id}'"
                    )
        coords = teachpoint.coordinates
        if isinstance(coords, CartesianCoordinates):
            sim_message = f"Driver: {self.name} picking at coords ({coords.x}, {coords.y}, {coords.z})..."
            log_message = f"Driver: {self.name} picked at coords ({coords.x}, {coords.y}, {coords.z})"
        else:
            assert isinstance(coords, JointCoordinates)
            sim_message = f"Driver: {self.name} picking at joint coords (elbow={coords.elbow})..."
            log_message = f"Driver: {self.name} picked at joint coords (elbow={coords.elbow})"
        await self._validated_pick(
            position_id=teachpoint.position_id, sim_message=sim_message, log_message=log_message,
        )

    async def place_at_coords(self, request: PlaceAtCoordsRequest) -> None:
        """Place plate at coordinates specified by teachpoint.

        Validation keys off `teachpoint.position_id`. When
        `request.expected_labware` is set, additionally cross-checks that
        the resource currently held by the gripper carries the expected
        identity (placeholder PLR resource name encodes `labware_id`).
        Mismatch raises `SimTransporterValidationError` BEFORE the place
        proceeds, so a misauthored caller surfaces the bug at the place
        site rather than at the next pick (which would look like world
        drift).
        """
        teachpoint = request.teachpoint
        if request.external_control:
            await self._external_control_noop("placing", "placed")
            return
        if request.expected_labware is not None:
            held = self._gripper_payload()
            if held is not None:
                expected_name = self._placeholder_name_for_id(
                    request.expected_labware.labware_id
                )
                if held.name != expected_name:
                    raise SimTransporterValidationError(
                        f"place rejected: gripper holds '{held.name}', "
                        f"expected labware id "
                        f"'{request.expected_labware.labware_id}'"
                    )
        coords = teachpoint.coordinates
        if isinstance(coords, CartesianCoordinates):
            sim_message = f"Driver: {self.name} placing at coords ({coords.x}, {coords.y}, {coords.z})..."
            log_message = f"Driver: {self.name} placed at coords ({coords.x}, {coords.y}, {coords.z})"
        else:
            assert isinstance(coords, JointCoordinates)
            sim_message = f"Driver: {self.name} placing at joint coords (elbow={coords.elbow})..."
            log_message = f"Driver: {self.name} placed at joint coords (elbow={coords.elbow})"
        await self._validated_place(
            position_id=teachpoint.position_id, sim_message=sim_message, log_message=log_message,
        )

    async def move_to_coords(self, request: MoveToCoordsRequest) -> None:
        """Move to coordinates specified by teachpoint."""
        teachpoint = request.teachpoint
        coords = teachpoint.coordinates
        if isinstance(coords, CartesianCoordinates):
            await self._sim(f"Driver: {self.name} moving to coords ({coords.x}, {coords.y}, {coords.z})...")
            logger.info(f"Driver: {self.name} moved to coords ({coords.x}, {coords.y}, {coords.z})")
        else:
            assert isinstance(coords, JointCoordinates)
            await self._sim(f"Driver: {self.name} moving to joint coords (elbow={coords.elbow})...")
            logger.info(f"Driver: {self.name} moved to joint coords (elbow={coords.elbow})")

    async def get_joint_position(self, request: GetJointPositionRequest) -> JointCoordinates:
        """Return simulated joint position (default safe position)."""
        return JointCoordinates(rail=0.0, base=170.0, shoulder=0.0, elbow=180.0, wrist=0.0, gripper=0.0)

    async def move_single_axis(self, request: MoveSingleAxisRequest) -> None:
        """Move a single axis to absolute position."""
        await self._sim(f"Driver: {self.name} moving {request.axis} to {request.position}...")
        logger.info(f"Driver: {self.name} moved {request.axis} to {request.position}")

    async def move_single_axis_relative(self, request: MoveSingleAxisRelativeRequest) -> None:
        """Move a single axis by relative distance from current position."""
        await self._sim(f"Driver: {self.name} moving {request.axis} by {request.distance}...")
        logger.info(f"Driver: {self.name} moved {request.axis} by {request.distance}")

    async def set_free_mode(self, request: SetFreeModeRequest) -> None:
        """Enable/disable free mode (freedrive) for specified axes."""
        await self._sim(f"Driver: {self.name} setting free mode: {request.axes}...")
        logger.info(f"Driver: {self.name} free mode set to {request.axes}")

    async def open_gripper(self, request: OpenGripperRequest) -> None:
        """Open the jaws, by a stated opening, to a named position, or by the default."""
        if request.position is not None:
            target = f"{request.position}"
        elif request.jaw_opening is not None:
            target = f"{request.jaw_opening} past its grip position"
        else:
            target = "its standoff"
        await self._sim(f"Driver: {self.name} opening gripper to {target}...")
        logger.info(f"Driver: {self.name} gripper opened to {target}")

    async def close_gripper(self, request: CloseGripperRequest) -> None:
        """Close the jaws, to a named position or the calibrated grip position."""
        target = "its grip position" if request.position is None else f"{request.position}"
        await self._sim(f"Driver: {self.name} closing gripper to {target}...")
        logger.info(f"Driver: {self.name} gripper closed to {target}")

    async def get_cartesian_position(self, request: GetCartesianPositionRequest) -> CartesianCoordinates:
        """Return simulated Cartesian position."""
        return CartesianCoordinates(x=0.0, y=0.0, z=0.0, roll=0.0, pitch=0.0, yaw=0.0)

    async def set_speed(self, request: SetSpeedRequest) -> None:
        """Set movement speed as percentage of maximum (0.0 to 1.0)."""
        await self._sim(f"Driver: {self.name} setting speed to {request.speed * 100}%...")
        logger.info(f"Driver: {self.name} speed set to {request.speed * 100}%")

    async def get_speed(self, request: GetSpeedRequest) -> float:
        """Get current movement speed setting as percentage (0.0 to 1.0)."""
        return 0.5  # Default 50% speed

    async def halt(self, request: HaltRequest) -> None:
        """Emergency stop - immediately halt all movement."""
        await self._sim(f"Driver: {self.name} HALT!")
        logger.warning(f"Driver: {self.name} emergency halt executed")


@_apply_fault_hook
class StorageSimMixin(Sim, IStorageDriver):
    async def dispense(self) -> None:
        await self._sim("Dispensing plate from source")

@functools.lru_cache(maxsize=1)
def _labware_seed_index() -> Dict[str, LabwareSeedEntry]:
    return {entry.labware_type: entry for entry in load_labware_seed()}


def _site_label_for(parent_id: str, site_index: int) -> str:
    """Site label for an occupancy entry, in the vocabulary commands accept.

    An Opentrons slot is named ('C2', or '7' on an OT-2) and takes the '-slot'
    suffix; a Hamilton carrier site is the carrier plus its numeric index.
    """
    is_opentrons_slot = parent_id.isdigit() or (
        len(parent_id) >= 2 and parent_id[0].isalpha() and parent_id[1:].isdigit()
    )
    return f"{parent_id}-slot" if is_opentrons_slot else f"{parent_id}-{site_index}"


@dataclass
class _SimDeckItem:
    """One labware on the sim deck, described from the bundled catalog."""

    name: str
    catalog_ref: str

    @property
    def _seed(self) -> Optional[LabwareSeedEntry]:
        return _labware_seed_index().get(self.catalog_ref)

    @property
    def category(self) -> Optional[str]:
        seed = self._seed
        return None if seed is None else seed.category

    @property
    def plr_type(self) -> str:
        """Class name a PLR-backed driver would report, so both look alike to a caller."""
        return {"plate": "Plate", "tip_rack": "TipRack", "trough": "Trough"}.get(
            self.category or "", "Resource",
        )

    @property
    def total_tips(self) -> Optional[int]:
        seed = self._seed
        return len(seed.tip_spots) if isinstance(seed, TipRackSeedEntry) else None


@_apply_fault_hook
class LiquidHandlerSimMixin(Sim, ILiquidHandlerDriver):
    """Sim mixin implementing ILiquidHandlerDriver with string-based Pydantic requests.

    Volumes and tip state are not tracked (provides_state=False, so the
    orca-core bridge keeps its own operation-derived bookkeeping rather than
    overwriting trackers with empty driver state). Deck OCCUPANCY is tracked,
    because a caller driving this sim with no hardware needs to see what it
    placed and needs an occupied site refused, exactly as PyLabRobot refuses it.
    """

    provides_state: bool = False

    _empty_response = LabwareStateResponse(success=True)

    @property
    def _deck(self) -> Dict[str, "_SimDeckItem"]:
        """Site label -> what sits there. Built on first use so the mixin needs no __init__."""
        existing = getattr(self, "_deck_items", None)
        if existing is None:
            existing = {}
            self._deck_items = existing
        return existing

    def _site_of(self, name: str) -> Optional[str]:
        return next((site for site, item in self._deck.items() if item.name == name), None)

    def _place_on_deck(self, name: str, catalog_ref: str, site: str) -> None:
        occupant = self._deck.get(site)
        if occupant is not None:
            raise ValueError(f"Site {site} is already occupied by '{occupant.name}'.")
        if self._site_of(name) is not None:
            raise ValueError(f"Labware '{name}' is already on the deck.")
        self._deck[site] = _SimDeckItem(name=name, catalog_ref=catalog_ref)

    async def configure_deck(self, config: DeckLayoutConfig) -> LabwareStateResponse:
        await self._sim(f"Configure deck: {config.deck_type} with {len(config.resources)} resources")
        # A rebuilt deck starts bare; labware arrives through the occupancy ops.
        self._deck.clear()
        return self._empty_response

    async def get_deck_state(self, request: GetDeckStateRequest) -> DeckStateResponse:
        labware: List[DeckResourceState] = []
        tip_racks: List[TipRackState] = []
        for site, item in self._deck.items():
            labware.append(DeckResourceState(
                name=item.name,
                type=item.plr_type,
                category=item.category,
                site=site,
            ))
            if item.total_tips is not None:
                tip_racks.append(TipRackState(
                    name=item.name, tips_remaining=item.total_tips, total_tips=item.total_tips,
                ))
        return DeckStateResponse(
            tips_mounted=[False] * 8, labware=labware, tip_racks=tip_racks,
        )

    # Eight independent plungers is the PERMISSIVE end of the range: set this to model the head
    # you mean to stand in for, or a ganged head's refusals will not show up until hardware.
    head_configuration: HeadConfigurationResponse = HeadConfigurationResponse(
        groups=[NozzleGroup(channels=[c]) for c in range(8)]
    )

    async def get_head_configuration(
        self, request: GetHeadConfigurationRequest
    ) -> HeadConfigurationResponse:
        return self.head_configuration

    async def reset_deck_labware(self, request: ResetDeckLabwareRequest) -> LabwareStateResponse:
        await self._sim("Reset deck labware (occupancy wipe)")
        self._deck.clear()
        return self._empty_response

    async def reconcile_deck_occupancy(
        self, request: ReconcileDeckOccupancyRequest,
    ) -> LabwareStateResponse:
        await self._sim(f"Reconcile deck occupancy: {len(request.resources)} resources")
        # Authoritative set: whatever the engine says is on the deck now, is.
        self._deck.clear()
        for resource in request.resources:
            if resource.parent_id is None:
                continue
            self._place_on_deck(
                resource.name,
                resource.catalog_ref,
                _site_label_for(resource.parent_id, resource.site_index or 0),
            )
        return self._empty_response

    async def reconcile_hardware_state(
        self, request: ReconcileHardwareStateRequest
    ) -> ReconcileHardwareStateResponse:
        await self._sim("Reconcile hardware state")
        return ReconcileHardwareStateResponse(
            checked=True, message="simulated hardware cannot diverge from its own model"
        )

    async def discard_stranded_tips(
        self, request: DiscardStrandedTipsRequest
    ) -> ReconcileHardwareStateResponse:
        await self._sim("Discard stranded tips (none in simulation)")
        return ReconcileHardwareStateResponse(
            checked=True, message="no stranded tips: simulated hardware tracks its own model"
        )

    async def aspirate(self, request: AspirateRequest) -> LabwareStateResponse:
        targets_str = ", ".join(f"{a.volumes} from {a.labware} {a.positions}" for a in request.aspirations)
        await self._sim(f"Aspirate {targets_str}")
        partial = self._faults.partial_for("aspirate")
        if partial is not None:
            return build_aspirate_partial_failure(
                request,
                {ch: (partial.error_code, partial.error_message) for ch in partial.failed_channels},
            )
        return self._empty_response

    async def dispense(self, request: DispenseRequest) -> LabwareStateResponse:
        targets_str = ", ".join(f"{d.volumes} to {d.labware} {d.positions}" for d in request.dispenses)
        await self._sim(f"Dispense {targets_str}")
        partial = self._faults.partial_for("dispense")
        if partial is not None:
            return build_dispense_partial_failure(
                request,
                {ch: (partial.error_code, partial.error_message) for ch in partial.failed_channels},
            )
        return self._empty_response

    async def pick_up_tips(self, request: PickUpTipsRequest) -> LabwareStateResponse:
        picks_str = ", ".join(f"{p.tip_rack} {p.positions}" for p in request.picks)
        await self._sim(f"Pick up tips from {picks_str}")
        return self._empty_response

    async def drop_tips(self, request: DropTipsRequest) -> LabwareStateResponse:
        if request.to_waste:
            await self._sim("Drop tips to waste")
        else:
            assert request.drops is not None
            drops_str = ", ".join(f"{d.tip_rack} {d.positions}" for d in request.drops)
            await self._sim(f"Drop tips to {drops_str}")
        return self._empty_response

    async def discard_tips(self, request: DiscardTipsRequest) -> LabwareStateResponse:
        await self._sim(f"Discard tips (channels={request.use_channels})")
        return self._empty_response

    async def move_plate(self, request: MovePlateRequest) -> None:
        from_str = f" from {request.from_position}" if request.from_position else ""
        await self._sim(f"Move plate {request.plate}{from_str} to {request.to_position}")

    async def add_deck_labware(self, request: AddDeckLabwareRequest) -> None:
        await self._sim(f"Add deck labware {request.name} ({request.catalog_ref}) at {request.at}")
        self._place_on_deck(request.name, request.catalog_ref, request.at)

    async def remove_deck_labware(self, request: RemoveDeckLabwareRequest) -> None:
        await self._sim(f"Remove deck labware {request.name}")
        site = self._site_of(request.name)
        if site is None:
            raise ValueError(f"remove_deck_labware target '{request.name}' is not on the deck.")
        del self._deck[site]

    async def mix(self, request: MixRequest) -> LabwareStateResponse:
        await self._sim(f"Mix {request.repetitions}x {request.volume}uL in {request.labware} {request.positions}")
        return self._empty_response

    async def aspirate96(self, request: Aspirate96Request) -> LabwareStateResponse:
        await self._sim(f"Aspirate96 {request.volume}uL from {request.labware}")
        return self._empty_response

    async def dispense96(self, request: Dispense96Request) -> LabwareStateResponse:
        await self._sim(f"Dispense96 {request.volume}uL to {request.labware}")
        return self._empty_response

    async def pick_up_tips96(self, request: PickUpTips96Request) -> LabwareStateResponse:
        await self._sim(f"Pick up tips96 from {request.tip_rack}")
        return self._empty_response

    async def drop_tips96(self, request: DropTips96Request) -> LabwareStateResponse:
        if request.to_waste:
            await self._sim("Drop tips96 to waste")
        else:
            await self._sim(f"Drop tips96 to {request.tip_rack}")
        return self._empty_response

    async def return_tips96(self, request: ReturnTips96Request) -> LabwareStateResponse:
        await self._sim("Return tips96")
        return self._empty_response

SIM_LIQUID_HEIGHT_MM = 5.0
"""What a sim probe reports finding. Nothing here models liquid, so this is a
plausible height that keeps a probe-then-pipette workflow runnable, not a
measurement of anything."""


@_apply_fault_hook
class LiquidProbeSimMixin(Sim, ILiquidProbeDriver):
    """Sim liquid-level detection: always finds liquid, always at the same height."""

    async def liquid_probe(self, request: LiquidProbeRequest) -> LiquidProbeResponse:
        where = "the container" if request.positions is None else ", ".join(request.positions)
        await self._sim(f"Liquid probe: {request.labware} {where}")
        return LiquidProbeResponse(height=SIM_LIQUID_HEIGHT_MM)


@_apply_fault_hook
class HomeableSimMixin(Sim, IHomeableDriver):
    """Sim homing: reports the sweep, moves nothing.

    Here so a workflow that homes before its first move runs unchanged with no
    hardware. Without it a device in sim mode refuses a command its real driver
    accepts, which is the kind of gap only the bench finds.
    """

    async def home(self, request: HomeRequest) -> None:
        await self._sim(f"Driver: {self.name} homing...")
        logger.info(f"Driver: {self.name} homed successfully.")


@_apply_fault_hook
class GantryParkingSimMixin(Sim, IGantryParkingDriver):
    """Sim parking: reports the move, moves nothing, never refuses.

    A real handler refuses while it carries tips or holds a plate in its jaws.
    This sim keeps neither, so a sim run always parks; the refusal path is the
    real driver's, and a sim that faked it would only pin the fake.
    """

    async def park_gantry(self, request: ParkGantryRequest) -> None:
        where = "clear of its deck" if request.at is None else f"at {request.at}"
        await self._sim(f"Driver: {self.name} parking {where}...")
        logger.info(f"Driver: {self.name} parked successfully.")


@_apply_fault_hook
class PlateWasherSimMixin(ProtocolRunnerSimMixin, IPlateWasherDriver):
    pass

@_apply_fault_hook
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

class SimThermocyclerDriver(BaseSimDriver, ThermocyclerSimMixin):
    """Simulation thermocycler driver using mixin"""
    pass

class SimStorageDriver(BaseSimDriver, StorageSimMixin):
    pass

class SimPlateWasherDriver(BaseSimDriver, PlateWasherSimMixin):
    pass

class SimLiquidHandlerDriver(
    BaseSimDriver, LiquidHandlerSimMixin, LiquidProbeSimMixin, HomeableSimMixin,
    GantryParkingSimMixin,
):
    """Sim liquid handler implementing ILiquidHandlerDriver. No PLR dependency.

    Declares ILiquidProbe so a workflow that asks where the liquid is can be
    written and run with no hardware, IHomeable because bring-up no longer
    homes anything, so homing is an ordinary first step a sim run has to accept,
    and IGantryParking so a run that steps a handler aside for an arm does not
    need hardware either. Inheritance shadows ``interfaces`` rather than unioning
    it, so the full set is restated here.
    """

    interfaces: ClassVar[frozenset[str]] = frozenset(
        {"ILiquidHandler", "ILiquidProbe", "IHomeable", "IGantryParking"}
    )

class SimLiquidHandlerWithProtocolDriver(
    BaseSimDriver,
    LiquidHandlerSimMixin,
    LiquidProbeSimMixin,
    ProtocolRunnerSimMixin,
    HomeableSimMixin,
    GantryParkingSimMixin,
    ILiquidHandlerWithProtocolDriver,
):
    """Sim LH that supports BOTH atomic ops AND vendor protocol files.

    The composite case the `ILiquidHandlerDriver` docstring describes:
    a Hamilton MLSTAR + Venus, an Agilent Bravo + VWorks, etc. Real
    drivers in this category multi-inherit ILiquidHandlerDriver and
    IProtocolRunnerDriver and merge their interface sets.
    """

    interfaces: ClassVar[frozenset[str]] = frozenset(
        {"ILiquidHandler", "ILiquidProbe", "IProtocolRunner", "IHomeable",
         "IGantryParking"}
    )

class SimDelidderDriver(BaseSimDriver, DelidderSimMixin):
    pass

class SimWasteDriver(BaseSimDriver, WasteSimMixin):
    pass

class SimReaderDriver(BaseSimDriver, ReaderSimMixin):
    pass


# ----------------------------------------------------------------------
# Recording wrappers
# ----------------------------------------------------------------------
#
# Each Recording*Driver wraps an inner driver of the same interface and
# captures every method call as a RecordedCall, then delegates. Tests
# assert against the captured `calls` list to verify wire shape.
# Realism / state / timing all come from the inner driver; recording is
# orthogonal.


@dataclass(frozen=True)
class RecordedCall:
    """A single captured method call with its arguments."""
    method: str
    args: Dict[str, Any]


class RecordingLiquidHandlerDriver(ILiquidHandlerDriver):
    """Wraps an ILiquidHandlerDriver and records every method call.

    Delegates to the inner driver so the full pipeline is exercised.
    Recorded calls are available via the ``calls`` list for assertion.
    """

    interfaces: ClassVar[frozenset[str]] = frozenset({"ILiquidHandler"})
    provides_state: ClassVar[bool] = True

    def __init__(self, inner: ILiquidHandlerDriver) -> None:
        self._inner = inner
        self.calls: List[RecordedCall] = []

    async def initialize(self) -> None:
        await self._inner.initialize()

    @property
    def is_initialized(self) -> bool:
        return self._inner.is_initialized

    async def open(self) -> None:
        self.calls.append(RecordedCall(method="open", args={}))
        await self._inner.open()

    async def close(self) -> None:
        self.calls.append(RecordedCall(method="close", args={}))
        await self._inner.close()

    async def configure_deck(self, config: DeckLayoutConfig) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="configure_deck", args={"config": config.model_dump()}))
        return await self._inner.configure_deck(config)

    async def get_deck_state(self, request: GetDeckStateRequest) -> DeckStateResponse:
        self.calls.append(RecordedCall(method="get_deck_state", args=request.model_dump()))
        return await self._inner.get_deck_state(request)

    async def get_head_configuration(
        self, request: GetHeadConfigurationRequest
    ) -> HeadConfigurationResponse:
        self.calls.append(RecordedCall(method="get_head_configuration", args=request.model_dump()))
        return await self._inner.get_head_configuration(request)

    async def reset_deck_labware(self, request: ResetDeckLabwareRequest) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="reset_deck_labware", args=request.model_dump()))
        return await self._inner.reset_deck_labware(request)

    async def reconcile_deck_occupancy(
        self, request: ReconcileDeckOccupancyRequest,
    ) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="reconcile_deck_occupancy", args=request.model_dump()))
        return await self._inner.reconcile_deck_occupancy(request)

    async def reconcile_hardware_state(
        self, request: ReconcileHardwareStateRequest,
    ) -> ReconcileHardwareStateResponse:
        self.calls.append(
            RecordedCall(method="reconcile_hardware_state", args=request.model_dump())
        )
        return await self._inner.reconcile_hardware_state(request)

    async def discard_stranded_tips(
        self, request: DiscardStrandedTipsRequest,
    ) -> ReconcileHardwareStateResponse:
        self.calls.append(RecordedCall(method="discard_stranded_tips", args=request.model_dump()))
        return await self._inner.discard_stranded_tips(request)

    async def aspirate(self, request: AspirateRequest) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="aspirate", args=request.model_dump()))
        return await self._inner.aspirate(request)

    async def dispense(self, request: DispenseRequest) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="dispense", args=request.model_dump()))
        return await self._inner.dispense(request)

    async def pick_up_tips(self, request: PickUpTipsRequest) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="pick_up_tips", args=request.model_dump()))
        return await self._inner.pick_up_tips(request)

    async def drop_tips(self, request: DropTipsRequest) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="drop_tips", args=request.model_dump()))
        return await self._inner.drop_tips(request)

    async def discard_tips(self, request: DiscardTipsRequest) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="discard_tips", args=request.model_dump()))
        return await self._inner.discard_tips(request)

    async def move_plate(self, request: MovePlateRequest) -> None:
        self.calls.append(RecordedCall(method="move_plate", args=request.model_dump()))
        await self._inner.move_plate(request)

    async def add_deck_labware(self, request: AddDeckLabwareRequest) -> None:
        self.calls.append(RecordedCall(method="add_deck_labware", args=request.model_dump()))
        await self._inner.add_deck_labware(request)

    async def remove_deck_labware(self, request: RemoveDeckLabwareRequest) -> None:
        self.calls.append(RecordedCall(method="remove_deck_labware", args=request.model_dump()))
        await self._inner.remove_deck_labware(request)

    async def mix(self, request: MixRequest) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="mix", args=request.model_dump()))
        return await self._inner.mix(request)

    async def aspirate96(self, request: Aspirate96Request) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="aspirate96", args=request.model_dump()))
        return await self._inner.aspirate96(request)

    async def dispense96(self, request: Dispense96Request) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="dispense96", args=request.model_dump()))
        return await self._inner.dispense96(request)

    async def pick_up_tips96(self, request: PickUpTips96Request) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="pick_up_tips96", args=request.model_dump()))
        return await self._inner.pick_up_tips96(request)

    async def drop_tips96(self, request: DropTips96Request) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="drop_tips96", args=request.model_dump()))
        return await self._inner.drop_tips96(request)

    async def return_tips96(self, request: ReturnTips96Request) -> LabwareStateResponse:
        self.calls.append(RecordedCall(method="return_tips96", args=request.model_dump()))
        return await self._inner.return_tips96(request)


class RecordingShakerDriver(IShakerDriver):
    """Wraps an IShakerDriver and records every domain-op method call.

    Delegates to the inner driver so timing + state come through unchanged.
    Recorded calls are available via the ``calls`` list for assertion.

    Lifecycle ops (``initialize``, ``open``, ``close``, ``stop``) delegate
    silently and are NOT recorded -- mirroring ``RecordingLiquidHandlerDriver``.
    Tests assert against the device's real work (shake / lock / unlock); a
    test that wants to verify init/open/close happened can read the inner
    driver's state instead.
    """

    interfaces: ClassVar[frozenset[str]] = frozenset({"IShaker"})

    def __init__(self, inner: IShakerDriver) -> None:
        self._inner = inner
        self.calls: List[RecordedCall] = []

    @property
    def is_initialized(self) -> bool:
        return self._inner.is_initialized

    @property
    def supports_locking(self) -> bool:
        return self._inner.supports_locking

    async def initialize(self) -> None:
        await self._inner.initialize()

    async def open(self) -> None:
        await self._inner.open()

    async def close(self) -> None:
        await self._inner.close()

    async def stop(self) -> None:
        await self._inner.stop()

    async def shake(self, request: ShakeRequest) -> None:
        self.calls.append(RecordedCall(method="shake", args=request.model_dump()))
        await self._inner.shake(request)

    async def stop_shaking(self, request: StopShakingRequest) -> None:
        self.calls.append(RecordedCall(method="stop_shaking", args=request.model_dump()))
        await self._inner.stop_shaking(request)

    async def lock_plate(self, request: LockPlateRequest) -> None:
        self.calls.append(RecordedCall(method="lock_plate", args=request.model_dump()))
        await self._inner.lock_plate(request)

    async def unlock_plate(self, request: UnlockPlateRequest) -> None:
        self.calls.append(RecordedCall(method="unlock_plate", args=request.model_dump()))
        await self._inner.unlock_plate(request)
