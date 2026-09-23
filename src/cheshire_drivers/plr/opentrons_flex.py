"""Opentrons Flex driver over the ``pylabrobot.opentrons`` device tree.

``pylabrobot.opentrons.OpentronsFlex`` is a device that owns its deck and composes
mount-addressed heads, so this driver implements ``ILiquidHandlerDriver``
directly instead of going through ``PLRLiquidHandlerWrapper`` (which is built
around a ``LiquidHandler`` + backend pair the Flex no longer has).

Cheshire's 8-channel requests carry arbitrary per-well position lists, but a
Flex head is a fixed nozzle block on one plunger: ``FlexHead1`` pipettes one
well, ``FlexHead8`` a whole column (or one nozzle in SINGLE mode), ``FlexHead96``
a whole plate. Only those shapes have a Flex form, so a request asking for
anything else -- a partial column, volumes that differ across a column, more
positions than the head has nozzles -- is refused with a message naming what the
mounted head can do, rather than emulated as something the caller did not ask for.
"""

from contextlib import contextmanager
from math import isclose
from typing import Awaitable, Callable, ClassVar, Iterator, Literal, NamedTuple, TypeAlias

from pylabrobot.opentrons import OpentronsFlex, OpentronsTransport
from pylabrobot.opentrons.transport import HttpxTransport
from pylabrobot.opentrons.flex_gripper import FlexGripper
from pylabrobot.opentrons.flex_head import (
    RECONCILE_CLEARED_LOST_TIPS,
    RECONCILE_UNTRACKED_TIP,
    RECONCILE_UNVERIFIED,
    FlexHead1,
    FlexHead8,
    FlexHead96,
)
from pylabrobot.resources.container import Container as PLRContainer
from pylabrobot.resources.errors import ResourceNotFoundError as PLRResourceNotFoundError
from pylabrobot.resources.itemized_resource import ItemizedResource as PLRItemized
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.plate import Plate as PLRPlate
from pylabrobot.resources.resource import Resource as PLRResource
from pylabrobot.resources.tip_rack import TipRack as PLRTipRack
from pylabrobot.resources.tip_tracker import TipTracker as PLRTipTracker, set_tip_tracking
from pylabrobot.resources.trash import Trash as PLRTrash
from pylabrobot.resources.volume_tracker import (
    VolumeTracker as PLRVolumeTracker,
    set_volume_tracking,
)
from pylabrobot.resources.well import Well as PLRWell
from pylabrobot.visualizer import Visualizer

from cheshire_drivers.command_timings import command_timing
from cheshire_drivers.driver_introspection import VendorSurface, external
from cheshire_drivers.gantry_models import GantryBusyError, ParkGantryRequest
from cheshire_drivers.interrupted_move import InterruptedMoveTracking
from cheshire_drivers.gripper_models import (
    GripWithForceRequest,
    MoveGripperToRequest,
    ReleaseJawRequest,
)
from cheshire_drivers.homing_models import HomeRequest
from cheshire_drivers.interfaces import (
    IForceGripperJawDriver,
    IGantryParkingDriver,
    IGripperMotionDriver,
    IHomeableDriver,
    ILiquidHandlerDriver,
    ILiquidProbeDriver,
    IPipetteMotionDriver,
)
from cheshire_drivers.liquid_handler_models import (
    AddDeckLabwareRequest,
    Aspirate96Request,
    AspirateRequest,
    AspirateTarget,
    DeckLayoutConfig,
    DeckResourceConfig,
    DeckResourceState,
    DeckStateResponse,
    DiscardStrandedTipsRequest,
    DiscardTipsRequest,
    Dispense96Request,
    DispenseRequest,
    DispenseTarget,
    DropTips96Request,
    DropTipsRequest,
    GetDeckStateRequest,
    GetHeadConfigurationRequest,
    HeadConfigurationResponse,
    LabwareStateResponse,
    LiquidProbeRequest,
    LiquidProbeResponse,
    LabwareWellState,
    MixRequest,
    MovePlateRequest,
    MountTipReconcile,
    NozzleGroup,
    PickUpTips96Request,
    PickUpTipsRequest,
    ReconcileDeckOccupancyRequest,
    ReconcileHardwareStateRequest,
    ReconcileHardwareStateResponse,
    RemoveDeckLabwareRequest,
    ResetDeckLabwareRequest,
    ReturnTips96Request,
    TROUGH_WELL_ID,
    TipPick,
    TipRackState,
    VOLUME_TOLERANCE_UL,
)
from cheshire_drivers.pipette_motion_models import (
    ChannelPosition,
    GetChannelPositionRequest,
    MoveChannelRelativeRequest,
    MoveChannelToRequest,
    require_tip_end_datum,
)
from cheshire_drivers.plr_tracker_seeding import (
    is_standalone_container,
    swap_container_to_replenishing,
    swap_container_to_strict,
    swap_to_lenient,
    swap_to_replenishing_tracker,
    swap_to_strict_tracker,
)
from cheshire_drivers.plr_wrappers import (
    MOVABLE_LABWARE,
    VendorSurfaceForwarding,
    SimServerLifecycle,
    create_catalog_resource,
    empty_the_spots_a_head_carries,
    slot_key_from_site,
    start_deck_visualizer,
)
from cheshire_drivers.wire_timeouts import WireTimeout, resolve_wire_timeout

FLEX_DECK_TYPE = "FlexDeck"

def _stamp_ot_load_name(resource: PLRResource, catalog_ref: str) -> None:
    """A catalog ref that IS an Opentrons load name loads the vendor definition.

    Anything else is left unstamped so the robot gets a definition built from the
    resource's own PLR geometry: one geometry, on both sides. Mapping a plate from
    another vendor's catalog onto an Opentrons name would move the robot on
    Opentrons' numbers while PLR tracks on its own, and nobody would see the gap.
    """
    if hasattr(resource, "ot_load_name") or not catalog_ref.startswith("opentrons_"):
        return
    # PLR carries the load name as a plain attribute, not a declared field.
    setattr(resource, "ot_load_name", catalog_ref)


def _create_resource(config: DeckResourceConfig) -> PLRResource:
    """Build the PLR resource a catalog ref names, stamped with its Opentrons load name."""
    resource = create_catalog_resource(config.catalog_ref, config.name)
    _stamp_ot_load_name(resource, config.catalog_ref)
    return resource


def _labware_trackers(resource: PLRResource) -> list[PLRVolumeTracker | PLRTipTracker]:
    """Every tracker a head op can stage while working one labware.

    A disabled tracker stages nothing and raises if asked to roll back, so it is
    not one of them.
    """
    trackers: list[PLRVolumeTracker | PLRTipTracker] = []
    if isinstance(resource, PLRPlate):
        trackers = [well.tracker for well in resource.get_all_items()]
    elif isinstance(resource, PLRTipRack):
        trackers = [spot.tracker for spot in resource.get_all_items()]
    elif is_standalone_container(resource):
        trackers = [resource.tracker]
    return [tracker for tracker in trackers if not tracker.is_disabled]


@contextmanager
def _undo_partial_staging(resource: PLRResource) -> Iterator[None]:
    """Discard what a failed head op staged on this labware.

    A head stages one tracker per item and validates as it goes, so an item it
    cannot serve leaves the items ahead of it holding an uncommitted delta.
    Nothing else is uncommitted at that moment, so rolling the whole labware
    back restores exactly what the op found. Without it, liquid that never moved
    is reported to the caller as observed truth.
    """
    try:
        yield
    except Exception:
        for tracker in _labware_trackers(resource):
            tracker.rollback()
        raise


def _movable_occupants(deck: FlexDeck) -> list[PLRResource]:
    """Deck occupants that can be taken off it. The trash is deck furniture."""
    return [
        occupant
        for occupant in deck.slots.values()
        if occupant is not None
        and isinstance(occupant, MOVABLE_LABWARE)
        and not isinstance(occupant, PLRTrash)
    ]


def derive_labware_state(deck: FlexDeck) -> dict[str, LabwareWellState]:
    """Per-labware volumes and tip presence, read off the deck's PLR trackers.

    Keys are labware instance names; inner keys are the SHORT well / tip-spot
    identifier within that labware ("A1"). A single-cavity container reports its
    one pool under ``TROUGH_WELL_ID``.
    """
    state: dict[str, LabwareWellState] = {}
    for resource in deck.get_all_resources():
        if isinstance(resource, PLRPlate):
            volumes = {
                well.get_identifier(): well.tracker.get_used_volume()
                for well in resource.get_all_items()
                if not well.tracker.is_disabled
            }
            if volumes:
                state[resource.name] = LabwareWellState(volumes=volumes)
        elif isinstance(resource, PLRTipRack):
            state[resource.name] = LabwareWellState(
                tips={spot.get_identifier(): spot.has_tip() for spot in resource.get_all_items()}
            )
        elif is_standalone_container(resource) and not resource.tracker.is_disabled:
            state[resource.name] = LabwareWellState(
                volumes={TROUGH_WELL_ID: resource.tracker.get_used_volume()}
            )
    return state


def _apply_labware_state(deck: FlexDeck, labware_state: dict[str, LabwareWellState]) -> None:
    """Seed volumes and tip layout onto labware already placed on the deck."""
    missing: list[str] = []
    for labware_name, seed in labware_state.items():
        try:
            resource = deck.get_resource(labware_name)
        except PLRResourceNotFoundError:
            missing.append(labware_name)
            continue
        if seed.volumes is not None and isinstance(resource, PLRPlate):
            for well in resource.get_all_items():
                volume = seed.volumes.get(well.get_identifier())
                if volume is None:
                    continue
                if seed.replenished:
                    swap_to_replenishing_tracker(well, volume)
                else:
                    swap_to_strict_tracker(well, volume)
        elif seed.volumes is not None and is_standalone_container(resource):
            pooled = seed.volumes.get(TROUGH_WELL_ID)
            if pooled is not None:
                if seed.replenished:
                    swap_container_to_replenishing(resource, pooled)
                else:
                    swap_container_to_strict(resource, pooled)
        if seed.tips is not None and isinstance(resource, PLRTipRack):
            resource.set_tip_state(seed.tips)
    if missing:
        raise ValueError(
            f"labware_state seeds {sorted(missing)}, which this deck does not hold. A deck "
            f"layout declares carriers only, so labware state arrives with the labware through "
            f"reconcile_deck_occupancy / add_deck_labware, or from the labware template's "
            f"initial_state."
        )


def _slot_occupants(deck: FlexDeck) -> list[tuple[str, PLRResource]]:
    """Slot key and what sits on it, for every occupied slot (staging pads included)."""
    return [(slot, occupant) for slot, occupant in deck.slots.items() if occupant is not None]


def _require_slot(site: str, field: str) -> str:
    """The deck slot a '<slot>-slot' site label names."""
    slot = slot_key_from_site(site)
    if slot is None:
        raise ValueError(
            f"{field} '{site}' is not a '<slot>-slot' Flex deck site (e.g. 'C2-slot'); a Flex has "
            f"no carrier sites."
        )
    return slot


_NOZZLES_PER_COLUMN = 8

# Only an end nozzle can be isolated, so a single-tip op runs on the head's first or last
# channel. Which one is not free: the rear nozzle cannot reach the deck's front row.
_CHERRY_PICK_NOZZLES = {0: "A1", 7: "H1"}

_MountHead: TypeAlias = FlexHead1 | FlexHead8
_AnyHead: TypeAlias = FlexHead1 | FlexHead8 | FlexHead96

DEFAULT_PIPETTING_HEIGHT_MM = 3.0
"""Tip height above the well bottom when a command names none. The Opentrons
default of 1 mm pins the tip on the floor of anything deeper than a well."""


def _pipetting_height(named: float | None) -> float:
    """The height a command asked for, or the default clearance."""
    return DEFAULT_PIPETTING_HEIGHT_MM if named is None else named


_LiquidCall = Callable[[float, float | None, float], Awaitable[None]]


class _LiquidSeam(NamedTuple):
    """The head calls a resolved slice runs, one per direction.

    Each call takes the volume, the flow rate and the tip height above the well
    bottom, so a mix is the same pair issued repeatedly at the same target."""

    head: _MountHead
    aspirate: _LiquidCall
    dispense: _LiquidCall


def _row_and_column(resource: PLRItemized, labware: str, position: str) -> tuple[int, int]:
    """A well id ("C5") as its zero-based (row, column) in this labware's own grid."""
    row = ord(position[:1].upper()) - ord("A")
    if not 0 <= row < resource.num_items_y or not position[1:].isdigit():
        raise ValueError(f"'{labware}' has no well '{position}'.")
    return row, int(position[1:]) - 1


def _require_well_ids(resource: PLRResource, labware: str, positions: list[str]) -> None:
    """Refuse positions this labware has no such well for, before any wire command.

    Read off the labware, not a fixed A-H alphabet: a 384-well plate's rows run to P,
    and refusing those would hide half the plate. A single-cavity resource is left to
    the checks that explain it holds one pool and takes no positions."""
    if not isinstance(resource, PLRItemized):
        return
    for position in positions:
        try:
            resource.get_item(position)
        except IndexError:
            raise ValueError(
                f"'{labware}' has no well '{position}'. A position is a well id: the row "
                f"letter, then the 1-based column (this labware has "
                f"{resource.num_items_y} rows and {resource.num_items_x} columns)."
            ) from None


def _nozzle_row_stride(resource: PLRItemized, labware: str) -> int:
    """How many rows apart the 8 nozzles land on this labware.

    They sit at a fixed 9 mm pitch, so on a plate with twice the rows they cover every
    second one. A row count that is not a multiple of 8 has no such set at all."""
    stride, remainder = divmod(resource.num_items_y, _NOZZLES_PER_COLUMN)
    if stride < 1 or remainder:
        raise ValueError(
            f"The 8 nozzles cover 8 evenly spaced rows, and '{labware}' has "
            f"{resource.num_items_y}, so no set of its rows lines up with the head."
        )
    return stride


def _full_column(resource: PLRItemized, labware: str, positions: list[str]) -> int:
    """The zero-based column index the head's 8 nozzles cover.

    On a 96-well plate that is the plain column. On a denser plate each physical column
    holds several interleaved sets of 8, numbered consecutively, which is the same
    indexing the head itself uses."""
    stride = _nozzle_row_stride(resource, labware)
    parsed = [_row_and_column(resource, labware, position) for position in positions]
    rows = [row for row, _ in parsed]
    columns = {column for _, column in parsed}
    phase = rows[0] % stride
    if len(columns) != 1 or rows != list(range(phase, resource.num_items_y, stride)):
        sets = " or ".join(
            "".join(chr(ord("A") + row) for row in range(start, resource.num_items_y, stride))
            for start in range(stride)
        )
        raise ValueError(
            f"The 8-channel head pipettes a whole column in one stroke, so its positions must be "
            f"one column of '{labware}' in row order covering rows {sets}, not {positions}."
        )
    return columns.pop() * stride + phase


def _one_value(values: list[float], what: str, labware: str) -> float:
    """The single value a ganged head can honor, or a refusal naming the spread."""
    first = values[0]
    if any(not isclose(v, first, rel_tol=0.0, abs_tol=VOLUME_TOLERANCE_UL) for v in values):
        raise ValueError(
            f"A Flex head's nozzles share one plunger and one motion, so a command carries one "
            f"{what} for '{labware}', but {sorted(set(values))} were requested. Split this into "
            f"one command per {what}."
        )
    return first


def _one_per_command(
    per_channel: list[float] | None, fallback: float | None, what: str, labware: str
) -> float | None:
    """The per-command value, from the per-channel list if given, else the liquid class."""
    return fallback if per_channel is None else _one_value(per_channel, what, labware)


def _head_channels(flex: OpentronsFlex) -> list[tuple[int, _AnyHead]]:
    """(first channel, head) per composed head, numbered as get_head_configuration reports it."""
    numbered: list[tuple[int, _AnyHead]] = []
    next_channel = 0
    for head in (flex.left, flex.right, flex.head96):
        if isinstance(head, (FlexHead1, FlexHead8, FlexHead96)):
            numbered.append((next_channel, head))
            next_channel += head.channels
    return numbered


def _mount_heads(flex: OpentronsFlex) -> list[tuple[int, _MountHead]]:
    """The pipette mounts, which are the heads the 8-channel ops address."""
    return [
        (base, head)
        for base, head in _head_channels(flex)
        if isinstance(head, (FlexHead1, FlexHead8))
    ]


def _named_heads(flex: OpentronsFlex) -> list[tuple[str, _AnyHead]]:
    """(label, head) per composed head: the mount name, or '96' for the 96-head."""
    named: list[tuple[str, _AnyHead]] = [(head.mount, head) for _, head in _mount_heads(flex)]
    if isinstance(flex.head96, FlexHead96):
        named.append(("96", flex.head96))
    return named


def _reconcile_message(session_recovered: bool, mounts: list[MountTipReconcile]) -> str:
    """One operator-readable sentence per repair or finding, or the all-clear."""
    cleared = [m.mount for m in mounts if m.outcome == "cleared_lost_tips"]
    stranded = [m.mount for m in mounts if m.outcome == "untracked_tip_present"]
    unverified = [m.mount for m in mounts if m.outcome == "unverified"]
    parts: list[str] = []
    if session_recovered:
        parts.append(
            "the instrument session had died (operator recovery at the robot?) and was "
            "rebuilt without motion; labware reloads on next use"
        )
    if cleared:
        parts.append(
            f"cleared tip records on mount(s) {', '.join(cleared)}: the hardware sensor "
            "reads no tip there"
        )
    if stranded:
        parts.append(
            f"mount(s) {', '.join(stranded)} hold a tip the driver does not track; "
            "discard_stranded_tips trashes it, or resolve at the instrument"
        )
    if unverified:
        parts.append(
            f"mount(s) {', '.join(unverified)} could not be verified: the tip sensor "
            "read neither present nor absent, so their tip records stand unchecked"
        )
    return "; ".join(parts) if parts else "hardware and driver state agree"


def _describe_heads(flex: OpentronsFlex) -> str:
    """The mounted heads and the channels they answer to, for a refusal message."""
    numbered = _head_channels(flex)
    if not numbered:
        return "no pipette is mounted"
    return ", ".join(
        f"{type(head).__name__} on channels {base}-{base + head.channels - 1}"
        for base, head in numbered
    )


def _head_owning_channels(
    flex: OpentronsFlex, use_channels: list[int], operation: str
) -> tuple[int, _MountHead]:
    """The one mount head every requested channel sits on."""
    for base, head in _head_channels(flex):
        if not all(base <= channel < base + head.channels for channel in use_channels):
            continue
        if isinstance(head, FlexHead96):
            raise ValueError(
                f"{operation}: use_channels {use_channels} names the 96 head, which has its own "
                f"ops (aspirate96, dispense96, pick_up_tips96, drop_tips96)."
            )
        return base, head
    raise ValueError(
        f"{operation}: use_channels {use_channels} does not sit on one mounted head "
        f"({_describe_heads(flex)}). A Flex head is one ganged pipette, so a command drives the "
        f"nozzles of a single head."
    )


def _head_for_count(flex: OpentronsFlex, count: int, operation: str) -> tuple[int, _MountHead]:
    """The one mount head that serves this many positions at once."""
    mounts = _mount_heads(flex)
    serving = [(base, head) for base, head in mounts if head.channels == count]
    if not serving and count == 1:
        # One nozzle of a column head, cherry-picked in SINGLE mode.
        serving = [(base, head) for base, head in mounts if isinstance(head, FlexHead8)]
    if len(serving) == 1:
        return serving[0]
    if serving:
        raise ValueError(
            f"{operation}: {len(serving)} mounted heads pipette {count} positions at once "
            f"({_describe_heads(flex)}), so the command has to name the one that runs it with "
            f"use_channels."
        )
    raise ValueError(
        f"{operation}: no mounted head pipettes {count} positions at once ({_describe_heads(flex)}"
        f"). A mount head takes one well or one whole column of 8; 96 at once is the 96-head "
        f"surface (aspirate96, dispense96, pick_up_tips96, drop_tips96)."
    )


def _require_channel_list(use_channels: list[int], count: int, operation: str) -> None:
    """Refuse a channel list that cannot name one channel per position."""
    if len(use_channels) != count:
        raise ValueError(
            f"{operation}: use_channels names {len(use_channels)} channels but the request "
            f"carries {count}; each names the channel that serves it."
        )
    if len(set(use_channels)) != len(use_channels):
        raise ValueError(f"{operation}: use_channels repeats a channel: {use_channels}.")


def _require_shape_fits_head(
    head: _MountHead, labware: str, positions: list[str] | None, count: int, operation: str
) -> None:
    """Refuse a slice whose channel count the chosen head cannot drive."""
    if positions is None:
        if count != head.channels:
            raise ValueError(
                f"{operation}: every nozzle dips into the same cavity of '{labware}', so a "
                f"container op drives the whole head ({head.channels} channels), not {count}."
            )
    elif count != 1 and count != head.channels:
        raise ValueError(
            f"{operation}: a {type(head).__name__} pipettes one well or all {head.channels} of "
            f"its nozzles together, so it cannot serve {count} positions of '{labware}'."
        )


def _require_cherry_pick_channel(
    base: int,
    head: _MountHead,
    positions: list[str] | None,
    use_channels: list[int],
    operation: str,
) -> None:
    """Refuse a single-tip op aimed at a nozzle the head cannot isolate.

    A ganged head reaches one well by driving a single nozzle, and only an end
    nozzle can be driven alone, so a single-tip op runs on the head's first or last
    channel whatever row the well is in. Which of the two is not interchangeable:
    the pipette body hangs forward of the anchored nozzle, so only the front nozzle
    reaches a front-row slot. Leave use_channels off for the rear nozzle; the head
    refuses an anchor that cannot reach rather than swapping to the other. The
    whole-head shapes need no such check: they carry as many distinct in-range
    channels as the head has nozzles, so they are already the whole head."""
    if positions is None or len(positions) != 1 or head.channels == 1:
        return
    if use_channels[0] - base not in _CHERRY_PICK_NOZZLES:
        anchors = ", ".join(str(base + offset) for offset in sorted(_CHERRY_PICK_NOZZLES))
        raise ValueError(
            f"{operation}: a single-tip op runs on an end nozzle of the head, so '{positions[0]}' "
            f"is channel {anchors}, not {use_channels}. Omit use_channels to run on the rear "
            f"nozzle, channel {base}."
        )


def _cherry_pick_nozzle(
    flex: OpentronsFlex, head: _MountHead, use_channels: list[int] | None
) -> str:
    """The anchor nozzle the caller's channel names, or the rear one when it names none.

    Naming a channel names an anchor, and the head refuses one that cannot reach the
    slot rather than quietly taking the other."""
    if use_channels is None:
        return _CHERRY_PICK_NOZZLES[0]
    base = next(base for base, mounted in _mount_heads(flex) if mounted is head)
    return _CHERRY_PICK_NOZZLES[use_channels[0] - base]


def _resolve_head(
    flex: OpentronsFlex,
    labware: str,
    positions: list[str] | None,
    count: int,
    use_channels: list[int] | None,
    operation: str,
    carrying: _MountHead | None = None,
) -> _MountHead:
    """The head that runs this slice, refusing every shape the Flex cannot express.

    ``carrying`` is the head already known to hold the tips a return names, which
    settles the choice when two mounts have the same channel count.
    """
    if positions is not None:
        _require_well_ids(flex.deck.get_resource(labware), labware, positions)
    if use_channels is not None:
        _require_channel_list(use_channels, count, operation)
        base, head = _head_owning_channels(flex, use_channels, operation)
        _require_shape_fits_head(head, labware, positions, count, operation)
        _require_cherry_pick_channel(base, head, positions, use_channels, operation)
        return head
    head = carrying if carrying is not None else _head_for_count(flex, count, operation)[1]
    _require_shape_fits_head(head, labware, positions, count, operation)
    return head


def _require_plate(resource: PLRResource, labware: str) -> PLRPlate:
    """The itemized plate a positioned op addresses."""
    if not isinstance(resource, PLRPlate):
        raise TypeError(
            f"'{labware}' is a single-cavity {type(resource).__name__}, so its op omits positions "
            f"and passes one volume per channel."
        )
    return resource


def _require_container(resource: PLRResource, labware: str) -> PLRContainer:
    """The single-cavity container a position-less op addresses."""
    if not is_standalone_container(resource):
        raise TypeError(
            f"'{labware}' is itemized ({type(resource).__name__}), so its op names one position "
            f"per channel rather than drawing from a single pool."
        )
    return resource


def _require_tip_rack(flex: OpentronsFlex, name: str) -> PLRTipRack:
    """The tip rack a tip op names."""
    resource = flex.deck.get_resource(name)
    if not isinstance(resource, PLRTipRack):
        raise TypeError(f"'{name}' is not a tip rack, got {type(resource).__name__}.")
    return resource


def _cherry_pick_seam(head: FlexHead8, plate: PLRPlate, well_id: str) -> _LiquidSeam:
    """One nozzle of the 8-channel head at one well."""

    async def aspirate(volume: float, rate: float | None, height: float) -> None:
        await head.aspirate_single(plate, well_id, volume, flow_rate=rate, liquid_height=height)

    async def dispense(volume: float, rate: float | None, height: float) -> None:
        await head.dispense_single(plate, well_id, volume, flow_rate=rate, liquid_height=height)

    return _LiquidSeam(head, aspirate, dispense)


def _liquid_seam(
    flex: OpentronsFlex, head: _MountHead, labware: str, positions: list[str] | None
) -> _LiquidSeam:
    """Bind a resolved slice to the head calls that run it, undoing a failed stage."""
    resource = flex.deck.get_resource(labware)
    seam = _unguarded_seam(head, resource, labware, positions)

    def guard(call: _LiquidCall) -> _LiquidCall:
        async def run(volume: float, rate: float | None, height: float) -> None:
            with _undo_partial_staging(resource):
                await call(volume, rate, height)

        return run

    return _LiquidSeam(seam.head, guard(seam.aspirate), guard(seam.dispense))


def _unguarded_seam(
    head: _MountHead, resource: PLRResource, labware: str, positions: list[str] | None
) -> _LiquidSeam:
    """The raw head calls a resolved slice runs."""
    if isinstance(head, FlexHead1):
        item: PLRWell | PLRContainer = (
            _require_container(resource, labware)
            if positions is None
            else _require_plate(resource, labware).get_item(positions[0])
        )
        return _LiquidSeam(
            head,
            lambda vol, rate, z: head.aspirate(item, vol, flow_rate=rate, liquid_height=z),
            lambda vol, rate, z: head.dispense(item, vol, flow_rate=rate, liquid_height=z),
        )
    if positions is None:
        pool = _require_container(resource, labware)
        return _LiquidSeam(
            head,
            lambda vol, rate, z: head.aspirate_container(
                pool, vol, flow_rate=rate, liquid_height=z
            ),
            lambda vol, rate, z: head.dispense_container(
                pool, vol, flow_rate=rate, liquid_height=z
            ),
        )
    plate = _require_plate(resource, labware)
    if len(positions) == 1:
        return _cherry_pick_seam(head, plate, positions[0])
    column = _full_column(plate, labware, positions)
    return _LiquidSeam(
        head,
        lambda vol, rate, z: head.aspirate(
            plate, vol, column=column, flow_rate=rate, liquid_height=z
        ),
        lambda vol, rate, z: head.dispense(
            plate, vol, column=column, flow_rate=rate, liquid_height=z
        ),
    )


_ProbeCall: TypeAlias = Callable[[], Awaitable[float | None]]


class _Probe(NamedTuple):
    """The head call that reads a liquid level, and the well it reads it in.

    The call answers in the robot's DECK frame. `resource` and `well` are what
    turn that into the height this surface speaks, which is measured from the
    well's own floor.
    """

    read: _ProbeCall
    resource: PLRResource
    well: str


def _probe_call(
    flex: OpentronsFlex, head: _MountHead, labware: str, positions: list[str] | None
) -> _Probe:
    """Bind a resolved slice to the head call that reads its liquid level.

    Non-raising throughout: an empty well is an answer, not a failure, so the
    ``try_`` variants are what this reaches for.
    """
    resource = flex.deck.get_resource(labware)
    if positions is None:
        pool = _require_container(resource, labware)
        if isinstance(head, FlexHead1):
            return _Probe(lambda: head.try_liquid_probe(pool), pool, TROUGH_WELL_ID)
        return _Probe(
            lambda: head.try_liquid_probe_container(pool), pool, TROUGH_WELL_ID
        )
    plate = _require_plate(resource, labware)
    if isinstance(head, FlexHead1):
        well = plate.get_item(positions[0])
        return _Probe(lambda: head.try_liquid_probe(well), plate, positions[0])
    if len(positions) == 1:
        return _Probe(
            lambda: head.try_liquid_probe_single(plate, positions[0]),
            plate,
            positions[0],
        )
    column = _full_column(plate, labware, positions)
    # One command reads the whole column, so one well frames the answer. Any of
    # the column's wells does: a plate's wells share one floor.
    return _Probe(lambda: head.try_liquid_probe(plate, column), plate, positions[0])


def _liquid_height(flex: OpentronsFlex, probe: _Probe, deck_z: float | None) -> float | None:
    """A probe's deck-frame answer as a height above the well's own floor.

    That is the frame every pipetting height on this surface uses, so the number
    feeds straight back into the next aspirate. The floor comes from the
    definition the robot loaded, which is the only place that agrees with where
    the robot actually thinks the well is.
    """
    if deck_z is None:
        return None
    return deck_z - flex.well_bottom_deck_z(probe.resource, probe.well)


async def _run_mix_cycles(
    seam: _LiquidSeam,
    volume: float,
    repetitions: int,
    flow_rate: float | None,
    height: float,
) -> None:
    """Mix in place. The Flex has no mix primitive, so a mix is aspirate/dispense cycles."""
    for _ in range(repetitions):
        await seam.aspirate(volume, flow_rate, height)
        await seam.dispense(volume, flow_rate, height)


def _only_slice(
    slices: list[AspirateTarget] | list[DispenseTarget], operation: str
) -> AspirateTarget | DispenseTarget:
    """The one labware slice a Flex command can address."""
    if len(slices) > 1:
        raise ValueError(
            f"{operation}: one Flex command names one labware, so it cannot serve slices of "
            f"{', '.join(s.labware for s in slices)} at once. Issue one command per labware."
        )
    return slices[0]


def _only_tip_slice(picks: list[TipPick], operation: str) -> TipPick:
    """The one rack slice a Flex tip command can address."""
    if len(picks) > 1:
        raise ValueError(
            f"{operation}: one Flex command names one tip rack, so it cannot serve "
            f"{', '.join(p.tip_rack for p in picks)} at once. Issue one command per rack."
        )
    return picks[0]


def _heads_holding_tips(
    flex: OpentronsFlex, use_channels: list[int] | None, operation: str
) -> list[_MountHead]:
    """The heads a waste drop empties: the one use_channels names, or every head holding tips.

    The Flex ejector clears every nozzle of a head at once, so a channel list
    that names some but not all of a head's tips is refused rather than served
    by throwing away tips the caller did not name.
    """
    if use_channels is None:
        candidates = [head for _, head in _mount_heads(flex)]
    else:
        base, head = _head_owning_channels(flex, use_channels, operation)
        mounted = {i for i, tip in enumerate(head.get_mounted_tips()) if tip is not None}
        requested = {channel - base for channel in use_channels}
        if mounted and requested != mounted:
            raise ValueError(
                f"{operation}: the Flex ejector drops every tip the head holds at once, so it "
                f"cannot discard channels {sorted(base + i for i in requested)} while the head "
                f"holds {sorted(base + i for i in mounted)}. Name every channel holding a tip, "
                f"or omit use_channels."
            )
        candidates = [head]
    holding = [
        head for head in candidates if any(tip is not None for tip in head.get_mounted_tips())
    ]
    if not holding:
        raise ValueError(
            f"{operation}: the addressed head holds no tips, so there is nothing to drop "
            f"({_describe_heads(flex)})."
        )
    return holding


def _any_head_with_tips_on(flex: OpentronsFlex) -> list[_AnyHead]:
    """Every head with a tip still on it, whatever channel it sits in."""
    return [head for _, head in _head_channels(flex)
            if any(tip is not None for tip in head.get_mounted_tips())]


def _tips_refusal(flex: OpentronsFlex) -> str | None:
    """Why tips stop this operation, or None."""
    holding = _any_head_with_tips_on(flex)
    if not holding:
        return None
    names = ", ".join(type(head).__name__ for head in holding)
    verb = "holds" if len(holding) == 1 else "hold"
    return (
        f"{names} still {verb} tips, so a transfer is under way. "
        f"Ask again once the tips are off."
    )


def _vertical_axes_to_retract(flex: OpentronsFlex) -> list[str]:
    """Every z that has to be up before the gantry traverses.

    An empty mount is skipped rather than retracted: the robot rejects an axis
    it has no instrument for, and a bare carriage is not what collides.
    """
    axes = [f"{head.mount}Z" for _, head in _head_channels(flex)]
    if flex.gripper is not None:
        axes.append("extensionZ")
    return axes


async def _lift_everything_that_hangs(flex: OpentronsFlex) -> None:
    """Retract every populated mount, so nothing is left low to be dragged.

    retractAxis is a move to the axis home, not a homing routine, so nothing is
    re-referenced and mounted tips do not refuse it.
    """
    for axis in _vertical_axes_to_retract(flex):
        await flex.retract_axis(axis)


def _require_mounted_tips(head: _AnyHead, operation: str) -> None:
    """Refuse an op that drives mounted tips from a head that holds none."""
    if all(tip is None for tip in head.get_mounted_tips()):
        raise ValueError(
            f"{operation}: the {type(head).__name__} holds no tips; pick up tips first."
        )


def _require_head96(flex: OpentronsFlex, operation: str) -> FlexHead96:
    """The 96 head a whole-plate op needs."""
    head = flex.head96
    if not isinstance(head, FlexHead96):
        raise ValueError(
            f"{operation} needs a 96-channel head, and the robot reports none ({_describe_heads(flex)})."
        )
    return head


def _head96_target(flex: OpentronsFlex, labware: str) -> PLRPlate | PLRContainer:
    """The whole plate or single-cavity container the 96 head covers."""
    resource = flex.deck.get_resource(labware)
    if isinstance(resource, PLRPlate):
        return resource
    if is_standalone_container(resource):
        return resource
    raise TypeError(
        f"'{labware}' is a {type(resource).__name__}; the 96 head covers a whole plate or one "
        f"single-cavity container."
    )


async def _reparent_to_slot(flex: OpentronsFlex, resource: PLRResource, slot: str) -> None:
    """Put the labware where the caller says it is, when the deck projection disagrees.

    The engine ledger owns where a plate sits, so a from_position that
    contradicts the projection corrects the projection rather than failing. The
    robot picks from the slot IT recorded at load time, so the correction has to
    reach the robot too: the labware is logically moved off deck and re-loads at
    the corrected slot on the next op that needs it. A destination the projection
    shows occupied is refused, since freeing the labware first would leave it
    parented nowhere and unaddressable for every later op.
    """
    deck = flex.deck
    if deck.get_slot(resource) == slot:
        return
    occupant = deck.get_resource_at_slot(slot)
    if occupant is not None and occupant is not resource:
        raise ValueError(
            f"Cannot place '{resource.name}' at {slot}: the deck projection has "
            f"'{occupant.name}' there. Reconcile deck occupancy before moving."
        )
    await flex.labware_moved_off_deck(resource)
    if resource.parent is not None:
        resource.unassign()
    deck.assign_child_at_slot(resource, slot)


class _MountedTip(NamedTuple):
    """The rack spot whose tip one channel of one head is carrying right now."""

    head: _AnyHead
    channel: int
    rack: str
    position: str


# Every object the Flex forwards commands to. The robot's own surface is bare;
# the gripper and the heads are prefixed, because they all define `move_to` and
# one wire name cannot mean three things. A mount is declared once per head it
# can hold, since which one is fitted is known only after the robot answers and
# a driver advertises before it has connected. A 96-channel head is never on a
# mount: it lands on `head96`.
_FLEX_VENDOR_SURFACES: tuple[VendorSurface, ...] = (
    VendorSurface(path="_flex", type=OpentronsFlex),
    VendorSurface(path="_flex.gripper", type=FlexGripper, prefix="gripper"),
    VendorSurface(path="_flex.head96", type=FlexHead96, prefix="head96"),
) + tuple(
    VendorSurface(path=f"_flex.{mount}", type=head, prefix=mount)
    for mount in ("left", "right")
    for head in (FlexHead1, FlexHead8)
)


class FlexLiquidHandlerDriver(
    VendorSurfaceForwarding,
    InterruptedMoveTracking,
    ILiquidHandlerDriver,
    ILiquidProbeDriver,
    IPipetteMotionDriver,
    IGripperMotionDriver,
    IForceGripperJawDriver,
    IGantryParkingDriver,
    IHomeableDriver,
):
    """Opentrons Flex liquid handler driven through ``pylabrobot.opentrons.OpentronsFlex``.

    Declares IGripperMotion but not IGripperPosition, and IForceGripperJaw but
    not IWidthGripperJaw: the Flex gripper places absolutely with no position
    feedback and closes to a force with no width command. Inheritance shadows
    ``interfaces`` rather than unioning it, so the full set is restated here.
    """

    interfaces: ClassVar[frozenset[str]] = frozenset(
        {
            "ILiquidHandler",
            "ILiquidProbe",
            "IPipetteMotion",
            "IGripperMotion",
            "IForceGripperJaw",
            "IGantryParking",
            "IHomeable",
        }
    )

    provides_state: ClassVar[bool] = True

    vendor_surfaces: ClassVar[tuple[VendorSurface, ...]] = _FLEX_VENDOR_SURFACES

    def __init__(
        self,
        host: str,
        port: int = 31950,
        transport: OpentronsTransport | None = None,
        sim_server: SimServerLifecycle | None = None,
        visualize: bool = False,
        wire_timeout: WireTimeout | None = None,
    ) -> None:
        """Args:
        host: the robot's address.
        port: the robot-server's port.
        transport: wire transport to use instead of a real one, for offline runs.
        sim_server: a device-owned simulator to bring up before first contact.
        visualize: open PyLabRobot's visualizer alongside the run.
        wire_timeout: how long to wait on the link before giving up. Defaults to
            a budget that outlasts every command this driver declares, so the
            engine's abort fires first. Raise it for a slow or lossy link; one
            that would give up sooner is refused. An injected ``transport``
            carries its own budget, so it takes this one's place on the wire.
        """
        self._host = host
        self._port = port
        self._transport = transport
        self._sim_server = sim_server
        self._visualize = visualize
        self._visualizer: Visualizer | None = None
        self.wire_timeout = resolve_wire_timeout(wire_timeout, type(self), poll_interval=0.2)
        # Built here, not in configure_deck: reaching a robot must not require
        # declaring its deck first. configure_deck attaches the real one.
        self._flex: OpentronsFlex | None = OpentronsFlex(
            deck=FlexDeck(),
            host=host,
            port=port,
            transport=transport or self._budgeted_transport(host, port),
            command_timeout=self.wire_timeout.seconds,
            status_poll_interval=self.wire_timeout.poll_interval_seconds,
        )
        self._is_initialized = False
        self._deck_configured = False
        self._sim_server_started = False
        # A Flex head reports the tips it holds but not the spots they came from,
        # so the driver remembers that itself, per channel.
        self._mounted_tips: list[_MountedTip] = []

    def _budgeted_transport(self, host: str, port: int) -> OpentronsTransport:
        """The wire this driver talks over, carrying the request half of its budget.

        The transport is the only place a request budget can be set: the robot builds
        an unbudgeted one otherwise, and nothing above the backend can reach the client
        it made. It bounds one request/response; the enqueue-and-poll round a command
        waits out is bounded separately, by the robot's own command budget.
        """
        return HttpxTransport(
            base_url=f"http://{host}:{port}", timeout=self.wire_timeout.seconds
        )

    # --- Lifecycle ---

    @command_timing(typical=40.0, max=300.0)
    async def initialize(self) -> None:
        """Bring the robot up: link, run, heads. THE GANTRY DOES NOT MOVE.

        Homing is ``home``, kept apart on purpose: a home sweep drives the gantry
        through whatever the head is holding, and nothing about discovering what
        is mounted needs the robot moved. PLR's compound ``setup`` ends in that
        sweep, so this composes the granular verbs instead of calling it.

        The tip-presence read this ends on drives the tip motors down on a
        96-channel head, which is inside the pipette and reaches nothing on the
        deck.

        After an explicit ``connect`` the link and run already exist, so only
        head discovery remains.

        Holding a run is only trusted after verifying the robot still holds OUR
        session. An operator recovering the robot at the instrument stops our
        run, and local state that outlives the session it describes turns every
        later initialize into a silent no-op (flag set) or a doomed retry loop
        (connected but not yet initialized) against a robot with no run at all
        -- the wedge behind the 2026-08-21 incident.
        """
        flex = self._require_flex("initialize")
        if flex.run_id is not None and not await flex.run_is_live():
            # The session died under us: forget the dead run so connect can
            # take a fresh one, then rebuild from the top.
            await flex.cancel_run()
            self._is_initialized = False
        if self._is_initialized:
            return
        await self.connect()
        await flex.initialize()
        self._drop_stale_mounted_tips(flex)
        await self._refuse_if_a_tip_is_stranded(flex)
        self._is_initialized = True

    async def _refuse_if_a_tip_is_stranded(self, flex: OpentronsFlex) -> None:
        """Refuse to come up while a mount holds a tip the driver cannot account for.

        Freshly composed heads track no tips, so a sensor that reads present is
        a tip left on the nozzle by whatever happened before us. Reporting
        initialized over that state is the collision: the next pick_up_tips
        checks the model, sees an empty nozzle, and drives a tipped one into a
        rack spot. Nothing can say which channel or which tip, so the only
        answer is the operator's, and refusing is what puts the question in
        front of them. discard_stranded_tips resolves it without initialize.
        """
        mounts, requires_intervention = await self._reconcile_mounts(flex)
        if requires_intervention:
            raise RuntimeError(
                f"{_reconcile_message(False, mounts)}. Run discard_stranded_tips (or clear "
                f"the tip at the instrument), then initialize again."
            )

    async def _start_sim_server(self) -> None:
        """Bring a device-owned simulator up before the first device contact.

        Guarded because connect and initialize are both entry points now, and
        either can be the first one a caller uses.
        """
        if self._sim_server is not None and not self._sim_server_started:
            await self._sim_server.start()
            self._sim_server_started = True

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    @property
    def is_connected(self) -> bool:
        """Whether this robot is ours: linked AND holding the run.

        Read off the vendor object rather than mirrored in a flag. A shadow copy
        of state PyLabRobot already owns is what let "the socket is open" and
        "the run is mine" drift apart, which showed up as a robot reporting
        itself released while its touchscreen stayed locked.
        """
        return self._flex is not None and self._flex.run_id is not None

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        # A per-operation device command (fired around deck access), not driver
        # disposal, so it must not stop the sim server. Disposal is `_shutdown`.
        pass

    @command_timing(typical=10.0, max=180.0)
    async def connect(self) -> None:
        """Open the link and take a run, so the robot accepts commands.

        Needs no deck: reaching a robot is not a question about what is on it.

        Does nothing when this robot is already ours. Several surfaces drive the
        same one, and re-linking a live robot swaps in a fresh socket and throws
        away the labware the old session had cached. PyLabRobot cancels the run
        it holds before taking another, so the cost is the dropped session, not
        a run stranded on the robot.

        Takes far longer than the interface's default allows: under DEVICE_SIM
        this is what starts the robot's own simulator, which alone budgets 90s
        to answer.
        """
        if self.is_connected:
            return
        flex = self._require_flex("connect")
        await self._start_sim_server()
        await flex.connect()
        # Gated on holding the run, not on having tried: a connect that reaches
        # the robot but cannot take the run (the touchscreen already has one
        # open) must be retryable, or the device is wedged with every later
        # command failing "no active run".
        await flex.create_run()

    async def disconnect(self) -> None:
        """Hand the robot back: ends the run, drops the link, moves nothing.

        Keeps the driver usable. A later connect opens a fresh link on the same
        object and the configured deck survives, so handing a robot back does
        not cost the deck description.
        """
        if self._flex is None:
            # Already disposed: nothing to hand back, and is_connected reads
            # False off the missing robot on its own.
            self._is_initialized = False
            return
        try:
            await self._flex.disconnect()
        finally:
            self._is_initialized = False

    @external
    @command_timing(typical=15.0, max=90.0)
    async def home(self, request: HomeRequest) -> None:
        """Park the gantry at its home position. MOVES the robot.

        Bring-up leaves the axes wherever power-off left them, so this is what
        makes the first move of the day safe.
        """
        await self._require_flex("home").home()

    @external
    @command_timing(typical=2.0, max=15.0)
    async def cancel_run(self) -> None:
        """Give the robot its own touchscreen back, staying connected.

        The robot refuses local control while a run is current, so an operator
        who wants to drive it by hand ends the run and leaves the link up.
        Commands from here need a fresh run, which `initialize` opens.
        """
        if self._flex is None:
            return
        await self._flex.cancel_run()
        self._is_initialized = False

    async def _shutdown(self) -> None:
        # Release, don't tear down: `stop()` homes on its way out, so disposal
        # would move a live robot just because a process exited.
        try:
            if self._flex is not None:
                await self._flex.disconnect()
                self._flex = None
                self._is_initialized = False
                self._mounted_tips = []
        finally:
            if self._sim_server is not None:
                self._sim_server.stop()

    def _record_pick(self, head: _AnyHead, rack: str, spots_by_channel: dict[int, str]) -> None:
        """Remember the spot each channel took its tip from.

        A pick addresses spots but only mounts the tips that were there, so the
        spots that gave up nothing are not recorded as carried.
        """
        mounted = head.get_mounted_tips()
        taken = {
            channel: spot
            for channel, spot in spots_by_channel.items()
            if mounted[channel] is not None
        }
        self._mounted_tips = [
            held
            for held in self._mounted_tips
            if held.head is not head or held.channel not in taken
        ]
        self._mounted_tips.extend(
            _MountedTip(head, channel, rack, spot) for channel, spot in sorted(taken.items())
        )

    def _forget_pick(self, head: _AnyHead) -> None:
        """One Flex drop empties every nozzle of a head, so it forgets all of them."""
        self._mounted_tips = [held for held in self._mounted_tips if held.head is not head]

    def _drop_stale_mounted_tips(self, flex: OpentronsFlex) -> None:
        """Forget tips recorded against heads a re-discovery has replaced.

        Head discovery composes NEW head objects, so entries keyed by the old
        ones can never match a real head again yet still count as carried in
        _spots_a_head_carries, quietly excluding rack spots forever.
        """
        live = [head for _, head in _named_heads(flex)]
        self._mounted_tips = [
            held for held in self._mounted_tips if any(held.head is head for head in live)
        ]

    def _racks_the_tips_came_from(self, head: _AnyHead) -> list[str]:
        return sorted({held.rack for held in self._mounted_tips if held.head is head})

    def _head_carrying(
        self, flex: OpentronsFlex, rack: str, positions: list[str]
    ) -> _MountHead | None:
        """The one mount head whose tips came from these spots, if exactly one did."""
        wanted = set(positions)
        holders: list[_MountHead] = []
        for _, head in _mount_heads(flex):
            spots = {
                held.position
                for held in self._mounted_tips
                if held.head is head and held.rack == rack
            }
            if spots and spots <= wanted:
                holders.append(head)
        return holders[0] if len(holders) == 1 else None

    def _spots_a_head_carries(self) -> dict[str, set[str]]:
        """The spots each rack gave up to a head that is still holding them."""
        carried: dict[str, set[str]] = {}
        for held in self._mounted_tips:
            carried.setdefault(held.rack, set()).add(held.position)
        return carried

    def _require_flex(self, operation: str) -> OpentronsFlex:
        """The robot object, which exists from construction until disposal."""
        if self._flex is None:
            raise RuntimeError(f"{operation} called on a disposed LiquidHandler")
        return self._flex

    def _require_deck(self, operation: str) -> OpentronsFlex:
        """The robot, refusing ops that address deck contents before a deck exists."""
        if not self._deck_configured:
            raise RuntimeError(
                f"{operation} called before configure_deck; declare a deck_layout "
                f"for this LiquidHandler"
            )
        return self._require_flex(operation)

    # --- Deck management ---

    async def configure_deck(self, config: DeckLayoutConfig) -> LabwareStateResponse:
        if config.deck_type != FLEX_DECK_TYPE:
            raise ValueError(
                f"{type(self).__name__} drives an Opentrons Flex, so its deck_type must be "
                f"'{FLEX_DECK_TYPE}', not '{config.deck_type}'."
            )
        if config.resources:
            names = ", ".join(repr(res.name) for res in config.resources)
            raise ValueError(
                f"Flex deck layout declares resources ({names}). A layout declares carriers, which "
                f"mount on deck rails; a FlexDeck has neither, so its labware sits directly in "
                f"deck slots and arrives from the ledger via reconcile_deck_occupancy / "
                f"add_deck_labware. A Flex layout declares an empty resources list."
            )

        deck = FlexDeck()
        flex = self._require_flex("configure_deck")
        await self._release_before_deck_swap(flex)
        flex.attach_deck(deck)
        self._deck_configured = True
        # Attaching a deck swaps the deck, never the heads, so tips on a nozzle are
        # still on it. A head that a rediscovery does replace is dropped elsewhere.
        self._resolve_interrupted_move()

        # PLR gates tip and volume tracking behind module globals that default
        # OFF; without them the heads move no state for us to report.
        set_volume_tracking(True)
        set_tip_tracking(True)

        if self._visualize:
            self._visualizer = await start_deck_visualizer(deck)

        if config.labware_state is not None:
            _apply_labware_state(deck, config.labware_state)

        return LabwareStateResponse(success=True, labware_state=derive_labware_state(deck))

    async def get_deck_state(self, request: GetDeckStateRequest) -> DeckStateResponse:
        if not self._deck_configured or self._flex is None:
            # No heads are composed yet, so there are no channels to report on.
            return DeckStateResponse(tips_mounted=[])

        mount_tips = [
            tip is not None
            for _, head in _mount_heads(self._flex)
            for tip in head.get_mounted_tips()
        ]
        head96 = self._flex.head96
        labware: list[DeckResourceState] = []
        tip_racks: list[TipRackState] = []
        for slot, occupant in _slot_occupants(self._flex.deck):
            labware.append(
                DeckResourceState(
                    name=occupant.name,
                    type=occupant.__class__.__name__,
                    category=occupant.category,
                    site=f"{slot}-slot",
                )
            )
            if isinstance(occupant, PLRTipRack):
                tip_racks.append(
                    TipRackState(
                        name=occupant.name,
                        tips_remaining=sum(1 for s in occupant.get_all_items() if s.has_tip()),
                        total_tips=occupant.num_items,
                    )
                )

        return DeckStateResponse(
            # One entry per mount channel, indexed as get_head_configuration numbers
            # them, so a two-mount Flex reports 9 and a lone 1-channel mount reports 1.
            tips_mounted=mount_tips,
            tips_mounted_96=head96 is not None
            and any(tip is not None for tip in head96.get_mounted_tips()),
            labware=labware,
            tip_racks=tip_racks,
            interrupted_move=self._interrupted_move,
        )

    async def get_head_configuration(
        self, request: GetHeadConfigurationRequest
    ) -> HeadConfigurationResponse:
        """Report the mounted nozzles and which of them share a plunger.

        Every Opentrons pipette has exactly one plunger, so each composed head is
        one ganged group; channels are numbered across mounts, left first.
        """
        if self._flex is None:
            return HeadConfigurationResponse(groups=[])

        groups: list[NozzleGroup] = []
        next_channel = 0
        for head in (self._flex.left, self._flex.right, self._flex.head96):
            if head is None:
                continue
            groups.append(
                NozzleGroup(
                    channels=list(range(next_channel, next_channel + head.channels)),
                    max_volume_ul=head.max_volume,
                    pipette_model=head.pipette_model,
                )
            )
            next_channel += head.channels
        return HeadConfigurationResponse(groups=groups)

    async def _clear_occupancy(self, flex: OpentronsFlex) -> None:
        """Free every slot of materialized labware, deck structure left in place.

        Wipes the staging pads too, though state reporting hides them: a panic
        clear must leave no occupancy behind, on any slot the gripper can reach.
        ``labware_moved_off_deck`` frees the slot server-side as well, so a later
        placement into it is not rejected as occupied.
        """
        for occupant in _movable_occupants(flex.deck):
            await flex.labware_moved_off_deck(occupant)
        # A wipe that finished answers where an interrupted move's labware ended
        # up. One that raised part-way answered nothing, so the record survives.
        self._resolve_interrupted_move()

    async def _release_before_deck_swap(self, flex: OpentronsFlex) -> None:
        """Hand back what the robot still holds, before the deck under it is replaced.

        Attaching a deck drops PyLabRobot's labware ids, and those ids are the
        only handle on labware the run has loaded. Whatever is left loaded
        becomes unreachable: the next operation on it re-loads, the robot
        refuses the slot as already occupied, and no driver call can free it
        because freeing needs the id that just went. Labware loads are
        run-scoped, so a dead run holds nothing worth freeing, and a deck with
        no movable occupants costs no wire read at all.
        """
        if not _movable_occupants(flex.deck) or flex.run_id is None:
            return
        if not await flex.run_is_live():
            return
        await self._clear_occupancy(flex)

    async def reset_deck_labware(self, request: ResetDeckLabwareRequest) -> LabwareStateResponse:
        if not self._deck_configured or self._flex is None:
            return LabwareStateResponse(success=True)
        await self._clear_occupancy(self._flex)
        return LabwareStateResponse(
            success=True, labware_state=derive_labware_state(self._flex.deck)
        )

    async def reconcile_deck_occupancy(
        self, request: ReconcileDeckOccupancyRequest
    ) -> LabwareStateResponse:
        if not self._deck_configured or self._flex is None:
            return LabwareStateResponse(success=True)
        # Pre-resolve before wiping: an unknown catalog_ref fails with the deck
        # intact. Placement after the wipe is the residual tear risk (re-issue to fix).
        created = [(_create_resource(config), config) for config in request.resources]
        await self._clear_occupancy(self._flex)
        for resource, config in created:
            self._place(resource, config)
            swap_to_lenient(resource)
            if config.well_state is not None:
                _apply_labware_state(self._flex.deck, {config.name: config.well_state})
            empty_the_spots_a_head_carries(resource, self._spots_a_head_carries())
        return LabwareStateResponse(
            success=True, labware_state=derive_labware_state(self._flex.deck)
        )

    # Both budgets cover a dead-session rebuild, which is the same head
    # re-discovery `initialize` above budgets 300s for.
    @command_timing(typical=10.0, max=300.0)
    async def reconcile_hardware_state(
        self, request: ReconcileHardwareStateRequest
    ) -> ReconcileHardwareStateResponse:
        if self._flex is None:
            return ReconcileHardwareStateResponse(
                checked=False, message="driver disposed: no robot to reconcile against."
            )
        flex = self._flex
        if flex.run_id is None:
            return ReconcileHardwareStateResponse(
                checked=False,
                message="not connected: nothing held to verify. connect and initialize first.",
            )
        session_recovered = False
        if not await flex.run_is_live():
            await self._rebuild_session(flex)
            session_recovered = True
        mounts, requires_intervention = await self._reconcile_mounts(flex)
        if not mounts:
            # Connected but no heads composed: no sensor exists to read yet, and
            # an all-clear here would hide a seated tip. Say so instead.
            return ReconcileHardwareStateResponse(
                checked=False,
                session_recovered=session_recovered,
                message="no heads are composed, so no tip sensor was read; initialize first.",
            )
        return ReconcileHardwareStateResponse(
            # A mount whose sensor answered neither present nor absent was not
            # verified; claiming otherwise reads as an all-clear it is not.
            checked=any(m.outcome != "unverified" for m in mounts),
            session_recovered=session_recovered,
            mounts=mounts,
            requires_intervention=requires_intervention,
            message=_reconcile_message(session_recovered, mounts),
        )

    @command_timing(typical=30.0, max=300.0)
    async def discard_stranded_tips(
        self, request: DiscardStrandedTipsRequest
    ) -> ReconcileHardwareStateResponse:
        # Not _require_deck: the constructor deck already carries the trash, and
        # the escape must not need configure_deck when the report naming it does not.
        flex = self._require_flex("discard_stranded_tips")
        if flex.run_id is None:
            raise RuntimeError(
                "discard_stranded_tips called before connect; connect and initialize first"
            )
        if not await flex.run_is_live():
            await self._rebuild_session(flex)
        trash = flex.deck.get_trash_area()
        for _, head in _named_heads(flex):
            tracked = any(tip is not None for tip in head.get_mounted_tips())
            if not tracked and await head.has_tip_on_hardware() is True:
                await head.unsafe_discard_tips(trash)
                self._forget_pick(head)
        return await self.reconcile_hardware_state(ReconcileHardwareStateRequest())

    async def _rebuild_session(self, flex: OpentronsFlex) -> None:
        """Take a fresh instrument session after ours died out from under us. No motion.

        create_run clears the run-scoped labware caches (they reload lazily on
        next use) and head discovery composes fresh heads with empty tip
        bookkeeping, so what the dead session carried is answered by the sensor
        pass that follows, never assumed. Homing is deliberately absent: the
        robot's own recovery homes, an unhomed axis fails its next motion
        loudly, and home is a separate operator command.
        """
        # State drops FIRST: a rebuild failing partway must leave the driver
        # reporting uninitialized, not a set flag over zero composed heads.
        self._is_initialized = False
        self._mounted_tips = []
        await flex.cancel_run()
        await flex.create_run()
        await flex.initialize()
        self._is_initialized = True

    async def _reconcile_mounts(
        self, flex: OpentronsFlex
    ) -> tuple[list[MountTipReconcile], bool]:
        """One sensor read per composed head, repairing only what is definitive."""
        self._drop_stale_mounted_tips(flex)
        mounts: list[MountTipReconcile] = []
        requires_intervention = False
        for label, head in _named_heads(flex):
            tracked = sum(1 for tip in head.get_mounted_tips() if tip is not None)
            outcome = await head.reconcile_tips_with_hardware()
            sensor: Literal["present", "absent", "unknown"]
            if outcome == RECONCILE_UNVERIFIED:
                sensor = "unknown"
            elif outcome == RECONCILE_UNTRACKED_TIP:
                sensor = "present"
                requires_intervention = True
            elif outcome == RECONCILE_CLEARED_LOST_TIPS:
                sensor = "absent"
                self._forget_pick(head)
            else:
                sensor = "present" if tracked else "absent"
            mounts.append(
                MountTipReconcile(
                    mount=label, sensor=sensor, tracked_tips=tracked, outcome=outcome
                )
            )
        return mounts, requires_intervention

    def _place(self, resource: PLRResource, config: DeckResourceConfig) -> None:
        """Put a resource in the deck slot its occupancy entry names."""
        if config.parent_id is None:
            raise ValueError(
                f"Deck occupancy entry '{config.name}' names no slot; a Flex resource mounts "
                f"in a deck slot (parent_id='C2'), never on a rail."
            )
        flex = self._require_deck("_place")
        flex.deck.assign_child_at_slot(resource, config.parent_id)

    async def add_deck_labware(self, request: AddDeckLabwareRequest) -> None:
        self._resolve_interrupted_move(request.name)
        flex = self._require_deck("add_deck_labware")
        slot_key = _require_slot(request.at, "add_deck_labware.at")
        config = DeckResourceConfig(
            name=request.name, catalog_ref=request.catalog_ref, parent_id=slot_key, site_index=0
        )
        resource = _create_resource(config)
        self._place(resource, config)
        swap_to_lenient(resource)
        if request.well_state is not None:
            _apply_labware_state(flex.deck, {request.name: request.well_state})
        empty_the_spots_a_head_carries(resource, self._spots_a_head_carries())

    async def remove_deck_labware(self, request: RemoveDeckLabwareRequest) -> None:
        self._resolve_interrupted_move(request.name)
        flex = self._require_deck("remove_deck_labware")
        resource = flex.deck.get_resource(request.name)
        if not isinstance(resource, MOVABLE_LABWARE) or isinstance(resource, PLRTrash):
            raise TypeError(
                f"remove_deck_labware target '{request.name}' is not materialized labware "
                f"(plate, tip rack, or trough), got {type(resource).__name__}; carriers and "
                f"deck structure are not removable"
            )
        await flex.labware_moved_off_deck(resource)

    # --- 8-channel operations ---

    def _state(self, flex: OpentronsFlex) -> LabwareStateResponse:
        """The post-op labware state. Read only once the head op has committed its trackers."""
        return LabwareStateResponse(success=True, labware_state=derive_labware_state(flex.deck))

    async def aspirate(self, request: AspirateRequest) -> LabwareStateResponse:
        flex = self._require_deck("aspirate")
        target = _only_slice(request.aspirations, "aspirate")
        parameters = request.parameters
        head = _resolve_head(
            flex,
            target.labware,
            target.positions,
            len(target.volumes),
            request.use_channels,
            "aspirate",
        )
        seam = _liquid_seam(flex, head, target.labware, target.positions)
        volume = _one_value(target.volumes, "volume", target.labware)
        flow_rate = _one_per_command(
            request.flow_rates, parameters.flow_rate, "flow rate", target.labware
        )
        height = _pipetting_height(
            _one_per_command(
                request.offsets_z, parameters.height, "z offset", target.labware
            )
        )
        # Last check before the first wire command: every impossible request shape is
        # already refused above, so what remains is the state of the machine.
        _require_mounted_tips(head, "aspirate")
        if parameters.mix is not None:
            await _run_mix_cycles(
                seam,
                parameters.mix.volume,
                parameters.mix.repetitions,
                parameters.mix.flow_rate,
                height,
            )
        await seam.aspirate(volume, flow_rate, height)
        return self._state(flex)

    async def dispense(self, request: DispenseRequest) -> LabwareStateResponse:
        flex = self._require_deck("dispense")
        parameters = request.parameters
        target = _only_slice(request.dispenses, "dispense")
        head = _resolve_head(
            flex,
            target.labware,
            target.positions,
            len(target.volumes),
            request.use_channels,
            "dispense",
        )
        seam = _liquid_seam(flex, head, target.labware, target.positions)
        volume = _one_value(target.volumes, "volume", target.labware)
        flow_rate = _one_per_command(
            request.flow_rates, parameters.flow_rate, "flow rate", target.labware
        )
        height = _pipetting_height(
            _one_per_command(
                request.offsets_z, parameters.height, "z offset", target.labware
            )
        )
        _require_mounted_tips(head, "dispense")
        await seam.dispense(volume, flow_rate, height)
        if parameters.mix is not None:
            await _run_mix_cycles(
                seam,
                parameters.mix.volume,
                parameters.mix.repetitions,
                parameters.mix.flow_rate,
                height,
            )
        if parameters.blow_out:
            # blow_out_volume goes unread: one plunger position, so the air is whatever
            # it gives. Refusing it would leave no setting valid on every head.
            await seam.head.blow_out(flow_rate=parameters.blow_out_flow_rate)
        return self._state(flex)

    async def pick_up_tips(self, request: PickUpTipsRequest) -> LabwareStateResponse:
        flex = self._require_deck("pick_up_tips")
        pick = _only_tip_slice(request.picks, "pick_up_tips")
        head = _resolve_head(
            flex,
            pick.tip_rack,
            pick.positions,
            len(pick.positions),
            request.use_channels,
            "pick_up_tips",
        )
        rack = _require_tip_rack(flex, pick.tip_rack)
        with _undo_partial_staging(rack):
            if isinstance(head, FlexHead1):
                spots: dict[int, str] = {0: pick.positions[0]}
                await head.pick_up_tips(rack.get_item(pick.positions[0]))
            elif len(pick.positions) == 1:
                # The tip rides the anchor nozzle's channel, not the spot's own row.
                held_before = [tip is not None for tip in head.get_mounted_tips()]
                await head.pick_up_single_tip(
                    rack,
                    pick.positions[0],
                    primary_nozzle=_cherry_pick_nozzle(flex, head, request.use_channels),
                )
                spots = {
                    channel: pick.positions[0]
                    for channel, tip in enumerate(head.get_mounted_tips())
                    if tip is not None and not held_before[channel]
                }
            else:
                spots = dict(enumerate(pick.positions))
                await head.pick_up_tips(
                    rack, column=_full_column(rack, pick.tip_rack, pick.positions)
                )
        self._record_pick(head, pick.tip_rack, spots)
        return self._state(flex)

    async def drop_tips(self, request: DropTipsRequest) -> LabwareStateResponse:
        flex = self._require_deck("drop_tips")
        if request.to_waste:
            await self._discard_to_waste(
                flex, _heads_holding_tips(flex, request.use_channels, "drop_tips")
            )
            return self._state(flex)
        if request.drops is None:
            raise ValueError("drop_tips: drops is required when to_waste=False.")
        drop = _only_tip_slice(request.drops, "drop_tips")
        head = _resolve_head(
            flex,
            drop.tip_rack,
            drop.positions,
            len(drop.positions),
            request.use_channels,
            "drop_tips",
            self._head_carrying(flex, drop.tip_rack, drop.positions),
        )
        _require_mounted_tips(head, "drop_tips")
        if len(drop.positions) == 1 and not isinstance(head, FlexHead1):
            raise ValueError(
                "drop_tips: the 8-channel head returns a whole column to a rack, never one spot. "
                "Send the column, or discard the tip to waste (to_waste=True)."
            )
        rack = _require_tip_rack(flex, drop.tip_rack)
        with _undo_partial_staging(rack):
            if isinstance(head, FlexHead1):
                await head.drop_tips(rack.get_item(drop.positions[0]))
            else:
                await head.drop_tips(rack, _full_column(rack, drop.tip_rack, drop.positions))
        self._forget_pick(head)
        return self._state(flex)

    async def discard_tips(self, request: DiscardTipsRequest) -> LabwareStateResponse:
        flex = self._require_deck("discard_tips")
        heads = _heads_holding_tips(flex, request.use_channels, "discard_tips")
        await self._discard_to_waste(flex, heads)
        return self._state(flex)

    async def _discard_to_waste(self, flex: OpentronsFlex, heads: list[_MountHead]) -> None:
        """Drop each head's tips into the deck's trash bin."""
        trash = flex.deck.get_trash_area()
        for head in heads:
            mounted = sum(1 for tip in head.get_mounted_tips() if tip is not None)
            if isinstance(head, FlexHead8) and mounted == 1:
                # A cherry-picked tip: this path also restores the ALL nozzle layout.
                await head.drop_single_tip(trash)
            else:
                await head.discard_tips(trash)
            self._forget_pick(head)

    async def mix(self, request: MixRequest) -> LabwareStateResponse:
        flex = self._require_deck("mix")
        # A container mix carries no positions, so its channel count rides on
        # use_channels (the request validator guarantees one is there).
        count = (
            len(request.positions)
            if request.positions is not None
            else len(request.use_channels or [])
        )
        head = _resolve_head(
            flex, request.labware, request.positions, count, request.use_channels, "mix"
        )
        seam = _liquid_seam(flex, head, request.labware, request.positions)
        _require_mounted_tips(head, "mix")
        await _run_mix_cycles(
            seam, request.volume, request.repetitions, request.parameters.flow_rate,
            _pipetting_height(request.parameters.height),
        )
        return self._state(flex)

    async def move_plate(self, request: MovePlateRequest) -> None:
        flex = self._require_deck("move_plate")
        gripper = self._require_gripper("move_plate")
        resource = flex.deck.get_resource(request.plate)
        to_slot = _require_slot(request.to_position, "move_plate.to_position")
        if request.from_position is not None:
            from_slot = _require_slot(request.from_position, "move_plate.from_position")
            await _reparent_to_slot(flex, resource, from_slot)
        async with self._tracking_gripper_move(
            request.plate, request.to_position, request.from_position,
        ):
            await gripper.move_labware(
                resource, to_slot, grip_distance_from_top=request.grip_distance_from_top
            )

    async def liquid_probe(self, request: LiquidProbeRequest) -> LiquidProbeResponse:
        flex = self._require_deck("liquid_probe")
        positions = request.positions
        count = len(positions) if positions is not None else len(request.use_channels or [])
        head = _resolve_head(
            flex, request.labware, positions, count, request.use_channels, "liquid_probe"
        )
        probe = _probe_call(flex, head, request.labware, positions)
        # Last check before the first wire command: the shape is settled above, so
        # what is left is the state of the machine.
        _require_mounted_tips(head, "liquid_probe")
        return LiquidProbeResponse(height=_liquid_height(flex, probe, await probe.read()))

    # --- 96-head operations ---

    async def aspirate96(self, request: Aspirate96Request) -> LabwareStateResponse:
        flex = self._require_deck("aspirate96")
        head = _require_head96(flex, "aspirate96")
        target = _head96_target(flex, request.labware)
        with _undo_partial_staging(target):
            await head.aspirate(
                target,
                request.volume,
                flow_rate=request.flow_rate,
                liquid_height=_pipetting_height(request.liquid_height),
            )
        return self._state(flex)

    async def dispense96(self, request: Dispense96Request) -> LabwareStateResponse:
        flex = self._require_deck("dispense96")
        head = _require_head96(flex, "dispense96")
        target = _head96_target(flex, request.labware)
        with _undo_partial_staging(target):
            await head.dispense(
                target,
                request.volume,
                flow_rate=request.flow_rate,
                liquid_height=_pipetting_height(request.liquid_height),
            )
        return self._state(flex)

    async def pick_up_tips96(self, request: PickUpTips96Request) -> LabwareStateResponse:
        flex = self._require_deck("pick_up_tips96")
        head = _require_head96(flex, "pick_up_tips96")
        rack = _require_tip_rack(flex, request.tip_rack)
        with _undo_partial_staging(rack):
            await head.pick_up_tips(rack)
        self._record_pick(
            head,
            request.tip_rack,
            dict(enumerate(spot.get_identifier() for spot in rack.get_all_items())),
        )
        return self._state(flex)

    async def drop_tips96(self, request: DropTips96Request) -> LabwareStateResponse:
        flex = self._require_deck("drop_tips96")
        head = _require_head96(flex, "drop_tips96")
        _require_mounted_tips(head, "drop_tips96")
        if request.to_waste:
            await head.drop_tips(flex.deck.get_trash_area96())
        else:
            if request.tip_rack is None:
                raise ValueError("drop_tips96: tip_rack is required when to_waste=False.")
            rack = _require_tip_rack(flex, request.tip_rack)
            with _undo_partial_staging(rack):
                await head.drop_tips(rack)
        self._forget_pick(head)
        return self._state(flex)

    async def return_tips96(self, request: ReturnTips96Request) -> LabwareStateResponse:
        flex = self._require_deck("return_tips96")
        head = _require_head96(flex, "return_tips96")
        origins = self._racks_the_tips_came_from(head)
        if not origins:
            raise ValueError(
                "return_tips96: no rack is recorded for the mounted tips. Pick them up with "
                "pick_up_tips96 first, or name the rack on drop_tips96."
            )
        if len(origins) > 1:
            raise ValueError(
                f"return_tips96: the mounted tips came from {origins}, and one drop returns them "
                f"all to one rack. Name the rack on drop_tips96."
            )
        try:
            rack = _require_tip_rack(flex, origins[0])
        except PLRResourceNotFoundError:
            raise ValueError(
                f"return_tips96: rack '{origins[0]}' the tips came from is no longer on the deck. "
                f"Put it back, or discard the tips to waste."
            ) from None
        with _undo_partial_staging(rack):
            await head.drop_tips(rack)
        self._forget_pick(head)
        return self._state(flex)

    # --- Motion ---

    def _head_for_channel(self, channel: int, operation: str) -> _AnyHead:
        """The head a channel index moves.

        The Flex positions a whole pipette by its rearmost (A-row) nozzle, so
        only that nozzle's channel names a motion; the others cannot move alone."""
        flex = self._require_flex(operation)
        for base, head in _head_channels(flex):
            if channel == base:
                return head
            if base < channel < base + head.channels:
                raise ValueError(
                    f"{operation}: the Flex moves a whole pipette, positioned by its rearmost "
                    f"(A-row) nozzle, so channel {base} moves this head; channel {channel} "
                    f"cannot move on its own."
                )
        raise ValueError(f"{operation}: no mounted head has channel {channel} "
                         f"({_describe_heads(flex)}).")

    def _require_gripper(self, operation: str) -> FlexGripper:
        """The Flex gripper, which is optional hardware."""
        flex = self._require_flex(operation)
        if flex.gripper is None:
            raise ValueError(
                f"{operation} needs the Flex gripper, and the robot reports none on its extension "
                f"mount."
            )
        return flex.gripper

    async def move_channel_to(self, request: MoveChannelToRequest) -> None:
        require_tip_end_datum(request.z_reference)
        head = self._head_for_channel(request.channel, "move_channel_to")
        await head.move_to(x=request.x, y=request.y, z=request.z)

    async def move_channel_relative(self, request: MoveChannelRelativeRequest) -> None:
        head = self._head_for_channel(request.channel, "move_channel_relative")
        current = await head.position()
        await head.move_to(
            x=current.x + (0.0 if request.dx is None else request.dx),
            y=current.y + (0.0 if request.dy is None else request.dy),
            z=current.z + (0.0 if request.dz is None else request.dz),
        )

    async def get_channel_position(self, request: GetChannelPositionRequest) -> ChannelPosition:
        require_tip_end_datum(request.z_reference)
        head = self._head_for_channel(request.channel, "get_channel_position")
        position = await head.position()
        return ChannelPosition(
            channel=request.channel,
            x=position.x,
            y=position.y,
            z=position.z,
            z_reference="tip_end",
        )

    def _refuse_while_mid_work(self, flex: OpentronsFlex, operation: str) -> None:
        """Refuse to travel while something is still hanging off the gantry.

        One gantry carries both pipette mounts and the gripper, so travelling
        drags whatever any of them is holding across the deck. Two things count
        and both have to be asked about: tips on a head, and a plate in the
        jaws. Only tips used to be, and a gripper move that stalled with a plate
        clamped in the jaws sailed through into a collision.
        """
        reason = _tips_refusal(flex)
        if reason is None and self._jaws_may_be_loaded:
            reason = (
                "the gripper jaws may still be holding something. Open them "
                "with release_jaw, then ask again."
            )
        if reason is not None:
            raise GantryBusyError(f"{operation}: {reason}")

    async def move_gripper_to(self, request: MoveGripperToRequest) -> None:
        flex = self._require_flex("move_gripper_to")
        self._refuse_while_mid_work(flex, "move_gripper_to")
        gripper = self._require_gripper("move_gripper_to")
        await _lift_everything_that_hangs(flex)
        await gripper.move_to(x=request.x, y=request.y, z=request.z)

    async def park_gantry(self, request: ParkGantryRequest) -> None:
        flex = self._require_flex("park_gantry")
        self._refuse_while_mid_work(flex, "park_gantry")
        # Everything up before anything travels. One gantry carries both pipette
        # mounts and the gripper, so a mount left down is dragged across the deck.
        await _lift_everything_that_hangs(flex)
        if request.at is not None:
            gripper = self._require_gripper("park_gantry")
            await gripper.move_to(x=request.at.x, y=request.at.y, z=request.at.z)
            return
        await flex.retract_axis("y")

    async def grip_with_force(self, request: GripWithForceRequest) -> None:
        gripper = self._require_gripper("grip_with_force")
        # Recorded before the call because the call is what closes the jaw: a
        # grip that dies part-way can leave it clamped on a plate, and nothing
        # reports whether it caught anything either way.
        self._jaws_may_be_loaded = True
        await gripper.grip(force=request.force)

    async def release_jaw(self, request: ReleaseJawRequest) -> None:
        """Open the jaw fully, dropping anything in it.

        The recovery after a gripper move died part-way: it is what puts the
        plate down so the gantry can travel again. Where the plate lands is
        wherever the gripper was, so this does not settle the interrupted-move
        record; someone still has to say where the labware ended up.
        """
        gripper = self._require_gripper("release_jaw")
        await gripper.open_jaw()
        self._note_jaws_opened()
