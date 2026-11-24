"""The STAR and the OT-2 lift their heads just before a command's first move.

The real backends run with only their wire faked, so these pin which firmware or
robot-server commands go out and in what order.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import JsonValue
from pylabrobot.legacy.liquid_handling.backends.hamilton.base import HamiltonLiquidHandler
from pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_backend import STARBackend
from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import OpentronsOT2Backend

from cheshire_drivers.gantry_models import GantryBusyError
from cheshire_drivers.gripper_models import SetJawWidthRequest
from cheshire_drivers.pipette_motion_models import MoveChannelToRequest
from cheshire_drivers.plr.liquid_handler import OT2LiquidHandlerDriver, STARLiquidHandlerDriver

pytestmark = pytest.mark.asyncio


def _star(monkeypatch: pytest.MonkeyPatch, head96: bool = False, iswap_parked: bool = True) -> tuple[STARLiquidHandlerDriver, list[str]]:
    sent: list[str] = []

    async def _send(self: STARBackend, module: str, command: str, **kwargs: JsonValue) -> None:
        sent.append(command)

    async def _open(self: STARBackend, open_position: float | None = None) -> None:
        sent.append("open_gripper")

    monkeypatch.setattr(HamiltonLiquidHandler, "send_command", _send)
    monkeypatch.setattr(STARBackend, "iswap_open_gripper", _open)
    driver = STARLiquidHandlerDriver(serial_number="sim")
    backend = driver._star_backend
    backend._extended_conf = MagicMock()
    backend._extended_conf.left_x_drive.core_96_head_installed = head96
    backend._extended_conf.left_x_drive.iswap_installed = True
    backend._iswap_parked = iswap_parked
    monkeypatch.setattr(backend, "head96_move_to_z_safety", AsyncMock(side_effect=lambda: sent.append("head96_z_safety")))
    monkeypatch.setattr(backend, "move_iswap_z", AsyncMock(side_effect=lambda z: sent.append(f"iswap_z {z}")))
    driver._lh = MagicMock(setup_finished=True)
    return driver, sent


async def test_a_star_lifts_every_channel_just_before_the_first_move(monkeypatch: pytest.MonkeyPatch) -> None:
    driver, sent = _star(monkeypatch)

    await getattr(driver, "position_left_x_arm_")(1000)

    assert sent == ["ZA", "JX"]


async def test_a_star_lifts_the_96_head_and_an_unparked_iswap_too(monkeypatch: pytest.MonkeyPatch) -> None:
    driver, sent = _star(monkeypatch, head96=True, iswap_parked=False)

    await getattr(driver, "position_left_x_arm_")(1000)

    assert sent == ["ZA", "head96_z_safety", f"iswap_z {driver._star_backend.iswap_traversal_height}", "JX"]


async def test_a_star_request_the_backend_refuses_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    driver, sent = _star(monkeypatch)

    with pytest.raises(AssertionError):
        await getattr(driver, "position_left_x_arm_")(99999)

    assert sent == []


async def test_a_star_refuses_to_travel_while_the_iswap_may_hold_labware(monkeypatch: pytest.MonkeyPatch) -> None:
    driver, sent = _star(monkeypatch)
    driver._jaws_may_be_loaded = True

    with pytest.raises(GantryBusyError, match="iSWAP"):
        await getattr(driver, "position_left_x_arm_")(1000)

    assert sent == []


async def test_opening_the_iswap_by_its_vendor_command_lets_a_star_travel_again(monkeypatch: pytest.MonkeyPatch) -> None:
    driver, sent = _star(monkeypatch)
    driver._jaws_may_be_loaded = True

    await getattr(driver, "iswap_open_gripper")(90.0)
    await getattr(driver, "position_left_x_arm_")(1000)

    assert sent == ["open_gripper", "ZA", "JX"]


async def test_opening_the_iswap_with_set_jaw_width_lets_a_star_travel_again(monkeypatch: pytest.MonkeyPatch) -> None:
    driver, sent = _star(monkeypatch)
    driver._jaws_may_be_loaded = True

    await driver.set_jaw_width(SetJawWidthRequest(width=90.0))
    await getattr(driver, "position_left_x_arm_")(1000)

    assert sent == ["open_gripper", "ZA", "JX"]


def _ot2(monkeypatch: pytest.MonkeyPatch, z_by_pipette: dict[str, float]) -> tuple[OT2LiquidHandlerDriver, list[str]]:
    sent: list[str] = []

    async def _command(
        self: OpentronsOT2Backend, command_type: str, params: dict[str, JsonValue],
        timeout: float | None = None, abandon_run_on_timeout: bool = True,
    ) -> dict[str, JsonValue]:
        pipette = str(params["pipetteId"])
        if command_type == "savePosition":
            return {"data": {"result": {"position": {"x": 0.0, "y": 0.0, "z": z_by_pipette[pipette]}}}}
        coordinates = params["coordinates"]
        assert isinstance(coordinates, dict)
        sent.append(f"{command_type} {pipette} z={coordinates['z']}")
        return {}

    monkeypatch.setattr(OpentronsOT2Backend, "_command", _command)
    driver = OT2LiquidHandlerDriver(host="localhost")
    driver._ot_backend.left_pipette = {"pipetteId": "L"}
    driver._ot_backend.right_pipette = {"pipetteId": "R"}
    driver._lh = MagicMock(setup_finished=True)
    return driver, sent


async def test_an_ot2_lifts_a_low_pipette_before_the_other_one_travels(monkeypatch: pytest.MonkeyPatch) -> None:
    driver, sent = _ot2(monkeypatch, {"L": 40.0, "R": 200.0})

    await driver.move_channel_to(MoveChannelToRequest(channel=1, x=50.0, y=50.0))

    assert sent == ["moveToCoordinates L z=120", "moveToCoordinates R z=200.0"]


async def test_an_ot2_request_the_backend_refuses_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    driver, sent = _ot2(monkeypatch, {"L": 40.0, "R": 200.0})

    with pytest.raises(Exception, match="Channel 5"):
        await driver.move_channel_to(MoveChannelToRequest(channel=5, x=50.0))

    assert sent == []


_READS = ("request", "get_")
_STAYS_IN_Z = ("_z", "z_", "plld", "clld_probe_z", "ztouch")

_NOT_XY_MOVES: dict[str, str] = {
    "move_left_x_arm_to_position_with_all_attached_components_in_z_safety_position": "raises every component to Z safety itself",
    "move_right_x_arm_to_position_with_all_attached_components_in_z_safety_position": "raises every component to Z safety itself",
    "move_core_96_to_safe_position": "the 96 head to Z safety only",
    "channel_dispensing_drive_move_to_volume_position": "a plunger",
    "head96_dispensing_drive_and_squeezer_driver_initialize": "a plunger",
    "head96_dispensing_drive_move_to_home_volume": "a plunger",
    "head96_dispensing_drive_move_to_position": "a plunger",
    "initialize_auto_load": "the autoload carriage, not the gantry",
    "initialize_autoload": "the autoload carriage, not the gantry",
    "park_autoload": "the autoload carriage, not the gantry",
    "move_autoload_to_slot": "the autoload carriage, not the gantry",
    "move_autoload_to_track": "the autoload carriage, not the gantry",
    "initialize_dual_pump_station_valves": "pump valves",
    "initialize_hhc": "a heater-cooler module",
    "open_not_initialized_gripper": "the jaw only",
    "core_open_gripper": "the jaw only",
    "save_xl_channel_validation_status": "a stored setting",
    "set_x_offset_x_axis_core_96_head": "a stored setting",
    "set_x_offset_x_axis_core_nano_pipettor_head": "a stored setting",
    "set_x_offset_x_axis_iswap": "a stored setting",
}
"""STAR commands whose names look like motion but that never carry a head in X or Y, and why."""


def test_every_star_command_that_moves_x_or_y_lifts_first() -> None:
    import inspect
    import re

    from cheshire_drivers.driver_introspection import vendor_surfaces

    travels = vendor_surfaces(STARLiquidHandlerDriver)[0].travels
    looks_like_motion = re.compile(
        r"move|position|park|spread|pick_up|drop|discard|aspirat|dispens|plate|probe|initiali|empty_tip|core|teach|search|_x|_y"
    )
    commands = [name for name, member in inspect.getmembers(STARBackend)
                if not name.startswith("_") and inspect.iscoroutinefunction(member)]
    unclassified = [
        name for name in commands
        if looks_like_motion.search(name) and name not in travels and name not in _NOT_XY_MOVES
        and not any(read in name for read in _READS) and not any(z in name for z in _STAYS_IN_Z)
    ]

    assert unclassified == []
    assert travels.isdisjoint(_NOT_XY_MOVES)
