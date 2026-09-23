"""Tests for T11: Pipetting Interface Richness.

Covers:
- MixParams and PipettingProfile frozen dataclass construction
- PLRLiquidHandlerWrapper: flow_rates, offsets_z, mix and blow-out passthrough
- PLRLiquidHandlerWrapper: mix() and discard_tips() operations
- LiquidHandlerSimMixin: accepts new params without crash
- PLRLiquidHandlerWrapper 96-head: aspirate96, pick_up_tips96
"""

import pytest
from unittest.mock import AsyncMock, patch
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well, WellBottomType
from pylabrobot.resources.tip_rack import TipRack, TipSpot, Tip
from pylabrobot.resources.utils import create_ordered_items_2d
from pylabrobot.resources.coordinate import Coordinate

from cheshire_drivers.pipetting import MixParams, PipettingProfile
from cheshire_drivers.liquid_handler_models import (
    AspirateRequest,
    AspirateTarget,
    Aspirate96Request,
    DeckLayoutConfig,
    DiscardTipsRequest,
    DispenseRequest,
    DispenseTarget,
    MixParamsModel,
    PipettingParameters,
    MixRequest,
    PickUpTipsRequest,
    PickUpTips96Request,
    TipPick,
)
from cheshire_drivers.plr.labware import PLRPlateAdapter, PLRTipRackAdapter
from cheshire_drivers.plr_tracker_seeding import swap_to_lenient_tracker
from cheshire_drivers.plr.liquid_handler import ChatterboxLiquidHandlerDriver
from cheshire_drivers.sims import SimLiquidHandlerDriver


# --- Test setup helper ---

async def _setup_chatterbox(driver: ChatterboxLiquidHandlerDriver,
                             plate: PLRPlateAdapter | None = None,
                             rack: PLRTipRackAdapter | None = None) -> None:
    """Configure deck (empty) and manually attach raw test resources by name.

    These tests use ad-hoc PLR Plate/TipRack objects (not catalog-defined ones),
    so we configure_deck with no resources and then assign the test fixtures
    to the deck directly via PLR's resource API. This makes them lookup-able by
    name during driver.aspirate(...) etc., without the deck-config catalog round-trip.

    Late-attached plates miss configure_deck's tracker-swap loop (which runs
    over whatever was on the deck at config time). With PLR's volume tracking
    now globally enabled by `configure_deck`, an unseeded stock tracker will
    raise `TooLittleLiquidError` on the first aspirate-from-empty. Swap the
    late-attached plate's wells to LenientVolumeTracker here so the test
    pattern (aspirate from a fresh test plate without pre-seeding) keeps
    working.
    """
    await driver.configure_deck(DeckLayoutConfig(deck_type="STARLet", resources=[]))
    if plate is not None:
        driver._lh.deck.assign_child_resource(plate._plate, location=Coordinate(100, 100, 100))
        for well in plate._plate.get_all_items():
            swap_to_lenient_tracker(well)
    if rack is not None:
        driver._lh.deck.assign_child_resource(rack._rack, location=Coordinate(300, 100, 100))


# --- Helpers ---

def _make_96_well_plate(name: str = "test_plate") -> Plate:
    ordered_items = create_ordered_items_2d(
        Well,
        num_items_x=12,
        num_items_y=8,
        dx=9.0, dy=9.0, dz=0.0,
        item_dx=9.0, item_dy=9.0,
        size_x=9.0, size_y=9.0, size_z=10.0,
        bottom_type=WellBottomType.FLAT,
    )
    return Plate(
        name=name,
        size_x=127.76, size_y=85.48, size_z=14.5,
        ordered_items=ordered_items,
    )


def _make_tip_rack(name: str = "test_tips") -> TipRack:
    def _make_tip(name: str = "tip", maximal_volume: float = 1000.0) -> Tip:
        return Tip(has_filter=False, total_tip_length=50.0, maximal_volume=maximal_volume, fitting_depth=8.0, name=name)

    ordered_items = create_ordered_items_2d(
        TipSpot,
        num_items_x=12,
        num_items_y=8,
        dx=9.0, dy=9.0, dz=0.0,
        item_dx=9.0, item_dy=9.0,
        size_x=9.0, size_y=9.0, size_z=10.0,
        make_tip=_make_tip,
    )
    return TipRack(
        name=name,
        size_x=127.76, size_y=85.48, size_z=20.0,
        ordered_items=ordered_items,
    )


# --- Dataclass construction tests ---

class TestMixParams:
    def test_construction(self) -> None:
        m = MixParams(volume=200.0, repetitions=90, flow_rate=300.0)
        assert m.volume == 200.0
        assert m.repetitions == 90
        assert m.flow_rate == 300.0

    def test_frozen(self) -> None:
        m = MixParams(volume=200.0, repetitions=90, flow_rate=300.0)
        with pytest.raises(AttributeError):
            m.volume = 100.0  # type: ignore[misc]


class TestPipettingProfile:
    """One shape carries both the liquid and the step, so a caller builds a
    profile once and reuses it. Every field is optional: None says nothing and
    inherits whatever the layer under it decided."""

    def test_a_fresh_profile_states_nothing(self) -> None:
        profile = PipettingProfile(name="water")
        assert profile.name == "water"
        assert profile.height is None
        assert profile.flow_rate is None
        assert profile.blow_out is None
        assert profile.mix is None

    def test_a_profile_carries_both_kinds_of_parameter(self) -> None:
        profile = PipettingProfile(
            name="viscous",
            flow_rate=25.0,
            height=-6.75,
            blow_out=True,
            blow_out_volume=20.0,
        )
        assert profile.flow_rate == 25.0
        assert profile.height == -6.75
        assert profile.blow_out is True
        assert profile.blow_out_volume == 20.0

    def test_frozen(self) -> None:
        profile = PipettingProfile(name="water")
        with pytest.raises(AttributeError):
            profile.name = "other"  # type: ignore[misc]


# --- PLR wrapper passthrough tests ---

class TestPLRWrapperAspirate:

    @pytest.fixture
    def driver(self) -> ChatterboxLiquidHandlerDriver:
        return ChatterboxLiquidHandlerDriver(num_channels=8)

    @pytest.fixture
    def plate(self) -> PLRPlateAdapter:
        return PLRPlateAdapter(_make_96_well_plate())

    @pytest.fixture
    def rack(self) -> PLRTipRackAdapter:
        return PLRTipRackAdapter(_make_tip_rack())

    @pytest.mark.asyncio
    async def test_aspirate_with_flow_rates(
        self, driver: ChatterboxLiquidHandlerDriver,
        plate: PLRPlateAdapter, rack: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, plate=plate, rack=rack)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))

        with patch.object(driver._lh, "aspirate", new_callable=AsyncMock) as mock_asp:
            await driver.aspirate(AspirateRequest(
                aspirations=[AspirateTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
                flow_rates=[25.0],
            ))
            mock_asp.assert_called_once()
            call_kwargs = mock_asp.call_args
            assert call_kwargs.kwargs["flow_rates"] == [25.0]

    @pytest.mark.asyncio
    async def test_aspirate_uses_the_resolved_flow_rate(
        self, driver: ChatterboxLiquidHandlerDriver,
        plate: PLRPlateAdapter, rack: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, plate=plate, rack=rack)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))

        with patch.object(driver._lh, "aspirate", new_callable=AsyncMock) as mock_asp:
            await driver.aspirate(AspirateRequest(
                aspirations=[AspirateTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
                parameters=PipettingParameters(flow_rate=10.0),
            ))
            mock_asp.assert_called_once()
            call_kwargs = mock_asp.call_args
            assert call_kwargs.kwargs["flow_rates"] == [10.0]

    @pytest.mark.asyncio
    async def test_per_channel_flow_rates_override_the_resolved_one(
        self, driver: ChatterboxLiquidHandlerDriver,
        plate: PLRPlateAdapter, rack: PLRTipRackAdapter,
    ) -> None:
        """A per-channel list is narrower than the one resolved for the step."""
        await _setup_chatterbox(driver, plate=plate, rack=rack)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))

        with patch.object(driver._lh, "aspirate", new_callable=AsyncMock) as mock_asp:
            await driver.aspirate(AspirateRequest(
                aspirations=[AspirateTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
                flow_rates=[99.0],
                parameters=PipettingParameters(flow_rate=10.0),
            ))
            mock_asp.assert_called_once()
            call_kwargs = mock_asp.call_args
            # The per-channel list wins over the step's resolved rate
            assert call_kwargs.kwargs["flow_rates"] == [99.0]

    @pytest.mark.asyncio
    async def test_aspirate_with_offsets_z(
        self, driver: ChatterboxLiquidHandlerDriver,
        plate: PLRPlateAdapter, rack: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, plate=plate, rack=rack)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))

        with patch.object(driver._lh, "aspirate", new_callable=AsyncMock) as mock_asp:
            await driver.aspirate(AspirateRequest(
                aspirations=[AspirateTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
                offsets_z=[-5.0],
            ))
            mock_asp.assert_called_once()
            offsets = mock_asp.call_args.kwargs["offsets"]
            assert len(offsets) == 1
            assert offsets[0].x == 0
            assert offsets[0].y == 0
            assert offsets[0].z == -5.0

    @pytest.mark.asyncio
    async def test_aspirate_with_mix_before(
        self, driver: ChatterboxLiquidHandlerDriver,
        plate: PLRPlateAdapter, rack: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, plate=plate, rack=rack)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))

        mix = MixParamsModel(volume=100.0, repetitions=3, flow_rate=200.0)
        with patch.object(driver._lh, "aspirate", new_callable=AsyncMock) as mock_asp:
            await driver.aspirate(AspirateRequest(
                aspirations=[AspirateTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
                parameters=PipettingParameters(mix=mix),
            ))
            mock_asp.assert_called_once()
            plr_mixes = mock_asp.call_args.kwargs["mix"]
            assert len(plr_mixes) == 1
            assert plr_mixes[0].volume == 100.0
            assert plr_mixes[0].repetitions == 3
            assert plr_mixes[0].flow_rate == 200.0


class TestPLRWrapperDispense:

    @pytest.fixture
    def driver(self) -> ChatterboxLiquidHandlerDriver:
        return ChatterboxLiquidHandlerDriver(num_channels=8)

    @pytest.fixture
    def plate(self) -> PLRPlateAdapter:
        return PLRPlateAdapter(_make_96_well_plate())

    @pytest.fixture
    def rack(self) -> PLRTipRackAdapter:
        return PLRTipRackAdapter(_make_tip_rack())

    @pytest.mark.asyncio
    async def test_dispense_with_blow_out(
        self, driver: ChatterboxLiquidHandlerDriver,
        plate: PLRPlateAdapter, rack: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, plate=plate, rack=rack)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))

        with patch.object(driver._lh, "dispense", new_callable=AsyncMock) as mock_disp:
            await driver.dispense(DispenseRequest(
                dispenses=[DispenseTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
                parameters=PipettingParameters(blow_out=True, blow_out_volume=15.0),
            ))
            mock_disp.assert_called_once()
            blow_out_vols = mock_disp.call_args.kwargs["blow_out_air_volume"]
            assert blow_out_vols == [15.0]

    @pytest.mark.asyncio
    async def test_a_blow_out_takes_its_air_in_before_it_expels_it(
        self, driver: ChatterboxLiquidHandlerDriver,
        plate: PLRPlateAdapter, rack: PLRTipRackAdapter,
    ) -> None:
        """Unmocked, so volume tracking is real. This backend expels air the tip
        already holds, so an aspirate that skipped the air leaves the dispense
        expelling something that was never taken in."""
        await _setup_chatterbox(driver, plate=plate, rack=rack)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))
        parameters = PipettingParameters(blow_out=True, blow_out_volume=15.0)

        with patch.object(driver._lh, "aspirate", wraps=driver._lh.aspirate) as mock_asp:
            await driver.aspirate(AspirateRequest(
                aspirations=[AspirateTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
                parameters=parameters,
            ))
        response = await driver.dispense(DispenseRequest(
            dispenses=[DispenseTarget(labware="test_plate", positions=["B1"], volumes=[50.0])],
            parameters=parameters,
        ))

        # The dispense is what has teeth: PyLabRobot raises BlowOutVolumeError if the
        # tip is asked to expel air no aspirate took in.
        assert mock_asp.call_args.kwargs["blow_out_air_volume"] == [15.0]
        assert response.success

    @pytest.mark.asyncio
    async def test_a_blow_out_with_no_volume_is_refused_rather_than_dropped(
        self, driver: ChatterboxLiquidHandlerDriver,
        plate: PLRPlateAdapter, rack: PLRTipRackAdapter,
    ) -> None:
        """This backend has no way to blow out except by expelling a named air
        volume, so accepting the request and doing nothing would leave liquid in
        a tip the caller believes it emptied."""
        await _setup_chatterbox(driver, plate=plate, rack=rack)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))

        with pytest.raises(ValueError, match="blow_out_volume"):
            await driver.dispense(DispenseRequest(
                dispenses=[DispenseTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
                parameters=PipettingParameters(blow_out=True),
            ))

    @pytest.mark.asyncio
    async def test_a_blow_out_rate_this_backend_cannot_set_goes_unread(
        self, driver: ChatterboxLiquidHandlerDriver,
        plate: PLRPlateAdapter, rack: PLRTipRackAdapter,
    ) -> None:
        """The Flex sets a blow-out rate and this backend cannot, and one record
        travels across both. So the field goes unread here rather than refusing a
        profile that is correct for the other machine."""
        await _setup_chatterbox(driver, plate=plate, rack=rack)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))
        parameters = PipettingParameters(
            blow_out=True, blow_out_volume=15.0, blow_out_flow_rate=30.0
        )

        await driver.aspirate(AspirateRequest(
            aspirations=[AspirateTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
            parameters=parameters,
        ))
        with patch.object(driver._lh, "dispense", wraps=driver._lh.dispense) as mock_disp:
            response = await driver.dispense(DispenseRequest(
                dispenses=[DispenseTarget(labware="test_plate", positions=["B1"], volumes=[50.0])],
                parameters=parameters,
            ))

        assert response.success
        assert mock_disp.call_args.kwargs["blow_out_air_volume"] == [15.0]
        assert "blow_out_flow_rate" not in mock_disp.call_args.kwargs


class TestPLRWrapperMix:

    @pytest.fixture
    def driver(self) -> ChatterboxLiquidHandlerDriver:
        return ChatterboxLiquidHandlerDriver(num_channels=8)

    @pytest.fixture
    def plate(self) -> PLRPlateAdapter:
        return PLRPlateAdapter(_make_96_well_plate())

    @pytest.fixture
    def rack(self) -> PLRTipRackAdapter:
        return PLRTipRackAdapter(_make_tip_rack())

    @pytest.mark.asyncio
    async def test_mix_calls_zero_volume_aspirate(
        self, driver: ChatterboxLiquidHandlerDriver,
        plate: PLRPlateAdapter, rack: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, plate=plate, rack=rack)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))

        with patch.object(driver._lh, "aspirate", new_callable=AsyncMock) as mock_asp:
            await driver.mix(MixRequest(
                labware="test_plate", positions=["A1"],
                volume=100.0, repetitions=5,
                parameters=PipettingParameters(flow_rate=200.0),
            ))
            mock_asp.assert_called_once()
            call_kwargs = mock_asp.call_args
            assert call_kwargs.kwargs["vols"] == [0.0]
            plr_mixes = call_kwargs.kwargs["mix"]
            assert len(plr_mixes) == 1
            assert plr_mixes[0].volume == 100.0
            assert plr_mixes[0].repetitions == 5


class TestPLRWrapperDiscardTips:

    @pytest.fixture
    def driver(self) -> ChatterboxLiquidHandlerDriver:
        return ChatterboxLiquidHandlerDriver(num_channels=8)

    @pytest.fixture
    def rack(self) -> PLRTipRackAdapter:
        return PLRTipRackAdapter(_make_tip_rack())

    @pytest.mark.asyncio
    async def test_discard_tips_delegates_to_plr(
        self, driver: ChatterboxLiquidHandlerDriver,
        rack: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, rack=rack)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))

        with patch.object(driver._lh, "discard_tips", new_callable=AsyncMock) as mock_discard:
            await driver.discard_tips(DiscardTipsRequest(use_channels=[0]))
            mock_discard.assert_called_once_with(
                use_channels=[0], allow_nonzero_volume=True,
            )


# --- Sim driver tests ---

class TestSimLiquidHandlerNewParams:

    @pytest.mark.asyncio
    async def test_aspirate_accepts_all_new_params(self) -> None:
        driver = SimLiquidHandlerDriver("test_lh")
        await driver.initialize()

        mix = MixParamsModel(volume=50.0, repetitions=3, flow_rate=100.0)

        # Should not raise
        await driver.aspirate(AspirateRequest(
            aspirations=[AspirateTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
            flow_rates=[25.0],
            offsets_z=[-3.0],
            use_channels=[0],
            parameters=PipettingParameters(flow_rate=25.0, mix=mix),
        ))

    @pytest.mark.asyncio
    async def test_dispense_accepts_all_new_params(self) -> None:
        driver = SimLiquidHandlerDriver("test_lh")
        await driver.initialize()

        mix = MixParamsModel(volume=50.0, repetitions=3, flow_rate=100.0)

        await driver.dispense(DispenseRequest(
            dispenses=[DispenseTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
            flow_rates=[50.0],
            offsets_z=[-3.0],
            use_channels=[0],
            parameters=PipettingParameters(
                flow_rate=50.0, mix=mix, blow_out=True, blow_out_volume=15.0
            ),
        ))

    @pytest.mark.asyncio
    async def test_mix_no_op(self) -> None:
        driver = SimLiquidHandlerDriver("test_lh")
        await driver.initialize()
        await driver.mix(MixRequest(
            labware="test_plate", positions=["A1"],
            volume=100.0, repetitions=5,
            parameters=PipettingParameters(flow_rate=200.0),
        ))

    @pytest.mark.asyncio
    async def test_discard_tips_no_op(self) -> None:
        driver = SimLiquidHandlerDriver("test_lh")
        await driver.initialize()
        await driver.discard_tips(DiscardTipsRequest(use_channels=[0]))


# --- Bulk handler tests ---

class TestPLRBulkLiquidHandlerWrapper:
    """96-head ops on PLRLiquidHandlerWrapper directly (no separate bulk handler
    class anymore — `IBulkLiquidHandler` was merged into `ILiquidHandler`)."""

    @pytest.fixture
    def driver(self) -> ChatterboxLiquidHandlerDriver:
        return ChatterboxLiquidHandlerDriver(num_channels=8)

    @pytest.fixture
    def plate(self) -> PLRPlateAdapter:
        return PLRPlateAdapter(_make_96_well_plate())

    @pytest.fixture
    def rack(self) -> PLRTipRackAdapter:
        return PLRTipRackAdapter(_make_tip_rack())

    # NOTE: test_aspirate96_with_offset was deleted 2026-04-25.
    # The Aspirate96Request wire model does NOT carry `offset` (removed in the
    # cheshire-drivers Pydantic refactor 77d445a — only volume / flow_rate /
    # liquid_height remain). The PLR aspirate96 wrapper consequently does not
    # propagate any offset. The feature this test verified no longer exists.

    @pytest.mark.asyncio
    async def test_pick_up_tips96(
        self, driver: ChatterboxLiquidHandlerDriver,
        rack: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, rack=rack)

        with patch.object(driver._lh, "pick_up_tips96", new_callable=AsyncMock) as mock_pickup:
            await driver.pick_up_tips96(PickUpTips96Request(tip_rack="test_tips"))
            mock_pickup.assert_called_once()


# --- Multi-rack 8-channel tests (prevents silent rack[0] selection) ---

class TestPLRWrapperMultiRack:
    """Verify the PLR wrapper passes channels from multiple racks/plates to PLR
    in one physical call. The audit found that the wire shape collapsed multi-
    rack requests to the first rack only, producing wrong-answer experiments
    that completed without error. The new wire shape carries a list of per-
    labware target slices; the wrapper flattens them into PLR's per-channel
    parallel lists in target order.
    """

    @pytest.fixture
    def driver(self) -> ChatterboxLiquidHandlerDriver:
        return ChatterboxLiquidHandlerDriver(num_channels=8)

    @pytest.fixture
    def plate_a(self) -> PLRPlateAdapter:
        return PLRPlateAdapter(_make_96_well_plate(name="plate_a"))

    @pytest.fixture
    def plate_b(self) -> PLRPlateAdapter:
        return PLRPlateAdapter(_make_96_well_plate(name="plate_b"))

    @pytest.fixture
    def rack_a(self) -> PLRTipRackAdapter:
        return PLRTipRackAdapter(_make_tip_rack(name="rack_a"))

    @pytest.fixture
    def rack_b(self) -> PLRTipRackAdapter:
        return PLRTipRackAdapter(_make_tip_rack(name="rack_b"))

    @pytest.mark.asyncio
    async def test_pick_up_tips_spans_two_racks(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        rack_a: PLRTipRackAdapter,
        rack_b: PLRTipRackAdapter,
    ) -> None:
        await driver.configure_deck(DeckLayoutConfig(deck_type="STARLet", resources=[]))
        from pylabrobot.resources.coordinate import Coordinate
        driver._lh.deck.assign_child_resource(rack_a._rack, location=Coordinate(100, 100, 100))
        driver._lh.deck.assign_child_resource(rack_b._rack, location=Coordinate(300, 100, 100))

        with patch.object(driver._lh, "pick_up_tips", new_callable=AsyncMock) as mock_pick:
            await driver.pick_up_tips(PickUpTipsRequest(picks=[
                TipPick(tip_rack="rack_a", positions=["A1", "B1"]),
                TipPick(tip_rack="rack_b", positions=["C1", "D1"]),
            ]))
            mock_pick.assert_called_once()
            spots = mock_pick.call_args.args[0]
            assert len(spots) == 4
            # Channel order: rack_a A1/B1 then rack_b C1/D1
            assert spots[0].parent.name == "rack_a"
            assert spots[1].parent.name == "rack_a"
            assert spots[2].parent.name == "rack_b"
            assert spots[3].parent.name == "rack_b"

    @pytest.mark.asyncio
    async def test_aspirate_spans_two_plates(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        plate_a: PLRPlateAdapter,
        plate_b: PLRPlateAdapter,
        rack_a: PLRTipRackAdapter,
    ) -> None:
        await driver.configure_deck(DeckLayoutConfig(deck_type="STARLet", resources=[]))
        from pylabrobot.resources.coordinate import Coordinate
        driver._lh.deck.assign_child_resource(plate_a._plate, location=Coordinate(100, 100, 100))
        driver._lh.deck.assign_child_resource(plate_b._plate, location=Coordinate(300, 100, 100))
        driver._lh.deck.assign_child_resource(rack_a._rack, location=Coordinate(500, 100, 100))
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="rack_a", positions=["A1", "B1"])]))

        with patch.object(driver._lh, "aspirate", new_callable=AsyncMock) as mock_asp:
            await driver.aspirate(AspirateRequest(aspirations=[
                AspirateTarget(labware="plate_a", positions=["A1"], volumes=[50.0]),
                AspirateTarget(labware="plate_b", positions=["A1"], volumes=[25.0]),
            ]))
            mock_asp.assert_called_once()
            wells = mock_asp.call_args.args[0]
            assert len(wells) == 2
            assert wells[0].parent.name == "plate_a"
            assert wells[1].parent.name == "plate_b"
            assert mock_asp.call_args.kwargs["vols"] == [50.0, 25.0]

    @pytest.mark.asyncio
    async def test_dispense_spans_two_plates(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        plate_a: PLRPlateAdapter,
        plate_b: PLRPlateAdapter,
        rack_a: PLRTipRackAdapter,
    ) -> None:
        await driver.configure_deck(DeckLayoutConfig(deck_type="STARLet", resources=[]))
        from pylabrobot.resources.coordinate import Coordinate
        driver._lh.deck.assign_child_resource(plate_a._plate, location=Coordinate(100, 100, 100))
        driver._lh.deck.assign_child_resource(plate_b._plate, location=Coordinate(300, 100, 100))
        driver._lh.deck.assign_child_resource(rack_a._rack, location=Coordinate(500, 100, 100))
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="rack_a", positions=["A1", "B1"])]))

        with patch.object(driver._lh, "dispense", new_callable=AsyncMock) as mock_disp:
            await driver.dispense(DispenseRequest(dispenses=[
                DispenseTarget(labware="plate_a", positions=["A1", "A2"], volumes=[10.0, 20.0]),
                DispenseTarget(labware="plate_b", positions=["A1"], volumes=[15.0]),
            ]))
            mock_disp.assert_called_once()
            wells = mock_disp.call_args.args[0]
            assert len(wells) == 3
            assert wells[0].parent.name == "plate_a"
            assert wells[1].parent.name == "plate_a"
            assert wells[2].parent.name == "plate_b"
            assert mock_disp.call_args.kwargs["vols"] == [10.0, 20.0, 15.0]


class TestRequestModelValidators:
    """Pin the model_validators on the new wire shape so silently-broken inputs
    are caught at the boundary."""

    def test_aspirate_rejects_empty_aspirations(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            AspirateRequest(aspirations=[])

    def test_aspirate_target_rejects_position_volume_mismatch(self) -> None:
        with pytest.raises(ValueError, match="must be the same length"):
            AspirateTarget(labware="p", positions=["A1", "A2"], volumes=[10.0])

    def test_aspirate_rejects_flow_rate_length_mismatch(self) -> None:
        with pytest.raises(ValueError, match="flow_rates length"):
            AspirateRequest(
                aspirations=[
                    AspirateTarget(labware="p", positions=["A1", "A2"], volumes=[10.0, 20.0]),
                    AspirateTarget(labware="q", positions=["A1"], volumes=[5.0]),
                ],
                flow_rates=[1.0, 2.0],  # need 3
            )

    def test_dispense_rejects_empty_dispenses(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            DispenseRequest(dispenses=[])

    def test_pick_up_tips_rejects_empty_picks(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            PickUpTipsRequest(picks=[])
