"""LH deck occupancy primitives: reset_deck_labware + reconcile_deck_occupancy.

The driver deck is a projection of the engine ledger. These two ops let the
engine wipe stale occupancy (panic clear-all) and reconcile the deck to an
authoritative occupancy set, without rebuilding the whole deck. Carriers
(deck structure) are always preserved.
"""

import pytest
from pydantic import ValidationError

from cheshire_drivers.lh_request_validation import (
    LH_REQUEST_MODELS,
    LH_RESPONSE_MODELS,
    wrap_lh_payload,
)
from cheshire_drivers.liquid_handler_models import (
    DeckLayoutConfig,
    DeckResourceConfig,
    LabwareStateResponse,
    AddDeckLabwareRequest,
    GetDeckStateRequest,
    MovePlateRequest,
    DropTipsRequest,
    PickUpTipsRequest,
    ReconcileDeckOccupancyRequest,
    RemoveDeckLabwareRequest,
    ResetDeckLabwareRequest,
    TipPick,
)
from cheshire_drivers.plr.liquid_handler import ChatterboxLiquidHandlerDriver


def _carrier(name: str = "carrier-25", rail: int = 25) -> DeckResourceConfig:
    return DeckResourceConfig(name=name, catalog_ref="PLT_CAR_L5AC_A00", rail=rail)


def _plate(name: str, parent_id: str = "carrier-25", site_index: int = 0) -> DeckResourceConfig:
    return DeckResourceConfig(
        name=name, catalog_ref="Cor_96_wellplate_360ul_Fb",
        parent_id=parent_id, site_index=site_index,
    )


async def _driver(carriers: list[DeckResourceConfig]) -> ChatterboxLiquidHandlerDriver:
    """Configure a carriers-only deck. Labware reaches the deck only via the
    occupancy wire format (reconcile_deck_occupancy), never the layout."""
    driver = ChatterboxLiquidHandlerDriver()
    await driver.configure_deck(DeckLayoutConfig(deck_type="STARLet", resources=carriers))
    return driver


async def _seed_occupancy(
    driver: ChatterboxLiquidHandlerDriver, plates: list[DeckResourceConfig],
) -> None:
    """Place labware on the deck via the live occupancy wire format."""
    await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=plates))


def _names(driver: ChatterboxLiquidHandlerDriver) -> set[str]:
    assert driver._lh is not None
    return {r.name for r in driver._lh.deck.get_all_resources()}


class TestResetDeckLabware:
    @pytest.mark.asyncio
    async def test_reset_removes_occupancy_keeps_carrier(self) -> None:
        driver = await _driver([_carrier("carrier-25", rail=25)])
        await _seed_occupancy(driver, [
            _plate("plate_1", site_index=0),
            _plate("plate_2", site_index=1),
        ])
        assert {"carrier-25", "plate_1", "plate_2"} <= _names(driver)

        await driver.reset_deck_labware(ResetDeckLabwareRequest())

        names = _names(driver)
        assert "carrier-25" in names
        assert "plate_1" not in names
        assert "plate_2" not in names

    @pytest.mark.asyncio
    async def test_reset_is_idempotent(self) -> None:
        driver = await _driver([_carrier()])
        await _seed_occupancy(driver, [_plate("plate_1")])
        await driver.reset_deck_labware(ResetDeckLabwareRequest())
        await driver.reset_deck_labware(ResetDeckLabwareRequest())
        names = _names(driver)
        assert "carrier-25" in names
        assert "plate_1" not in names

    @pytest.mark.asyncio
    async def test_reset_on_unconfigured_deck_is_noop_success(self) -> None:
        driver = ChatterboxLiquidHandlerDriver()
        response = await driver.reset_deck_labware(ResetDeckLabwareRequest())
        assert isinstance(response, LabwareStateResponse)
        assert response.success is True


class TestReconcileDeckOccupancy:
    @pytest.mark.asyncio
    async def test_reconcile_places_the_given_set(self) -> None:
        driver = await _driver([_carrier()])
        assert "plate_1" not in _names(driver)

        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_plate("plate_1", site_index=0)])
        )

        names = _names(driver)
        assert "carrier-25" in names
        assert "plate_1" in names

    @pytest.mark.asyncio
    async def test_reconcile_replaces_prior_occupancy(self) -> None:
        driver = await _driver([_carrier()])
        await _seed_occupancy(driver, [_plate("old_plate", site_index=0)])
        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_plate("new_plate", site_index=1)])
        )
        names = _names(driver)
        assert "old_plate" not in names
        assert "new_plate" in names
        assert "carrier-25" in names

    @pytest.mark.asyncio
    async def test_reconcile_empty_set_wipes_occupancy(self) -> None:
        driver = await _driver([_carrier()])
        await _seed_occupancy(driver, [_plate("plate_1")])
        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[]))
        names = _names(driver)
        assert "carrier-25" in names
        assert "plate_1" not in names

    @pytest.mark.asyncio
    async def test_reconcile_is_idempotent(self) -> None:
        driver = await _driver([_carrier()])
        spec = ReconcileDeckOccupancyRequest(resources=[_plate("plate_1", site_index=0)])
        await driver.reconcile_deck_occupancy(spec)
        await driver.reconcile_deck_occupancy(spec)
        assert "plate_1" in _names(driver)

    def test_reconcile_rejects_carrier_entries(self) -> None:
        with pytest.raises(ValidationError, match="Carriers are structure"):
            ReconcileDeckOccupancyRequest(resources=[_carrier("carrier-99", rail=15)])


class TestWireRegistration:
    def test_both_commands_in_request_and_response_tables(self) -> None:
        for command in ("reset_deck_labware", "reconcile_deck_occupancy"):
            assert command in LH_REQUEST_MODELS
            assert command in LH_RESPONSE_MODELS
            assert LH_RESPONSE_MODELS[command] is LabwareStateResponse

    def test_reset_wire_round_trip(self) -> None:
        wrapped = wrap_lh_payload("reset_deck_labware", {})
        assert isinstance(wrapped["request"], ResetDeckLabwareRequest)

    def test_reconcile_wire_round_trip(self) -> None:
        wrapped = wrap_lh_payload(
            "reconcile_deck_occupancy",
            {"resources": [_plate("plate_1", site_index=0).model_dump()]},
        )
        request = wrapped["request"]
        assert isinstance(request, ReconcileDeckOccupancyRequest)
        assert request.resources[0].name == "plate_1"


class TestLiveOccupancyWireFormatStaysParentSiteIndex:
    """Carriers-only is a DeckLayoutConfig rule, NOT a wire-format rule.

    The live runtime occupancy wire format -- ReconcileDeckOccupancyRequest and
    add_deck_labware -- still carries parent_id + site_index DeckResourceConfig
    entries. The carriers-only gate lives only on DeckLayoutConfig; this guard
    pins that the occupancy path keeps working so the gate can't accidentally
    break the runtime projection of the ledger onto the driver deck.
    """

    def test_reconcile_request_with_parent_site_still_constructs(self) -> None:
        request = ReconcileDeckOccupancyRequest(
            resources=[_plate("plate_1", parent_id="carrier-25", site_index=0)]
        )
        assert request.resources[0].parent_id == "carrier-25"
        assert request.resources[0].site_index == 0

    def test_deck_resource_config_with_parent_site_still_constructs(self) -> None:
        config = DeckResourceConfig(
            name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb",
            parent_id="carrier-25", site_index=1,
        )
        assert config.parent_id == "carrier-25"
        assert config.site_index == 1

    @pytest.mark.asyncio
    async def test_reconcile_places_parent_site_labware_on_deck(self) -> None:
        driver = await _driver([_carrier()])
        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(
                resources=[_plate("plate_1", parent_id="carrier-25", site_index=0)]
            )
        )
        assert "plate_1" in _names(driver)
        assert "carrier-25" in _names(driver)

    @pytest.mark.asyncio
    async def test_add_deck_labware_materializes_at_carrier_site(self) -> None:
        driver = await _driver([_carrier()])
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="carrier-25-0",
        ))
        assert "plate_1" in _names(driver)
        assert "carrier-25" in _names(driver)

    @pytest.mark.asyncio
    async def test_add_deck_labware_before_configure_deck_raises_runtime_error(self) -> None:
        driver = ChatterboxLiquidHandlerDriver()
        with pytest.raises(RuntimeError, match="add_deck_labware called before configure_deck"):
            await driver.add_deck_labware(AddDeckLabwareRequest(
                name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="carrier-25-0",
            ))


class TestRemoveDeckLabware:
    @pytest.mark.asyncio
    async def test_unmaterializes_a_plate_keeps_the_carrier(self) -> None:
        driver = await _driver([_carrier()])
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="carrier-25-0",
        ))
        await driver.remove_deck_labware(RemoveDeckLabwareRequest(name="plate_1"))
        assert "plate_1" not in _names(driver)
        assert "carrier-25" in _names(driver)

    @pytest.mark.asyncio
    async def test_rejects_a_carrier(self) -> None:
        driver = await _driver([_carrier()])
        with pytest.raises(TypeError, match="not materialized"):
            await driver.remove_deck_labware(RemoveDeckLabwareRequest(name="carrier-25"))
        assert "carrier-25" in _names(driver)


class TestDeckStateReportsSites:
    @pytest.mark.asyncio
    async def test_labware_on_a_carrier_reports_the_site_it_sits_on(self) -> None:
        """The reported site is the same label add_deck_labware.at took, so a caller can
        move labware from where it was reported without rebuilding the label."""
        driver = await _driver([_carrier()])
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="carrier-25-1",
        ))

        state = await driver.get_deck_state(GetDeckStateRequest())

        assert [(i.name, i.site) for i in state.labware if i.name == "plate_1"] == [
            ("plate_1", "carrier-25-1")
        ]

    @pytest.mark.asyncio
    async def test_the_carrier_itself_reports_no_site(self) -> None:
        """A carrier is deck structure mounted on a rail, not labware on a site."""
        driver = await _driver([_carrier()])

        state = await driver.get_deck_state(GetDeckStateRequest())

        assert [i.site for i in state.labware if i.name == "carrier-25"] == [None]


class TestMovePlateGripDistance:
    """A plate keeps PLR's plate pickup_distance_from_top; other labware (tip
    racks, troughs) ride the generic move_resource at distance 0. Sim-invisible,
    so this captures the distance reaching PLR rather than any deck state."""

    @pytest.mark.asyncio
    async def test_plate_keeps_grip_distance_other_labware_does_not(self, monkeypatch) -> None:
        plate_car = DeckResourceConfig(name="carrier-25", catalog_ref="PLT_CAR_L5AC_A00", rail=25)
        tip_car = DeckResourceConfig(name="carrier-15", catalog_ref="TIP_CAR_480_A00", rail=15)
        driver = await _driver([plate_car, tip_car])
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="carrier-25-0",
        ))
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="tips", catalog_ref="hamilton_96_tiprack_1000uL_filter", at="carrier-15-0",
        ))
        assert driver._lh is not None
        captured: list[float] = []
        original = driver._lh.move_resource

        async def _spy(resource, **kwargs):
            captured.append(kwargs.get("pickup_distance_from_top", 0))
            return await original(resource, **kwargs)

        monkeypatch.setattr(driver._lh, "move_resource", _spy)

        await driver.move_plate(MovePlateRequest(plate="plate_1", to_position="carrier-25-1"))
        assert captured[-1] == pytest.approx(13.2 - 3.33)

        await driver.move_plate(MovePlateRequest(plate="tips", to_position="carrier-15-1"))
        assert captured[-1] == 0

    @pytest.mark.asyncio
    async def test_a_move_can_name_its_own_grip_distance(self, monkeypatch) -> None:
        """The distance is a property of the move: named, it replaces the plate default
        and the zero that other labware rides at."""
        plate_car = DeckResourceConfig(name="carrier-25", catalog_ref="PLT_CAR_L5AC_A00", rail=25)
        tip_car = DeckResourceConfig(name="carrier-15", catalog_ref="TIP_CAR_480_A00", rail=15)
        driver = await _driver([plate_car, tip_car])
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="carrier-25-0",
        ))
        await driver.add_deck_labware(AddDeckLabwareRequest(
            name="tips", catalog_ref="hamilton_96_tiprack_1000uL_filter", at="carrier-15-0",
        ))
        assert driver._lh is not None
        captured: list[float] = []
        original = driver._lh.move_resource

        async def _spy(resource, **kwargs):
            captured.append(kwargs.get("pickup_distance_from_top", 0))
            return await original(resource, **kwargs)

        monkeypatch.setattr(driver._lh, "move_resource", _spy)

        await driver.move_plate(MovePlateRequest(
            plate="plate_1", to_position="carrier-25-1", grip_distance_from_top=4.0,
        ))
        assert captured[-1] == pytest.approx(4.0)

        await driver.move_plate(MovePlateRequest(
            plate="tips", to_position="carrier-15-1", grip_distance_from_top=2.5,
        ))
        assert captured[-1] == pytest.approx(2.5)


class TestReconcileWithTipsOnTheHead:
    """Reconcile rebuilds every rack from the declared layout, which counts every tip as
    racked. The tips a head is holding would then exist twice, and returning them to their
    own spots is refused as already occupied."""

    def _deck(self) -> DeckLayoutConfig:
        return DeckLayoutConfig(
            deck_type="STARLet",
            resources=[
                DeckResourceConfig(name="tip_car", catalog_ref="TIP_CAR_480_A00", rail=15),
            ],
        )

    def _occupancy(self) -> ReconcileDeckOccupancyRequest:
        return ReconcileDeckOccupancyRequest(resources=[
            DeckResourceConfig(
                name="tips", catalog_ref="hamilton_96_tiprack_1000uL_filter",
                parent_id="tip_car", site_index=0,
            ),
        ])

    @pytest.mark.asyncio
    async def test_a_rebuilt_rack_does_not_refill_the_spots_the_head_carries(self) -> None:
        driver = ChatterboxLiquidHandlerDriver(num_channels=8)
        await driver.configure_deck(self._deck())
        await driver.reconcile_deck_occupancy(self._occupancy())
        picks = [TipPick(tip_rack="tips", positions=["A1", "B1"])]
        await driver.pick_up_tips(PickUpTipsRequest(picks=picks))

        response = await driver.reconcile_deck_occupancy(self._occupancy())

        tips = response.labware_state["tips"].tips
        assert tips is not None
        assert tips["A1"] is False and tips["B1"] is False
        assert tips["C1"] is True
        # The head can still put them back, which is what the double count prevented.
        returned = await driver.drop_tips(DropTipsRequest(to_waste=False, drops=picks))
        back = returned.labware_state["tips"].tips
        assert back is not None
        assert back["A1"] is True and back["B1"] is True
