"""A gripper move with no plate on record carries a stand-in. A move that fails leaves no trace of the stand-in."""

import pytest
from pylabrobot.resources import Cor_96_wellplate_360ul_Fb, Resource

from cheshire_drivers import DeckLayoutConfig, DeckResourceConfig
from cheshire_drivers.default_grip import DEFAULT_STAND_IN, StandInLabware
from cheshire_drivers.liquid_handler_models import (
    AddDeckLabwareRequest,
    DeckStateResponse,
    GetDeckStateRequest,
    MovePlateRequest,
)
from cheshire_drivers.plr import ChatterboxLiquidHandlerDriver
from cheshire_drivers.plr.opentrons_flex import FlexLiquidHandlerDriver

from tests.test_opentrons_flex_pipetting import _EIGHT_CHANNEL, _SINGLE_CHANNEL, _bench

pytestmark = pytest.mark.asyncio

_OTHER_STAND_IN = StandInLabware(name="other_stand_in", plate=Cor_96_wellplate_360ul_Fb)


async def _hamilton() -> ChatterboxLiquidHandlerDriver:
    driver = ChatterboxLiquidHandlerDriver()
    carrier = DeckResourceConfig(name="carrier-25", catalog_ref="PLT_CAR_L5AC_A00", rail=25)
    await driver.configure_deck(DeckLayoutConfig(deck_type="STARLet", resources=[carrier]))
    return driver


async def _flex() -> FlexLiquidHandlerDriver:
    driver, _transport = await _bench(_EIGHT_CHANNEL, _SINGLE_CHANNEL, gripper=True)
    return driver


class _Gripper:
    """Stands in for the vendor gripper call: records what it was asked to carry, and fails when told to."""

    def __init__(self, fails: bool) -> None:
        self.carried: list[Resource] = []
        self._fails = fails

    async def hamilton(self, plate: Resource, to: Resource, pickup_distance_from_top: float | None = None) -> None:
        self._carry(plate)

    async def flex(self, resource: Resource, to_slot: str, grip_distance_from_top: float | None = None) -> None:
        self._carry(resource)

    def _carry(self, resource: Resource) -> None:
        self.carried.append(resource)
        if self._fails:
            raise RuntimeError("Run was cancelled")


def _on_the_hamilton(driver: ChatterboxLiquidHandlerDriver, gripper: _Gripper, monkeypatch: pytest.MonkeyPatch) -> None:
    assert driver._lh is not None
    monkeypatch.setattr(driver._lh, "move_plate", gripper.hamilton)


def _on_the_flex(driver: FlexLiquidHandlerDriver, gripper: _Gripper, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(driver._require_gripper("test"), "move_labware", gripper.flex)


_HAMILTON_MOVE = MovePlateRequest(plate=None, from_position="carrier-25-0", to_position="carrier-25-1")
_FLEX_MOVE = MovePlateRequest(plate=None, from_position="B2-slot", to_position="B3-slot")


def _assert_the_note_names_no_labware(state: DeckStateResponse, move: MovePlateRequest) -> None:
    note = state.interrupted_move
    assert note is not None
    assert note.labware is None
    assert (note.from_site, note.to_site) == (move.from_position, move.to_position)
    assert "Run was cancelled" in note.error
    assert DEFAULT_STAND_IN.name not in state.model_dump_json()


async def test_a_failed_stand_in_move_on_a_hamilton_leaves_no_stand_in_and_names_no_labware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = await _hamilton()
    before = (await driver.get_deck_state(GetDeckStateRequest())).labware
    _on_the_hamilton(driver, _Gripper(fails=True), monkeypatch)

    with pytest.raises(RuntimeError, match="Run was cancelled"):
        await driver.move_plate(_HAMILTON_MOVE)

    state = await driver.get_deck_state(GetDeckStateRequest())
    assert state.labware == before
    _assert_the_note_names_no_labware(state, _HAMILTON_MOVE)
    assert driver._jaws_may_be_loaded


async def test_a_failed_stand_in_move_on_a_flex_leaves_no_stand_in_and_names_no_labware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = await _flex()
    before = (await driver.get_deck_state(GetDeckStateRequest())).labware
    _on_the_flex(driver, _Gripper(fails=True), monkeypatch)

    with pytest.raises(RuntimeError, match="Run was cancelled"):
        await driver.move_plate(_FLEX_MOVE)

    state = await driver.get_deck_state(GetDeckStateRequest())
    assert state.labware == before
    _assert_the_note_names_no_labware(state, _FLEX_MOVE)
    assert driver._jaws_may_be_loaded


async def test_a_hamilton_given_another_stand_in_moves_that_one(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = await _hamilton()
    driver._stand_in = _OTHER_STAND_IN
    gripper = _Gripper(fails=False)
    _on_the_hamilton(driver, gripper, monkeypatch)

    await driver.move_plate(_HAMILTON_MOVE)

    [carried] = gripper.carried
    assert carried.name == "other_stand_in"
    assert carried.model == Cor_96_wellplate_360ul_Fb("reference").model


async def test_a_flex_given_another_stand_in_moves_that_one(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = await _flex()
    driver._stand_in = _OTHER_STAND_IN
    gripper = _Gripper(fails=False)
    _on_the_flex(driver, gripper, monkeypatch)

    await driver.move_plate(_FLEX_MOVE)

    [carried] = gripper.carried
    assert carried.name == "other_stand_in"
    assert carried.model == Cor_96_wellplate_360ul_Fb("reference").model


async def test_a_stand_in_move_that_works_does_not_settle_another_plates_interrupted_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = await _hamilton()
    await driver.add_deck_labware(
        AddDeckLabwareRequest(name="p1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="carrier-25-4"),
    )
    _on_the_hamilton(driver, _Gripper(fails=True), monkeypatch)
    with pytest.raises(RuntimeError):
        await driver.move_plate(MovePlateRequest(plate="p1", from_position="carrier-25-4", to_position="carrier-25-3"))
    monkeypatch.undo()

    await driver.move_plate(_HAMILTON_MOVE)

    note = (await driver.get_deck_state(GetDeckStateRequest())).interrupted_move
    assert note is not None and note.labware == "p1"
