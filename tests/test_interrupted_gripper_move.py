"""A gripper move that dies mid-flight must be visible in the deck state.

An interrupted move leaves the labware reported at the site it STARTED from,
which is the one place it is definitely not, and nothing distinguished that from
a plate sitting quietly on that slot.
"""

import asyncio

import pytest
from pylabrobot.opentrons import ChatterboxTransport
from pylabrobot.resources import Resource

from cheshire_drivers.liquid_handler_models import (
    AddDeckLabwareRequest,
    DeckLayoutConfig,
    DeckResourceConfig,
    GetDeckStateRequest,
    MovePlateRequest,
    ReconcileDeckOccupancyRequest,
    RemoveDeckLabwareRequest,
    ResetDeckLabwareRequest,
)
from cheshire_drivers.plr.liquid_handler import ChatterboxLiquidHandlerDriver
from cheshire_drivers.plr.opentrons_flex import FlexLiquidHandlerDriver

PLATE = "Cor_96_wellplate_360ul_Fb"


async def _driver_with_plate_at(slot: str) -> ChatterboxLiquidHandlerDriver:
    driver = ChatterboxLiquidHandlerDriver()
    await driver.configure_deck(DeckLayoutConfig(deck_type="FlexDeck", resources=[]))
    await driver.initialize()
    await driver.reconcile_deck_occupancy(
        ReconcileDeckOccupancyRequest(
            resources=[
                DeckResourceConfig(
                    name="p1", catalog_ref=PLATE, parent_id=slot, site_index=0,
                )
            ]
        )
    )
    return driver


async def _estop(
    plate: Resource, to: Resource, pickup_distance_from_top: float | None = None,
) -> None:
    """Raise where an e-stop does: past the driver's own validation, at the
    point the command reaches the machine."""
    raise RuntimeError("Run was cancelled")


def _break_the_gripper(
    driver: ChatterboxLiquidHandlerDriver, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert driver._lh is not None
    monkeypatch.setattr(driver._lh, "move_plate", _estop)


async def _fail_a_move(driver: ChatterboxLiquidHandlerDriver) -> None:
    with pytest.raises(RuntimeError):
        await driver.move_plate(
            MovePlateRequest(plate="p1", to_position="B2-slot", from_position="B4-slot")
        )


@pytest.mark.asyncio
async def test_a_failed_move_is_reported_not_silently_left_at_the_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = await _driver_with_plate_at("B4")
    _break_the_gripper(driver, monkeypatch)

    await _fail_a_move(driver)
    state = await driver.get_deck_state(GetDeckStateRequest())

    assert state.interrupted_move is not None, (
        "a gripper move that raised must be reported; without it the deck still "
        "says B4-slot and nothing says that is stale"
    )
    assert state.interrupted_move.labware == "p1"
    assert state.interrupted_move.to_site == "B2-slot"
    assert state.interrupted_move.from_site == "B4-slot"
    assert "Run was cancelled" in state.interrupted_move.error
    site = next(item.site for item in state.labware if item.name == "p1")
    assert site == "B4-slot", (
        f"the deck still names the site the move began at, which is the claim "
        f"`interrupted_move` exists to qualify; got {site!r}"
    )


@pytest.mark.asyncio
async def test_a_clean_deck_reports_no_interrupted_move() -> None:
    driver = await _driver_with_plate_at("B4")

    state = await driver.get_deck_state(GetDeckStateRequest())

    assert state.interrupted_move is None


@pytest.mark.asyncio
async def test_moving_the_same_labware_successfully_clears_the_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = await _driver_with_plate_at("B4")
    _break_the_gripper(driver, monkeypatch)
    await _fail_a_move(driver)

    monkeypatch.undo()
    await driver.move_plate(
        MovePlateRequest(plate="p1", to_position="B2-slot", from_position="B4-slot")
    )

    state = await driver.get_deck_state(GetDeckStateRequest())
    assert state.interrupted_move is None, (
        "the labware has been located, so the record must not outlive it"
    )


@pytest.mark.asyncio
async def test_a_deck_wide_reconcile_clears_the_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ledger re-asserting the whole deck is the authority speaking."""
    driver = await _driver_with_plate_at("B4")
    _break_the_gripper(driver, monkeypatch)
    await _fail_a_move(driver)

    await driver.reconcile_deck_occupancy(
        ReconcileDeckOccupancyRequest(
            resources=[
                DeckResourceConfig(
                    name="p1", catalog_ref=PLATE, parent_id="B2", site_index=0,
                )
            ]
        )
    )

    state = await driver.get_deck_state(GetDeckStateRequest())
    assert state.interrupted_move is None


@pytest.mark.asyncio
async def test_a_cancelled_move_is_reported_like_any_other_interruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An abort or a wire timeout CANCELS the move rather than raising into it.

    `CancelledError` is a `BaseException`, so catching `Exception` would let the
    most ordinary way a move dies on this system go unrecorded.
    """
    driver = await _driver_with_plate_at("B4")

    async def _cancelled(
        plate: Resource, to: Resource, pickup_distance_from_top: float | None = None,
    ) -> None:
        raise asyncio.CancelledError()

    assert driver._lh is not None
    monkeypatch.setattr(driver._lh, "move_plate", _cancelled)

    with pytest.raises(asyncio.CancelledError):
        await driver.move_plate(
            MovePlateRequest(plate="p1", to_position="B2-slot", from_position="B4-slot")
        )

    state = await driver.get_deck_state(GetDeckStateRequest())
    assert state.interrupted_move is not None
    assert state.interrupted_move.labware == "p1"


@pytest.mark.asyncio
async def test_clearing_the_deck_does_not_leave_a_stranded_plate_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleared deck and a plate in the jaws cannot both be true."""
    driver = await _driver_with_plate_at("B4")
    _break_the_gripper(driver, monkeypatch)
    await _fail_a_move(driver)

    await driver.reset_deck_labware(ResetDeckLabwareRequest())

    state = await driver.get_deck_state(GetDeckStateRequest())
    assert state.interrupted_move is None


@pytest.mark.asyncio
async def test_taking_the_labware_off_the_deck_settles_where_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator who takes that labware off has answered the question."""
    driver = await _driver_with_plate_at("B4")
    _break_the_gripper(driver, monkeypatch)
    await _fail_a_move(driver)

    await driver.remove_deck_labware(RemoveDeckLabwareRequest(name="p1"))

    state = await driver.get_deck_state(GetDeckStateRequest())
    assert state.interrupted_move is None


async def _flex_with_a_stranded_plate(
    monkeypatch: pytest.MonkeyPatch,
) -> FlexLiquidHandlerDriver:
    """A Flex whose gripper died holding p1, on its way from B4-slot to B2-slot."""
    driver = FlexLiquidHandlerDriver(
        host="localhost",
        transport=ChatterboxTransport(
            pipettes=[("p50_multi_flex", 8, 1.0, 50.0, "left")], gripper=True,
        ),
    )
    await driver.configure_deck(DeckLayoutConfig(deck_type="FlexDeck", resources=[]))
    await driver.initialize()
    await driver.add_deck_labware(
        AddDeckLabwareRequest(name="p1", catalog_ref=PLATE, at="B4-slot")
    )

    async def _estop_move_labware(
        resource: Resource, to_slot: str, grip_distance_from_top: float | None = None,
    ) -> None:
        raise RuntimeError("Run was cancelled")

    monkeypatch.setattr(driver._require_gripper("test"), "move_labware", _estop_move_labware)

    with pytest.raises(RuntimeError):
        await driver.move_plate(
            MovePlateRequest(plate="p1", to_position="B2-slot", from_position="B4-slot")
        )
    return driver


@pytest.mark.asyncio
async def test_the_flex_reports_an_interrupted_move_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bench failure happened on a Flex, which is a separate driver class.

    The Flex drives its gripper through `FlexGripper.move_labware`, not through
    PyLabRobot's generic `LiquidHandler.move_plate`, so the generic wrapper's
    coverage says nothing about it.
    """
    driver = await _flex_with_a_stranded_plate(monkeypatch)

    state = await driver.get_deck_state(GetDeckStateRequest())
    assert state.interrupted_move is not None, (
        "the driver the incident happened on must report this too"
    )
    assert state.interrupted_move.labware == "p1"
    assert state.interrupted_move.to_site == "B2-slot"


@pytest.mark.asyncio
async def test_a_deck_swap_that_could_not_finish_keeps_the_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restating the deck settles where a stranded plate went, but only once the
    restate actually happens. A swap refused part-way has restated nothing, and
    this record is the only thing saying a plate is in the jaws rather than on
    the slot the deck still names.
    """
    driver = await _flex_with_a_stranded_plate(monkeypatch)
    flex = driver._flex
    assert flex is not None

    async def _unreachable(resource: Resource) -> None:
        raise ConnectionError("robot unreachable")

    monkeypatch.setattr(flex, "labware_moved_off_deck", _unreachable)

    with pytest.raises(ConnectionError):
        await driver.configure_deck(DeckLayoutConfig(deck_type="FlexDeck", resources=[]))

    state = await driver.get_deck_state(GetDeckStateRequest())
    assert state.interrupted_move is not None, (
        "the deck was never restated, so the plate in the jaws is still unaccounted for"
    )
    assert state.interrupted_move.labware == "p1"


@pytest.mark.asyncio
async def test_a_later_failure_does_not_rename_the_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Jaws already holding something refuse the next move as well.

    That second refusal is the first strand's echo, so letting it overwrite the
    record would point recovery at the wrong plate and lose the stranded one.
    """
    driver = await _driver_with_plate_at("B4")
    await driver.add_deck_labware(
        AddDeckLabwareRequest(name="p2", catalog_ref=PLATE, at="C1-slot")
    )
    _break_the_gripper(driver, monkeypatch)
    await _fail_a_move(driver)

    with pytest.raises(RuntimeError):
        await driver.move_plate(
            MovePlateRequest(plate="p2", to_position="D1-slot", from_position="C1-slot")
        )

    state = await driver.get_deck_state(GetDeckStateRequest())
    assert state.interrupted_move is not None
    assert state.interrupted_move.labware == "p1", (
        f"the first unsettled strand must survive a later refusal; got "
        f"{state.interrupted_move.labware!r}"
    )
