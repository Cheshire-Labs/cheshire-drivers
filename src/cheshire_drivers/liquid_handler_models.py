"""Pydantic models for liquid handler driver interface.

These models define the serialization boundary for all liquid handler operations.
Used by ILiquidHandlerDriver methods. Serializable for wire transport (WebSocket,
REST, etc.) while also usable as plain Python objects for in-process calls.

All models reject unknown fields (`extra='forbid'`). Silent-drop is a wire bug we
do not tolerate.

8-channel atomic ops carry a list of per-labware target slices so a single
physical pickup / aspirate / dispense can span multiple racks or plates. PLR
already supports this (one ``pick_up_tips`` / ``aspirate`` / ``dispense`` call
with TipSpots / Wells from arbitrary racks / plates); the wire shape exposes
the same capability.
"""

from math import isclose
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator
from typing_extensions import Self

from cheshire_drivers.layered_parameters import merge_patch_over


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Volumes this close are the same command, not a ganging violation: callers splitting a total
# across channels by ratio land femtolitres apart, far below any pipette's resolution.
VOLUME_TOLERANCE_UL = 1e-6


# Canonical single-pool key: a trough/tube tracks volume as one labware with this
# one pseudo-well, shared so seed, driver-state report, and interpreter agree.
TROUGH_WELL_ID = "A1"


class MixParamsModel(_StrictModel):
    volume: float
    repetitions: int
    flow_rate: float | None = None


# --- Pipetting parameters ---


class PipettingParameters(_StrictModel):
    """Resolved parameters for one pipetting step. Total: every field present.

    A field that is None is still an answer: it means "whatever the driver does
    when nobody says", which is a backend's own flow rate or its own bottom
    clearance, and differs per machine.
    """

    height: float | None = None
    """Tip end height above the inner floor of the well, in mm. None leaves the
    driver's own bottom clearance, which differs per machine."""
    flow_rate: float | None = None
    """uL/s. None leaves the backend's own rate for the mounted pipette."""
    blow_out: bool = False
    """Push the plunger past its stop at the end of a dispense."""
    blow_out_volume: float | None = None
    """uL of air to expel, and the setting that works everywhere.

    Heads blow out in two different ways. One expels air the tip took in at
    aspirate time, so it needs the volume named and refuses a blow-out without
    one. The other drives its plunger to a fixed blow-out position and expels
    whatever that gives, leaving this unread. Naming a volume satisfies both."""
    blow_out_flow_rate: float | None = None
    """uL/s for the blow-out, which the same head can still honor."""
    mix: MixParamsModel | None = None
    """Cycles run at the same target, before an aspirate or after a dispense."""


class PipettingPatch(_StrictModel):
    """One layer's contribution to a step. Every field optional; None inherits.

    The same shape carries the liquid and the step because a parameter gets set
    for two different reasons and only one of them is about the liquid: glycerol
    is drawn slowly wherever it goes, and a last top-up is dispensed slowly
    whatever is in it. Splitting the fields between two objects would make each
    of those an argument, so instead the layers differ by what they are keyed on
    and the step wins.
    """

    height: float | None = None
    flow_rate: float | None = None
    blow_out: bool | None = None
    blow_out_volume: float | None = None
    blow_out_flow_rate: float | None = None
    mix: MixParamsModel | None = None

    def apply_to(self, parameters: PipettingParameters) -> PipettingParameters:
        """This layer merged over an already-resolved record. See
        `merge_patch_over` for what a None field means."""
        return merge_patch_over(self, parameters)


SEED_PIPETTING_PARAMETERS = PipettingParameters()
"""What a deployment's editable defaults start out as, the same way the built-in
move parameters are seeded.

Every field is what a driver already did when a command named nothing, so
seeding from this changes nothing an unconfigured deployment did. `height` and
`flow_rate` stay None on purpose: the right bottom clearance differs per
machine, and pinning a number here would raise a Hamilton's tip by the
Opentrons Flex's 3 mm. They are a starting point and not a fallback: once
seeded, the stored row is the answer.
"""


class LabwareWellState(_StrictModel):
    """Per-well volumes and/or per-tip presence for one labware. A sparse
    override: only the named wells/positions are applied; the rest keep their
    placement defaults (lenient volume tracker / factory tip layout)."""

    volumes: dict[str, float] | None = None
    tips: dict[str, bool] | None = None
    replenished: bool = False
    """Reagent source: seed `volumes` for display but keep a non-depleting
    tracker in sim, so aspirates never raise on an empty container. Sim-only
    (no tracker runs on real hardware). Applies to every well/container in
    `volumes`."""


# --- 8-channel target slices ---

class TipPick(_StrictModel):
    """One rack's slice of an 8-channel pickup or drop. Each entry contributes
    ``len(positions)`` channels to the operation in target order."""
    tip_rack: str
    positions: list[str]


class AspirateTarget(_StrictModel):
    """One labware's slice of an 8-channel aspirate. Each entry contributes
    ``len(volumes)`` channels. For an itemized labware (plate) ``positions``
    carries one well id per channel; for a single container (trough/tube)
    ``positions`` is None and every channel draws from the one container.
    Per-channel ``flow_rates`` and ``offsets_z`` on the parent request are
    concatenated across slices in target order."""
    labware: str
    positions: list[str] | None = None
    volumes: list[float]

    @model_validator(mode="after")
    def _positions_match_volumes(self) -> Self:
        if self.positions is not None and len(self.positions) != len(self.volumes):
            raise ValueError(
                f"AspirateTarget '{self.labware}': positions ({len(self.positions)}) "
                f"and volumes ({len(self.volumes)}) must be the same length"
            )
        return self


class DispenseTarget(_StrictModel):
    """One labware's slice of an 8-channel dispense. See ``AspirateTarget``."""
    labware: str
    positions: list[str] | None = None
    volumes: list[float]

    @model_validator(mode="after")
    def _positions_match_volumes(self) -> Self:
        if self.positions is not None and len(self.positions) != len(self.volumes):
            raise ValueError(
                f"DispenseTarget '{self.labware}': positions ({len(self.positions)}) "
                f"and volumes ({len(self.volumes)}) must be the same length"
            )
        return self


# --- 8-channel request models ---

class AspirateRequest(_StrictModel):
    aspirations: list[AspirateTarget]
    flow_rates: list[float] | None = None
    offsets_z: list[float] | None = None
    """Per-channel pipetting height in mm: the END OF THE TIP sits this far above the
    inner floor of the well (or container cavity). Tip length is the robot's to add.
    Omit for the backend's default bottom clearance."""
    use_channels: list[int] | None = None
    parameters: PipettingParameters = PipettingParameters()
    """What this aspirate runs under, already resolved. The layers that narrowed
    it (the deployment's defaults, the liquid, the step) are folded before the
    wire, so nothing down here chooses between them.

    One record travels with a liquid across every call it takes part in, and
    across machines that differ, so each command and each driver reads the fields
    that apply to it. An aspirate reads ``height``, ``flow_rate`` and ``mix``;
    the blow-out fields belong to the dispense that follows, except for the air
    an aspirate has to take in to have something to blow out later. A field the
    hardware cannot honour goes unread rather than refusing the call, which is
    what keeps one profile usable on a Flex and a Hamilton both. What IS refused
    is a request the machine cannot perform at all, such as asking a
    volume-driven blow-out for no volume. ``flow_rates`` and ``offsets_z`` above
    still override this per channel, which is the one place a driver picks
    between two sources."""

    @model_validator(mode="after")
    def _check_per_channel_lengths(self) -> Self:
        if not self.aspirations:
            raise ValueError("AspirateRequest.aspirations must not be empty")
        total = sum(len(a.volumes) for a in self.aspirations)
        if self.flow_rates is not None and len(self.flow_rates) != total:
            raise ValueError(
                f"AspirateRequest.flow_rates length ({len(self.flow_rates)}) "
                f"must equal total channels across aspirations ({total})"
            )
        if self.offsets_z is not None and len(self.offsets_z) != total:
            raise ValueError(
                f"AspirateRequest.offsets_z length ({len(self.offsets_z)}) "
                f"must equal total channels across aspirations ({total})"
            )
        return self


class DispenseRequest(_StrictModel):
    dispenses: list[DispenseTarget]
    flow_rates: list[float] | None = None
    offsets_z: list[float] | None = None
    """Per-channel pipetting height in mm, same frame as `AspirateRequest.offsets_z`."""
    use_channels: list[int] | None = None
    parameters: PipettingParameters = PipettingParameters()
    """What this dispense runs under, already resolved. See
    `AspirateRequest.parameters`."""

    @model_validator(mode="after")
    def _check_per_channel_lengths(self) -> Self:
        if not self.dispenses:
            raise ValueError("DispenseRequest.dispenses must not be empty")
        total = sum(len(d.volumes) for d in self.dispenses)
        if self.flow_rates is not None and len(self.flow_rates) != total:
            raise ValueError(
                f"DispenseRequest.flow_rates length ({len(self.flow_rates)}) "
                f"must equal total channels across dispenses ({total})"
            )
        if self.offsets_z is not None and len(self.offsets_z) != total:
            raise ValueError(
                f"DispenseRequest.offsets_z length ({len(self.offsets_z)}) "
                f"must equal total channels across dispenses ({total})"
            )
        return self


class PickUpTipsRequest(_StrictModel):
    picks: list[TipPick]
    use_channels: list[int] | None = None

    @model_validator(mode="after")
    def _picks_not_empty(self) -> Self:
        if not self.picks:
            raise ValueError("PickUpTipsRequest.picks must not be empty")
        return self


class DropTipsRequest(_StrictModel):
    """Drop tips to waste (``to_waste=True``) or back to one or more racks
    (``to_waste=False`` with ``drops`` populated). Like ``PickUpTipsRequest``,
    ``drops`` carries a slice per rack so a single physical drop can return
    tips to multiple racks."""
    drops: list[TipPick] | None = None
    to_waste: bool = True
    use_channels: list[int] | None = None
    """The channels to empty, for a robot whose mounts a rack slice alone cannot tell apart."""

    @model_validator(mode="after")
    def _drops_required_when_returning(self) -> Self:
        if not self.to_waste and not self.drops:
            raise ValueError(
                "DropTipsRequest.drops is required when to_waste=False"
            )
        return self


class MovePlateRequest(_StrictModel):
    plate: str
    to_position: str
    from_position: str | None = None
    grip_distance_from_top: float | None = None
    """How far below the labware's top the gripper takes it, in mm. None means the
    driver's default: PLR's plate grip on a Hamilton; on an Opentrons robot the
    vendor definition's grip height for labware Opentrons defines, mid-height for
    labware synthesized from PLR geometry. It is a property of this move, not of
    the labware. On an Opentrons robot it only shapes a synthesized definition's
    first upload in a run; later moves of that labware in the run reuse it."""


class AddDeckLabwareRequest(_StrictModel):
    """Materialize one labware (plate, tip rack, or trough) on the deck at a
    site, created from catalog_ref. Adds the deck-projection object; moves no
    hardware. ``well_state`` optionally seeds the new labware's initial per-well
    volumes / tip layout (sparse override)."""

    name: str
    catalog_ref: str
    at: str
    well_state: LabwareWellState | None = None


class RemoveDeckLabwareRequest(_StrictModel):
    """Un-materialize one named labware from the deck (departure). Removes the
    deck-projection object; ejects no hardware."""

    name: str


def _require_resolvable_channels(
    model: str,
    verb: str,
    labware: str,
    positions: list[str] | None,
    use_channels: list[int] | None,
) -> None:
    """Refuse a target the driver cannot count channels for.

    `not use_channels` rejects both None and an empty list: either would resolve
    to zero channels, and the call would go to the machine and quietly do
    nothing. Same for positions stated as an empty list.
    """
    if positions is None and not use_channels:
        raise ValueError(
            f"{model} '{labware}': a container {verb} (no positions) requires a "
            f"non-empty use_channels to determine the channel count"
        )
    if positions is not None and not positions:
        raise ValueError(f"{model} '{labware}': positions must name at least one well")


class MixRequest(_StrictModel):
    """Mix in place. ``positions`` carries one well id per channel for an
    itemized labware (plate); for a single container (trough) ``positions`` is
    None and the channel count comes from ``use_channels`` (which is then
    required, since a trough mix has no positions to count)."""
    labware: str
    positions: list[str] | None = None
    volume: float
    repetitions: int
    parameters: PipettingParameters = PipettingParameters()
    """What this mix runs under, already resolved. The cycle count and volume are
    the mix's own; how it is pipetted comes from ``height`` and ``flow_rate``
    here. A mix neither blows out nor nests another mix, so those fields go
    unread on a mix even when the same record carries them for the dispense it
    is reused across."""
    use_channels: list[int] | None = None

    @model_validator(mode="after")
    def _channel_count_resolvable(self) -> Self:
        _require_resolvable_channels(
            "MixRequest", "mix", self.labware, self.positions, self.use_channels
        )
        return self


class DiscardTipsRequest(_StrictModel):
    use_channels: list[int] | None = None


class GetDeckStateRequest(_StrictModel):
    pass


class ReconcileHardwareStateRequest(_StrictModel):
    pass


class DiscardStrandedTipsRequest(_StrictModel):
    pass

class LiquidProbeRequest(_StrictModel):
    """Find where the liquid surface sits, without moving any of it.

    Addressed like the other per-channel commands: ``positions`` carries one
    well id per channel for an itemized labware, or is None for a single
    container (trough/tube) where every channel probes the one cavity. A probe
    reports ONE height whatever the channel count, because a head reads its
    pressure sensors together rather than per nozzle.

    Requires mounted tips: the probe descends a tip until the pressure changes.
    """

    labware: str
    positions: list[str] | None = None
    use_channels: list[int] | None = None
    """The channels to probe with, for a head a position list alone cannot
    place. Required for a container probe, which has no positions to count."""

    @model_validator(mode="after")
    def _channel_count_resolvable(self) -> Self:
        _require_resolvable_channels(
            "LiquidProbeRequest", "probe", self.labware, self.positions, self.use_channels
        )
        return self


class LiquidProbeResponse(_StrictModel):
    """Where the probe found liquid, or that it found none."""

    height: float | None
    """Height of the liquid surface above the inner floor of the well (or
    container cavity), in mm: the same frame as ``AspirateRequest.offsets_z``,
    so it feeds straight back in as a pipetting height. None means the probe
    reached the floor without detecting liquid, which is an answer and not a
    failure. Stated with no default, so a driver that answers without saying
    anything fails to validate rather than reading as "no liquid"."""


# --- 96-head request models ---

class Aspirate96Request(_StrictModel):
    labware: str
    volume: float
    flow_rate: float | None = None
    liquid_height: float | None = None


class Dispense96Request(_StrictModel):
    labware: str
    volume: float
    flow_rate: float | None = None
    liquid_height: float | None = None


class PickUpTips96Request(_StrictModel):
    tip_rack: str


class DropTips96Request(_StrictModel):
    tip_rack: str | None = None
    to_waste: bool = True


class ReturnTips96Request(_StrictModel):
    pass


# --- State models ---


class ChannelError(_StrictModel):
    """One channel of a multi-channel call that failed mid-operation.

    Listed in ``LabwareStateResponse.per_channel_errors`` only for channels
    that failed; successful channels are implicit (not in the list). The
    presence of an entry means the channel's content was definitely not
    transferred to/from the named well; the orca-core interpreter renders
    this as a ``definitely_not_transferred`` outcome on the per-well record.

    ``channel_id`` is the 0-indexed physical channel as reported by the
    underlying backend (Hamilton STAR, sim driver). The ``labware`` /
    ``well_position`` pair identifies the well the channel was assigned to;
    callers map back via ``use_channels`` if the call used a non-default
    channel layout.
    """

    channel_id: int
    well_position: str | None
    labware: str
    attempted_volume: float
    error_code: str
    error_message: str


class LabwareStateResponse(_StrictModel):
    success: bool
    labware_state: dict[str, LabwareWellState] = {}
    per_channel_errors: list[ChannelError] = []


class DeckResourceState(_StrictModel):
    """One labware item on the deck (matches ``get_deck_state`` items)."""

    name: str
    type: str
    category: str | None = None
    # Same label ``add_deck_labware.at`` and ``move_plate.to_position`` accept
    # ('C2-slot', 'carrier_1-0'), so a reported site feeds straight back in.
    site: str | None = None


class TipRackState(_StrictModel):
    """Per-tip-rack inventory snapshot."""

    name: str
    tips_remaining: int
    total_tips: int


class InterruptedMove(_StrictModel):
    """A gripper move that started and did not finish.

    While this is set, the ``site`` reported for ``labware`` is where the move
    BEGAN, not where the labware is. Treat its location as unknown until
    someone looks.
    """

    labware: str
    to_site: str
    error: str
    from_site: str | None = None


class DeckStateResponse(_StrictModel):
    """Response payload for ``get_deck_state``.

    Mirrors the dict shape PLR / sim drivers were building by hand: which
    head channels currently carry tips, deck resources, and per-tip-rack
    inventory. Operators / AI assistants consume this to plan the next
    aspirate / dispense / pickup_tips step.
    """

    tips_mounted: list[bool] = []
    tips_mounted_96: bool = False
    labware: list[DeckResourceState] = []
    tip_racks: list[TipRackState] = []
    # Per-process, like `tips_mounted`: a driver restart clears it, so None means
    # "no interrupted move known to THIS driver", never "verified nothing is held".
    interrupted_move: InterruptedMove | None = None


class MountTipReconcile(_StrictModel):
    """One mount's tip-presence sensor read against the driver's bookkeeping.

    The Flex sensor is one reading per mount, never per channel, which is why
    ``outcome`` can be a wholesale clear but never a per-channel repair.
    """

    mount: str
    sensor: Literal["present", "absent", "unknown"]
    tracked_tips: int
    outcome: Literal["in_sync", "cleared_lost_tips", "untracked_tip_present", "unverified"]


class ReconcileHardwareStateResponse(_StrictModel):
    """Response payload for ``reconcile_hardware_state`` / ``discard_stranded_tips``.

    ``checked`` is False whenever NOTHING was verified: the driver has no
    hardware ground-truth read at all, or it had nothing to read (no session,
    no composed heads), or every sensor answered unknown. It is a per-call
    statement, not a property of the driver family.
    ``session_recovered`` reports that the instrument-side control session had
    died under the driver (typically an operator recovering the device at the
    instrument) and was rebuilt without motion. ``requires_intervention`` is
    the one outcome an engine must not run past: hardware holds a tip the
    model does not know, and no automatic repair is safe.
    """

    success: bool = True
    checked: bool = False
    session_recovered: bool = False
    mounts: list[MountTipReconcile] = []
    requires_intervention: bool = False
    message: str | None = None


# --- Head configuration models ---

class GetHeadConfigurationRequest(_StrictModel):
    """Marker request for ``get_head_configuration``. Takes no parameters."""


class NozzleGroup(_StrictModel):
    """The nozzles driven by ONE plunger."""

    channels: list[int]
    # Both stay optional because not every backend names the mounted hardware: PLR exposes a
    # channel count for every liquid handler but a pipette catalog only for some.
    max_volume_ul: float | None = None
    pipette_model: str | None = None

    @model_validator(mode="after")
    def _check_non_empty(self) -> Self:
        if not self.channels:
            raise ValueError("NozzleGroup.channels must not be empty: a plunger drives nozzles")
        return self


class HeadConfigurationResponse(_StrictModel):
    """What is mounted on the head RIGHT NOW, and which channels move together.

    A group holding several channels is GANGED: one plunger stroke moves every nozzle in it, so
    those channels deliver ONE volume and are addressed together or not at all. A group holding one
    channel is independently addressable. A Hamilton MLSTAR 8-channel reports eight single-channel
    groups; an Opentrons 8-channel reports one group of eight; a 96 head reports one group of 96.

    This is runtime state, not a property of the driver class. Whether channels gang depends on the
    pipette currently in the mount, so the same driver reports a different configuration after a
    pipette change, and a class-level capability declaration could only ever describe one of them.
    """

    groups: list[NozzleGroup] = []

    @property
    def nozzle_count(self) -> int:
        return sum(len(group.channels) for group in self.groups)

    @property
    def independent_volume_count(self) -> int:
        """How many distinct volumes the head can deliver at once, which is its plunger count."""
        return len(self.groups)

    def require_volumes_honorable(self, volumes_by_channel: dict[int, float]) -> None:
        """Raise unless the mounted head can actually perform this per-channel volume request.

        Enforced against runtime state rather than on the request models because it depends on what
        is mounted: the identical request is valid on an MLSTAR and impossible on an Opentrons
        multi. An empty configuration means the head is not known yet, which is refused rather than
        waved through, so an uninitialized driver cannot silently disable the check.
        """
        if not self.groups:
            raise ValueError(
                "The head configuration is not known, so per-channel volumes cannot be checked. "
                "Initialize the driver before commanding it."
            )

        known = {channel for group in self.groups for channel in group.channels}
        unknown = sorted(set(volumes_by_channel) - known)
        if unknown:
            raise ValueError(
                f"Channels {unknown} are not on the mounted head, which has {sorted(known)}."
            )

        for group in self.groups:
            addressed = [c for c in group.channels if c in volumes_by_channel]
            if not addressed or len(group.channels) == 1:
                continue
            if len(addressed) != len(group.channels):
                raise ValueError(
                    f"Channels {sorted(group.channels)} share one plunger, so a stroke moves all "
                    f"of them, but only {sorted(addressed)} were addressed. Use the whole group, "
                    "or a head whose nozzles move independently."
                )
            first = volumes_by_channel[addressed[0]]
            if any(
                not isclose(volumes_by_channel[c], first, rel_tol=0.0, abs_tol=VOLUME_TOLERANCE_UL)
                for c in addressed
            ):
                requested = sorted({volumes_by_channel[c] for c in addressed})
                raise ValueError(
                    f"Channels {sorted(group.channels)} share one plunger, so they deliver one "
                    f"volume per stroke, but {requested} were requested. Split this into one "
                    "command per volume, or use a head with an independent plunger per channel."
                )


# --- Deck configuration models ---

class DeckResourceConfig(_StrictModel):
    """One resource on a liquid-handler deck.

    Two placement modes, used by DIFFERENT surfaces:

    * **Carrier on rail**: ``rail`` set, ``parent_id`` and ``site_index`` both
      ``None``. Carriers mount directly on the deck rail grid. This is the
      ONLY mode :class:`DeckLayoutConfig` accepts -- a deck layout declares
      carriers (deck structure), never labware.
    * **Labware on carrier site**: ``parent_id`` and ``site_index`` both set,
      ``rail`` ``None``. Labware (plates, tip racks, troughs) mount on a named
      site of a carrier. This mode is the live runtime OCCUPANCY wire format,
      carried by :class:`ReconcileDeckOccupancyRequest` and ``add_deck_labware``;
      the engine derives occupancy from the ledger and projects it onto the
      driver deck through these requests. It is NOT a deck-layout mode:
      :class:`DeckLayoutConfig` rejects it (see that model's validator). Anchor
      labware in the workflow instead -- ``@orca.thread(start=("<lh>/<carrier>
      -<site>", REUSE_EXISTING), ...)`` for residents, ``@orca.action(
      deck_positions=...)`` for transient labware -- and the ledger drives the
      occupancy requests.

    Mixed shapes (rail + parent_id, parent_id without site_index, neither
    rail nor parent_id) are rejected by :class:`DeckLayoutConfig`'s
    structural validator.
    """

    name: str
    catalog_ref: str
    rail: int | None = None
    parent_id: str | None = None
    site_index: int | None = None
    well_state: LabwareWellState | None = None


class DeckLayoutConfig(_StrictModel):
    deck_type: str
    resources: list[DeckResourceConfig]
    labware_state: dict[str, LabwareWellState] | None = None

    @model_validator(mode="after")
    def _validate_placement(self) -> Self:
        """A deck layout declares CARRIERS ONLY; reject labware-on-site entries.

        Carriers (``rail`` set) are deck structure. Labware occupancy
        (``parent_id`` + ``site_index``) is NOT a layout concern: the engine
        derives it from the ledger and projects it onto the driver deck via
        ``ReconcileDeckOccupancyRequest`` / ``add_deck_labware`` at runtime. Putting
        labware in the layout creates a second source of truth for occupancy,
        the exact divergence this model now forbids.

        Errors raised here surface as Pydantic ValidationErrors at config
        construction time, so authors see the problem at topology-build rather
        than as an opaque PLR exception during configure_deck. The mirror image
        of this gate is :meth:`ReconcileDeckOccupancyRequest._reject_carrier_entries`,
        which keeps carriers out of the occupancy wire format.
        """
        seen_names: set[str] = set()
        for res in self.resources:
            if res.name in seen_names:
                raise ValueError(
                    f"duplicate deck resource name {res.name!r}; "
                    f"every resource on a layout must have a unique name"
                )
            seen_names.add(res.name)

            if res.parent_id is not None or res.site_index is not None:
                raise ValueError(
                    f"DeckLayoutConfig resource {res.name!r} declares labware on a "
                    f"carrier site (parent_id={res.parent_id!r}, "
                    f"site_index={res.site_index!r}); the deck config declares carriers "
                    f"only. Route labware as a thread with deck_positions or a "
                    f"REUSE_EXISTING resident; occupancy reaches the deck from the "
                    f"ledger via reconcile_deck_occupancy / add_deck_labware, not the layout."
                )
            if res.well_state is not None:
                raise ValueError(
                    f"DeckLayoutConfig resource {res.name!r} declares well_state; a "
                    f"layout declares carriers only (no labware, no labware state). "
                    f"Seed initial state via the labware template's initial_state."
                )
            if res.rail is None:
                raise ValueError(
                    f"DeckLayoutConfig resource {res.name!r} has no rail; every layout "
                    f"resource must mount on a rail (carriers are the only deck-config "
                    f"resource kind)."
                )
        return self


class ResetDeckLabwareRequest(_StrictModel):
    """Wipe deck occupancy (plates / tip racks / troughs) while preserving
    carriers. The engine ledger is the source of truth; this resets the
    driver's deck projection so it holds no stale labware."""
    pass


class ReconcileDeckOccupancyRequest(_StrictModel):
    """Set the driver deck's labware occupancy to exactly ``resources``.

    The driver wipes current occupancy (carriers preserved) and places each
    resource. Idempotent: the deck projection ends matching the authoritative
    occupancy set the engine derives from the ledger. Each entry uses the
    carrier-site placement mode (``parent_id`` + ``site_index``)."""
    resources: list[DeckResourceConfig]

    @model_validator(mode="after")
    def _reject_carrier_entries(self) -> Self:
        """Occupancy is carrier-site labware only; carriers are deck structure
        and are never reconciled (placing one would append, breaking idempotency)."""
        for res in self.resources:
            if res.rail is not None:
                raise ValueError(
                    f"reconcile_deck_occupancy resource {res.name!r} sets "
                    f"rail={res.rail!r}: occupancy entries mount on a carrier site "
                    f"(parent_id + site_index), not a rail. Carriers are structure."
                )
        return self


def channel_to_aspirate_well(
    request: AspirateRequest, channel_id: int,
) -> tuple[str, str | None, float] | None:
    """Map a physical channel index back to the (labware, position, volume) it served.

    Used by drivers translating per-channel backend errors (Hamilton
    ChannelizedError, sim PartialFault) into ``ChannelError`` entries. Returns
    None if ``channel_id`` was not part of this request -- happens when
    ``use_channels`` is sparse and the failed channel was not selected.

    Mapping rule: aspirate slices are flattened in target order by CHANNEL
    (``len(volumes)``); each flat channel is driven by ``use_channels[i]`` (or
    ``i`` if use_channels is None). ``position`` is the well id for an itemized
    target, or None for a single-container (trough) target.
    """
    flat: list[tuple[str, str | None, float]] = []
    for asp in request.aspirations:
        for i, vol in enumerate(asp.volumes):
            pos = asp.positions[i] if asp.positions is not None else None
            flat.append((asp.labware, pos, vol))
    use = (
        request.use_channels
        if request.use_channels is not None
        else list(range(len(flat)))
    )
    try:
        idx = use.index(channel_id)
    except ValueError:
        return None
    if idx >= len(flat):
        return None
    return flat[idx]


def channel_to_dispense_well(
    request: DispenseRequest, channel_id: int,
) -> tuple[str, str | None, float] | None:
    """Map a physical channel index back to the (labware, position, volume) it served.

    Mirror of ``channel_to_aspirate_well`` for dispense calls.
    """
    flat: list[tuple[str, str | None, float]] = []
    for disp in request.dispenses:
        for i, vol in enumerate(disp.volumes):
            pos = disp.positions[i] if disp.positions is not None else None
            flat.append((disp.labware, pos, vol))
    use = (
        request.use_channels
        if request.use_channels is not None
        else list(range(len(flat)))
    )
    try:
        idx = use.index(channel_id)
    except ValueError:
        return None
    if idx >= len(flat):
        return None
    return flat[idx]


def build_aspirate_partial_failure(
    request: AspirateRequest,
    failed_channels: dict[int, tuple[str, str]],
    labware_state: dict[str, "LabwareWellState"] | None = None,
) -> LabwareStateResponse:
    """Compose a partial-failure aspirate response from per-channel error info.

    ``failed_channels`` maps physical channel index -> (error_code,
    error_message). For each entry, looks up the well via
    ``channel_to_aspirate_well`` and emits a ``ChannelError``. Channels not
    in the dict are implicit successes (not included in the response). If a
    failed channel was not part of the request (sparse use_channels), it is
    silently skipped: the call did not address that channel, so no error
    record is produced.
    """
    errors: list[ChannelError] = []
    for channel_id, (code, msg) in failed_channels.items():
        well = channel_to_aspirate_well(request, channel_id)
        if well is None:
            continue
        labware, position, volume = well
        errors.append(ChannelError(
            channel_id=channel_id,
            well_position=position,
            labware=labware,
            attempted_volume=volume,
            error_code=code,
            error_message=msg,
        ))
    return LabwareStateResponse(
        success=False,
        labware_state=labware_state if labware_state is not None else {},
        per_channel_errors=errors,
    )


def build_dispense_partial_failure(
    request: DispenseRequest,
    failed_channels: dict[int, tuple[str, str]],
    labware_state: dict[str, "LabwareWellState"] | None = None,
) -> LabwareStateResponse:
    """Compose a partial-failure dispense response from per-channel error info.

    Mirror of ``build_aspirate_partial_failure`` for dispense calls.
    """
    errors: list[ChannelError] = []
    for channel_id, (code, msg) in failed_channels.items():
        well = channel_to_dispense_well(request, channel_id)
        if well is None:
            continue
        labware, position, volume = well
        errors.append(ChannelError(
            channel_id=channel_id,
            well_position=position,
            labware=labware,
            attempted_volume=volume,
            error_code=code,
            error_message=msg,
        ))
    return LabwareStateResponse(
        success=False,
        labware_state=labware_state if labware_state is not None else {},
        per_channel_errors=errors,
    )
