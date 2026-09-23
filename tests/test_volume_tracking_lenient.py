"""LenientVolumeTracker + PLRLiquidHandlerWrapper integration tests.

Pins the two-mode tracker semantics. The deck is carriers-only; labware is
materialized via the occupancy wire format (reconcile_deck_occupancy), and
initial volumes are seeded through the same production `_apply_labware_state`
path configure_deck uses:

  * Wells NOT in the seeded `labware_state.volumes` get LenientVolumeTracker
    (cumulative delta, no raise on under/overflow). Aspirate from empty goes
    negative; dispense beyond max exceeds it. Both are valid audit values.

  * Wells IN the seeded `labware_state.volumes` get PLR's stock VolumeTracker
    (initial_volume=seeded). Aspirate/dispense enforce physical bounds and
    raise TooLittleLiquidError / TooLittleVolumeError on violation.

  * Tip racks: tracking is enabled, pickup decrements rack inventory.

Without these pins, the next PLR refactor or wrapper edit could silently
re-introduce the pre-fix behavior where every well reports 0.0 and the
deck_state endpoint surfaces no per-op state change.
"""

import pytest
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.errors import TooLittleLiquidError, TooLittleVolumeError
from pylabrobot.resources.volume_tracker import VolumeTracker as PLRVolumeTracker

from cheshire_drivers.liquid_handler_models import (
    AddDeckLabwareRequest,
    Aspirate96Request,
    AspirateRequest,
    AspirateTarget,
    DeckLayoutConfig,
    DeckResourceConfig,
    Dispense96Request,
    DispenseRequest,
    DispenseTarget,
    LabwareWellState,
    PickUpTips96Request,
    PickUpTipsRequest,
    ReconcileDeckOccupancyRequest,
    TipPick,
)
from cheshire_drivers.plr.liquid_handler import ChatterboxLiquidHandlerDriver
from cheshire_drivers.plr_volume_tracker import (
    LenientVolumeTracker,
    ReplenishingVolumeTracker,
)


# ---- Unit: LenientVolumeTracker semantics in isolation ----


class TestLenientVolumeTrackerSemantics:
    """The subclass must skip both enforcement raises while otherwise behaving
    identically to PLR's stock VolumeTracker (commit/rollback/serialize)."""

    def test_remove_liquid_goes_negative_without_raising(self) -> None:
        t = LenientVolumeTracker(thing="test", max_volume=200.0)
        t.remove_liquid(50.0)
        t.commit()
        # Stock VolumeTracker would have raised TooLittleLiquidError on
        # remove_liquid(50) when volume=0; lenient accumulates the delta.
        assert t.volume == -50.0

    def test_add_liquid_exceeds_max_without_raising(self) -> None:
        t = LenientVolumeTracker(thing="test", max_volume=100.0)
        t.add_liquid(250.0)
        t.commit()
        # Stock VolumeTracker would have raised TooLittleVolumeError on
        # add_liquid(250) when max=100; lenient accumulates the delta.
        assert t.volume == 250.0

    def test_cumulative_aspirate_then_dispense_nets_correctly(self) -> None:
        t = LenientVolumeTracker(thing="test", max_volume=200.0)
        t.remove_liquid(100.0)
        t.commit()
        t.add_liquid(40.0)
        t.commit()
        assert t.volume == -60.0  # net movement from zero

    def test_callback_fires_on_remove(self) -> None:
        calls: list[int] = []
        t = LenientVolumeTracker(thing="test", max_volume=200.0)
        t.register_callback(lambda: calls.append(1))
        t.remove_liquid(10.0)
        assert calls == [1]

    def test_callback_fires_on_add(self) -> None:
        calls: list[int] = []
        t = LenientVolumeTracker(thing="test", max_volume=200.0)
        t.register_callback(lambda: calls.append(1))
        t.add_liquid(10.0)
        assert calls == [1]

    def test_commit_rollback_inherited(self) -> None:
        t = LenientVolumeTracker(thing="test", max_volume=200.0)
        t.remove_liquid(50.0)
        # pending_volume reflects pending op; volume hasn't committed yet
        assert t.pending_volume == -50.0
        assert t.volume == 0
        t.rollback()
        assert t.pending_volume == 0


class TestReplenishingVolumeTrackerSemantics:
    """The subclass pins the level at its seeded fill: `remove_liquid` and
    `add_liquid` never move it and never raise, so a reagent source reads full
    forever in sim, however much a batch draws."""

    def test_remove_liquid_does_not_lower_level(self) -> None:
        t = ReplenishingVolumeTracker(thing="test", max_volume=200.0, initial_volume=100.0)
        # Stock VolumeTracker would raise TooLittleLiquidError (500 > 100);
        # replenished leaves the level untouched.
        t.remove_liquid(500.0)
        t.commit()
        assert t.volume == 100.0

    def test_add_liquid_does_not_exceed_max(self) -> None:
        t = ReplenishingVolumeTracker(thing="test", max_volume=200.0, initial_volume=100.0)
        # Stock VolumeTracker would raise TooLittleVolumeError (500 > 100 free);
        # replenished leaves the level untouched.
        t.add_liquid(500.0)
        t.commit()
        assert t.volume == 100.0

    def test_get_used_volume_pinned_after_many_removes(self) -> None:
        t = ReplenishingVolumeTracker(thing="test", max_volume=200.0, initial_volume=150.0)
        for _ in range(20):
            t.remove_liquid(50.0)
            t.commit()
        assert t.get_used_volume() == 150.0

    def test_callback_fires_on_remove(self) -> None:
        calls: list[int] = []
        t = ReplenishingVolumeTracker(thing="test", max_volume=200.0, initial_volume=100.0)
        t.register_callback(lambda: calls.append(1))
        t.remove_liquid(10.0)
        assert calls == [1]

    def test_callback_fires_on_add(self) -> None:
        calls: list[int] = []
        t = ReplenishingVolumeTracker(thing="test", max_volume=200.0, initial_volume=100.0)
        t.register_callback(lambda: calls.append(1))
        t.add_liquid(10.0)
        assert calls == [1]

    def test_commit_rollback_keep_level_pinned(self) -> None:
        t = ReplenishingVolumeTracker(thing="test", max_volume=200.0, initial_volume=100.0)
        # remove is a no-op, so pending never diverges from the seeded level.
        t.remove_liquid(50.0)
        assert t.pending_volume == 100.0
        t.commit()
        assert t.volume == 100.0
        t.rollback()
        assert t.pending_volume == 100.0


# ---- Integration: PLRLiquidHandlerWrapper swaps trackers correctly ----


def _carriers_only_deck() -> DeckLayoutConfig:
    """Carriers-only layout. Labware reaches the deck via the occupancy wire
    format (reconcile_deck_occupancy), never the layout -- see
    test_deck_layout_placement.py for the carriers-only gate."""
    return DeckLayoutConfig(
        deck_type="STARLet",
        resources=[
            DeckResourceConfig(name="plate_car", catalog_ref="PLT_CAR_L5AC_A00", rail=7),
            DeckResourceConfig(name="tip_car", catalog_ref="TIP_CAR_480_A00", rail=15),
        ],
    )


def _occupancy() -> list[DeckResourceConfig]:
    """The catalog-backed labware the tracker-swap tests need on the deck:
    two plates + one tip rack, placed at carrier sites via the occupancy wire."""
    return [
        DeckResourceConfig(
            name="sample_plate",
            catalog_ref="Cor_Falcon_96_wellplate_340ul_Fb_Black",
            parent_id="plate_car", site_index=0,
        ),
        DeckResourceConfig(
            name="dest_plate",
            catalog_ref="Cor_Falcon_96_wellplate_340ul_Fb_Black",
            parent_id="plate_car", site_index=1,
        ),
        DeckResourceConfig(
            name="tips",
            catalog_ref="hamilton_96_tiprack_1000uL_filter",
            parent_id="tip_car", site_index=0,
        ),
    ]


async def _configure_with_occupancy(
    driver: ChatterboxLiquidHandlerDriver,
    labware_state: dict[str, LabwareWellState] | None = None,
) -> None:
    """Bring up a carriers-only deck, then materialize the labware via the live
    occupancy wire format. Optionally seed initial well volumes through the same
    production `_apply_labware_state` path `configure_deck` uses, so seeded wells
    get the strict tracker after the reconcile placement installs lenient ones.
    """
    await driver.configure_deck(_carriers_only_deck())
    await driver.reconcile_deck_occupancy(
        ReconcileDeckOccupancyRequest(resources=_occupancy())
    )
    if labware_state is not None:
        driver._apply_labware_state(labware_state)


@pytest.fixture(autouse=True)
def _reset_plr_tracking_flags():
    """`configure_deck` flips PLR's module-global `set_volume_tracking` /
    `set_tip_tracking` to True. Without a teardown, those globals leak into
    every subsequent test file in the pytest session (and across repos when
    cheshire-drivers is an editable install). Restore the originals after
    each test so tests run in isolation regardless of order.
    """
    from pylabrobot.resources.tip_tracker import (
        does_tip_tracking, set_tip_tracking,
    )
    from pylabrobot.resources.volume_tracker import (
        does_volume_tracking, set_volume_tracking,
    )
    saved_vol, saved_tip = does_volume_tracking(), does_tip_tracking()
    try:
        yield
    finally:
        set_volume_tracking(saved_vol)
        set_tip_tracking(saved_tip)


@pytest.fixture
def driver() -> ChatterboxLiquidHandlerDriver:
    return ChatterboxLiquidHandlerDriver(num_channels=8)


class TestNinetySixHeadPlateVolume:
    """A 96-head op on a plate moves volume through every well (driver side).

    The single-pool trough case is in test_trough_liquid_handling; this pins the
    plate grid path so both 96-head shapes carry a volume assertion."""

    @pytest.mark.asyncio
    async def test_aspirate96_then_dispense96_move_every_well(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await _configure_with_occupancy(driver)
        await driver.pick_up_tips96(PickUpTips96Request(tip_rack="tips"))

        asp = await driver.aspirate96(Aspirate96Request(labware="sample_plate", volume=10.0))
        vols = asp.labware_state["sample_plate"].volumes
        assert vols is not None
        # Every well drew 10 uL (lenient default 0 -> -10).
        assert vols["A1"] == pytest.approx(-10.0)
        assert vols["H12"] == pytest.approx(-10.0)

        disp = await driver.dispense96(Dispense96Request(labware="sample_plate", volume=10.0))
        vols2 = disp.labware_state["sample_plate"].volumes
        assert vols2 is not None
        assert vols2["A1"] == pytest.approx(0.0)
        assert vols2["H12"] == pytest.approx(0.0)


class TestConfigureDeckInstallsLenientTrackers:
    """After configure_deck (no labware_state), every plate well must have
    a LenientVolumeTracker so subsequent aspirate/dispense never raises."""

    @pytest.mark.asyncio
    async def test_unseeded_plate_wells_are_lenient(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await _configure_with_occupancy(driver)
        from pylabrobot.resources.plate import Plate as PLRPlate
        assert driver._lh is not None
        sample = driver._lh.deck.get_resource("sample_plate")
        assert isinstance(sample, PLRPlate)
        for well in sample.get_all_items():
            assert isinstance(well.tracker, LenientVolumeTracker), (
                f"well {well.name} got {type(well.tracker).__name__}, "
                f"expected LenientVolumeTracker"
            )

    @pytest.mark.asyncio
    async def test_unseeded_aspirate_goes_negative_via_wire(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await _configure_with_occupancy(driver)
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="tips", positions=["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1"])],
        ))
        response = await driver.aspirate(AspirateRequest(
            aspirations=[AspirateTarget(
                labware="sample_plate",
                positions=["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1"],
                volumes=[100.0] * 8,
            )],
        ))
        assert response.success is True
        from pylabrobot.resources.plate import Plate as PLRPlate
        assert driver._lh is not None
        sample = driver._lh.deck.get_resource("sample_plate")
        assert isinstance(sample, PLRPlate)
        # Per-channel volume tracker shows negative-100uL on each picked well.
        for spot in ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1"]:
            assert sample.get_item(spot).tracker.volume == pytest.approx(-100.0)

    @pytest.mark.asyncio
    async def test_unseeded_dispense_exceeds_max_via_wire(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await _configure_with_occupancy(driver)
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="tips", positions=["A1"])],
        ))
        # Add liquid to the tip first via an aspirate from somewhere, so the
        # dispense isn't itself a pure-air op the tip tracker complains about.
        await driver.aspirate(AspirateRequest(
            aspirations=[AspirateTarget(
                labware="sample_plate", positions=["A1"], volumes=[600.0],
            )],
        ))
        response = await driver.dispense(DispenseRequest(
            dispenses=[DispenseTarget(
                labware="dest_plate", positions=["A1"], volumes=[600.0],
            )],
        ))
        assert response.success is True
        dest = driver._lh.deck.get_resource("dest_plate")
        # 600uL > the 340uL max of the Cor Falcon well; lenient accepts.
        assert dest.get_item("A1").tracker.volume == pytest.approx(600.0)


class TestSeededWellsGetStrictTracker:
    """labware_state.volumes opts a well into strict enforcement: the wrapper
    swaps from LenientVolumeTracker to PLR's stock VolumeTracker."""

    @pytest.mark.asyncio
    async def test_seeded_well_uses_strict_tracker(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await _configure_with_occupancy(driver, labware_state={
            "sample_plate": LabwareWellState(volumes={"A1": 200.0}),
        })
        from pylabrobot.resources.plate import Plate as PLRPlate
        assert driver._lh is not None
        sample = driver._lh.deck.get_resource("sample_plate")
        assert isinstance(sample, PLRPlate)
        # A1 was seeded -> strict (PLRVolumeTracker exactly, NOT lenient subclass)
        assert type(sample.get_item("A1").tracker) is PLRVolumeTracker
        assert sample.get_item("A1").tracker.volume == 200.0
        # B1 was not seeded -> lenient
        assert isinstance(sample.get_item("B1").tracker, LenientVolumeTracker)

    @pytest.mark.asyncio
    async def test_seeded_aspirate_below_initial_succeeds(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await _configure_with_occupancy(driver, labware_state={
            "sample_plate": LabwareWellState(volumes={"A1": 200.0}),
        })
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="tips", positions=["A1"])],
        ))
        response = await driver.aspirate(AspirateRequest(
            aspirations=[AspirateTarget(
                labware="sample_plate", positions=["A1"], volumes=[100.0],
            )],
        ))
        assert response.success is True
        from pylabrobot.resources.plate import Plate as PLRPlate
        assert driver._lh is not None
        sample = driver._lh.deck.get_resource("sample_plate")
        assert isinstance(sample, PLRPlate)
        # 200 (seeded) - 100 (aspirate) = 100
        assert sample.get_item("A1").tracker.volume == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_seeded_aspirate_beyond_initial_raises(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await _configure_with_occupancy(driver, labware_state={
            "sample_plate": LabwareWellState(volumes={"A1": 50.0}),
        })
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="tips", positions=["A1"])],
        ))
        # Seeded 50uL, asking for 200uL -> TooLittleLiquidError from PLR.
        with pytest.raises(TooLittleLiquidError):
            await driver.aspirate(AspirateRequest(
                aspirations=[AspirateTarget(
                    labware="sample_plate", positions=["A1"], volumes=[200.0],
                )],
            ))


class TestMixedPlateStrictAndLenientSideBySide:
    """One plate, one seeded well + one unseeded well. Ops on the seeded
    well enforce bounds; ops on the unseeded well silently accumulate."""

    @pytest.mark.asyncio
    async def test_strict_raises_lenient_doesnt(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await _configure_with_occupancy(driver, labware_state={
            "sample_plate": LabwareWellState(volumes={"A1": 10.0}),
        })
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="tips", positions=["A1", "B1"])],
        ))
        # B1 unseeded -> lenient -> 100uL aspirate from empty just goes to -100.
        await driver.aspirate(AspirateRequest(
            aspirations=[AspirateTarget(
                labware="sample_plate", positions=["B1"], volumes=[100.0],
            )],
        ))
        from pylabrobot.resources.plate import Plate as PLRPlate
        assert driver._lh is not None
        sample = driver._lh.deck.get_resource("sample_plate")
        assert isinstance(sample, PLRPlate)
        assert sample.get_item("B1").tracker.volume == pytest.approx(-100.0)
        # A1 seeded with only 10uL -> strict -> 100uL aspirate raises.
        with pytest.raises(TooLittleLiquidError):
            await driver.aspirate(AspirateRequest(
                aspirations=[AspirateTarget(
                    labware="sample_plate", positions=["A1"], volumes=[100.0],
                )],
            ))


class TestTipTrackerLenientAfterPickup:
    """`pick_up_tips` swaps each mounted tip's volume tracker to lenient.

    Without this swap, PLR's stock tip-volume tracker would raise
    `TooLittleVolumeError` on the first aspirate exceeding the tip's
    physical `maximal_volume` -- a real failure shape for sim workflows
    that don't constrain themselves to physical tip capacity. Pins the
    audit-not-enforce semantics on the tip side, matching the well swap.
    """

    @pytest.mark.asyncio
    async def test_aspirate_beyond_tip_max_volume_does_not_raise(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        # hamilton_96_tiprack_1000uL_filter tips have maximal_volume = 1065uL.
        # Aspirate 2000uL (~2x the tip capacity) into the mounted tip -- a
        # stock VolumeTracker raises; lenient lets it through.
        await _configure_with_occupancy(driver)
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="tips", positions=["A1"])],
        ))
        # Should succeed (no TooLittleVolumeError on tip overflow).
        response = await driver.aspirate(AspirateRequest(
            aspirations=[AspirateTarget(
                labware="sample_plate", positions=["A1"], volumes=[2000.0],
            )],
        ))
        assert response.success is True

    @pytest.mark.asyncio
    async def test_mounted_tip_tracker_class_is_lenient(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        from cheshire_drivers.plr_volume_tracker import LenientVolumeTracker

        await _configure_with_occupancy(driver)
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="tips", positions=["A1"])],
        ))
        assert driver._lh is not None
        # Channel 0 should have a tip with a LenientVolumeTracker.
        assert driver._lh.head[0].has_tip
        mounted_tip = driver._lh.head[0].get_tip()
        assert isinstance(mounted_tip.tracker, LenientVolumeTracker), (
            f"channel 0 tip got {type(mounted_tip.tracker).__name__}, "
            f"expected LenientVolumeTracker"
        )


class TestUnseededPlateEmitsAllWellsInWireResponse:
    """Pre-fix `_apply_labware_state` disabled unmentioned wells in seeded
    plates (and `_get_labware_state` filtered out disabled wells), so the
    wire only carried the seeded wells. Post-fix every well stays trackable
    so the wire emits all 96 entries on every plate. This is the correct
    shape for the audit-cumulative-delta design but is a wire payload
    growth that should be pinned so it isn't accidentally reverted to the
    old seeded-only emission.
    """

    @pytest.mark.asyncio
    async def test_unseeded_plate_emits_all_96_wells(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await _configure_with_occupancy(driver)
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="tips", positions=["A1"])],
        ))
        response = await driver.aspirate(AspirateRequest(
            aspirations=[AspirateTarget(
                labware="sample_plate", positions=["A1"], volumes=[100.0],
            )],
        ))
        ls = response.labware_state
        assert "sample_plate" in ls
        volumes = ls["sample_plate"].volumes
        assert volumes is not None
        # All 96 wells should be present: 8 rows (A-H) x 12 columns.
        assert len(volumes) == 96
        # The well we aspirated from went lenient-negative; every other
        # untouched well sits at 0 (lenient tracker default).
        assert volumes["A1"] == pytest.approx(-100.0)
        assert volumes["B1"] == pytest.approx(0.0)
        assert volumes["H12"] == pytest.approx(0.0)


class TestOccupancyWireSeedsInitialState:
    """Initial well volumes / tip state must ride the OCCUPANCY WIRE itself.

    Production path: orca derives occupancy from the ledger and projects it
    onto the driver deck via reconcile_deck_occupancy (residents) and
    add_deck_labware (transient arrivals). Each labware's declared initial
    state must travel ON that wire so the driver seeds the tracker at
    introduction. The seeded tests above poke ``_apply_labware_state``
    directly, which bypasses the wire and hid that the occupancy requests
    carried no state -- the dead path this re-wires. RED until the wire's
    ``well_state`` field exists and the driver applies it.
    """

    @pytest.mark.asyncio
    async def test_reconcile_seeds_strict_tracker_via_wire(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await driver.configure_deck(_carriers_only_deck())
        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[
            DeckResourceConfig(
                name="sample_plate",
                catalog_ref="Cor_Falcon_96_wellplate_340ul_Fb_Black",
                parent_id="plate_car", site_index=0,
                well_state=LabwareWellState(volumes={"A1": 200.0}),
            ),
        ]))
        from pylabrobot.resources.plate import Plate as PLRPlate
        assert driver._lh is not None
        sample = driver._lh.deck.get_resource("sample_plate")
        assert isinstance(sample, PLRPlate)
        # Seeded A1 -> strict tracker initialised to 200; unseeded B1 -> lenient.
        assert type(sample.get_item("A1").tracker) is PLRVolumeTracker
        assert sample.get_item("A1").tracker.volume == 200.0
        assert isinstance(sample.get_item("B1").tracker, LenientVolumeTracker)

    @pytest.mark.asyncio
    async def test_add_deck_labware_seeds_strict_tracker_via_wire(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await driver.configure_deck(_carriers_only_deck())
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="sample_plate",
            catalog_ref="Cor_Falcon_96_wellplate_340ul_Fb_Black",
            at="plate_car-0",
            well_state=LabwareWellState(volumes={"A1": 200.0}),
        ))
        from pylabrobot.resources.plate import Plate as PLRPlate
        assert driver._lh is not None
        sample = driver._lh.deck.get_resource("sample_plate")
        assert isinstance(sample, PLRPlate)
        assert type(sample.get_item("A1").tracker) is PLRVolumeTracker
        assert sample.get_item("A1").tracker.volume == 200.0

    @pytest.mark.asyncio
    async def test_wire_seeded_aspirate_beyond_initial_raises(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await driver.configure_deck(_carriers_only_deck())
        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[
            DeckResourceConfig(
                name="sample_plate",
                catalog_ref="Cor_Falcon_96_wellplate_340ul_Fb_Black",
                parent_id="plate_car", site_index=0,
                well_state=LabwareWellState(volumes={"A1": 50.0}),
            ),
            DeckResourceConfig(
                name="tips", catalog_ref="hamilton_96_tiprack_1000uL_filter",
                parent_id="tip_car", site_index=0,
            ),
        ]))
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="tips", positions=["A1"])],
        ))
        # Seeded 50uL strict via the wire, asking for 200uL -> PLR raises.
        with pytest.raises(TooLittleLiquidError):
            await driver.aspirate(AspirateRequest(aspirations=[AspirateTarget(
                labware="sample_plate", positions=["A1"], volumes=[200.0],
            )]))

    @pytest.mark.asyncio
    async def test_add_deck_labware_seeds_tip_state_via_wire(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await driver.configure_deck(_carriers_only_deck())
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="tips", catalog_ref="hamilton_96_tiprack_1000uL_filter",
            at="tip_car-0",
            well_state=LabwareWellState(tips={"A1": False, "B1": True}),
        ))
        from pylabrobot.resources.tip_rack import TipRack as PLRTipRack
        assert driver._lh is not None
        rack = driver._lh.deck.get_resource("tips")
        assert isinstance(rack, PLRTipRack)
        assert rack.get_item("A1").has_tip() is False
        assert rack.get_item("B1").has_tip() is True


class TestReservoirTroughplateOptOut:
    """A reservoir declared with a 'troughplate' labware_type resolves to a PLR
    Plate, so it rides the same lenient-opt-out path as any plate. Pins that
    opt-out volume tracking (no PLR raise on empty) survives for reservoirs --
    the case raised when the trough ledger default changed to 0/lenient."""

    def _reservoir_deck(self) -> DeckLayoutConfig:
        return DeckLayoutConfig(
            deck_type="STARLet",
            resources=[
                DeckResourceConfig(name="trough_car", catalog_ref="Trough_CAR_4R200_A00", rail=25),
                DeckResourceConfig(name="tip_car", catalog_ref="TIP_CAR_480_A00", rail=15),
            ],
        )

    @pytest.mark.asyncio
    async def test_undeclared_reservoir_aspirate_does_not_raise(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await driver.configure_deck(self._reservoir_deck())
        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[
            DeckResourceConfig(
                name="rsv", catalog_ref="AGenBio_1_troughplate_190000uL_Fl",
                parent_id="trough_car", site_index=0,
            ),
            DeckResourceConfig(
                name="tips", catalog_ref="hamilton_96_tiprack_1000uL_filter",
                parent_id="tip_car", site_index=0,
            ),
        ]))
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="tips", positions=["A1"])],
        ))
        # Opt-out (no well_state): lenient tracker -> aspirate-from-empty must NOT raise.
        response = await driver.aspirate(AspirateRequest(aspirations=[AspirateTarget(
            labware="rsv", positions=["A1"], volumes=[1000.0],
        )]))
        assert response.success is True

    @pytest.mark.asyncio
    async def test_seeded_reservoir_overdraw_raises(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await driver.configure_deck(self._reservoir_deck())
        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[
            DeckResourceConfig(
                name="rsv", catalog_ref="AGenBio_1_troughplate_190000uL_Fl",
                parent_id="trough_car", site_index=0,
                well_state=LabwareWellState(volumes={"A1": 500.0}),
            ),
            DeckResourceConfig(
                name="tips", catalog_ref="hamilton_96_tiprack_1000uL_filter",
                parent_id="tip_car", site_index=0,
            ),
        ]))
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="tips", positions=["A1"])],
        ))
        # Opt-in (seeded 500uL strict): drawing 1000uL is an overdraw -> PLR raises.
        with pytest.raises(TooLittleLiquidError):
            await driver.aspirate(AspirateRequest(aspirations=[AspirateTarget(
                labware="rsv", positions=["A1"], volumes=[1000.0],
            )]))


class TestReplenishedWellsGetReplenishingTracker:
    """`LabwareWellState.replenished` opts seeded wells into the non-depleting
    tracker: the wrapper installs ReplenishingVolumeTracker instead of strict,
    so aspirates never drain the source or raise. Unseeded wells stay lenient."""

    @pytest.mark.asyncio
    async def test_replenished_well_uses_replenishing_tracker(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await _configure_with_occupancy(driver, labware_state={
            "sample_plate": LabwareWellState(volumes={"A1": 200.0}, replenished=True),
        })
        from pylabrobot.resources.plate import Plate as PLRPlate
        assert driver._lh is not None
        sample = driver._lh.deck.get_resource("sample_plate")
        assert isinstance(sample, PLRPlate)
        # A1 was seeded + replenished -> ReplenishingVolumeTracker at the seed.
        assert isinstance(sample.get_item("A1").tracker, ReplenishingVolumeTracker)
        assert sample.get_item("A1").tracker.volume == 200.0
        # B1 was not seeded -> lenient (replenished is per-well-state, applies
        # only to the seeded wells).
        assert isinstance(sample.get_item("B1").tracker, LenientVolumeTracker)

    @pytest.mark.asyncio
    async def test_replenished_well_survives_overdraw_without_raising(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await _configure_with_occupancy(driver, labware_state={
            "sample_plate": LabwareWellState(volumes={"A1": 50.0}, replenished=True),
        })
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="tips", positions=["A1"])],
        ))
        # Seeded only 50uL, drawing 200uL: strict would raise TooLittleLiquidError,
        # replenished draws without depleting.
        response = await driver.aspirate(AspirateRequest(aspirations=[AspirateTarget(
            labware="sample_plate", positions=["A1"], volumes=[200.0],
        )]))
        assert response.success is True
        from pylabrobot.resources.plate import Plate as PLRPlate
        assert driver._lh is not None
        sample = driver._lh.deck.get_resource("sample_plate")
        assert isinstance(sample, PLRPlate)
        # Level stays pinned at the seed after the draw.
        assert sample.get_item("A1").tracker.volume == pytest.approx(50.0)


class TestOccupancyWireSeedsReplenished:
    """`replenished` must ride the OCCUPANCY WIRE (the production entry point),
    not just direct `_apply_labware_state`: orca projects a reagent source onto
    the driver deck via reconcile_deck_occupancy / add_deck_labware, and the
    non-depleting tracker must be installed at introduction."""

    @pytest.mark.asyncio
    async def test_reconcile_seeds_replenishing_tracker_via_wire(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await driver.configure_deck(_carriers_only_deck())
        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[
            DeckResourceConfig(
                name="sample_plate",
                catalog_ref="Cor_Falcon_96_wellplate_340ul_Fb_Black",
                parent_id="plate_car", site_index=0,
                well_state=LabwareWellState(volumes={"A1": 200.0}, replenished=True),
            ),
        ]))
        from pylabrobot.resources.plate import Plate as PLRPlate
        assert driver._lh is not None
        sample = driver._lh.deck.get_resource("sample_plate")
        assert isinstance(sample, PLRPlate)
        assert isinstance(sample.get_item("A1").tracker, ReplenishingVolumeTracker)
        assert sample.get_item("A1").tracker.volume == 200.0
        assert isinstance(sample.get_item("B1").tracker, LenientVolumeTracker)

    @pytest.mark.asyncio
    async def test_add_deck_labware_seeds_replenishing_tracker_via_wire(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await driver.configure_deck(_carriers_only_deck())
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="sample_plate",
            catalog_ref="Cor_Falcon_96_wellplate_340ul_Fb_Black",
            at="plate_car-0",
            well_state=LabwareWellState(volumes={"A1": 200.0}, replenished=True),
        ))
        from pylabrobot.resources.plate import Plate as PLRPlate
        assert driver._lh is not None
        sample = driver._lh.deck.get_resource("sample_plate")
        assert isinstance(sample, PLRPlate)
        assert isinstance(sample.get_item("A1").tracker, ReplenishingVolumeTracker)
        assert sample.get_item("A1").tracker.volume == 200.0

    @pytest.mark.asyncio
    async def test_wire_replenished_survives_repeated_draws(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await driver.configure_deck(_carriers_only_deck())
        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[
            DeckResourceConfig(
                name="sample_plate",
                catalog_ref="Cor_Falcon_96_wellplate_340ul_Fb_Black",
                parent_id="plate_car", site_index=0,
                well_state=LabwareWellState(volumes={"A1": 50.0}, replenished=True),
            ),
            DeckResourceConfig(
                name="tips", catalog_ref="hamilton_96_tiprack_1000uL_filter",
                parent_id="tip_car", site_index=0,
            ),
        ]))
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="tips", positions=["A1"])],
        ))
        # Five 200uL draws from a 50uL seed: cumulatively 1000uL, far past the
        # seed. A strict tracker raises on the first; replenished survives all.
        for _ in range(5):
            response = await driver.aspirate(AspirateRequest(aspirations=[AspirateTarget(
                labware="sample_plate", positions=["A1"], volumes=[200.0],
            )]))
            assert response.success is True
        from pylabrobot.resources.plate import Plate as PLRPlate
        assert driver._lh is not None
        sample = driver._lh.deck.get_resource("sample_plate")
        assert isinstance(sample, PLRPlate)
        assert sample.get_item("A1").tracker.volume == pytest.approx(50.0)


class TestTroughMaterializedThroughOccupancy:
    """A real single-cavity Trough is not a Plate, so the plate-only lenient default
    left it on PLR's stock tracker at 0 uL and its first aspirate raised. Both
    occupancy routes have to install the same audit-only default configure_deck does."""

    def _trough_deck(self) -> DeckLayoutConfig:
        return DeckLayoutConfig(
            deck_type="STARLet",
            resources=[
                DeckResourceConfig(name="trough_car", catalog_ref="Trough_CAR_4R200_A00", rail=25),
                DeckResourceConfig(name="tip_car", catalog_ref="TIP_CAR_480_A00", rail=15),
            ],
        )

    def _trough(self) -> DeckResourceConfig:
        return DeckResourceConfig(
            name="trough", catalog_ref="Hamilton_1_trough_200ml_Vb",
            parent_id="trough_car", site_index=0,
        )

    def _tips(self) -> DeckResourceConfig:
        return DeckResourceConfig(
            name="tips", catalog_ref="hamilton_96_tiprack_1000uL_filter",
            parent_id="tip_car", site_index=0,
        )

    async def _pick_up(self, driver: ChatterboxLiquidHandlerDriver) -> None:
        await driver.pick_up_tips(
            PickUpTipsRequest(picks=[TipPick(tip_rack="tips", positions=["A1"])])
        )

    def _draw(self) -> AspirateRequest:
        return AspirateRequest(
            aspirations=[AspirateTarget(labware="trough", positions=None, volumes=[25.0])],
            use_channels=[0],
        )

    @pytest.mark.asyncio
    async def test_reconciled_trough_aspirate_from_undeclared_does_not_raise(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await driver.configure_deck(self._trough_deck())
        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[self._trough(), self._tips()])
        )
        await self._pick_up(driver)

        assert (await driver.aspirate(self._draw())).success is True

    @pytest.mark.asyncio
    async def test_added_trough_aspirate_from_undeclared_does_not_raise(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        await driver.configure_deck(self._trough_deck())
        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[self._tips()])
        )
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="trough", catalog_ref="Hamilton_1_trough_200ml_Vb", at="trough_car-0",
        ))
        await self._pick_up(driver)

        assert (await driver.aspirate(self._draw())).success is True

    @pytest.mark.asyncio
    async def test_a_seeded_trough_still_enforces_its_level(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        """Seeding wins over the lenient default, so a declared trough still raises."""
        await driver.configure_deck(self._trough_deck())
        seeded = self._trough()
        seeded = DeckResourceConfig(
            name=seeded.name, catalog_ref=seeded.catalog_ref,
            parent_id=seeded.parent_id, site_index=seeded.site_index,
            well_state=LabwareWellState(volumes={"A1": 10.0}),
        )
        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[seeded, self._tips()])
        )
        await self._pick_up(driver)

        with pytest.raises(TooLittleLiquidError):
            await driver.aspirate(self._draw())


class TestLabwareSeedForAbsentLabware:
    """`configure_deck` builds a fresh deck from a carriers-only layout, so a top-level
    labware_state name it does not hold cannot be seeded. Skipping it silently reported
    success for state that was never applied."""

    @pytest.mark.asyncio
    async def test_a_seed_for_labware_the_deck_does_not_hold_says_where_state_belongs(
        self, driver: ChatterboxLiquidHandlerDriver,
    ) -> None:
        layout = DeckLayoutConfig(
            deck_type="STARLet",
            resources=[
                DeckResourceConfig(name="plate_car", catalog_ref="PLT_CAR_L5AC_A00", rail=7),
            ],
            labware_state={"ghost_plate": LabwareWellState(volumes={"A1": 150.0})},
        )

        with pytest.raises(ValueError, match="reconcile_deck_occupancy"):
            await driver.configure_deck(layout)
