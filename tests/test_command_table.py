"""The command table reads every command, its request and its response from the interface classes."""

from abc import ABC, abstractmethod

import pytest
from pydantic import BaseModel, ValidationError

from cheshire_drivers.command_responses import EmptyCommandResponse, InitializedResponse, TemperatureResponse
from cheshire_drivers.command_table import COMMANDS, CommandTable
from cheshire_drivers.gripper_models import GripperPosition, GripperRotationResponse, JawWidthResponse
from cheshire_drivers.liquid_handler_models import AspirateRequest, LabwareStateResponse, LiquidProbeResponse
from cheshire_drivers.pipette_motion_models import ChannelPosition
from cheshire_drivers.shaker_models import ShakeRequest
from cheshire_drivers.thermocycler_models import TemperatureListResponse
from cheshire_drivers.transporter_models import InitializeRequest, SpeedResponse


def test_a_command_that_takes_a_model_is_called_with_that_model_under_its_argument_name() -> None:
    shake = COMMANDS.find({"IShaker"}, "shake")

    assert shake is not None and shake.request_model is ShakeRequest
    kwargs = shake.driver_kwargs({"speed": 500, "duration": 10})
    assert set(kwargs) == {"request"} and isinstance(kwargs["request"], ShakeRequest)


def test_a_command_with_plain_arguments_takes_them_flat_and_refuses_any_other() -> None:
    set_temperature = COMMANDS.find({"ISealer"}, "set_temperature")

    assert set_temperature is not None
    assert set_temperature.driver_kwargs({"temperature": 40.0}) == {"temperature": 40.0}
    with pytest.raises(ValidationError):
        set_temperature.validate({"temperature": 40.0, "tempreature": 41.0})


def test_a_command_with_no_arguments_refuses_any_argument() -> None:
    stop = COMMANDS.find({"IShaker"}, "stop")

    assert stop is not None and stop.driver_kwargs({}) == {}
    with pytest.raises(ValidationError):
        stop.validate({"now": True})


def test_an_interface_that_overrides_a_base_command_is_the_one_found() -> None:
    transporter_init = COMMANDS.find({"ITransporter", "IHomeable"}, "initialize")
    shaker_init = COMMANDS.find({"IShaker"}, "initialize")

    assert transporter_init is not None and transporter_init.request_model is InitializeRequest
    assert shaker_init is not None and shaker_init.request_param is None


def test_a_bare_value_is_answered_with_the_model_its_returns_marker_names() -> None:
    assert COMMANDS.response_model({"ITransporter"}, "get_speed") is SpeedResponse


def test_a_marked_property_is_a_readable_command_and_an_unmarked_one_is_not() -> None:
    is_initialized = COMMANDS.find({"IShaker"}, "is_initialized")

    assert is_initialized is not None and is_initialized.is_property
    assert is_initialized.response_model is InitializedResponse
    assert COMMANDS.find({"IShaker"}, "supports_locking") is None


def test_a_name_no_interface_declares_is_a_vendor_extra_with_an_empty_response() -> None:
    assert COMMANDS.find({"IShaker"}, "set_vendor_mode") is None
    assert COMMANDS.response_model({"IShaker"}, "set_vendor_mode") is EmptyCommandResponse


def test_a_command_is_found_only_on_an_interface_the_device_has() -> None:
    assert COMMANDS.find({"IShaker"}, "aspirate") is None


def test_a_scalar_flow_rate_is_spread_across_every_channel() -> None:
    aspirate = COMMANDS.find({"ILiquidHandler"}, "aspirate")
    assert aspirate is not None

    request = aspirate.validate({"aspirations": [{"labware": "p", "positions": ["A1", "B1"], "volumes": [5, 5]}], "flow_rate": 2})

    assert isinstance(request, AspirateRequest) and request.flow_rates == [2.0, 2.0]


def test_an_interface_method_that_returns_a_bare_value_without_a_marker_is_refused() -> None:
    class IUnmarkedDriver(ABC):
        @abstractmethod
        async def read_level(self) -> float: ...

    with pytest.raises(TypeError, match="read_level"):
        CommandTable({"IUnmarked": IUnmarkedDriver})


def test_a_model_returning_method_needs_no_marker() -> None:
    class Level(BaseModel):
        level: float

    class ILevelDriver(ABC):
        @abstractmethod
        async def read_level(self) -> Level: ...

    assert CommandTable({"ILevel": ILevelDriver}).response_model({"ILevel"}, "read_level") is Level


@pytest.mark.parametrize(
    "interface,command,expected",
    [
        ("ILiquidHandler", "aspirate", LabwareStateResponse),
        ("ITransporter", "get_speed", SpeedResponse),
        ("IThermocycler", "get_block_current_temperature", TemperatureListResponse),
        ("IShaker", "initialize", EmptyCommandResponse),
        ("ITransporter", "is_initialized", InitializedResponse),
        ("ISealer", "get_temperature", TemperatureResponse),
        ("ILiquidProbe", "liquid_probe", LiquidProbeResponse),
        ("IPipetteMotion", "get_channel_position", ChannelPosition),
        ("IGripperPosition", "get_gripper_position", GripperPosition),
        ("IWidthGripperJaw", "get_jaw_width", JawWidthResponse),
        ("IGripperRotation", "get_gripper_rotation", GripperRotationResponse),
    ],
)
def test_each_command_answers_with_the_model_that_carries_its_data(
    interface: str, command: str, expected: type[BaseModel],
) -> None:
    assert COMMANDS.response_model({interface}, command) is expected


@pytest.mark.parametrize(
    "interfaces,command",
    [({"ILiquidHandler"}, "liquid_probe"), ({"IForceGripperJaw"}, "get_jaw_width")],
)
def test_hardware_a_device_does_not_declare_gives_it_no_command(interfaces: set[str], command: str) -> None:
    assert COMMANDS.find(interfaces, command) is None


def test_every_request_refuses_a_field_it_does_not_declare() -> None:
    loose = sorted(
        f"{name}.{command.name}"
        for name in COMMANDS.interface_names()
        for command in COMMANDS.commands_of(name).values()
        if command.request_model.model_config.get("extra") != "forbid"
    )
    assert not loose


def test_a_name_two_interfaces_declare_the_same_way_is_one_command() -> None:
    found = COMMANDS.find({"ITransporter", "IShaker"}, "is_initialized")

    assert found is not None and found.is_property and found.response_model is InitializedResponse


def test_a_name_two_interfaces_declare_differently_is_refused_naming_both() -> None:
    with pytest.raises(ValueError, match=r"ILiquidHandler.*IWaste"):
        COMMANDS.find({"ILiquidHandler", "IWaste"}, "dispense")


def _interface_classes() -> list[type]:
    from cheshire_drivers import interfaces

    return [
        cls for name, cls in vars(interfaces).items()
        if isinstance(cls, type) and name.startswith("I") and name.endswith("Driver") and cls.__module__ == interfaces.__name__
    ]


@pytest.mark.parametrize("cls", _interface_classes(), ids=lambda cls: cls.__name__)
def test_every_interface_class_declares_its_own_interface_names(cls: type) -> None:
    """A class that inherits its base's names makes devices handshake under the base's name, so its commands never resolve."""
    assert cls.__dict__.get("interfaces"), f"{cls.__name__} declares no `interfaces` of its own"


def test_every_name_a_device_can_advertise_is_an_interface_the_table_reads() -> None:
    """The table names an interface after its class; a device advertises its `interfaces` names. The two must agree."""
    advertised = {name for cls in _interface_classes() for name in cls.__dict__.get("interfaces", frozenset())}

    assert advertised - COMMANDS.interface_names() == set()


def test_a_command_says_whether_operators_and_workflows_may_send_it() -> None:
    """`@external` on the interface decides it; a deck-occupancy write the engine sends is not external."""
    shake = COMMANDS.find({"IShaker"}, "shake")
    reset = COMMANDS.find({"ILiquidHandler"}, "reset_deck_labware")

    assert shake is not None and shake.external
    assert reset is not None and not reset.external
    assert "shake" in COMMANDS.external_names() and "reset_deck_labware" not in COMMANDS.external_names()


def test_only_bring_up_link_and_hand_back_are_lifecycle_commands() -> None:
    """`@lifecycle` on the interface marks the commands only their own operation sends, on devices and arms alike."""
    lifecycle = {(c.interface, c.name) for i in COMMANDS.interface_names() for c in COMMANDS.commands_of(i).values() if c.lifecycle}
    names = {name for _interface, name in lifecycle}
    interfaces_with_them = {interface for interface, _name in lifecycle}

    assert names == {"initialize", "connect", "disconnect"} == COMMANDS.lifecycle_names()
    assert {"IShaker", "ITransporter"} <= interfaces_with_them
