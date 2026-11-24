"""A gripper move with no plate on record uses the driver's default labware, its default grip.

The stand-in is carried for the one move and leaves the deck after it, so the deck record gains nothing.
"""

import pytest
from pydantic import ValidationError

from cheshire_drivers import DeckLayoutConfig, DeckResourceConfig
from cheshire_drivers.liquid_handler_models import DEFAULT_GRIP_LABWARE, GetDeckStateRequest, MovePlateRequest
from cheshire_drivers.plr import ChatterboxLiquidHandlerDriver
from cheshire_drivers import CartesianCoordinates, Teachpoint
from cheshire_drivers.labware_models import LabwareIdentity
from cheshire_drivers.move_parameters import SEED_MOVE_PARAMETERS
from cheshire_drivers.protocol_runner_models import LabwareHandoffRequest
from cheshire_drivers.sims import SimLiquidHandlerDriver, SimTransporterDriver, SimTransporterValidationError
from cheshire_drivers.transporter_models import PickAtCoordsRequest, PlaceAtCoordsRequest

from tests.test_opentrons_flex_pipetting import _EIGHT_CHANNEL, _SINGLE_CHANNEL, _bench

pytestmark = pytest.mark.asyncio


async def _hamilton() -> ChatterboxLiquidHandlerDriver:
    driver = ChatterboxLiquidHandlerDriver()
    carrier = DeckResourceConfig(name="carrier-25", catalog_ref="PLT_CAR_L5AC_A00", rail=25)
    await driver.configure_deck(DeckLayoutConfig(deck_type="STARLet", resources=[carrier]))
    return driver


async def test_a_hamilton_gripper_moves_with_its_default_grip_and_leaves_no_labware() -> None:
    driver = await _hamilton()
    before = (await driver.get_deck_state(GetDeckStateRequest())).labware

    await driver.move_plate(MovePlateRequest(plate=None, from_position="carrier-25-0", to_position="carrier-25-1"))

    assert (await driver.get_deck_state(GetDeckStateRequest())).labware == before


async def test_a_simulated_gripper_moves_with_nothing_on_record() -> None:
    driver = SimLiquidHandlerDriver("lh_sim")
    before = (await driver.get_deck_state(GetDeckStateRequest())).labware

    await driver.move_plate(MovePlateRequest(plate=None, from_position="A1-slot", to_position="D4-slot"))

    assert (await driver.get_deck_state(GetDeckStateRequest())).labware == before


async def test_a_flex_gripper_moves_with_its_default_grip_and_the_robot_keeps_nothing() -> None:
    driver, transport = await _bench(_EIGHT_CHANNEL, _SINGLE_CHANNEL, gripper=True)
    before = len(transport.commands)

    await driver.move_plate(MovePlateRequest(plate=None, from_position="B2-slot", to_position="B3-slot"))

    sent = [command["commandType"] for command in transport.commands[before:]]
    assert "moveLabware" in sent
    assert DEFAULT_GRIP_LABWARE not in {item.name for item in (await driver.get_deck_state(GetDeckStateRequest())).labware}


def test_a_move_with_no_plate_must_say_where_it_starts() -> None:
    with pytest.raises(ValidationError, match="from_position"):
        MovePlateRequest(plate=None, to_position="carrier-25-1")


async def test_a_simulated_arm_picks_and_places_with_nothing_on_record() -> None:
    """With no labware named, an empty site is not a mistake: the arm moves with its default grip."""
    driver = SimTransporterDriver("arm_1")
    pad = Teachpoint("pad_1", CartesianCoordinates(0, 0, 0, 0, 90, 180), orientation="right")

    await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=pad, handling=SEED_MOVE_PARAMETERS))
    await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=pad, handling=SEED_MOVE_PARAMETERS))


async def test_a_simulated_arm_still_refuses_an_empty_site_when_a_labware_type_is_named() -> None:
    driver = SimTransporterDriver("arm_1")
    pad = Teachpoint("pad_1", CartesianCoordinates(0, 0, 0, 0, 90, 180), orientation="right")

    with pytest.raises(SimTransporterValidationError, match="is empty"):
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=pad, handling=SEED_MOVE_PARAMETERS, labware_type="Plate_96"))


async def test_a_simulated_arm_still_refuses_an_empty_site_when_a_plate_is_named() -> None:
    driver = SimTransporterDriver("arm_1")
    pad = Teachpoint("pad_1", CartesianCoordinates(0, 0, 0, 0, 90, 180), orientation="right")
    named = LabwareIdentity(labware_id="plate-1", labware_type="plate_96")

    with pytest.raises(SimTransporterValidationError, match="is empty"):
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=pad, handling=SEED_MOVE_PARAMETERS, expected_labware=named))


def test_a_move_with_no_plate_survives_the_wire() -> None:
    """The remote driver drops None fields when it sends; the request must still read back."""
    sent = MovePlateRequest(plate=None, from_position="carrier-25-0", to_position="carrier-25-1")

    assert MovePlateRequest.model_validate(sent.model_dump(exclude_none=True)) == sent


def test_a_handoff_with_nothing_on_record_reads_back() -> None:
    sent = LabwareHandoffRequest(site="deck-1")

    assert LabwareHandoffRequest.model_validate(sent.model_dump(exclude_none=True)) == sent
