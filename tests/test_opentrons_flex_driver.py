"""Deck and lifecycle behavior of FlexLiquidHandlerDriver.

The driver is the deck projection orca drives: labware arrives through
occupancy calls rather than the deck layout, and every placement must leave the
robot-server's own deck model agreeing with PLR's. These pin that, plus the
lifecycle ordering (sim server up before the first device contact) and the
labware-definition policy (official name when known, geometry upload otherwise).
"""

from typing import Iterator, Tuple

import pytest

from cheshire_drivers.homing_models import HomeRequest
from cheshire_drivers.liquid_handler_models import (
    AddDeckLabwareRequest,
    DeckLayoutConfig,
    DeckResourceConfig,
    DropTipsRequest,
    GetDeckStateRequest,
    GetHeadConfigurationRequest,
    LabwareWellState,
    PickUpTips96Request,
    PickUpTipsRequest,
    ReconcileDeckOccupancyRequest,
    RemoveDeckLabwareRequest,
    ResetDeckLabwareRequest,
    TipPick,
)
from cheshire_drivers.plr.opentrons_flex import (
    FlexLiquidHandlerDriver,
    derive_labware_state,
)
from cheshire_drivers.plr_volume_tracker import LenientVolumeTracker
from pylabrobot.opentrons import ChatterboxTransport, OpentronsFlex
from pylabrobot.resources import Plate, Resource, does_tip_tracking, does_volume_tracking
from pylabrobot.resources.tip_tracker import set_tip_tracking
from pylabrobot.resources.volume_tracker import set_volume_tracking
from pylabrobot.visualizer import Visualizer

_EIGHT_CHANNEL = ("p50_multi_flex", 8, 1.0, 50.0, "left")
_SINGLE_CHANNEL = ("p1000_single_flex", 1, 1.0, 1000.0, "right")
_NINETY_SIX = ("p1000_96", 96, 1.0, 1000.0, "left")

_FLEX_LAYOUT = DeckLayoutConfig(deck_type="FlexDeck", resources=[])

# A tip rack (PLR stamps its own Opentrons load name) and a plate this driver maps.
_RACK = DeckResourceConfig(
    name="rack_1", catalog_ref="flex_96_tiprack_50ul", parent_id="C1", site_index=0
)
_PLATE = DeckResourceConfig(
    name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", parent_id="C2", site_index=0
)


@pytest.fixture(autouse=True)
def restore_tracking_globals() -> Iterator[None]:
    """configure_deck flips PLR's tracking globals on; put them back for the rest of the suite."""
    volume, tips = does_volume_tracking(), does_tip_tracking()
    yield
    set_volume_tracking(volume)
    set_tip_tracking(tips)


class _RecordingSimServer:
    """A device-owned vendor simulator whose start/stop order the driver must honor."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")


def _driver(
    *pipettes: Tuple[str, int, float, float, str],
) -> Tuple[FlexLiquidHandlerDriver, ChatterboxTransport]:
    transport = ChatterboxTransport(pipettes=list(pipettes) or [_EIGHT_CHANNEL])
    return FlexLiquidHandlerDriver(host="localhost", transport=transport), transport


async def _configured(
    *pipettes: Tuple[str, int, float, float, str],
) -> Tuple[FlexLiquidHandlerDriver, ChatterboxTransport]:
    """A driver with a deck AND a live robot, which most tests want.

    Declaring a deck on a fresh driver contacts nothing, so bringing the robot up
    is its own step. Re-declaring one over labware the robot has loaded does
    reach the robot, to hand that labware back while its ids still exist. Tests
    that want the deck alone call configure_deck directly.
    """
    driver, transport = _driver(*pipettes)
    await driver.configure_deck(_FLEX_LAYOUT)
    await driver.initialize()
    return driver, transport


def _flex_of(driver: FlexLiquidHandlerDriver) -> OpentronsFlex:
    flex = driver._flex
    assert flex is not None
    return flex


async def _load_on_robot(driver: FlexLiquidHandlerDriver, name: str) -> None:
    """Get labware known to the robot-server, which the runtime reaches on its first head op."""
    flex = _flex_of(driver)
    await flex._ensure_labware_loaded(flex.deck.get_resource(name))


def _commands(transport: ChatterboxTransport, command_type: str) -> list[dict]:
    return [c for c in transport.commands if c["commandType"] == command_type]


class TestConfigureDeck:
    @pytest.mark.asyncio
    async def test_a_first_flex_layout_describes_the_deck_and_touches_no_hardware(self) -> None:
        """Declaring a deck over nothing is a data operation. It used to run the
        full bring-up, so describing a layout homed the robot and reported it
        initialized. A deck holding labware is not this case: see the release
        tests below, which do reach the robot."""
        driver, transport = _driver()

        response = await driver.configure_deck(_FLEX_LAYOUT)

        assert response.success is True
        assert response.labware_state == {}
        assert transport.commands == []
        assert driver.is_initialized is False

    @pytest.mark.asyncio
    async def test_tracking_is_on_so_the_heads_move_state_we_can_report(self) -> None:
        driver, _ = _driver()
        set_volume_tracking(False)
        set_tip_tracking(False)

        await driver.configure_deck(_FLEX_LAYOUT)

        assert does_volume_tracking() and does_tip_tracking()

    @pytest.mark.asyncio
    async def test_reconfiguring_frees_labware_the_robot_still_holds(self) -> None:
        """Attaching a deck drops PyLabRobot's labware ids, and those ids are the
        only handle on what the run has loaded. Anything left loaded afterwards is
        unreachable: the next operation on it re-loads and the robot refuses the
        slot as already occupied, with no way back short of a fresh run."""
        driver, transport = await _configured()
        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[_PLATE]))
        await _load_on_robot(driver, "plate_1")

        await driver.configure_deck(_FLEX_LAYOUT)

        moves = _commands(transport, "moveLabware")
        assert [m["params"]["newLocation"] for m in moves] == ["offDeck"]
        assert moves[0]["params"]["strategy"] == "manualMoveWithoutPause"

    @pytest.mark.asyncio
    async def test_reconfiguring_sends_nothing_for_labware_the_robot_never_loaded(self) -> None:
        """Freeing the robot's side is for what it actually holds. Labware only PLR
        knew about costs no wire command, so describing a deck stays cheap."""
        driver, transport = await _configured()
        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[_PLATE]))

        await driver.configure_deck(_FLEX_LAYOUT)

        assert _commands(transport, "moveLabware") == []

    @pytest.mark.asyncio
    async def test_a_swap_the_robot_cannot_be_reached_for_keeps_the_deck_it_has(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Handing labware back needs the robot, so an unreachable one fails the
        swap where declaring a deck used to be purely local. Refusing beats
        attaching a deck that orphans what the run still holds."""
        driver, transport = await _configured()
        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[_PLATE]))
        await _load_on_robot(driver, "plate_1")
        deck = _flex_of(driver).deck

        async def _unreachable(path: str) -> dict:
            raise ConnectionError("robot unreachable")

        monkeypatch.setattr(transport, "get", _unreachable)

        with pytest.raises(ConnectionError):
            await driver.configure_deck(_FLEX_LAYOUT)

        assert _flex_of(driver).deck is deck
        assert deck.get_resource("plate_1") is not None

    @pytest.mark.asyncio
    async def test_another_vendors_deck_type_is_refused(self) -> None:
        driver, transport = _driver()

        with pytest.raises(ValueError, match="FlexDeck"):
            await driver.configure_deck(DeckLayoutConfig(deck_type="STARlet", resources=[]))
        assert transport.commands == []

    @pytest.mark.asyncio
    async def test_a_layout_carrying_resources_is_refused_with_the_route_that_works(self) -> None:
        """A Flex has no carriers, so labware in a layout has nowhere to mount; the error names
        the occupancy route instead of failing later at placement."""
        driver, _ = _driver()
        layout = DeckLayoutConfig(
            deck_type="FlexDeck",
            resources=[DeckResourceConfig(name="carrier_1", catalog_ref="PLT_CAR_L5AC_A00", rail=1)],
        )

        with pytest.raises(ValueError, match="reconcile_deck_occupancy"):
            await driver.configure_deck(layout)

    @pytest.mark.asyncio
    async def test_a_layout_seeding_labware_the_deck_does_not_hold_says_where_state_belongs(
        self,
    ) -> None:
        """A layout declares carriers only, so on a Flex every labware_state name is absent:
        configure_deck builds an empty deck and labware arrives later through the occupancy
        calls (which carry their own well_state). Dropping the seed silently would report
        success for state that was never applied."""
        driver, _ = _driver()
        layout = DeckLayoutConfig(
            deck_type="FlexDeck",
            resources=[],
            labware_state={"plate_1": LabwareWellState(volumes={"A1": 150.0})},
        )

        with pytest.raises(ValueError, match="reconcile_deck_occupancy"):
            await driver.configure_deck(layout)

    @pytest.mark.asyncio
    async def test_the_sim_server_is_up_before_the_first_device_contact(self) -> None:
        """A device-owned simulator has to be running before the first call that
        reaches the device, which is connect (configure_deck reaches nothing)."""
        sim_server = _RecordingSimServer()
        sim_state_at_contact: list[list[str]] = []

        class _OrderedTransport(ChatterboxTransport):
            async def get(self, path: str) -> dict:
                sim_state_at_contact.append(list(sim_server.events))
                return await super().get(path)

        driver = FlexLiquidHandlerDriver(
            host="localhost",
            transport=_OrderedTransport(pipettes=[_EIGHT_CHANNEL]),
            sim_server=sim_server,
        )
        await driver.connect()

        assert sim_state_at_contact[0] == ["start"]


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_connect_needs_no_deck(self) -> None:
        """connect is the first lifecycle step, so gating it behind a deck made the
        robot unreachable until someone described a deck they may not have yet."""
        driver, transport = _driver()

        await driver.connect()

        assert "home" not in [c["commandType"] for c in transport.commands]
        assert driver.is_initialized is False

    @pytest.mark.asyncio
    async def test_initialize_brings_the_robot_up_without_moving_it(self) -> None:
        """Bring-up is not a request to move: a home sweep drives the gantry through
        whatever the head is holding, and nothing about discovering what is mounted
        needs the robot moved. Homing is `home`, reached on its own."""
        driver, transport = _driver()

        await driver.initialize()

        assert _commands(transport, "home") == []
        assert _commands(transport, "loadPipette")
        assert driver.is_initialized is True

        await driver.home(HomeRequest())

        assert len(_commands(transport, "home")) == 1

    @pytest.mark.asyncio
    async def test_a_second_connect_does_not_take_a_second_run(self) -> None:
        """Half the bench failure: re-linking a live robot orphans its session.

        The Opentrons robot is held per run, so a second connect used to open a
        fresh link and take another run, abandoning the first. Clicking Connect
        twice in the UI did exactly that.
        """
        class _CountingTransport(ChatterboxTransport):
            def __init__(self) -> None:
                super().__init__(pipettes=[_EIGHT_CHANNEL])
                self.setups = 0

            async def setup(self) -> None:
                self.setups += 1
                await super().setup()

        transport = _CountingTransport()
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)
        await driver.connect()

        await driver.connect()

        assert transport.setups == 1, "the second connect re-linked and orphaned the first run"
        assert driver.is_connected is True

    @pytest.mark.asyncio
    async def test_the_link_flag_follows_connect_and_disconnect(self) -> None:
        """The device's own link, reportable on its own rather than inferred."""
        driver, _ = _driver()
        assert driver.is_connected is False

        await driver.connect()
        assert driver.is_connected is True

        await driver.disconnect()
        assert driver.is_connected is False

    @pytest.mark.asyncio
    async def test_a_bring_up_that_fails_still_reports_the_robot_as_ours(self) -> None:
        """The run is what locks the touchscreen, so it must not go unreported.

        Plain `initialize` with no prior connect used to record the link only
        after the whole bring-up succeeded. Fail partway -- an unsupported
        pipette is the ordinary way -- and the robot reported itself released
        while its run was open and its screen locked out, with nothing to tell
        the operator why.
        """

        class _FailsAfterRun(ChatterboxTransport):
            def __init__(self) -> None:
                super().__init__(pipettes=[_EIGHT_CHANNEL])

        driver = FlexLiquidHandlerDriver(host="localhost", transport=_FailsAfterRun())
        flex = driver._require_flex("test")

        async def _boom() -> None:
            raise RuntimeError("no pipette attached")

        flex.initialize = _boom  # type: ignore[method-assign]

        with pytest.raises(RuntimeError):
            await driver.initialize()

        assert driver.is_connected is True, "the run is open; saying otherwise hides the lockout"
        assert driver.is_initialized is False

    @pytest.mark.asyncio
    async def test_a_connect_that_cannot_take_the_run_stays_retryable(self) -> None:
        """Otherwise the device is wedged: connected, runless, and un-retryable.

        The robot refuses a run while the touchscreen holds one. If connect
        recorded success on reaching the robot, the retry would return early and
        every later command would die on "no active run" with no way back.
        """
        driver, _ = _driver()
        flex = driver._require_flex("test")
        real_create = flex.create_run
        calls = {"n": 0}

        async def _refuse_once() -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("run already current")
            await real_create()

        flex.create_run = _refuse_once  # type: ignore[method-assign]

        with pytest.raises(RuntimeError):
            await driver.connect()
        assert driver.is_connected is False, "no run means the robot is not ours"

        await driver.connect()

        assert driver.is_connected is True, "the retry must actually retry"
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_a_disposed_robot_does_not_report_itself_connected(self) -> None:
        """Otherwise the early return makes connect answer "fine" on a dead object.

        `_shutdown` drops the robot. If the link flag survived that, the next
        connect would return early against a robot that no longer exists, and
        the operator would be told a released instrument was ready.
        """
        driver, _ = _driver()
        await driver.connect()

        await driver._shutdown()

        assert driver.is_connected is False
        assert driver.is_initialized is False

    @pytest.mark.asyncio
    async def test_initialize_after_connect_loads_the_heads_on_the_same_link(self) -> None:
        """connect already opened the link and took a run. initialize on top of it
        must load the heads without opening a second link (PLR's setup would
        connect again, leaking the first client)."""

        class _CountingTransport(ChatterboxTransport):
            def __init__(self) -> None:
                super().__init__(pipettes=[_EIGHT_CHANNEL])
                self.setups = 0

            async def setup(self) -> None:
                self.setups += 1
                await super().setup()

        transport = _CountingTransport()
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)
        await driver.connect()

        await driver.initialize()

        assert transport.setups == 1
        assert _commands(transport, "loadPipette")
        assert driver.is_initialized is True

    @pytest.mark.asyncio
    async def test_initialize_after_disconnect_opens_a_fresh_link(self) -> None:
        """disconnect drops the link, so the next initialize is a full bring-up again."""

        class _CountingTransport(ChatterboxTransport):
            def __init__(self) -> None:
                super().__init__(pipettes=[_EIGHT_CHANNEL])
                self.setups = 0

            async def setup(self) -> None:
                self.setups += 1
                await super().setup()

        transport = _CountingTransport()
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)
        await driver.initialize()
        await driver.disconnect()

        await driver.initialize()

        assert transport.setups == 2
        assert driver.is_initialized is True

    @pytest.mark.asyncio
    async def test_initialize_after_configure_deck_is_a_no_op_re_entry(self) -> None:
        driver, transport = await _configured()
        before = list(transport.commands)

        await driver.initialize()

        assert transport.commands == before
        assert driver.is_initialized is True

    @pytest.mark.asyncio
    async def test_close_leaves_the_sim_server_running_and_shutdown_stops_it(self) -> None:
        """`close` is a per-op device command (deck access), not disposal."""
        sim_server = _RecordingSimServer()
        driver = FlexLiquidHandlerDriver(
            host="localhost",
            transport=ChatterboxTransport(pipettes=[_EIGHT_CHANNEL]),
            sim_server=sim_server,
        )
        await driver.configure_deck(_FLEX_LAYOUT)
        await driver.connect()

        await driver.open()
        await driver.close()
        assert sim_server.events == ["start"]

        await driver._shutdown()
        assert sim_server.events == ["start", "stop"]
        assert driver.is_initialized is False

    @pytest.mark.asyncio
    async def test_shutdown_releases_the_robot_without_moving_it(self) -> None:
        """Disposal must not home. A client process exiting is not a request to
        move a robot that may be holding labware, and nobody is necessarily
        watching when it happens."""
        driver, transport = await _configured()
        await driver.initialize()
        before = len(transport.commands)

        await driver._shutdown()

        during_shutdown = [c["commandType"] for c in transport.commands[before:]]
        assert "home" not in during_shutdown
        assert driver.is_initialized is False

    @pytest.mark.asyncio
    async def test_occupancy_calls_before_configure_deck_are_tolerated_no_ops(self) -> None:
        """The engine projects occupancy at startup, possibly before a deck exists; that must not
        fail the run, unlike a single add/remove which names labware that has nowhere to go."""
        driver, _ = _driver()

        assert (await driver.reset_deck_labware(ResetDeckLabwareRequest())).success is True
        reconciled = await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_PLATE])
        )
        assert reconciled.success is True
        assert (await driver.get_deck_state(GetDeckStateRequest())).tips_mounted == []
        assert (
            await driver.get_head_configuration(GetHeadConfigurationRequest())
        ).groups == []

    @pytest.mark.asyncio
    async def test_add_and_remove_before_configure_deck_are_refused(self) -> None:
        driver, _ = _driver()

        with pytest.raises(RuntimeError, match="configure_deck"):
            await driver.add_deck_labware(
                AddDeckLabwareRequest(name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="C2-slot")
            )
        with pytest.raises(RuntimeError, match="configure_deck"):
            await driver.remove_deck_labware(RemoveDeckLabwareRequest(name="plate_1"))


class TestDeckLabware:
    @pytest.mark.asyncio
    async def test_add_places_labware_in_the_named_slot_and_seeds_it(self) -> None:
        driver, _ = await _configured()

        await driver.add_deck_labware(
            AddDeckLabwareRequest(
                name="plate_1",
                catalog_ref="Cor_96_wellplate_360ul_Fb",
                at="C2-slot",
                well_state=LabwareWellState(volumes={"A1": 150.0}),
            )
        )

        deck = _flex_of(driver).deck
        placed = deck.slots["C2"]
        assert placed is not None and placed.name == "plate_1"
        volumes = derive_labware_state(deck)["plate_1"].volumes
        assert volumes is not None
        assert volumes["A1"] == 150.0
        assert volumes["A2"] == 0.0

    @pytest.mark.asyncio
    async def test_only_the_seeded_wells_enforce_physical_bounds(self) -> None:
        """An unseeded well keeps the audit-only tracker: strict everywhere would reject the first
        aspirate from a deck whose volumes orca never declared."""
        driver, _ = await _configured()

        await driver.add_deck_labware(
            AddDeckLabwareRequest(
                name="plate_1",
                catalog_ref="Cor_96_wellplate_360ul_Fb",
                at="C2-slot",
                well_state=LabwareWellState(volumes={"A1": 150.0}),
            )
        )

        plate = _flex_of(driver).deck.get_resource("plate_1")
        assert isinstance(plate, Plate)
        assert not isinstance(plate.get_item("A1").tracker, LenientVolumeTracker)
        assert isinstance(plate.get_item("A2").tracker, LenientVolumeTracker)

    @pytest.mark.asyncio
    async def test_a_carrier_style_site_label_is_refused(self) -> None:
        driver, _ = await _configured()

        with pytest.raises(ValueError, match="'<slot>-slot'"):
            await driver.add_deck_labware(
                AddDeckLabwareRequest(
                    name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="carrier_1-2"
                )
            )

    @pytest.mark.asyncio
    async def test_remove_frees_the_slot_on_the_robot_too(self) -> None:
        """Without the off-deck move the robot-server keeps the slot occupied and the next load
        into it fails, even though PLR shows the slot empty."""
        driver, transport = await _configured()
        await driver.add_deck_labware(
            AddDeckLabwareRequest(
                name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="C2-slot"
            )
        )
        await _load_on_robot(driver, "plate_1")

        await driver.remove_deck_labware(RemoveDeckLabwareRequest(name="plate_1"))

        moves = _commands(transport, "moveLabware")
        assert len(moves) == 1
        assert moves[0]["params"]["newLocation"] == "offDeck"
        assert moves[0]["params"]["strategy"] == "manualMoveWithoutPause"
        assert _flex_of(driver).deck.slots["C2"] is None

    @pytest.mark.asyncio
    async def test_removing_labware_the_robot_never_loaded_sends_no_wire_command(self) -> None:
        driver, transport = await _configured()
        await driver.add_deck_labware(
            AddDeckLabwareRequest(
                name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="C2-slot"
            )
        )

        await driver.remove_deck_labware(RemoveDeckLabwareRequest(name="plate_1"))

        assert _commands(transport, "moveLabware") == []
        assert _flex_of(driver).deck.slots["C2"] is None

    @pytest.mark.asyncio
    async def test_deck_structure_is_not_removable(self) -> None:
        driver, _ = await _configured()

        with pytest.raises(TypeError, match="not materialized labware"):
            await driver.remove_deck_labware(RemoveDeckLabwareRequest(name="trash"))


class TestReconcile:
    @pytest.mark.asyncio
    async def test_reconciling_the_same_occupancy_twice_lands_in_the_same_place(self) -> None:
        """The projection is re-issued whenever the ledger is re-read, so a second identical pass
        must not trip over the slots the first one filled."""
        driver, _ = await _configured()
        request = ReconcileDeckOccupancyRequest(resources=[_RACK, _PLATE])

        first = await driver.reconcile_deck_occupancy(request)
        second = await driver.reconcile_deck_occupancy(request)

        assert first.success and second.success
        assert second.labware_state == first.labware_state
        deck = _flex_of(driver).deck
        assert [name for name, r in deck.slots.items() if r is not None] == ["C1", "C2", "A3"]

    @pytest.mark.asyncio
    async def test_reconcile_frees_the_robots_slots_before_refilling_them(self) -> None:
        driver, transport = await _configured()
        request = ReconcileDeckOccupancyRequest(resources=[_PLATE])
        await driver.reconcile_deck_occupancy(request)
        await _load_on_robot(driver, "plate_1")

        await driver.reconcile_deck_occupancy(request)

        assert [m["params"]["newLocation"] for m in _commands(transport, "moveLabware")] == [
            "offDeck"
        ]

    @pytest.mark.asyncio
    async def test_reconcile_seeds_the_well_state_each_entry_carries(self) -> None:
        """Every pass rebuilds the labware objects, so the seed has to be re-applied or the
        projection comes back empty after a re-read of the ledger."""
        driver, _ = await _configured()
        seeded = DeckResourceConfig(
            name="trough_1",
            catalog_ref="hamilton_1_trough_200ml_Vb",
            parent_id="C3",
            site_index=0,
            well_state=LabwareWellState(volumes={"A1": 50_000.0}),
        )

        first = await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[seeded])
        )
        second = await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[seeded])
        )

        assert first.labware_state["trough_1"].volumes == {"A1": 50_000.0}
        assert second.labware_state == first.labware_state

    @pytest.mark.asyncio
    async def test_an_occupancy_entry_naming_no_slot_is_refused(self) -> None:
        """A Flex resource mounts in a deck slot, never on a rail, so a carrier-shaped entry
        has nowhere to go."""
        driver, _ = await _configured()

        with pytest.raises(ValueError, match="names no slot"):
            await driver.reconcile_deck_occupancy(
                ReconcileDeckOccupancyRequest(
                    resources=[
                        DeckResourceConfig(
                            name="plate_2",
                            catalog_ref="Cor_96_wellplate_360ul_Fb",
                            parent_id=None,
                            site_index=0,
                        )
                    ]
                )
            )

    @pytest.mark.asyncio
    async def test_an_unknown_catalog_ref_fails_with_the_deck_intact(self) -> None:
        driver, _ = await _configured()
        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[_PLATE]))

        with pytest.raises(ValueError, match="Unknown catalog reference"):
            await driver.reconcile_deck_occupancy(
                ReconcileDeckOccupancyRequest(
                    resources=[
                        _PLATE,
                        DeckResourceConfig(name="x", catalog_ref="not_a_labware", parent_id="C3"),
                    ]
                )
            )
        assert _flex_of(driver).deck.slots["C2"] is not None


class TestVisualizer:
    """``enable_plr_visualizer`` reaches this driver as ``visualize``, same as the wrapper-backed
    drivers. Setup is stubbed: the real one binds websocket and file server ports and opens a
    browser, none of which belongs in a test run. The wiring under test is which deck the
    visualizer watches, which construction settles."""

    @pytest.mark.asyncio
    async def test_the_visualizer_watches_the_deck_the_driver_built(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def no_server(self: Visualizer) -> None:
            return None

        monkeypatch.setattr(Visualizer, "setup", no_server)
        transport = ChatterboxTransport(pipettes=[_EIGHT_CHANNEL])
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport, visualize=True)

        await driver.configure_deck(_FLEX_LAYOUT)

        assert driver._visualizer is not None
        assert driver._visualizer._root_resource is _flex_of(driver).deck

    @pytest.mark.asyncio
    async def test_no_visualizer_is_built_when_the_flag_is_off(self) -> None:
        driver, _ = await _configured(_EIGHT_CHANNEL)

        assert driver._visualizer is None

    @pytest.mark.asyncio
    async def test_a_visualizer_that_cannot_start_does_not_fail_the_deck(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A busy port or a headless box must cost the operator a viewer, not the run."""

        async def refuse(self: Visualizer) -> None:
            raise OSError("port 1337 already in use")

        monkeypatch.setattr(Visualizer, "setup", refuse)
        transport = ChatterboxTransport(pipettes=[_EIGHT_CHANNEL])
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport, visualize=True)

        response = await driver.configure_deck(_FLEX_LAYOUT)

        assert response.success is True
        assert driver._visualizer is None


class TestDeckState:
    @pytest.mark.asyncio
    async def test_a_staging_pad_resident_is_reported_at_its_pad(self) -> None:
        """The pads are arm-handoff deck sites; the runtime removes a departing
        labware from the driver world only if get_deck_state reports it."""
        driver, _ = await _configured()
        deck = _flex_of(driver).deck
        deck.assign_child_at_slot(
            Resource(name="staged_plate", size_x=127.0, size_y=85.0, size_z=14.0), "B4"
        )

        state = await driver.get_deck_state(GetDeckStateRequest())

        by_name = {item.name: item for item in state.labware}
        assert "staged_plate" in by_name and by_name["staged_plate"].site == "B4-slot"
        assert not any("slot_A4" in name or "slot_B4" in name for name in by_name)

    @pytest.mark.asyncio
    async def test_a_rebuilt_rack_does_not_refill_the_spots_a_head_still_carries(self) -> None:
        """Reconcile rebuilds a rack from the declared layout, which counts every tip as
        racked. The eight tips on the head would then exist twice, and returning them to
        their own spots would be refused as already occupied."""
        driver, _ = await _configured(_EIGHT_CHANNEL)
        occupancy = ReconcileDeckOccupancyRequest(resources=[_RACK, _PLATE])
        await driver.reconcile_deck_occupancy(occupancy)
        column = [f"{row}1" for row in "ABCDEFGH"]
        await driver.pick_up_tips(
            PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=column)])
        )

        await driver.reconcile_deck_occupancy(occupancy)

        state = await driver.get_deck_state(GetDeckStateRequest())
        assert [(r.name, r.tips_remaining) for r in state.tip_racks] == [("rack_1", 88)]
        returned = await driver.drop_tips(
            DropTipsRequest(to_waste=False, drops=[TipPick(tip_rack="rack_1", positions=column)])
        )
        tips = returned.labware_state["rack_1"].tips
        assert tips is not None and all(tips.values())

    @pytest.mark.asyncio
    async def test_each_labware_reports_the_site_it_sits_on(self) -> None:
        """The site is the same label add_deck_labware and move_plate accept, so a caller
        can feed a reported site straight back without translating it."""
        driver, _ = await _configured()
        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_RACK, _PLATE])
        )

        state = await driver.get_deck_state(GetDeckStateRequest())

        assert sorted((item.name, item.site) for item in state.labware) == [
            ("plate_1", "C2-slot"),
            ("rack_1", "C1-slot"),
            ("trash", "A3-slot"),
        ]

    @pytest.mark.asyncio
    async def test_tip_racks_report_their_remaining_inventory(self) -> None:
        driver, _ = await _configured()
        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[_RACK]))

        state = await driver.get_deck_state(GetDeckStateRequest())

        assert [(r.name, r.tips_remaining, r.total_tips) for r in state.tip_racks] == [
            ("rack_1", 96, 96)
        ]
        assert state.tips_mounted == [False] * 8
        assert state.tips_mounted_96 is False

    @pytest.mark.asyncio
    async def test_tips_mounted_carries_one_entry_per_channel_the_robot_reports(self) -> None:
        """Channels are numbered across mounts, so a two-mount Flex answers to 9 of them and a
        lone single-channel mount to 1. A fixed 8 would hide the ninth and invent seven."""
        two_mounts, _ = await _configured(_EIGHT_CHANNEL, _SINGLE_CHANNEL)
        await two_mounts.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_RACK])
        )
        await two_mounts.pick_up_tips(
            PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=["A1"])])
        )

        state = await two_mounts.get_deck_state(GetDeckStateRequest())

        assert state.tips_mounted == [False] * 8 + [True]

        one_mount, _ = await _configured(_SINGLE_CHANNEL)
        assert (await one_mount.get_deck_state(GetDeckStateRequest())).tips_mounted == [False]

    @pytest.mark.asyncio
    async def test_the_96_head_is_reported_once_not_as_96_channels(self) -> None:
        """The 96 head is one addressable unit, so it rides tips_mounted_96 and leaves
        tips_mounted empty. Listing its 96 nozzles there would tell a caller reading the length
        that 96 channels can be driven separately."""
        driver, _ = await _configured(_NINETY_SIX)
        await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[_RACK]))
        await driver.pick_up_tips96(PickUpTips96Request(tip_rack="rack_1"))

        state = await driver.get_deck_state(GetDeckStateRequest())

        assert state.tips_mounted == []
        assert state.tips_mounted_96 is True


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_releases_the_robot_without_moving_it(self) -> None:
        """The point of disconnect is reclaiming the robot's own controls. Homing on
        the way out would move the arm over a deck the operator is already reaching
        into, so disconnect must not do what stop() does."""
        driver, transport = await _configured()
        assert driver.is_initialized is True
        homes_before = len(_commands(transport, "home"))

        await driver.disconnect()

        assert len(_commands(transport, "home")) == homes_before
        assert driver.is_initialized is False

    @pytest.mark.asyncio
    async def test_a_failed_handback_still_clears_bring_up(self) -> None:
        """A robot that answers nothing is not still brought up, so the flag must
        not survive the failure that proved it."""
        driver, _ = await _configured()

        async def _raise() -> None:
            raise ConnectionResetError("robot stopped answering")

        _flex_of(driver).disconnect = _raise

        with pytest.raises(ConnectionResetError):
            await driver.disconnect()
        assert driver.is_initialized is False

    @pytest.mark.asyncio
    async def test_disconnect_before_configure_deck_is_a_no_op(self) -> None:
        """Releasing something never connected is not an error worth raising."""
        driver, _ = _driver()
        await driver.disconnect()
        assert driver.is_initialized is False

    @pytest.mark.asyncio
    async def test_cancel_run_frees_the_touchscreen_but_keeps_the_link(self) -> None:
        """The run, not the link, is what the robot holds against local control, so
        an operator can take the robot by hand while the driver stays connected."""
        driver, transport = await _configured()
        homes_before = len(_commands(transport, "home"))

        await driver.cancel_run()

        assert driver._flex is not None
        assert _flex_of(driver).run_id is None
        assert len(_commands(transport, "home")) == homes_before
        assert driver.is_initialized is False


class TestHeadConfiguration:
    @pytest.mark.asyncio
    async def test_an_eight_channel_is_one_plunger_not_eight(self) -> None:
        """The reported model is what the robot says is fitted, which is not the name you load
        it by: `p50_multi_flex` goes out on loadPipette, `p50_multi_v3.5` comes back from the
        instrument. The robot's answer is the one that names the actual hardware revision."""
        driver, _ = await _configured(_EIGHT_CHANNEL)

        config = await driver.get_head_configuration(GetHeadConfigurationRequest())

        assert config.nozzle_count == 8
        assert config.independent_volume_count == 1
        assert config.groups[0].channels == list(range(8))
        assert config.groups[0].pipette_model == "p50_multi_v3.5"
        assert config.groups[0].max_volume_ul == 50.0

    @pytest.mark.asyncio
    async def test_two_mounts_are_two_plungers_numbered_left_first(self) -> None:
        driver, _ = await _configured(_EIGHT_CHANNEL, _SINGLE_CHANNEL)

        config = await driver.get_head_configuration(GetHeadConfigurationRequest())

        assert [group.channels for group in config.groups] == [list(range(8)), [8]]
        assert config.independent_volume_count == 2

    @pytest.mark.asyncio
    async def test_a_ninety_six_head_is_one_group_of_ninety_six(self) -> None:
        driver, _ = await _configured(_NINETY_SIX)

        config = await driver.get_head_configuration(GetHeadConfigurationRequest())

        assert config.nozzle_count == 96
        assert config.independent_volume_count == 1

    @pytest.mark.asyncio
    async def test_a_head_that_is_not_composed_reports_nothing_rather_than_guessing(self) -> None:
        """Before configure_deck there is no robot to ask, and an invented eight-nozzle group
        reads as a real answer to a caller sizing its volumes."""
        driver, _ = _driver()

        config = await driver.get_head_configuration(GetHeadConfigurationRequest())

        assert config.groups == []

    @pytest.mark.asyncio
    async def test_reading_the_head_configuration_asks_the_robot_nothing(self) -> None:
        """The heads already carry what they are, so describing them is a local read. It used
        to re-read /instruments, putting a round-trip in the path of every state poll."""
        reads: list[str] = []

        class _CountingTransport(ChatterboxTransport):
            async def get(self, path: str) -> dict:
                reads.append(path)
                return await super().get(path)

        driver = FlexLiquidHandlerDriver(
            host="localhost", transport=_CountingTransport(pipettes=[_EIGHT_CHANNEL])
        )
        await driver.configure_deck(_FLEX_LAYOUT)
        await driver.initialize()
        reads.clear()

        config = await driver.get_head_configuration(GetHeadConfigurationRequest())

        assert reads == []
        assert config.groups[0].pipette_model == "p50_multi_v3.5"
        assert config.groups[0].max_volume_ul == 50.0


class TestLabwareDefinitions:
    @pytest.mark.asyncio
    async def test_a_plate_from_another_catalog_is_built_from_its_own_geometry(self) -> None:
        """A Hamilton-catalog Corning is not mapped onto Opentrons' corning definition: the
        robot gets a definition synthesized from the PLR resource, so motion and tracking
        run on the same numbers instead of two vendors' versions of the same plate."""
        driver, transport = await _configured()
        await driver.add_deck_labware(
            AddDeckLabwareRequest(
                name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="C2-slot"
            )
        )

        await _load_on_robot(driver, "plate_1")

        assert len(transport.labware_definitions) == 1
        uploaded = transport.labware_definitions[0]
        assert uploaded["dimensions"]["zDimension"] == pytest.approx(14.2)
        loads = _commands(transport, "loadLabware")
        assert [c["params"]["namespace"] for c in loads] != ["opentrons"]
        assert loads[0]["params"]["loadName"] == uploaded["parameters"]["loadName"]

    @pytest.mark.asyncio
    async def test_a_plr_stamped_tip_rack_needs_no_mapping_of_ours(self) -> None:
        """PLR's Flex tip-rack factories already carry their Opentrons load name, so the driver
        must not overwrite it with a guess."""
        driver, transport = await _configured()
        await driver.add_deck_labware(
            AddDeckLabwareRequest(
                name="rack_1", catalog_ref="flex_96_tiprack_50ul", at="C1-slot"
            )
        )

        await _load_on_robot(driver, "rack_1")

        assert transport.labware_definitions == []
        assert _commands(transport, "loadLabware")[0]["params"]["loadName"] == (
            "opentrons_flex_96_tiprack_50ul"
        )

    @pytest.mark.asyncio
    async def test_unmapped_labware_uploads_a_definition_from_its_own_geometry(self) -> None:
        """A Hamilton trough has no official Opentrons definition; borrowing a lookalike load name
        would hand the robot the wrong geometry, so the real geometry is uploaded instead."""
        driver, transport = await _configured()
        await driver.add_deck_labware(
            AddDeckLabwareRequest(
                name="trough_1", catalog_ref="hamilton_1_trough_200ml_Vb", at="C3-slot"
            )
        )

        await _load_on_robot(driver, "trough_1")

        assert len(transport.labware_definitions) == 1
        uploaded = transport.labware_definitions[0]["parameters"]["loadName"]
        assert _commands(transport, "loadLabware")[0]["params"]["loadName"] == uploaded
