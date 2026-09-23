"""Hardware-truth reconciliation across the liquid-handler drivers.

An operator who recovers the robot at the instrument stops the driver's
control session (the touchscreen only works while no run is current) and can
leave every cached layer describing a world that no longer exists: tips the
sensor says are gone, an initialized flag over a dead session, labware ids
bound to it. These pin the repair paths: a liveness-verified initialize, the
reconcile_hardware_state command per driver family, and the stranded-tip
escape.
"""

from typing import Iterator, Tuple, get_args

import pytest

from cheshire_drivers.liquid_handler_models import (
    DeckLayoutConfig,
    DeckResourceConfig,
    DiscardStrandedTipsRequest,
    GetDeckStateRequest,
    MountTipReconcile,
    PickUpTipsRequest,
    ReconcileDeckOccupancyRequest,
    ReconcileHardwareStateRequest,
    TipPick,
)
from cheshire_drivers.plr.liquid_handler import ChatterboxLiquidHandlerDriver
from cheshire_drivers.plr.opentrons_flex import FlexLiquidHandlerDriver
from cheshire_drivers.sims import SimLiquidHandlerDriver
from pylabrobot.opentrons import ChatterboxTransport
from pylabrobot.opentrons.flex_head import TipReconcileOutcome
from pylabrobot.resources import does_tip_tracking, does_volume_tracking
from pylabrobot.resources.tip_tracker import set_tip_tracking
from pylabrobot.resources.volume_tracker import set_volume_tracking

_EIGHT_CHANNEL = ("p50_multi_flex", 8, 1.0, 50.0, "left")
_FLEX_LAYOUT = DeckLayoutConfig(deck_type="FlexDeck", resources=[])
_RACK = DeckResourceConfig(
    name="rack_1", catalog_ref="flex_96_tiprack_50ul", parent_id="C1", site_index=0
)


@pytest.fixture(autouse=True)
def restore_tracking_globals() -> Iterator[None]:
    volume, tips = does_volume_tracking(), does_tip_tracking()
    yield
    set_volume_tracking(volume)
    set_tip_tracking(tips)


async def _configured() -> Tuple[FlexLiquidHandlerDriver, ChatterboxTransport]:
    transport = ChatterboxTransport(pipettes=[_EIGHT_CHANNEL])
    driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)
    await driver.configure_deck(_FLEX_LAYOUT)
    await driver.initialize()
    return driver, transport


async def _holding_tips() -> Tuple[FlexLiquidHandlerDriver, ChatterboxTransport]:
    """A driver whose model and sensor both hold a column of tips."""
    driver, transport = await _configured()
    await driver.reconcile_deck_occupancy(ReconcileDeckOccupancyRequest(resources=[_RACK]))
    await driver.pick_up_tips(
        PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=[f"{r}1" for r in "ABCDEFGH"])])
    )
    return driver, transport


def _homes(transport: ChatterboxTransport) -> int:
    return len([c for c in transport.commands if c["commandType"] == "home"])


def _pipette_loads(transport: ChatterboxTransport) -> int:
    """Head discovery loads every mounted pipette, so this counts the bring-ups."""
    return len([c for c in transport.commands if c["commandType"] == "loadPipette"])


class TestInitializeVerifiesTheSession:
    @pytest.mark.asyncio
    async def test_initialize_after_an_external_recovery_rebuilds_instead_of_no_opping(
        self,
    ) -> None:
        """The incident's wedge: the flag said initialized, the robot had no run,
        and re-initializing did nothing. Now the flag is only trusted while the
        robot still holds our session."""
        driver, transport = await _configured()
        loads_before = _pipette_loads(transport)
        transport.end_run_externally()

        await driver.initialize()

        assert driver.is_connected
        assert driver.is_initialized
        assert _pipette_loads(transport) == loads_before + 1
        # The rebuilt session accepts commands again.
        state = await driver.get_deck_state(GetDeckStateRequest())
        assert state.tips_mounted == [False] * 8

    @pytest.mark.asyncio
    async def test_initialize_with_a_live_session_stays_a_no_op(self) -> None:
        driver, transport = await _configured()
        before = list(transport.commands)

        await driver.initialize()

        assert transport.commands == before


class _InstrumentsFailOnceTransport(ChatterboxTransport):
    """GET /instruments raises once when armed: a wire hiccup mid-head-discovery."""

    fail_next_instruments_read = False

    async def get(self, path: str) -> dict:
        if path == "/instruments" and self.fail_next_instruments_read:
            self.fail_next_instruments_read = False
            raise RuntimeError("wire dropped during head discovery")
        return await super().get(path)


class TestInitializeVerifiesTheSessionEdges:
    @pytest.mark.asyncio
    async def test_connected_but_uninitialized_heals_a_dead_session_too(self) -> None:
        """The adjacent interleaving: connect, operator recovery, initialize.
        Without the pre-flag liveness check, connect() no-ops on the dead
        run_id and home() fails against the dead run forever."""
        transport = ChatterboxTransport(pipettes=[_EIGHT_CHANNEL])
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)
        await driver.connect()
        transport.end_run_externally()

        await driver.initialize()

        assert driver.is_initialized
        state = await driver.get_deck_state(GetDeckStateRequest())
        assert state.tips_mounted == []  # no deck configured; the point is no wedge

    @pytest.mark.asyncio
    async def test_a_rebuild_that_fails_partway_reports_uninitialized(self) -> None:
        """A wire error during head discovery must not leave a set flag over
        zero composed heads; the next initialize must rebuild for real."""
        transport = _InstrumentsFailOnceTransport(pipettes=[_EIGHT_CHANNEL])
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)
        await driver.configure_deck(_FLEX_LAYOUT)
        await driver.initialize()
        transport.end_run_externally()
        transport.fail_next_instruments_read = True

        with pytest.raises(RuntimeError, match="wire dropped"):
            await driver.reconcile_hardware_state(ReconcileHardwareStateRequest())

        assert not driver.is_initialized
        await driver.initialize()
        assert driver.is_initialized
        state = await driver.get_deck_state(GetDeckStateRequest())
        assert state.tips_mounted == [False] * 8


class TestReconcileHardwareState:
    @pytest.mark.asyncio
    async def test_no_composed_heads_is_reported_as_unchecked(self) -> None:
        """Connected but never initialized: there is no sensor to read, and an
        all-clear here would hide a seated tip."""
        transport = ChatterboxTransport(pipettes=[_EIGHT_CHANNEL])
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)
        await driver.connect()

        report = await driver.reconcile_hardware_state(ReconcileHardwareStateRequest())

        assert not report.checked
        assert report.mounts == []
        assert report.message is not None and "initialize" in report.message

    @pytest.mark.asyncio
    async def test_agreement_reports_in_sync_and_repairs_nothing(self) -> None:
        driver, transport = await _holding_tips()

        report = await driver.reconcile_hardware_state(ReconcileHardwareStateRequest())

        assert report.checked
        assert not report.session_recovered
        assert not report.requires_intervention
        assert [m.outcome for m in report.mounts] == ["in_sync"]
        state = await driver.get_deck_state(GetDeckStateRequest())
        assert state.tips_mounted == [True] * 8

    @pytest.mark.asyncio
    async def test_sensor_absent_while_model_holds_clears_the_mount(self) -> None:
        """The operator dropped the tips at the instrument while our session
        survived; the model must follow the sensor, not the other way around."""
        driver, transport = await _holding_tips()
        transport.set_tip_detected("left", False)

        report = await driver.reconcile_hardware_state(ReconcileHardwareStateRequest())

        assert [m.outcome for m in report.mounts] == ["cleared_lost_tips"]
        assert not report.requires_intervention
        state = await driver.get_deck_state(GetDeckStateRequest())
        assert state.tips_mounted == [False] * 8

    @pytest.mark.asyncio
    async def test_a_dead_session_is_rebuilt_without_motion(self) -> None:
        """After the operator's recovery the robot dropped the tips and closed
        our run: reconcile takes a fresh session, moving nothing, and the
        cleared world reads in sync."""
        driver, transport = await _holding_tips()
        homes_before = _homes(transport)
        transport.end_run_externally()
        transport.set_tip_detected("left", False)

        report = await driver.reconcile_hardware_state(ReconcileHardwareStateRequest())

        assert report.session_recovered
        assert not report.requires_intervention
        assert [m.outcome for m in report.mounts] == ["in_sync"]
        assert _homes(transport) == homes_before
        assert driver.is_initialized
        state = await driver.get_deck_state(GetDeckStateRequest())
        assert state.tips_mounted == [False] * 8

    @pytest.mark.asyncio
    async def test_a_physically_present_untracked_tip_demands_intervention(self) -> None:
        """The sensor is one bool per mount: nothing can say which channel, so
        reconcile reports rather than guesses."""
        driver, transport = await _holding_tips()
        transport.end_run_externally()  # recovery left the tips ON the nozzles

        report = await driver.reconcile_hardware_state(ReconcileHardwareStateRequest())

        assert report.session_recovered
        assert report.requires_intervention
        assert [m.outcome for m in report.mounts] == ["untracked_tip_present"]
        assert report.message is not None and "discard_stranded_tips" in report.message

    @pytest.mark.asyncio
    async def test_before_connect_nothing_is_verifiable(self) -> None:
        transport = ChatterboxTransport(pipettes=[_EIGHT_CHANNEL])
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)

        report = await driver.reconcile_hardware_state(ReconcileHardwareStateRequest())

        assert not report.checked
        assert report.mounts == []


class _UnknownSensorTransport(ChatterboxTransport):
    """A tip sensor that answers neither present nor absent.

    The real one does this: `getTipPresence` reports "unknown" when the read
    itself did not settle, which is a failure to learn anything, not an
    all-clear.
    """

    async def post(self, path: str, json: dict | None = None) -> dict:
        response = await super().post(path, json)
        command = (json or {}).get("data", {}).get("commandType")
        if command == "getTipPresence":
            response["data"]["result"] = {"status": "unknown"}
        return response


class TestAnUnreadableSensorIsNotAnAllClear:
    @pytest.mark.asyncio
    async def test_an_unknown_sensor_read_is_reported_as_unverified(self) -> None:
        """Nothing was learned, so nothing may read as verified: an operator
        told 'hardware and driver state agree' would resume on a guess."""
        transport = _UnknownSensorTransport(pipettes=[_EIGHT_CHANNEL])
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)
        await driver.configure_deck(_FLEX_LAYOUT)
        await driver.initialize()

        report = await driver.reconcile_hardware_state(ReconcileHardwareStateRequest())

        assert [m.outcome for m in report.mounts] == ["unverified"]
        assert [m.sensor for m in report.mounts] == ["unknown"]
        assert not report.checked
        assert report.message is not None
        assert "could not be verified" in report.message
        assert "agree" not in report.message


class TestInitializeRefusesAStrandedTip:
    @pytest.mark.asyncio
    async def test_a_tip_left_on_the_nozzle_refuses_bring_up(self) -> None:
        """Freshly composed heads track no tips, so a sensor reading present is
        a tip nothing can account for. Coming up over that state hands the next
        pick_up_tips an empty model and a tipped nozzle."""
        transport = ChatterboxTransport(pipettes=[_EIGHT_CHANNEL])
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)
        await driver.configure_deck(_FLEX_LAYOUT)
        transport.set_tip_detected("left", True)

        with pytest.raises(RuntimeError, match="discard_stranded_tips"):
            await driver.initialize()

        assert not driver.is_initialized

    @pytest.mark.asyncio
    async def test_discarding_the_stranded_tip_lets_bring_up_finish(self) -> None:
        """The refusal names a resolution reachable without an initialized
        driver, so the operator is never wedged."""
        transport = ChatterboxTransport(pipettes=[_EIGHT_CHANNEL])
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)
        await driver.configure_deck(_FLEX_LAYOUT)
        transport.set_tip_detected("left", True)
        with pytest.raises(RuntimeError):
            await driver.initialize()

        await driver.discard_stranded_tips(DiscardStrandedTipsRequest())
        await driver.initialize()

        assert driver.is_initialized

    @pytest.mark.asyncio
    async def test_an_empty_nozzle_comes_up_as_before(self) -> None:
        driver, transport = await _configured()

        assert driver.is_initialized
        report = await driver.reconcile_hardware_state(ReconcileHardwareStateRequest())
        assert report.checked
        assert not report.requires_intervention


class TestDiscardStrandedTips:
    @pytest.mark.asyncio
    async def test_works_without_configure_deck(self) -> None:
        """The escape must not be gated behind configure_deck: the constructor
        deck already carries the trash, and the reconcile report prescribing
        this command is reachable without a configured deck."""
        transport = ChatterboxTransport(pipettes=[_EIGHT_CHANNEL])
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)
        await driver.initialize()
        transport.set_tip_detected("left", True)

        report = await driver.discard_stranded_tips(DiscardStrandedTipsRequest())

        assert [c["commandType"] for c in transport.commands].count("unsafe/dropTipInPlace") == 1
        assert not report.requires_intervention


    @pytest.mark.asyncio
    async def test_discards_what_only_the_sensor_knows_and_reports_in_sync(self) -> None:
        driver, transport = await _holding_tips()
        transport.end_run_externally()  # kills the session with tips still mounted

        report = await driver.discard_stranded_tips(DiscardStrandedTipsRequest())

        assert [c["commandType"] for c in transport.commands].count("unsafe/dropTipInPlace") == 1
        assert not report.requires_intervention
        assert [m.outcome for m in report.mounts] == ["in_sync"]
        state = await driver.get_deck_state(GetDeckStateRequest())
        assert state.tips_mounted == [False] * 8

    @pytest.mark.asyncio
    async def test_leaves_tips_the_model_tracks_for_discard_tips(self) -> None:
        driver, transport = await _holding_tips()

        report = await driver.discard_stranded_tips(DiscardStrandedTipsRequest())

        assert [c["commandType"] for c in transport.commands].count("unsafe/dropTipInPlace") == 0
        assert [m.outcome for m in report.mounts] == ["in_sync"]
        state = await driver.get_deck_state(GetDeckStateRequest())
        assert state.tips_mounted == [True] * 8


class TestOtherDriverFamilies:
    @pytest.mark.asyncio
    async def test_a_sensorless_wrapper_reports_unchecked(self) -> None:
        driver = ChatterboxLiquidHandlerDriver()

        report = await driver.reconcile_hardware_state(ReconcileHardwareStateRequest())

        assert not report.checked
        assert report.message is not None and "no hardware ground-truth" in report.message

    @pytest.mark.asyncio
    async def test_a_sensorless_wrapper_refuses_the_stranded_tip_escape(self) -> None:
        driver = ChatterboxLiquidHandlerDriver()

        with pytest.raises(RuntimeError, match="no tip-presence sensor"):
            await driver.discard_stranded_tips(DiscardStrandedTipsRequest())

    @pytest.mark.asyncio
    async def test_a_sim_driver_reports_checked_and_in_sync(self) -> None:
        driver = SimLiquidHandlerDriver(name="sim_lh")

        report = await driver.reconcile_hardware_state(ReconcileHardwareStateRequest())

        assert report.checked
        assert not report.requires_intervention


class TestTheWireOutcomesMatchTheDriverBelow:
    def test_mount_outcome_values_track_pylabrobot(self) -> None:
        """The wire model hand-lists what PLR's head can report. Let them drift
        and a real outcome fails validation on the way out of the driver."""
        wire = set(get_args(MountTipReconcile.model_fields["outcome"].annotation))
        assert wire == set(get_args(TipReconcileOutcome))
