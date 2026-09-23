import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Callable, ClassVar, Dict, List, Optional, Protocol, runtime_checkable

from cheshire_drivers.plr_tip_tracker import LenientTipTracker
from cheshire_drivers.plr_tracker_seeding import (
    swap_container_to_lenient,
    swap_container_to_replenishing,
    swap_container_to_strict,
    swap_to_lenient,
    swap_to_replenishing_tracker,
    swap_to_strict_tracker,
)

from cheshire_drivers.interfaces import (
    ICentrifugeDriver, ILiquidHandlerDriver,
    IReaderDriver, ISealerDriver, IShakerDriver,
    IStorageDriver, ITempGettableDriver, ITempSettableDriver,
    IThermocyclerDriver,
)
from cheshire_drivers.driver_introspection import (
    VendorSurface,
    resolve_vendor_object,
    vendor_surfaces,
)
from cheshire_drivers.interrupted_move import InterruptedMoveTracking
from cheshire_drivers.reader_models import ReadRequest
from cheshire_drivers.labware_interfaces import IPlate, ITipRack, ITipSpot, IWell
from cheshire_drivers.liquid_handler_models import (
    AspirateRequest, Aspirate96Request,
    DeckLayoutConfig, DeckResourceConfig, DeckResourceState,
    DeckStateResponse,
    DiscardTipsRequest,
    DispenseRequest, Dispense96Request,
    DropTipsRequest, DropTips96Request,
    GetDeckStateRequest,
    GetHeadConfigurationRequest,
    HeadConfigurationResponse,
    NozzleGroup,
    LabwareStateResponse, LabwareWellState,
    MixRequest, MovePlateRequest,
    PickUpTipsRequest, PickUpTips96Request,
    PipettingParameters,
    AddDeckLabwareRequest,
    DiscardStrandedTipsRequest,
    ReconcileDeckOccupancyRequest,
    ReconcileHardwareStateRequest,
    ReconcileHardwareStateResponse,
    ResetDeckLabwareRequest,
    ReturnTips96Request,
    TROUGH_WELL_ID,
    TipRackState,
    RemoveDeckLabwareRequest,
    build_aspirate_partial_failure,
    build_dispense_partial_failure,
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
    Protocol as ThermocyclerProtocol,
    RunProtocolRequest as ThermocyclerRunProtocolRequest,
    SetBlockTemperatureRequest,
    SetLidTemperatureRequest,
    StepCountResponse,
    StepIndexResponse,
    TemperatureListResponse,
    ThermocyclerStatusResponse,
)
from cheshire_drivers.sealer_models import SealRequest
from cheshire_drivers.shaker_models import (
    LockPlateRequest,
    ShakeRequest,
    StopShakingRequest,
    UnlockPlateRequest,
)
from cheshire_drivers._plr_compat import ensure_plr_stubs

ensure_plr_stubs()

from pylabrobot.legacy.liquid_handling.errors import ChannelizedError as PLRChannelizedError
from pylabrobot.legacy.liquid_handling import LiquidHandler as PLRLiquidHandler
from pylabrobot.legacy.liquid_handling.backends import (
    LiquidHandlerBackend as PLRLiquidHandlerBackend,
)
from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import (
    OpentronsOT2Backend as PLROpentronsOT2Backend,
)
from pylabrobot.legacy.liquid_handling.standard import Mix as PLRMix
from pylabrobot.resources.carrier import Carrier as PLRCarrier
from pylabrobot.resources.container import Container as PLRContainer
from pylabrobot.resources.deck import Deck as PLRDeck
from pylabrobot.resources.errors import ResourceNotFoundError as PLRResourceNotFoundError
from pylabrobot.resources.itemized_resource import ItemizedResource as PLRItemizedResource
from pylabrobot.resources.plate import Plate as PLRPlate
from pylabrobot.resources.resource_holder import ResourceHolder as PLRResourceHolder
from pylabrobot.resources.tip_rack import TipRack as PLRTipRack, TipSpot as PLRTipSpot
from pylabrobot.resources.tip_tracker import TipTracker as PLRTipTracker, set_tip_tracking
from pylabrobot.resources.trash import Trash as PLRTrash
from pylabrobot.resources.trough import Trough as PLRTrough
from pylabrobot.resources.volume_tracker import set_volume_tracking
from pylabrobot.resources.well import Well as PLRWell
from pylabrobot.visualizer import Visualizer

from cheshire_drivers.plr_volume_tracker import LenientVolumeTracker

from pylabrobot.legacy.sealing.backend import SealerBackend as PLRSealerBackend
from pylabrobot.shaking import ShakerBackend as PLRShakerBackend
from pylabrobot.legacy.centrifuge.backend import CentrifugeBackend as PLRCentrifugeBackend
from pylabrobot.thermocycling import ThermocyclerBackend as PLRThermocyclerBackend
from pylabrobot.legacy.thermocycling.standard import (
    Protocol as PLRProtocol,
    Stage as PLRStage,
    Step as PLRStep,
)
from pylabrobot.legacy.plate_reading.backend import PlateReaderBackend as PLRPlateReaderBackend
from pylabrobot.storage import IncubatorBackend as PLRIncubatorBackend
from pylabrobot.heating_shaking import HeaterShakerBackend as PLRHeaterShakerBackend
from pylabrobot.legacy.temperature_controlling.backend import TemperatureControllerBackend as PLRTempControllerBackend

logger = logging.getLogger(__name__)

# PLR backends re-exported for orca-core, plus the deck and labware helpers both
# liquid-handler adapters share.
__all__ = [
    "MOVABLE_LABWARE",
    "SimServerLifecycle",
    "create_catalog_resource",
    "slot_key_from_site",
    "slot_names_for_deck_type",
    "start_deck_visualizer",
    "PLRLiquidHandlerBackend",
    "PLRSealerBackend",
    "PLRShakerBackend",
    "PLRCentrifugeBackend",
    "PLRThermocyclerBackend",
    "PLRPlateReaderBackend",
    "PLRIncubatorBackend",
    "PLRHeaterShakerBackend",
    "PLRTempControllerBackend",
    "PLRLiquidHandlerWrapper",
    "PLRSealerBackendWrapper",
    "PLRShakerBackendWrapper",
    "PLRCentrifugeBackendWrapper",
    "PLRThermocyclerBackendWrapper",
    "PLRReaderBackendWrapper",
    "PLRStorageBackendWrapper",
    "PLRTempSettableMixin",
    "PLRTempGettableMixin",
    "PLRHeatingShakerBackendWrapper",
]

from pylabrobot.resources import Coordinate
from pylabrobot.resources.resource import Resource as PLRResource


class VendorSurfaceForwarding:
    """Forward a vendor command to the object the driver declared it on.

    The vendor object's own surface lives on an attribute, not on the driver; a
    driver that advertises it in ``vendor_surfaces`` without this would
    advertise commands nobody can dispatch. Python only routes to
    ``__getattr__`` for names normal lookup missed, so an interface method
    always wins over the vendor method it wraps, and underscore names are never
    forwarded (which also stops ``_backend`` recursing during construction).

    A prefixed name says which surface it came from: ``gripper.ungrip`` resolves
    against the surface declared with prefix ``gripper``.
    """

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        prefix, _, member = name.rpartition(".")
        for surface in vendor_surfaces(type(self)):
            if surface.prefix != prefix:
                continue
            vendor = resolve_vendor_object(self, surface)
            try:
                return getattr(vendor, member)
            except AttributeError as exc:
                raise AttributeError(
                    f"{type(self).__name__} has no {name!r}, and neither does "
                    f"its {type(vendor).__name__} at {surface.path!r}."
                ) from exc
        where = (
            f"named {prefix!r}" if prefix else "to forward an unprefixed name to"
        )
        raise AttributeError(
            f"{type(self).__name__} has no {name!r} and declares no vendor "
            f"surface {where}."
        )


class PLRSealerBackendWrapper(VendorSurfaceForwarding, ISealerDriver):
    def __init__(self, backend: PLRSealerBackend):
        self._backend = backend
        self._is_initialized = False

    async def initialize(self) -> None:
        await self._backend.setup()
        self._is_initialized = True

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def open(self) -> None:
        await self._backend.open()

    async def close(self) -> None:
        await self._backend.close()

    async def seal(self, request: SealRequest) -> None:
        await self._backend.seal(temperature=request.temperature, duration=request.duration)

    async def set_temperature(self, temperature: float) -> None:
        await self._backend.set_temperature(temperature)

    async def get_temperature(self) -> float:
        return await self._backend.get_temperature()


class PLRShakerBackendWrapper(VendorSurfaceForwarding, IShakerDriver):
    def __init__(self, backend: PLRShakerBackend):
        self._backend = backend
        self._is_initialized = False

    async def initialize(self) -> None:
        await self._backend.setup()
        self._is_initialized = True

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def stop(self) -> None:
        await self._backend.stop()

    def serialize(self) -> dict:
        return {"type": self.__class__.__name__}

    @property
    def supports_locking(self) -> bool:
        """Check if the shaker supports locking the plate"""
        return self._backend.supports_locking

    async def unlock_plate(self, request: UnlockPlateRequest) -> None:
        await self._backend.unlock_plate()

    async def lock_plate(self, request: LockPlateRequest) -> None:
        await self._backend.lock_plate()

    async def shake(self, request: ShakeRequest) -> None:
        await self._backend.start_shaking(request.speed)
        try:
            await asyncio.sleep(request.duration)
        finally:
            # An abort cancels the wait, and a shaker nobody stopped keeps spinning
            # with a plate on it. Stopping is the one thing that must still happen.
            await self.stop_shaking(StopShakingRequest())

    async def stop_shaking(self, request: StopShakingRequest) -> None:
        await self._backend.stop_shaking()

    async def open(self) -> None:
        await self.unlock_plate(UnlockPlateRequest())

    async def close(self) -> None:
        await self.lock_plate(LockPlateRequest())

class PLRCentrifugeBackendWrapper(VendorSurfaceForwarding, ICentrifugeDriver):

    def __init__(self, backend: PLRCentrifugeBackend):
        self._backend = backend
        self._is_initialized = False
        self._acceleration: float = 7.0

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def initialize(self) -> None:
        await self._backend.setup()
        self._is_initialized = True

    async def stop(self) -> None:
        await self._backend.stop()

    async def set_acceleration(self, acceleration: float) -> None:
        self._acceleration = acceleration

    async def centrifuge(self, request: CentrifugeRequest) -> None:
        await self._backend.spin(request.g, request.duration, self._acceleration)

    async def open(self) -> None:
        await self._backend.open_door()

    async def close(self) -> None:
        await self._backend.close_door()


def _to_plr_protocol(protocol: ThermocyclerProtocol) -> PLRProtocol:
    """Convert the Pydantic `Protocol` into PLR's dataclass `Protocol`.

    Field names match 1:1, so each level maps directly onto the PLR
    dataclass constructor.
    """
    return PLRProtocol(
        stages=[
            PLRStage(
                steps=[
                    PLRStep(
                        temperature=list(step.temperature),
                        hold_seconds=step.hold_seconds,
                        rate=step.rate,
                    )
                    for step in stage.steps
                ],
                repeats=stage.repeats,
            )
            for stage in protocol.stages
        ]
    )


class PLRThermocyclerBackendWrapper(VendorSurfaceForwarding, IThermocyclerDriver):
    """Wrap a PLR ThermocyclerBackend behind cheshire-drivers' IThermocyclerDriver.

    The four temperature getters and two status getters return typed Response
    models (the wire wrap rejects raw lists / enums); the scalar getters return
    the raw value and the wire wrap projects it onto its single-field Response.
    """

    def __init__(self, backend: PLRThermocyclerBackend):
        self._backend = backend
        self._is_initialized = False

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def initialize(self) -> None:
        await self._backend.setup()
        self._is_initialized = True

    async def open(self) -> None:
        await self._backend.open_lid()

    async def close(self) -> None:
        await self._backend.close_lid()

    async def open_lid(self, request: OpenLidRequest) -> None:
        await self._backend.open_lid()

    async def close_lid(self, request: CloseLidRequest) -> None:
        await self._backend.close_lid()

    async def set_block_temperature(self, request: SetBlockTemperatureRequest) -> None:
        await self._backend.set_block_temperature(request.temperature)

    async def set_lid_temperature(self, request: SetLidTemperatureRequest) -> None:
        await self._backend.set_lid_temperature(request.temperature)

    async def deactivate_block(self, request: DeactivateBlockRequest) -> None:
        await self._backend.deactivate_block()

    async def deactivate_lid(self, request: DeactivateLidRequest) -> None:
        await self._backend.deactivate_lid()

    async def run_protocol(self, request: ThermocyclerRunProtocolRequest) -> None:
        await self._backend.run_protocol(
            _to_plr_protocol(request.protocol), request.block_max_volume,
        )

    async def get_block_current_temperature(
        self, request: GetBlockCurrentTemperatureRequest,
    ) -> TemperatureListResponse:
        return TemperatureListResponse(
            temperatures=await self._backend.get_block_current_temperature(),
        )

    async def get_block_target_temperature(
        self, request: GetBlockTargetTemperatureRequest,
    ) -> TemperatureListResponse:
        return TemperatureListResponse(
            temperatures=await self._backend.get_block_target_temperature(),
        )

    async def get_lid_current_temperature(
        self, request: GetLidCurrentTemperatureRequest,
    ) -> TemperatureListResponse:
        return TemperatureListResponse(
            temperatures=await self._backend.get_lid_current_temperature(),
        )

    async def get_lid_target_temperature(
        self, request: GetLidTargetTemperatureRequest,
    ) -> TemperatureListResponse:
        return TemperatureListResponse(
            temperatures=await self._backend.get_lid_target_temperature(),
        )

    async def get_lid_open(self, request: GetLidOpenRequest) -> LidOpenResponse:
        return LidOpenResponse(open=await self._backend.get_lid_open())

    async def get_lid_status(self, request: GetLidStatusRequest) -> ThermocyclerStatusResponse:
        status = await self._backend.get_lid_status()
        return ThermocyclerStatusResponse(status=status.value)

    async def get_block_status(self, request: GetBlockStatusRequest) -> ThermocyclerStatusResponse:
        status = await self._backend.get_block_status()
        return ThermocyclerStatusResponse(status=status.value)

    async def get_hold_time(self, request: GetHoldTimeRequest) -> HoldTimeResponse:
        return HoldTimeResponse(seconds=await self._backend.get_hold_time())

    async def get_current_cycle_index(
        self, request: GetCurrentCycleIndexRequest,
    ) -> CycleIndexResponse:
        return CycleIndexResponse(index=await self._backend.get_current_cycle_index())

    async def get_total_cycle_count(
        self, request: GetTotalCycleCountRequest,
    ) -> CycleCountResponse:
        return CycleCountResponse(count=await self._backend.get_total_cycle_count())

    async def get_current_step_index(
        self, request: GetCurrentStepIndexRequest,
    ) -> StepIndexResponse:
        return StepIndexResponse(index=await self._backend.get_current_step_index())

    async def get_total_step_count(
        self, request: GetTotalStepCountRequest,
    ) -> StepCountResponse:
        return StepCountResponse(count=await self._backend.get_total_step_count())


def _unwrap_well(well: IWell) -> PLRWell:
    """Extract the underlying PLR Well from an IWell adapter."""
    from cheshire_drivers.plr.labware import PLRWellAdapter
    if isinstance(well, PLRWellAdapter):
        return well._well
    raise TypeError(
        f"Expected PLRWellAdapter, got {type(well).__name__}. "
        "Only PLR-backed labware is supported for atomic liquid handling."
    )


def _unwrap_tip_spot(spot: ITipSpot) -> PLRTipSpot:
    """Extract the underlying PLR TipSpot from an ITipSpot adapter."""
    from cheshire_drivers.plr.labware import PLRTipSpotAdapter
    if isinstance(spot, PLRTipSpotAdapter):
        return spot._spot
    raise TypeError(
        f"Expected PLRTipSpotAdapter, got {type(spot).__name__}. "
        "Only PLR-backed labware is supported for atomic liquid handling."
    )


def _unwrap_plate(plate: IPlate) -> PLRPlate:
    """Extract the underlying PLR Plate from an IPlate adapter."""
    from cheshire_drivers.plr.labware import PLRPlateAdapter
    if isinstance(plate, PLRPlateAdapter):
        return plate._plate
    raise TypeError(
        f"Expected PLRPlateAdapter, got {type(plate).__name__}. "
        "Only PLR-backed labware is supported for plate moves."
    )


def _unwrap_tip_rack(tip_rack: ITipRack) -> PLRTipRack:
    """Extract the underlying PLR TipRack from an ITipRack adapter."""
    from cheshire_drivers.plr.labware import PLRTipRackAdapter
    if isinstance(tip_rack, PLRTipRackAdapter):
        return tip_rack._rack
    raise TypeError(
        f"Expected PLRTipRackAdapter, got {type(tip_rack).__name__}. "
        "Only PLR-backed labware is supported for bulk liquid handling."
    )


@runtime_checkable
class _SlotDeck(Protocol):
    """An Opentrons-style deck whose built-in deck-sites are holders addressed by slot key.

    The Flex keys its slots by name (a name-``Mapping``, "A1".."D3"); the OT-2 by 1-based position
    (a ``Sequence``, 1..12). ``assign_child_at_slot`` therefore takes ``str | int`` -- the union of
    what the two decks accept -- and callers pick the right key off the runtime ``slots`` shape. A
    Hamilton rail deck exposes neither member, so it does not match.
    """

    @property
    def slots(self) -> Mapping[str, Optional[PLRResource]] | Sequence[Optional[PLRResource]]: ...

    def assign_child_at_slot(self, resource: PLRResource, slot: str | int) -> None: ...


def _slot_deck_names(deck: PLRDeck) -> list[str] | None:
    """Addressable slot names of an Opentrons slot deck, or ``None`` for a Hamilton rail deck.

    The runtime ``slots`` collection type selects the key form -- a name-keyed Mapping (Flex) or a
    positional Sequence (OT-2) -- and names surface as strings either way. The slot permanently
    holding the trash bin is not addressable and is dropped. The Flex staging pads (column 4) are
    deck sites like the rest: the arm hands off there and the gripper relays inward, and what sits
    on them is reported like any other occupant.
    """
    if not isinstance(deck, _SlotDeck):
        return None
    slots = deck.slots
    if isinstance(slots, Mapping):
        return [str(name) for name, occ in slots.items() if not isinstance(occ, PLRTrash)]
    return [str(i + 1) for i, occ in enumerate(slots) if not isinstance(occ, PLRTrash)]


def _place_at_named_slot(deck: PLRDeck, resource: PLRResource, slot_name: str) -> bool:
    """Place ``resource`` at a named deck slot if ``deck`` is an Opentrons slot deck; return whether
    it did. Flex takes the slot name as-is; OT-2 takes its 1-based integer. A Hamilton rail deck has
    no slots, so this returns ``False`` and the caller falls back to carrier-site placement.
    """
    if not isinstance(deck, _SlotDeck):
        return False
    if isinstance(deck.slots, Mapping):
        deck.assign_child_at_slot(resource, slot_name)
    elif slot_name.isdigit():
        deck.assign_child_at_slot(resource, int(slot_name))
    else:
        raise ValueError(
            f"OT-2 slot must be a 1-based integer name (e.g. '7'), got {slot_name!r}"
        )
    return True


@runtime_checkable
class _GripperSlotDeck(Protocol):
    """A slot deck whose gripper can target a slot holder directly -- the Flex. The OT-2 is a slot
    deck too but has no gripper, so it does not expose this and a slot move on it is refused."""

    def get_slot_holder(self, slot: str) -> PLRResourceHolder: ...


def empty_the_spots_a_head_carries(
    resource: PLRResource, carried: Dict[str, set[str]]
) -> None:
    """Clear the spots whose tips a head still holds, on a freshly built rack.

    A rebuilt rack starts from the declared layout, which counts every tip as
    racked. Without this the tips a head carries exist twice, and returning them
    to their own spots is refused as already occupied.
    """
    if not isinstance(resource, PLRTipRack):
        return
    spots = carried.get(resource.name)
    if spots:
        resource.set_tip_state({spot: False for spot in spots})


def occupies_a_deck_position(resource: PLRResource) -> bool:
    """Whether a resource holds a deck position of its own: a carrier, or labware on a site.

    A deck registers its descendants recursively, so it also hands back every
    well and tip spot inside the labware it holds, and the holders themselves.
    Those describe no deck position, so they are not deck occupants.
    """
    if isinstance(resource, PLRResourceHolder):
        return False
    parent = resource.parent
    return isinstance(parent, (PLRDeck, PLRResourceHolder))


async def start_deck_visualizer(deck: PLRDeck) -> Optional[Visualizer]:
    """Open the PLR Visualizer on a deck, or None when it cannot start.

    The viewer is a developer convenience, so a run continues without it rather
    than failing on a taken port or a box with no browser.
    """
    visualizer = Visualizer(deck)
    try:
        await visualizer.setup()
    except Exception:
        logger.warning("PLR Visualizer could not start; continuing without it", exc_info=True)
        return None
    return visualizer


def _site_label(deck: PLRDeck, resource: PLRResource) -> Optional[str]:
    """The deck site a resource sits on, spelled the way ``add_deck_labware.at`` accepts.

    The inverse of placement: an Opentrons slot reads back ``'<slot>-slot'``, a Hamilton
    carrier site ``'<carrier>-<index>'``. ``None`` for anything not on an addressable site
    (a carrier itself sits on a rail, not a site).
    """
    if isinstance(deck, _SlotDeck):
        slots = deck.slots
        # Flex keys slots by name, OT-2 by 1-based position; both map to the occupant.
        pairs = slots.items() if isinstance(slots, Mapping) else enumerate(slots, start=1)
        for key, occupant in pairs:
            if occupant is resource:
                return f"{key}-slot"
    holder = resource.parent
    if holder is None or holder.parent is None:
        return None
    carrier = holder.parent
    if isinstance(carrier, PLRCarrier):
        for index, site in carrier.sites.items():
            if site.resource is resource:
                return f"{carrier.name}-{index}"
    return None


def slot_key_from_site(site: str) -> Optional[str]:
    """The slot key of an Opentrons deck-site label, or ``None`` for a Hamilton carrier site.

    The runtime labels an Opentrons slot ``"<slot>-slot"`` (Flex "C2-slot", OT-2 "7-slot") and a
    Hamilton site ``"<carrier>-<index>"`` (index always numeric), so the ``-slot`` suffix cleanly
    tells the two apart.
    """
    prefix, _, suffix = site.rpartition("-")
    return prefix if suffix == "slot" and prefix else None


def _slot_holder(deck: PLRDeck, slot_key: str) -> PLRResourceHolder:
    """The slot's ``ResourceHolder`` as a gripper-move destination. Only the Flex has a gripper; an
    OT-2 slot move has no physical mechanism, so it is refused rather than silently mishandled."""
    if isinstance(deck, _GripperSlotDeck):
        return deck.get_slot_holder(slot_key)
    raise ValueError(
        f"Cannot move a plate onto slot {slot_key!r}: this deck has no gripper. "
        f"Gripper moves between deck slots are a Flex capability."
    )


# Labware kinds the runtime materializes on the deck: plates, tip racks, troughs (a Container).
# One source for add/remove/clear. Note Trash is also a Container, so a clear must exclude it.
MOVABLE_LABWARE = (PLRPlate, PLRTipRack, PLRContainer)


def _deck_factories() -> Dict[str, Callable[[], Any]]:
    """Deck-type name -> zero-arg PLR deck factory.

    Rail decks (Hamilton) and slot decks (Opentrons OT-2 + Flex) both register here; a slot deck is
    one whose built-in deck-sites are holders addressed by ``assign_child_at_slot`` (see
    ``slot_names_for_deck_type``) rather than carrier rails.
    """
    from pylabrobot.resources.hamilton.hamilton_decks import STARDeck, STARLetDeck
    from pylabrobot.resources.opentrons import FlexDeck, OTDeck

    return {
        "STARlet": STARLetDeck,
        "STARLet": STARLetDeck,
        "STAR": STARDeck,
        "OTDeck": OTDeck,
        "FlexDeck": FlexDeck,
    }


# Vendor submodules that hold plate / tip rack / trough definitions, searched after
# the top-level `pylabrobot.resources` namespace.
_CATALOG_SUBMODULES = (
    "pylabrobot.resources.corning.falcon.plates",
    "pylabrobot.resources.corning.plates",
    "pylabrobot.resources.hamilton.plates",
    "pylabrobot.resources.hamilton.tip_carriers",
    "pylabrobot.resources.hamilton.plate_carriers",
    "pylabrobot.resources.hamilton.trough_carriers",
    "pylabrobot.resources.hamilton.tube_carriers",
    "pylabrobot.resources.hamilton.mfx_carriers",
    # Labware PLR does not carry at all, defined here instead.
    "cheshire_drivers.plr.opentrons_troughs",
)


def create_catalog_resource(catalog_ref: str, name: str) -> PLRResource:
    """Build the PLR labware a catalog reference names."""
    import importlib

    import pylabrobot.resources as plr_resources

    factory = None
    if hasattr(plr_resources, catalog_ref) and callable(getattr(plr_resources, catalog_ref)):
        factory = getattr(plr_resources, catalog_ref)
    else:
        for module_path in _CATALOG_SUBMODULES:
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                continue
            if hasattr(module, catalog_ref) and callable(getattr(module, catalog_ref)):
                factory = getattr(module, catalog_ref)
                break
    if factory is None:
        raise ValueError(
            f"Unknown catalog reference: '{catalog_ref}'. "
            f"Not found in pylabrobot.resources or common submodules."
        )

    resource = factory(name=name)
    if not isinstance(resource, PLRResource):
        raise TypeError(
            f"Catalog reference '{catalog_ref}' did not return a Resource. "
            f"Got {type(resource).__name__}."
        )
    return resource


def slot_names_for_deck_type(deck_type: str) -> list[str] | None:
    """Placeable slot names of a slot-based deck (Opentrons Flex "A1".."D3" or OT-2 "1".."11"), or
    ``None`` for a rail deck (Hamilton) or an unknown type. The trash slot is excluded.

    Single-sourced from the PLR deck the factory builds, so orca-core's deck-site enumeration and
    this driver's placement agree on the slot names without a hand-copied list.
    """
    factory = _deck_factories().get(deck_type)
    if factory is None:
        return None
    return _slot_deck_names(factory())


@runtime_checkable
class SimServerLifecycle(Protocol):
    """A device-owned vendor simulator (e.g. an Opentrons robot-server) whose lifetime
    follows the driver's: started before the backend connects, stopped on close so it
    never outlives the device."""

    async def start(self) -> None: ...

    def stop(self) -> None: ...


def _per_channel_flow_rates(
    stated: list[float] | None, parameters: PipettingParameters, channels: int
) -> list[float | None] | None:
    """The rate each channel runs at: what the caller named per channel, else the
    resolved one for all of them, else the backend's own."""
    if stated is not None:
        per_channel: list[float | None] = list(stated)
        return per_channel
    if parameters.flow_rate is None:
        return None
    rate: list[float | None] = [parameters.flow_rate] * channels
    return rate


def _per_channel_blow_out(
    parameters: PipettingParameters, channels: int
) -> list[float | None] | None:
    """The air each channel takes in so it has something to blow out.

    PyLabRobot expels air the tip already holds, so the aspirate and the
    dispense have to name the same volume: naming it only at dispense makes a
    volume-tracked tip expel air it never took in, and an untracked one expel
    nothing.

    A blow-out here IS an air volume, so asking for one without naming a volume
    asks for nothing at all, and that is refused rather than performed as
    silence. `blow_out_flow_rate` is different: this backend expels at the
    dispense rate and cannot be given its own. That is a field this driver does
    not read, the same way a mix does not read the blow-out fields, and one
    record is meant to travel across drivers that differ.
    """
    if not parameters.blow_out:
        return None
    if parameters.blow_out_volume is None:
        raise ValueError(
            "blow_out: this liquid handler blows out an air volume the tip took in, so it "
            "needs blow_out_volume. Set it, or leave blow_out off."
        )
    air: list[float | None] = [parameters.blow_out_volume] * channels
    return air


def _per_channel_offsets(
    stated: list[float] | None, parameters: PipettingParameters, channels: int
) -> list[Coordinate] | None:
    """The tip height each channel pipettes at, in the same order."""
    if stated is not None:
        return [Coordinate(0, 0, z) for z in stated]
    if parameters.height is None:
        return None
    return [Coordinate(0, 0, parameters.height)] * channels


def _per_channel_mix(parameters: PipettingParameters, channels: int) -> list[PLRMix] | None:
    """The mix every channel runs, if the step calls for one."""
    if parameters.mix is None:
        return None
    mix = PLRMix(
        volume=parameters.mix.volume,
        repetitions=parameters.mix.repetitions,
        flow_rate=parameters.mix.flow_rate,
    )
    return [mix] * channels


class PLRLiquidHandlerWrapper(
    VendorSurfaceForwarding, InterruptedMoveTracking, ILiquidHandlerDriver,
):
    """Unified PLR liquid handler driver. Implements ILiquidHandlerDriver
    (string-based, serializable). Same interface for local and remote.

    Lifecycle:
        1. Construct with backend
        2. configure_deck() to set up PLR deck layout
        3. initialize() to start the hardware/sim
        4. aspirate/dispense/pick_up_tips/etc. using labware names

    PLR-driven liquid handlers track labware/tip state per call and report it
    back via LabwareStateResponse, so provides_state=True.

    A pipetting height (`technique.height`, or a liquid class z offset) becomes a
    PLR `offsets` z here: a translation from wherever the backend already puts the
    tip. The Flex driver sends the same number as an absolute height above the
    cavity floor, so the two agree on which knob a caller turns and differ on what
    it is measured from. Reconciling them belongs with the layered pipetting
    record, not with a blind change to a handler nothing here can drive.
    """

    provides_state: ClassVar[bool] = True

    def __init__(
        self,
        backend: PLRLiquidHandlerBackend,
        visualize: bool = False,
        sim_server: SimServerLifecycle | None = None,
    ) -> None:
        self._backend = backend
        self._sim_server = sim_server
        self._lh: PLRLiquidHandler | None = None
        self._visualize = visualize
        self._visualizer: Visualizer | None = None

    async def configure_deck(self, config: DeckLayoutConfig) -> LabwareStateResponse:

        factories = _deck_factories()
        factory = factories.get(config.deck_type)
        if factory is None:
            raise ValueError(
                f"Unknown deck type '{config.deck_type}'. "
                f"Available: {list(factories.keys())}"
            )
        deck = factory()
        self._resolve_interrupted_move()
        self._lh = PLRLiquidHandler(self._backend, deck=deck)

        for res_config in config.resources:
            resource = self._create_resource(res_config)
            self._place_resource(resource, res_config)

        # configure_deck is the first call that contacts the device, so the
        # device-owned sim server must be up before it (not at initialize()).
        if self._sim_server is not None:
            await self._sim_server.start()
        assert self._lh is not None
        await self._lh.setup()

        if self._visualize:
            self._visualizer = await start_deck_visualizer(deck)

        # PyLabRobot ships volume and tip tracking gated behind module-global
        # toggles that default OFF. Without enabling them, every aspirate /
        # dispense / pick_up_tips / drop_tips short-circuits past
        # well.tracker / tip_spot.tracker entirely and operations report
        # zero state movement on the wire. Enable both so the rest of the
        # wrapper (`_get_labware_state`, the per-op LabwareStateResponse)
        # has real per-well volumes and per-rack tip inventory to surface.
        set_volume_tracking(True)
        set_tip_tracking(True)

        self._install_lenient_head_trackers()

        for resource in self._lh.deck.get_all_resources():
            swap_to_lenient(resource)

        if config.labware_state is not None:
            self._apply_labware_state(config.labware_state)

        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    def _install_lenient_head_trackers(self) -> None:
        """Give every channel a tracker that records rather than refuses.

        Called wherever the head can come into existence: configure_deck builds
        it, and setup() REBUILDS it, so installing in only one of the two leaves
        a reconnect carrying stock trackers and refusing again.
        """
        assert self._lh is not None
        self._swap_head_presence_trackers_to_lenient(self._lh.head)
        if self._lh.head96 is not None:
            self._swap_head_presence_trackers_to_lenient(self._lh.head96)

    @staticmethod
    def _swap_head_presence_trackers_to_lenient(head: Dict[int, PLRTipTracker]) -> None:
        """Replace each channel's tip tracker with one that records rather than
        refuses, keeping whatever tip it currently believes it holds.

        A change of enforcement, not of state: the channel keeps its tip, its
        origin and its callback. What goes away is HasTipError on a pick onto a
        channel we told the driver was loaded, and NoTipError on a drop from one
        we told it was bare -- our own guess, enforced back at us, stopping the
        run at the instrument rather than at a surface an operator can reach.

        ``head`` is ``lh.head`` (8-channel) or ``lh.head96``.
        """
        for channel, old in head.items():
            replacement = LenientTipTracker(thing=old.thing)
            if old.has_tip:
                replacement.add_tip(old.get_tip(), origin=old.get_tip_origin())
            if old._callback is not None:
                replacement.register_callback(old._callback)
            head[channel] = replacement

    @staticmethod
    def _swap_mounted_tip_trackers_to_lenient(
        head: Dict[int, Any], channels: list[int] | None,
    ) -> None:
        """After pickup, replace each mounted tip's volume tracker with a
        LenientVolumeTracker so aspirate-into-tip beyond ``maximal_volume``
        accumulates a positive delta instead of raising ``TooLittleVolumeError``.

        Same audit-not-enforce rationale as the well swap: real Hamiltons
        enforce tip capacity; sim deployments running unconstrained
        workflows treat tip overflow as a delta to track, not a failure
        to abort. PLR's ``LiquidHandler.aspirate`` calls
        ``op.tip.tracker.add_liquid(op.volume)`` unconditionally when
        ``does_volume_tracking()`` is True (liquid_handler.py:950); without
        this swap the stock tracker raises on the first
        aspirate > tip.maximal_volume.

        ``head`` is a ``Dict[channel_id, TipTracker]`` -- either ``lh.head``
        (8-channel) or ``lh.head96`` (96-channel head). ``channels=None``
        means "all channels whose head has a tip" (matches PLR's
        ``use_channels`` semantics on pickup). The Tip dataclass is plain
        (no ``_state_updated`` callback), so the swap is a single attribute
        replacement with no callback re-registration.
        """
        target_channels = list(head.keys()) if channels is None else channels
        for ch in target_channels:
            head_tracker = head[ch]
            if not head_tracker.has_tip:
                continue
            tip = head_tracker.get_tip()
            new_vt = LenientVolumeTracker(
                thing=tip.tracker.thing, max_volume=tip.tracker.max_volume,
            )
            tip.tracker = new_vt

    async def initialize(self) -> None:
        """Bring the handler up through PyLabRobot's compound `setup()`. MOVES HARDWARE.

        The backends behind this wrapper offer no separable bring-up, so `setup()`
        is the only way in and it ends in whatever motion the machine needs to
        reach a known state: a STAR raises every channel to z-safety and
        initializes its autoload and iSWAP. A simulated backend runs the same
        sequence and moves nothing. A driver whose vendor library does offer the
        pieces composes them itself instead, and does not move here.
        """
        if self._lh is None:
            raise RuntimeError("configure_deck() must be called before initialize()")
        if self._lh.setup_finished:
            return
        await self._lh.setup()
        # setup() builds fresh stock head trackers, discarding the lenient ones
        # configure_deck installed. Without this a reconnect re-arms the refusals.
        self._install_lenient_head_trackers()

    async def disconnect(self) -> None:
        """Hand the handler back and take it down, so a later initialize() brings it up again.

        The one route back from a backend that has latched a refusal, since
        PyLabRobot refuses a second `setup()` on a handler already up. Safe to
        route through the backend's `stop` here, unlike the interface default:
        every backend this wrapper carries ends its session without moving.

        Retryable, because on a dead link it is likely to be cancelled: the
        engine gives `disconnect` its declared 15s while the wire budget lets
        `stop` wait far longer. A cancel leaves PyLabRobot's own flag set, and
        reading that flag rather than a copy is what makes the retry work.
        """
        if self._lh is None or not self._lh.setup_finished:
            return
        await self._lh.stop()

    @property
    def is_initialized(self) -> bool:
        """Read off PyLabRobot rather than mirrored here.

        A copy of a flag PyLabRobot already owns cannot survive a cancelled
        `stop()`: it clears its own flag after the await, so a copy cleared in a
        `finally` says "down" while PyLabRobot says "up", and the handler is then
        wedged both ways.
        """
        return self._lh is not None and self._lh.setup_finished

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        # `close` is a per-operation device command (fired around deck access), not
        # driver disposal, so it must not stop the sim server. Disposal is `_shutdown`.
        pass

    async def _shutdown(self) -> None:
        # Disposal (not the per-op `close`): stop the session-lived sim server.
        if self._sim_server is not None:
            self._sim_server.stop()

    async def _require_honorable_volumes(
        self, volumes: list[float], use_channels: list[int] | None
    ) -> None:
        """Refuse per-channel volumes the mounted head cannot deliver, before anything moves.

        Omitting ``use_channels`` addresses the leading channels, which is what PyLabRobot itself
        does with the same argument.
        """
        channels = use_channels if use_channels is not None else list(range(len(volumes)))
        if len(channels) != len(volumes):
            raise ValueError(
                f"use_channels names {len(channels)} channels but {len(volumes)} volumes were "
                "given; each volume must name the channel that delivers it."
            )
        if len(set(channels)) != len(channels):
            raise ValueError(f"use_channels repeats a channel: {channels}.")
        config = await self.get_head_configuration(GetHeadConfigurationRequest())
        config.require_volumes_honorable(dict(zip(channels, volumes)))

    async def get_head_configuration(
        self, request: GetHeadConfigurationRequest
    ) -> HeadConfigurationResponse:
        """Report which mounted nozzles share a plunger, asking the backend that knows.

        Every backend on this wrapper counts one plunger per channel, so the generic answer is
        one group per channel. The OT-2 backend numbers its channels by MOUNT (a multi-nozzle
        pipette is still one channel to it), so its groups also carry the pipette on that mount
        and the volume it can hold. Dispatching on the backend rather than the driver subclass
        keeps the answer tied to the hardware, so wrapping a backend in the plain wrapper cannot
        change what it reports.

        The Flex reports genuinely ganged groups, but it is not driven through this wrapper: its
        adapter is ``FlexLiquidHandlerDriver``, over ``pylabrobot.opentrons``.
        """
        backend = self._backend
        if isinstance(backend, PLROpentronsOT2Backend):
            mounted = [p for p in (backend.left_pipette, backend.right_pipette) if p is not None]
            return HeadConfigurationResponse(
                groups=[
                    NozzleGroup(
                        channels=[channel],
                        max_volume_ul=backend.pipette_name2volume.get(pipette["name"]),
                        pipette_model=pipette["name"],
                    )
                    for channel, pipette in enumerate(mounted)
                ]
            )
        return HeadConfigurationResponse(
            groups=[NozzleGroup(channels=[c]) for c in range(backend.num_channels)]
        )

    async def get_deck_state(self, request: GetDeckStateRequest) -> DeckStateResponse:
        if self._lh is None or not self._lh.setup_finished:
            # No head is composed yet, so there are no channels to report on.
            return DeckStateResponse(tips_mounted=[])

        from pylabrobot.resources import TipRack as PLRTipRackType

        # head/head96 are Dict[int, TipTracker] sized to the pipettes actually mounted, so a STAR
        # reports 8 and a Flex carrying a 1-channel and an 8-channel reports 9.
        head, head96 = self._lh.head, self._lh.head96
        # A 96 pipette fills head with head96's own nozzles; report those once, as the 96-head.
        head_is_the_96_head = bool(head96) and len(head96) == len(head)
        tips_mounted = [] if head_is_the_96_head else [head[i].has_tip for i in sorted(head)]
        tips_mounted_96 = any(t.has_tip for t in head96.values())

        labware: List[DeckResourceState] = []
        tip_racks: List[TipRackState] = []
        for resource in self._lh.deck.get_all_resources():
            if not occupies_a_deck_position(resource):
                continue
            labware.append(DeckResourceState(
                name=resource.name,
                type=resource.__class__.__name__,
                category=resource.category,
                site=_site_label(self._lh.deck, resource),
            ))
            if isinstance(resource, PLRTipRackType):
                tips_remaining = sum(1 for ts in resource.get_all_items() if ts.has_tip())
                tip_racks.append(TipRackState(
                    name=resource.name,
                    tips_remaining=tips_remaining,
                    total_tips=resource.num_items,
                ))

        return DeckStateResponse(
            tips_mounted=tips_mounted,
            tips_mounted_96=tips_mounted_96,
            labware=labware,
            tip_racks=tip_racks,
            interrupted_move=self._interrupted_move,
        )

    async def _clear_occupancy(self) -> None:
        """Unassign movable labware from every deck holder, leaving deck structure intact.

        Walks all ``ResourceHolder``s: Hamilton carrier sites and Opentrons slots are both holders,
        so one loop clears either deck shape. Clears only materialized labware, so the trash bin (a
        Container, hence the explicit exclusion) and any non-labware fixture on a holder stay put."""
        assert self._lh is not None
        # Wiping the deck answers where an interrupted move's labware ended up.
        self._resolve_interrupted_move()
        for holder in self._lh.deck.get_all_resources():
            if not isinstance(holder, PLRResourceHolder):
                continue
            occupant = holder.resource
            if isinstance(occupant, MOVABLE_LABWARE) and not isinstance(occupant, PLRTrash):
                await self._free_on_server(occupant)
                occupant.unassign()

    async def _free_on_server(self, resource: PLRResource) -> None:
        # Device-mirroring backends (the Opentrons robot-server) must be told a labware left or
        # its slot stays occupied server-side; sim/print backends have no such state (no-op).
        move_off = getattr(self._backend, "move_labware_off_deck", None)
        if move_off is not None:
            await move_off(resource)

    async def reset_deck_labware(self, request: ResetDeckLabwareRequest) -> LabwareStateResponse:
        if self._lh is None:
            return LabwareStateResponse(success=True)
        await self._clear_occupancy()
        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    async def reconcile_deck_occupancy(
        self, request: ReconcileDeckOccupancyRequest,
    ) -> LabwareStateResponse:
        if self._lh is None:
            return LabwareStateResponse(success=True)
        # Pre-resolve before wiping: an unknown catalog_ref fails with the deck
        # intact. Placement after the wipe is the residual tear risk (re-issue to fix).
        created = [
            (self._create_resource(rc), rc) for rc in request.resources
        ]
        await self._clear_occupancy()
        carried = self._spots_a_head_carries()
        for resource, res_config in created:
            self._place_resource(resource, res_config)
            swap_to_lenient(resource)
            if res_config.well_state is not None:
                self._apply_labware_state({res_config.name: res_config.well_state})
            empty_the_spots_a_head_carries(resource, carried)
        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    async def reconcile_hardware_state(
        self, request: ReconcileHardwareStateRequest
    ) -> ReconcileHardwareStateResponse:
        return ReconcileHardwareStateResponse(
            checked=False,
            message=f"{type(self).__name__} has no hardware ground-truth read; "
            "nothing was verified.",
        )

    async def discard_stranded_tips(
        self, request: DiscardStrandedTipsRequest
    ) -> ReconcileHardwareStateResponse:
        raise RuntimeError(
            f"{type(self).__name__} has no tip-presence sensor, so it cannot find a "
            "stranded tip to discard. Clear the tip at the instrument."
        )

    def _spots_a_head_carries(self) -> Dict[str, set[str]]:
        """Rack name -> spot ids whose tips a head is holding, read off the head trackers."""
        assert self._lh is not None
        carried: Dict[str, set[str]] = {}
        trackers = list(self._lh.head.values()) + list(self._lh.head96.values())
        for tracker in trackers:
            if not tracker.has_tip:
                continue
            origin = tracker.get_tip_origin()
            if origin is None or origin.parent is None:
                continue
            carried.setdefault(origin.parent.name, set()).add(origin.get_identifier())
        return carried

    # --- 8-channel operations ---

    def _get_plate(self, name: str) -> PLRPlate:
        assert self._lh is not None
        resource = self._lh.deck.get_resource(name)
        if not isinstance(resource, PLRPlate):
            raise TypeError(f"Resource '{name}' is not a Plate, got {type(resource).__name__}")
        return resource

    def _get_tip_rack(self, name: str) -> PLRTipRack:
        assert self._lh is not None
        resource = self._lh.deck.get_resource(name)
        if not isinstance(resource, PLRTipRack):
            raise TypeError(f"Resource '{name}' is not a TipRack, got {type(resource).__name__}")
        return resource

    def _resolve_op_containers(
        self, labware: str, positions: list[str] | None, n_channels: int,
    ) -> list[PLRContainer]:
        """Resolve one liquid-op slice to the PLR containers, one per channel.

        ``positions is None`` means a single-pool container (trough/tube): the
        one container is handed to every channel and PLR spreads them. A list of
        positions means an itemized labware (plate): each position resolves to a
        well. The two directions are guarded against the wrong resource type."""
        assert self._lh is not None
        resource = self._lh.deck.get_resource(labware)
        if positions is None:
            if not isinstance(resource, PLRContainer):
                raise TypeError(
                    f"Resource '{labware}' is itemized ({type(resource).__name__}); "
                    f"pass one position per channel, not a whole-container op"
                )
            return [resource] * n_channels
        if not isinstance(resource, PLRItemizedResource):
            raise TypeError(
                f"Resource '{labware}' is a single container ({type(resource).__name__}); "
                f"omit positions and pass one volume per channel"
            )
        return [resource.get_item(p) for p in positions]

    def _get_head96_resource(self, name: str) -> PLRContainer | PLRPlate:
        """Resolve a 96-head target: a Plate or a single Container (trough). PLR's
        ``aspirate96``/``dispense96`` accept either."""
        assert self._lh is not None
        resource = self._lh.deck.get_resource(name)
        if not isinstance(resource, (PLRPlate, PLRContainer)):
            raise TypeError(
                f"Resource '{name}' is not a Plate or Container, got {type(resource).__name__}"
            )
        return resource

    async def aspirate(self, request: AspirateRequest) -> LabwareStateResponse:
        await self._require_honorable_volumes(
            [v for target in request.aspirations for v in target.volumes], request.use_channels
        )
        assert self._lh is not None
        plr_wells = []
        flat_volumes: list[float] = []
        for asp in request.aspirations:
            plr_wells.extend(
                self._resolve_op_containers(asp.labware, asp.positions, len(asp.volumes))
            )
            flat_volumes.extend(asp.volumes)
        n = len(plr_wells)

        plr_flow_rates = _per_channel_flow_rates(request.flow_rates, request.parameters, n)
        plr_offsets = _per_channel_offsets(request.offsets_z, request.parameters, n)
        plr_mix = _per_channel_mix(request.parameters, n)
        plr_blow_out = _per_channel_blow_out(request.parameters, n)

        # Hamilton STAR raises ``ChannelizedError`` only when EVERY underlying
        # firmware error is per-channel-keyed (see STAR_backend.py
        # convert_star_firmware_error_to_plr_error). Mixed responses (e.g., a
        # deck-level error alongside per-channel errors) propagate as the raw
        # ``STARFirmwareError`` and bypass this translation -- the action still
        # ERRORs but ops_history gets no per-channel attribution. Surfacing the
        # mixed case requires deeper firmware-code parsing, which this does not do.
        try:
            await self._lh.aspirate(plr_wells, vols=flat_volumes, flow_rates=plr_flow_rates,
                                     offsets=plr_offsets, use_channels=request.use_channels,
                                     mix=plr_mix, blow_out_air_volume=plr_blow_out)
        except PLRChannelizedError as exc:
            return build_aspirate_partial_failure(
                request,
                {ch: (type(e).__name__, str(e)) for ch, e in exc.errors.items()},
                labware_state=self._get_labware_state(),
            )
        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    async def dispense(self, request: DispenseRequest) -> LabwareStateResponse:
        await self._require_honorable_volumes(
            [v for target in request.dispenses for v in target.volumes], request.use_channels
        )
        assert self._lh is not None
        plr_wells = []
        flat_volumes: list[float] = []
        for disp in request.dispenses:
            plr_wells.extend(
                self._resolve_op_containers(disp.labware, disp.positions, len(disp.volumes))
            )
            flat_volumes.extend(disp.volumes)
        n = len(plr_wells)

        plr_flow_rates = _per_channel_flow_rates(request.flow_rates, request.parameters, n)
        plr_offsets = _per_channel_offsets(request.offsets_z, request.parameters, n)
        plr_mix = _per_channel_mix(request.parameters, n)

        plr_blow_out = _per_channel_blow_out(request.parameters, n)

        try:
            await self._lh.dispense(plr_wells, vols=flat_volumes, flow_rates=plr_flow_rates,
                                     offsets=plr_offsets, use_channels=request.use_channels,
                                     mix=plr_mix, blow_out_air_volume=plr_blow_out)
        except PLRChannelizedError as exc:
            return build_dispense_partial_failure(
                request,
                {ch: (type(e).__name__, str(e)) for ch, e in exc.errors.items()},
                labware_state=self._get_labware_state(),
            )
        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    async def pick_up_tips(self, request: PickUpTipsRequest) -> LabwareStateResponse:
        # TODO: pick_up_tips, drop_tips, and 96-head ops
        # also raise ``ChannelizedError`` per
        # ``pylabrobot/legacy/liquid_handling/errors.py``. Per-channel partial-failure
        # translation for tip ops is deferred until the aspirate/dispense
        # contract has settled; today's tip-op exception path is unchanged.
        assert self._lh is not None
        spots = []
        for pick in request.picks:
            rack = self._get_tip_rack(pick.tip_rack)
            for pos in pick.positions:
                spots.append(rack.get_item(pos))
        await self._lh.pick_up_tips(spots, use_channels=request.use_channels)
        # Tip volume trackers default to PLR's strict VolumeTracker; with
        # tracking now globally enabled, a workflow that aspirates beyond
        # the tip's `maximal_volume` would raise. Swap the just-mounted
        # tips' trackers to lenient for the same audit-not-enforce reason
        # the well trackers were swapped at configure_deck time.
        self._swap_mounted_tip_trackers_to_lenient(self._lh.head, request.use_channels)
        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    async def drop_tips(self, request: DropTipsRequest) -> LabwareStateResponse:
        assert self._lh is not None
        if request.to_waste:
            # One trash resource is one tip_spot, so PLR read it as "channel 0
            # only": a waste drop binned that channel's tip and left every other
            # one mounted. `discard_tips` takes the channels instead, and with
            # none named it empties every channel holding a tip.
            await self._lh.discard_tips(
                use_channels=request.use_channels, allow_nonzero_volume=True,
            )
        else:
            if request.drops is None:
                raise ValueError("drops required when to_waste=False")
            spots = []
            for drop in request.drops:
                rack = self._get_tip_rack(drop.tip_rack)
                for pos in drop.positions:
                    spots.append(rack.get_item(pos))
            await self._lh.drop_tips(
                spots, use_channels=request.use_channels, allow_nonzero_volume=True,
            )
        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    async def discard_tips(self, request: DiscardTipsRequest) -> LabwareStateResponse:
        assert self._lh is not None
        await self._lh.discard_tips(
            use_channels=request.use_channels, allow_nonzero_volume=True,
        )
        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    def _resolve_site(self, position: str) -> PLRResource:
        """A move endpoint by name: a Hamilton carrier site resolves by resource name; an Opentrons
        '<slot>-slot' label resolves to the slot holder (the two site forms share this one path)."""
        assert self._lh is not None
        slot_key = slot_key_from_site(position)
        if slot_key is not None:
            return _slot_holder(self._lh.deck, slot_key)
        return self._lh.deck.get_resource(position)

    async def move_plate(self, request: MovePlateRequest) -> None:
        # Plates use PLR's typed move_plate to keep its pickup_distance_from_top
        # (~9.87mm); move_resource defaults it to 0, gripping the plate top on hw.
        assert self._lh is not None
        resource = self._lh.deck.get_resource(request.plate)
        destination = self._resolve_site(request.to_position)

        if request.from_position is not None:
            from_resource = self._resolve_site(request.from_position)
            if resource.parent != from_resource:
                if resource.parent is not None:
                    resource.unassign()
                from_resource.assign_child_resource(resource)

        grip = request.grip_distance_from_top
        async with self._tracking_gripper_move(
            request.plate, request.to_position, request.from_position,
        ):
            if isinstance(resource, PLRPlate):
                if grip is None:
                    await self._lh.move_plate(resource, to=destination)
                else:
                    await self._lh.move_plate(
                        resource, to=destination, pickup_distance_from_top=grip
                    )
            elif grip is None:
                await self._lh.move_resource(resource, to=destination)
            else:
                await self._lh.move_resource(
                    resource, to=destination, pickup_distance_from_top=grip
                )

    async def add_deck_labware(self, request: AddDeckLabwareRequest) -> None:
        self._resolve_interrupted_move(request.name)
        if self._lh is None:
            raise RuntimeError(
                "add_deck_labware called before configure_deck; declare a deck_layout "
                "for this LiquidHandler"
            )
        # Opentrons slot: '<slot>-slot' -> the slot key, placed by name at implicit index 0.
        # Hamilton carrier: '<carrier>-<int>' -> carrier + numeric site index.
        slot_key = slot_key_from_site(request.at)
        if slot_key is not None:
            parent_id, site_index = slot_key, 0
        else:
            parent_id, _, site_text = request.at.rpartition("-")
            if not parent_id or not site_text.isdigit():
                raise ValueError(
                    f"add_deck_labware.at '{request.at}' is not a "
                    f"'<carrier>-<site>' or '<slot>-slot' deck site."
                )
            site_index = int(site_text)
        config = DeckResourceConfig(
            name=request.name,
            catalog_ref=request.catalog_ref,
            parent_id=parent_id,
            site_index=site_index,
        )
        resource = self._create_resource(config)
        self._place_resource(resource, config)
        swap_to_lenient(resource)
        if request.well_state is not None:
            self._apply_labware_state({request.name: request.well_state})
        empty_the_spots_a_head_carries(resource, self._spots_a_head_carries())

    async def remove_deck_labware(self, request: RemoveDeckLabwareRequest) -> None:
        # Un-materialize a named deck resource (plate, tip rack, or trough),
        # symmetric with add_deck_labware which materializes any of those kinds.
        self._resolve_interrupted_move(request.name)
        if self._lh is None:
            raise RuntimeError(
                "remove_deck_labware called before configure_deck; declare a deck_layout "
                "for this LiquidHandler"
            )
        resource = self._lh.deck.get_resource(request.name)
        if not isinstance(resource, MOVABLE_LABWARE):
            raise TypeError(
                f"remove_deck_labware target '{request.name}' is not materialized "
                f"labware (plate, tip rack, or trough), got {type(resource).__name__}; "
                f"carriers and deck structure are not removable"
            )
        await self._free_on_server(resource)
        resource.unassign()

    async def mix(self, request: MixRequest) -> LabwareStateResponse:
        assert self._lh is not None
        # A trough mix has no positions; channel count comes from use_channels
        # (the request validator guarantees it is present in that case).
        n = len(request.positions) if request.positions is not None else len(request.use_channels or [])
        containers = self._resolve_op_containers(request.labware, request.positions, n)
        plr_mix = [
            PLRMix(
                volume=request.volume,
                repetitions=request.repetitions,
                flow_rate=request.parameters.flow_rate,
            )
        ] * n
        offsets = _per_channel_offsets(None, request.parameters, n)
        await self._lh.aspirate(
            containers, vols=[0.0] * n, mix=plr_mix, offsets=offsets,
            use_channels=request.use_channels,
        )
        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    # --- 96-head operations ---

    async def aspirate96(self, request: Aspirate96Request) -> LabwareStateResponse:
        assert self._lh is not None
        resource = self._get_head96_resource(request.labware)
        await self._lh.aspirate96(resource, request.volume, flow_rate=request.flow_rate, liquid_height=request.liquid_height)
        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    async def dispense96(self, request: Dispense96Request) -> LabwareStateResponse:
        assert self._lh is not None
        resource = self._get_head96_resource(request.labware)
        await self._lh.dispense96(resource, request.volume, flow_rate=request.flow_rate, liquid_height=request.liquid_height)
        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    async def pick_up_tips96(self, request: PickUpTips96Request) -> LabwareStateResponse:
        assert self._lh is not None
        rack = self._get_tip_rack(request.tip_rack)
        await self._lh.pick_up_tips96(rack)
        # 96-head pickup mounts a tip on every channel of the 96-head;
        # swap each tip's volume tracker to lenient, same rationale as
        # the 8-channel path.
        self._swap_mounted_tip_trackers_to_lenient(self._lh.head96, channels=None)
        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    async def drop_tips96(self, request: DropTips96Request) -> LabwareStateResponse:
        assert self._lh is not None
        if request.to_waste:
            trash = self._lh.deck.get_trash_area96()
            await self._lh.drop_tips96(resource=trash, allow_nonzero_volume=True)
        else:
            if request.tip_rack is None:
                raise ValueError("tip_rack required when to_waste=False")
            rack = self._get_tip_rack(request.tip_rack)
            await self._lh.drop_tips96(resource=rack, allow_nonzero_volume=True)
        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    async def return_tips96(self, request: ReturnTips96Request) -> LabwareStateResponse:
        assert self._lh is not None
        await self._lh.return_tips96(allow_nonzero_volume=True)
        return LabwareStateResponse(success=True, labware_state=self._get_labware_state())

    # --- Internal helpers ---

    def _create_resource(self, config: DeckResourceConfig) -> PLRResource:
        return create_catalog_resource(config.catalog_ref, config.name)

    def _place_resource(self, resource: PLRResource, config: DeckResourceConfig) -> None:
        from pylabrobot.resources import Carrier
        assert self._lh is not None
        if config.rail is not None:
            self._lh.deck.assign_child_resource(resource, rails=config.rail)
        elif config.parent_id is not None:
            # Opentrons: parent_id names a built-in deck slot; site_index is implicit (always 0).
            if _place_at_named_slot(self._lh.deck, resource, config.parent_id):
                return
            parent = self._lh.deck.get_resource(config.parent_id)
            if not isinstance(parent, Carrier):
                raise TypeError(f"Parent '{config.parent_id}' is not a Carrier, got {type(parent).__name__}")
            site_index = config.site_index if config.site_index is not None else 0
            parent[site_index] = resource
        else:
            raise ValueError(f"Resource '{config.name}' must have either 'rail' or 'parent_id' for placement.")

    def _apply_labware_state(self, labware_state: Dict[str, LabwareWellState]) -> None:
        assert self._lh is not None
        missing: List[str] = []
        for labware_name, state in labware_state.items():
            try:
                resource = self._lh.deck.get_resource(labware_name)
            except PLRResourceNotFoundError:
                # PLR signals an absent name with its own error type, not a KeyError.
                missing.append(labware_name)
                continue
            if state.volumes is not None and isinstance(resource, PLRPlate):
                # Seeded wells opt out of the default lenient tracker: strict
                # enforces physical bounds; replenished pins a reagent source.
                for well in resource.get_all_items():
                    short_id = well.get_identifier()
                    if short_id in state.volumes:
                        if state.replenished:
                            swap_to_replenishing_tracker(well, state.volumes[short_id])
                        else:
                            swap_to_strict_tracker(well, state.volumes[short_id])
            elif state.volumes is not None and isinstance(resource, PLRTrough):
                # A trough is one pool keyed by TROUGH_WELL_ID; replenished keeps
                # it non-depleting so a multi-plate batch never drains it.
                seeded = state.volumes.get(TROUGH_WELL_ID)
                if seeded is not None:
                    if state.replenished:
                        swap_container_to_replenishing(resource, seeded)
                    else:
                        swap_container_to_strict(resource, seeded)
            if state.tips is not None and isinstance(resource, PLRTipRack):
                resource.set_tip_state(state.tips)
        if missing:
            raise ValueError(
                f"labware_state seeds {sorted(missing)}, which this deck does not hold. A deck "
                f"layout declares carriers only, so labware state arrives with the labware "
                f"through reconcile_deck_occupancy / add_deck_labware, or from the labware "
                f"template's initial_state."
            )

    def _get_labware_state(self) -> Dict[str, LabwareWellState]:
        if self._lh is None:
            return {}
        result: Dict[str, LabwareWellState] = {}
        # The outer dict key is the labware (instance) name. The inner
        # well/tip key is the SHORT identifier within that labware
        # (e.g. "A1"), not PLR's combined `<labware_name>_well_A1`
        # internal form. The combined form duplicates the outer key and
        # fails as a standalone identifier across instances anyway, so
        # the wire surfaces only the well-within-labware identifier and
        # callers reassemble using the outer key when needed.
        # Display-only: read-back carries volumes, never the tracker MODE
        # (strict/lenient/replenished). Mode re-seeds from orca's ledger.
        for resource in self._lh.deck.get_all_resources():
            if isinstance(resource, PLRPlate):
                volumes: Dict[str, float] = {}
                for well in resource.get_all_items():
                    if not well.tracker.is_disabled:
                        volumes[well.get_identifier()] = well.tracker.get_used_volume()
                if volumes:
                    result[resource.name] = LabwareWellState(volumes=volumes)
            elif isinstance(resource, PLRTrough):
                if not resource.tracker.is_disabled:
                    result[resource.name] = LabwareWellState(
                        volumes={TROUGH_WELL_ID: resource.tracker.get_used_volume()}
                    )
            elif isinstance(resource, PLRTipRack):
                tips: Dict[str, bool] = {}
                for tip_spot in resource.get_all_items():
                    tips[tip_spot.get_identifier()] = tip_spot.has_tip()
                result[resource.name] = LabwareWellState(tips=tips)
        return result


class PLRReaderBackendWrapper(VendorSurfaceForwarding, IReaderDriver):
    """Wrap a PLR PlateReaderBackend behind cheshire-drivers' IReaderDriver.

    cheshire-drivers' IReaderDriver routes reads via a protocol-file pattern
    (`ReadRequest.protocol_filepath` / `output_filepath`), while PLR's plate
    reader backends expose atomic per-measurement methods (`read_luminescence`,
    `read_absorbance`, `read_fluorescence`) that consume a Plate + Wells +
    measurement params. The two shapes do not align, so this wrapper does not
    invoke PLR's atomic read methods. Lifecycle (setup/open/close) routes to
    PLR; `read()` is acknowledged at this layer (chatterbox will print via the
    backend's setup/open/close anyway). When real plate-reader hardware is
    wrapped, this method writes nothing to the output file -- the protocol
    runner shape is currently a placeholder for vendor-protocol-style readers
    that have not been integrated.
    """

    def __init__(self, backend: PLRPlateReaderBackend):
        self._backend = backend
        self._is_initialized = False

    async def initialize(self) -> None:
        await self._backend.setup()
        self._is_initialized = True

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def open(self) -> None:
        await self._backend.open()

    async def close(self) -> None:
        # PLR plate readers' close() takes an optional plate; the cheshire
        # IReaderDriver.close() is parameterless, so pass None.
        await self._backend.close(None)

    async def read(self, request: ReadRequest) -> None:
        # See class docstring: PLR's atomic-read API and cheshire's
        # protocol-file API do not align. Acknowledge the read here so the
        # chatterbox layer prints something meaningful; the real measurement
        # values are not produced.
        print(
            f"PLR reader: protocol={request.protocol_filepath} "
            f"-> output={request.output_filepath}"
        )


class PLRStorageBackendWrapper(VendorSurfaceForwarding, IStorageDriver):
    """Wrap a PLR storage/incubator backend behind cheshire-drivers' IStorageDriver.

    PLR exposes incubator-style storage via IncubatorBackend with door
    open/close + plate fetch/take-in primitives. `dispense()` is the minimum
    surface a caller needs to release one plate on demand; per-source
    extensions (barcode, plate-type hint) land when a use case asks for them.
    """

    def __init__(self, backend: PLRIncubatorBackend):
        self._backend = backend
        self._is_initialized = False

    async def initialize(self) -> None:
        await self._backend.setup()
        self._is_initialized = True

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def open(self) -> None:
        await self._backend.open_door()

    async def close(self) -> None:
        await self._backend.close_door()

    async def dispense(self) -> None:
        # PLR IncubatorBackend has no symmetric "advance queue" primitive yet;
        # the Chatterbox sim treats dispense as a logical advancement (no
        # physical mechanism). Live PLR backends override when the hardware
        # gains a dispense primitive.
        return None


class _PLRTempBackendHolder:
    """Shared attribute carrier so the temp mixins agree on `_temp_backend`.

    Pyright flags duplicate compatible declarations of `_temp_backend` across
    sibling mixins in an MRO as `reportIncompatibleVariableOverride`. Hoisting
    the declaration into a single base class resolves that without giving up
    the type annotation downstream wrappers rely on.
    """

    _temp_backend: PLRTempControllerBackend


class PLRTempSettableMixin(_PLRTempBackendHolder, ITempSettableDriver):
    """Mixin providing ITempSettable backed by a PLR temperature controller.

    Composes with `PLRShakerBackendWrapper` (and other PLR wrappers) when a
    device satisfies multiple cheshire-drivers interfaces. Expects the
    inheriting class to set `self._temp_backend: PLRTempControllerBackend`.
    """

    async def set_temperature(self, temperature: float) -> None:
        await self._temp_backend.set_temperature(temperature)


class PLRTempGettableMixin(_PLRTempBackendHolder, ITempGettableDriver):
    """Mixin providing ITempGettable backed by a PLR temperature controller.

    Composes with `PLRShakerBackendWrapper` (and other PLR wrappers) when a
    device satisfies multiple cheshire-drivers interfaces. Expects the
    inheriting class to set `self._temp_backend: PLRTempControllerBackend`.

    Maps cheshire's `get_temperature()` to PLR's `get_current_temperature()`.
    """

    async def get_temperature(self) -> float:
        return await self._temp_backend.get_current_temperature()


class PLRHeatingShakerBackendWrapper(
    PLRShakerBackendWrapper, PLRTempSettableMixin, PLRTempGettableMixin
):
    """Wrap a PLR HeaterShakerBackend as a combined IShaker + ITempSettable + ITempGettable driver.

    PLR's HeaterShakerBackend extends both ShakerBackend and (effectively) a
    temperature controller surface, so a single backend object satisfies all
    three cheshire-drivers interfaces via stacked mixins.

    The shaker side uses PLRShakerBackendWrapper's existing methods directly.
    The temperature side reads/writes via the same backend object, exposed
    through the temp mixins as `self._temp_backend`.

    interfaces ClassVar reflects all three so capability discovery
    surfaces them on the wire.
    """

    interfaces: ClassVar[frozenset[str]] = frozenset(
        {"IShaker", "ITempSettable", "ITempGettable"}
    )

    def __init__(self, backend: PLRHeaterShakerBackend):
        super().__init__(backend)
        # The same PLR backend object plays the temp controller role.
        self._temp_backend = backend
