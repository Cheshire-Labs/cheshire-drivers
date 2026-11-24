"""A command's request model holds its limits, so a workflow, a typed route and a command by name all refuse the same values."""

import pytest
from pydantic import JsonValue, ValidationError

from cheshire_drivers.command_table import COMMANDS


@pytest.mark.parametrize("interface, command, params", [
    ("IShaker", "shake", {"speed": 2500, "duration": 10}),
    ("IShaker", "shake", {"speed": 500, "duration": 0}),
    ("ICentrifuge", "centrifuge", {"g": 6000, "duration": 60}),
    ("ISealer", "seal", {"temperature": 250, "duration": 5}),
    ("ITempSettable", "set_temperature", {"temperature": 250}),
    ("ISealer", "set_temperature", {"temperature": -40}),
    ("ITransporter", "set_speed", {"speed": 1.5}),
    ("ILiquidHandler", "drop_tips", {"drops": [], "to_waste": False}),
    ("ILiquidHandler", "aspirate96", {"labware": "plate", "volume": 0}),
    ("ILiquidHandler", "dispense96", {"labware": "plate", "volume": 10, "liquid_height": -1}),
    ("ILiquidHandler", "drop_tips96", {"tip_rack": "r" * 101}),
])
def test_a_value_past_the_commands_limit_is_refused(interface: str, command: str, params: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        COMMANDS.commands_of(interface)[command].validate(params)


@pytest.mark.parametrize("interface, command, params", [
    ("IShaker", "shake", {"speed": 2000, "duration": 7200}),
    ("ICentrifuge", "centrifuge", {"g": 5000, "duration": 60}),
    ("ITempSettable", "set_temperature", {"temperature": -20}),
    ("ITransporter", "set_speed", {"speed": 1}),
])
def test_a_value_at_the_limit_is_accepted(interface: str, command: str, params: dict[str, JsonValue]) -> None:
    COMMANDS.commands_of(interface)[command].validate(params)
