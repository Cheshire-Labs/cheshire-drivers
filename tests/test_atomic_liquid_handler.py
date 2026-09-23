"""Tests for ILiquidHandlerDriver interface and PLR liquid handler drivers.

Covers:
- ChatterboxLiquidHandlerDriver: aspirate, dispense, pick_up_tips, drop_tips
- SimLiquidHandlerDriver: atomic ops work (no-op sim)
- PLRLiquidHandlerWrapper: name-based labware lookup via configured deck
"""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well, WellBottomType
from pylabrobot.resources.tip_rack import TipRack, TipSpot, Tip
from pylabrobot.resources.resource import Coordinate
from pylabrobot.resources.utils import create_ordered_items_2d

from cheshire_drivers.interfaces import ILiquidHandlerDriver
from cheshire_drivers.liquid_handler_models import (
    AspirateRequest,
    AspirateTarget,
    DeckLayoutConfig,
    DispenseRequest,
    DispenseTarget,
    DropTipsRequest,
    LiquidProbeRequest,
    LiquidProbeResponse,
    MixRequest,
    PickUpTipsRequest,
    PipettingParameters,
    TipPick,
)
from cheshire_drivers.lh_request_validation import normalize_lh_flow_rate
from cheshire_drivers.plr.labware import PLRPlateAdapter, PLRTipRackAdapter
from cheshire_drivers.plr_tracker_seeding import swap_to_lenient_tracker
from cheshire_drivers.plr.liquid_handler import ChatterboxLiquidHandlerDriver
from cheshire_drivers.sims import (
    SIM_LIQUID_HEIGHT_MM,
    SimLiquidHandlerDriver,
    SimLiquidHandlerWithProtocolDriver,
)
from cheshire_drivers.protocol_runner_models import RunProtocolRequest


# --- Test setup helper ---

async def _setup_chatterbox(driver: ChatterboxLiquidHandlerDriver,
                             plate: PLRPlateAdapter | None = None,
                             rack: PLRTipRackAdapter | None = None) -> None:
    """Configure deck (empty) and manually attach raw test resources by name.

    Late-attached plates miss configure_deck's tracker-swap loop (which runs
    over whatever was on the deck at config time). With PLR volume tracking
    now globally enabled by `configure_deck`, an unseeded stock tracker would
    raise `TooLittleLiquidError` on the first aspirate-from-empty. Swap the
    late-attached plate's wells to LenientVolumeTracker so the test pattern
    (aspirate from a fresh test plate without pre-seeding) keeps working.
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


# --- ChatterboxLiquidHandlerDriver ---

class TestChatterboxLiquidHandlerDriver:

    def test_implements_liquid_handler_interface(self) -> None:
        driver = ChatterboxLiquidHandlerDriver()
        assert isinstance(driver, ILiquidHandlerDriver)

    @pytest.fixture
    def driver(self) -> ChatterboxLiquidHandlerDriver:
        return ChatterboxLiquidHandlerDriver(num_channels=8)

    @pytest.fixture
    def plate_adapter(self) -> PLRPlateAdapter:
        return PLRPlateAdapter(_make_96_well_plate())

    @pytest.fixture
    def rack_adapter(self) -> PLRTipRackAdapter:
        return PLRTipRackAdapter(_make_tip_rack())

    @pytest.mark.asyncio
    async def test_initialize_and_teardown(self, driver: ChatterboxLiquidHandlerDriver) -> None:
        assert not driver.is_initialized
        await _setup_chatterbox(driver)
        assert driver.is_initialized

    @pytest.mark.asyncio
    async def test_pick_up_and_drop_tips(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        rack_adapter: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, rack=rack_adapter)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))
        await driver.drop_tips(DropTipsRequest(drops=[TipPick(tip_rack="test_tips", positions=["A1"])], to_waste=False))

    @pytest.mark.asyncio
    async def test_a_return_empties_the_channels_it_was_given(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        rack_adapter: PLRTipRackAdapter,
    ) -> None:
        """`use_channels` was accepted on the request and never passed on.

        The caller named channels 3 and 5, the head emptied 0 and 1, and orca's
        record said 3 and 5 were emptied. The two real tips stayed on the
        machine with the record insisting they were gone.
        """
        await _setup_chatterbox(driver, rack=rack_adapter)
        picks = [TipPick(tip_rack="test_tips", positions=["A1", "B1"])]
        await driver.pick_up_tips(
            PickUpTipsRequest(picks=picks, use_channels=[3, 5])
        )
        head = driver._lh.head
        assert [c for c, t in head.items() if t.has_tip] == [3, 5]

        await driver.drop_tips(
            DropTipsRequest(drops=picks, to_waste=False, use_channels=[3, 5])
        )

        assert [c for c, t in head.items() if t.has_tip] == []

    @pytest.mark.asyncio
    async def test_a_return_leaves_the_channels_it_was_not_given(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        rack_adapter: PLRTipRackAdapter,
    ) -> None:
        """The silent case: more channels loaded than the return names.

        With only the named channels loaded the old code raised, which is at
        least loud. With others loaded it raised nothing and returned the wrong
        two tips to the rack.
        """
        await _setup_chatterbox(driver, rack=rack_adapter)
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="test_tips", positions=["A1", "B1", "C1", "D1"])],
            use_channels=[0, 1, 3, 5],
        ))

        await driver.drop_tips(DropTipsRequest(
            drops=[TipPick(tip_rack="test_tips", positions=["C1", "D1"])],
            to_waste=False, use_channels=[3, 5],
        ))

        head = driver._lh.head
        assert [c for c, t in head.items() if t.has_tip] == [0, 1]

    @pytest.mark.asyncio
    async def test_a_waste_drop_bins_every_tip_the_head_is_holding(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        rack_adapter: PLRTipRackAdapter,
    ) -> None:
        """One trash resource is one tip_spot, so PLR emptied channel 0 alone.

        A head carrying four tips came back carrying three, and orca's record
        says a waste drop empties the head, so it read as bare while three tips
        were still on it.
        """
        await _setup_chatterbox(driver, rack=rack_adapter)
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="test_tips", positions=["A1", "B1", "C1", "D1"])],
            use_channels=[0, 1, 3, 5],
        ))

        await driver.drop_tips(DropTipsRequest(to_waste=True))

        head = driver._lh.head
        assert [c for c, t in head.items() if t.has_tip] == []

    @pytest.mark.asyncio
    async def test_a_waste_drop_on_a_bare_head_says_the_head_is_bare(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        rack_adapter: PLRTipRackAdapter,
    ) -> None:
        """It refused before too, naming channel zero, because the old path
        asked channel zero for a tip it had never held. The head being empty is
        the reason, so that is what the refusal has to say."""
        await _setup_chatterbox(driver, rack=rack_adapter)

        with pytest.raises(RuntimeError, match="No tips have been picked up"):
            await driver.drop_tips(DropTipsRequest(to_waste=True))

    @pytest.mark.asyncio
    async def test_a_waste_drop_bins_only_the_channels_it_was_given(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        rack_adapter: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, rack=rack_adapter)
        await driver.pick_up_tips(PickUpTipsRequest(
            picks=[TipPick(tip_rack="test_tips", positions=["A1", "B1", "C1"])],
            use_channels=[0, 3, 5],
        ))

        await driver.drop_tips(DropTipsRequest(to_waste=True, use_channels=[3, 5]))

        head = driver._lh.head
        assert [c for c, t in head.items() if t.has_tip] == [0]

    @pytest.mark.asyncio
    async def test_aspirate_and_dispense(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        plate_adapter: PLRPlateAdapter,
        rack_adapter: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, plate=plate_adapter, rack=rack_adapter)
        # Must pick up tips before aspirate/dispense
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))

        await driver.aspirate(AspirateRequest(aspirations=[AspirateTarget(labware="test_plate", positions=["A1"], volumes=[50.0])]))
        await driver.dispense(DispenseRequest(dispenses=[DispenseTarget(labware="test_plate", positions=["A1"], volumes=[50.0])]))

        await driver.drop_tips(DropTipsRequest(drops=[TipPick(tip_rack="test_tips", positions=["A1"])], to_waste=False))

    @pytest.mark.asyncio
    async def test_multi_well_aspirate_dispense(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        plate_adapter: PLRPlateAdapter,
        rack_adapter: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, plate=plate_adapter, rack=rack_adapter)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1", "B1"])]))

        await driver.aspirate(AspirateRequest(aspirations=[AspirateTarget(labware="test_plate", positions=["A1", "A2"], volumes=[25.0, 50.0])]))
        await driver.dispense(DispenseRequest(dispenses=[DispenseTarget(labware="test_plate", positions=["B1", "B2"], volumes=[25.0, 50.0])]))

        await driver.drop_tips(DropTipsRequest(drops=[TipPick(tip_rack="test_tips", positions=["A1", "B1"])], to_waste=False))


class TestTheGenericPathCarriesAPipetteTechnique:
    """What `technique.height` becomes on the wrapper every non-Flex handler uses.

    It lands as a PyLabRobot `offsets` z, which is a TRANSLATION from wherever that
    backend already puts the tip, not the absolute height above the cavity floor the
    Flex driver sends. The two agree on which knob a caller turns and disagree on
    what the number is measured from, so these pin the mapping rather than leave the
    only statement of it in an article.
    """

    @pytest.fixture
    def driver(self) -> ChatterboxLiquidHandlerDriver:
        return ChatterboxLiquidHandlerDriver(num_channels=8)

    @pytest.fixture
    def plate_adapter(self) -> PLRPlateAdapter:
        return PLRPlateAdapter(_make_96_well_plate())

    @pytest.fixture
    def rack_adapter(self) -> PLRTipRackAdapter:
        return PLRTipRackAdapter(_make_tip_rack())

    @pytest.mark.asyncio
    async def test_a_technique_height_becomes_the_aspirate_offset(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        plate_adapter: PLRPlateAdapter,
        rack_adapter: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, plate=plate_adapter, rack=rack_adapter)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))
        driver._lh.aspirate = AsyncMock()

        await driver.aspirate(AspirateRequest(
            aspirations=[AspirateTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
            parameters=PipettingParameters(height=6.5),
        ))

        assert driver._lh.aspirate.call_args.kwargs["offsets"] == [Coordinate(0, 0, 6.5)]

    @pytest.mark.asyncio
    async def test_a_dispense_reads_the_same_resolved_height(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        plate_adapter: PLRPlateAdapter,
        rack_adapter: PLRTipRackAdapter,
    ) -> None:
        """Aspirate and dispense have to read the record the same way, or one
        request means two heights depending on which half of it is running.

        Which layer won is settled before the wire now (see
        tests/test_pipetting_parameters.py); by here there is one number."""
        await _setup_chatterbox(driver, plate=plate_adapter, rack=rack_adapter)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))
        driver._lh.dispense = AsyncMock()

        await driver.dispense(DispenseRequest(
            dispenses=[DispenseTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
            parameters=PipettingParameters(height=6.5),
        ))

        assert driver._lh.dispense.call_args.kwargs["offsets"] == [Coordinate(0, 0, 6.5)]

    @pytest.mark.asyncio
    async def test_per_channel_offsets_still_outrank_the_technique(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        plate_adapter: PLRPlateAdapter,
        rack_adapter: PLRTipRackAdapter,
    ) -> None:
        await _setup_chatterbox(driver, plate=plate_adapter, rack=rack_adapter)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))
        driver._lh.aspirate = AsyncMock()

        await driver.aspirate(AspirateRequest(
            aspirations=[AspirateTarget(labware="test_plate", positions=["A1"], volumes=[50.0])],
            offsets_z=[9.0],
            parameters=PipettingParameters(height=6.5),
        ))

        assert driver._lh.aspirate.call_args.kwargs["offsets"] == [Coordinate(0, 0, 9.0)]

    @pytest.mark.asyncio
    async def test_a_mix_cycles_at_the_height_it_was_given(
        self,
        driver: ChatterboxLiquidHandlerDriver,
        plate_adapter: PLRPlateAdapter,
        rack_adapter: PLRTipRackAdapter,
    ) -> None:
        """A mix is aspirate/dispense cycles, so it takes the same knob; the surface
        above had no way to say it until this change."""
        await _setup_chatterbox(driver, plate=plate_adapter, rack=rack_adapter)
        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))
        driver._lh.aspirate = AsyncMock()

        await driver.mix(MixRequest(
            labware="test_plate", positions=["A1"], volume=20.0, repetitions=2,
            parameters=PipettingParameters(height=6.5),
        ))

        assert driver._lh.aspirate.call_args.kwargs["offsets"] == [Coordinate(0, 0, 6.5)]


# --- SimLiquidHandlerDriver ---

class TestSimLiquidHandlerDriver:

    def test_implements_liquid_handler_interface(self) -> None:
        driver = SimLiquidHandlerDriver("test_lh")
        assert isinstance(driver, ILiquidHandlerDriver)

    @pytest.mark.asyncio
    async def test_atomic_ops_no_error(self) -> None:
        driver = SimLiquidHandlerDriver("test_lh")
        await driver.initialize()

        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))
        await driver.aspirate(AspirateRequest(aspirations=[AspirateTarget(labware="test_plate", positions=["A1"], volumes=[50.0])]))
        await driver.dispense(DispenseRequest(dispenses=[DispenseTarget(labware="test_plate", positions=["B1"], volumes=[50.0])]))
        await driver.drop_tips(DropTipsRequest(drops=[TipPick(tip_rack="test_tips", positions=["A1"])], to_waste=False))

    @pytest.mark.asyncio
    async def test_sim_lh_does_not_advertise_iprotocolrunner(self) -> None:
        driver = SimLiquidHandlerDriver("test_lh")
        assert "ILiquidHandler" in driver.interfaces
        assert "IProtocolRunner" not in driver.interfaces
        assert not hasattr(driver, "run_protocol")


class TestSimLiquidHandlerWithProtocolDriver:
    """The composite Sim driver that satisfies both ILiquidHandlerDriver
    and IProtocolRunnerDriver in one class. Pairs with the orca-core
    `LiquidHandler` device which inherits both `ILiquidHandler` and
    `IProtocolRunner`."""

    @pytest.mark.asyncio
    async def test_atomic_ops_and_run_protocol_both_work(self) -> None:
        driver = SimLiquidHandlerWithProtocolDriver("test_lh")
        await driver.initialize()

        await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="test_tips", positions=["A1"])]))
        await driver.aspirate(AspirateRequest(aspirations=[AspirateTarget(labware="test_plate", positions=["A1"], volumes=[50.0])]))
        await driver.run_protocol(RunProtocolRequest(protocol_filepath="capture.pro", params={}))
        await driver.dispense(DispenseRequest(dispenses=[DispenseTarget(labware="test_plate", positions=["B1"], volumes=[50.0])]))
        await driver.drop_tips(DropTipsRequest(drops=[TipPick(tip_rack="test_tips", positions=["A1"])], to_waste=False))


class TestSimLiquidProbe:
    """The sim advertises ILiquidProbe so a probe-then-pipette workflow can be
    written and run with no hardware. It models no liquid, so the height it
    reports is a fixed stand-in, not a measurement."""

    @pytest.mark.asyncio
    async def test_a_sim_probe_answers_with_the_stand_in_height(self) -> None:
        driver = SimLiquidHandlerDriver("test_lh")
        await driver.initialize()

        response = await driver.liquid_probe(
            LiquidProbeRequest(labware="test_plate", positions=["A1"])
        )

        assert response.height == SIM_LIQUID_HEIGHT_MM
        assert "ILiquidProbe" in driver.interfaces

    @pytest.mark.asyncio
    async def test_a_container_probe_needs_no_positions(self) -> None:
        driver = SimLiquidHandlerDriver("test_lh")
        await driver.initialize()

        response = await driver.liquid_probe(
            LiquidProbeRequest(labware="test_trough", use_channels=[0])
        )

        assert response.height == SIM_LIQUID_HEIGHT_MM


class TestTheWireRefusesWhatWouldQuietlyDoNothing:
    """Three refusals that all fail the same way if they go missing: the call is
    accepted, reaches the machine, and nothing that happened is what was asked."""

    def test_a_flow_rate_that_is_not_a_number_is_refused(self) -> None:
        """The scalar is spread across every channel. A non-number that got
        through would be spread too, and the refusal would come from pydantic
        naming `flow_rates`, a field the caller never sent."""
        with pytest.raises(ValueError, match="flow_rate must be a number"):
            normalize_lh_flow_rate(
                {
                    "flow_rate": {"value": 5},
                    "aspirations": [{"labware": "p", "positions": ["A1"], "volumes": [10.0]}],
                },
                AspirateRequest,
            )

    def test_a_numeric_string_flow_rate_is_read_as_the_number(self) -> None:
        """`flow_rates` goes through pydantic, which reads "5" as 5.0, so the
        scalar that expands into it must not be stricter than the field it becomes."""
        expanded = normalize_lh_flow_rate(
            {
                "flow_rate": "5",
                "aspirations": [{"labware": "p", "positions": ["A1", "A2"], "volumes": [10.0, 10.0]}],
            },
            AspirateRequest,
        )

        assert expanded["flow_rates"] == [5.0, 5.0]
        assert "flow_rate" not in expanded

    def test_a_bool_flow_rate_is_refused_rather_than_read_as_one(self) -> None:
        with pytest.raises(ValueError, match="flow_rate must be a number, got bool"):
            normalize_lh_flow_rate(
                {
                    "flow_rate": True,
                    "aspirations": [{"labware": "p", "positions": ["A1"], "volumes": [10.0]}],
                },
                AspirateRequest,
            )

    def test_a_mix_with_an_empty_position_list_is_refused(self) -> None:
        """Zero positions is zero channels: the mix would run on nothing and
        report success."""
        with pytest.raises(ValidationError, match="positions must name at least one well"):
            MixRequest(labware="test_plate", positions=[], volume=50.0, repetitions=3)

    def test_a_probe_answer_that_states_no_height_at_all_is_refused(self) -> None:
        """None is a real answer here -- the probe reached the floor and found
        nothing -- so a driver that simply omits the field must not be read as
        having said that."""
        with pytest.raises(ValidationError):
            LiquidProbeResponse.model_validate({})

        assert LiquidProbeResponse.model_validate({"height": None}).height is None
