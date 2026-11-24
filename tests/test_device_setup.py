"""A device's setup file names one owner per fact, and a setup that could never connect is refused when it loads."""

import pytest
from pydantic import JsonValue, ValidationError

from cheshire_drivers.device_setup import DeviceSetup
from cheshire_drivers.driver_lookup import (
    DriverRequest,
    build_real_driver,
    build_sim_driver,
    check_device_setup,
    driver_interfaces,
    real_driver_class,
)
from cheshire_drivers.plr import FlexLiquidHandlerDriver, STARLiquidHandlerDriver
from cheshire_drivers.plr.liquid_handler import ChatterboxLiquidHandlerWithProtocolDriver
from cheshire_drivers.plr.transporter import PreciseFlexTransporterDriver
from cheshire_drivers.sims import SimLiquidHandlerWithProtocolDriver, SimShakerDriver


def _setup(**fields: JsonValue) -> DeviceSetup:
    return DeviceSetup.model_validate(fields)


def _request(name: str, kind: str, setup: DeviceSetup, deck_modeling: bool = False) -> DriverRequest:
    return DriverRequest(name=name, kind=kind, setup=setup, deck_modeling=deck_modeling)


_PF400: dict[str, JsonValue] = {
    "client": "lab_pc",
    "real": {"type": "plr", "backend": "PreciseFlex", "connection": {"type": "tcp", "host": "10.0.0.5"}},
    "arm": {"gripper_length": 140.0, "gripper_z_offset": 12.0, "closed_gripper_position": 90.0},
}


def test_no_setup_file_names_no_client_so_the_server_picks_its_default_client_and_a_default_sim() -> None:
    setup = DeviceSetup()

    assert setup.client is None
    assert setup.real is None
    assert isinstance(build_sim_driver(_request("shaker_1", "shaker", setup)), SimShakerDriver)


def test_a_field_nobody_declared_is_refused_so_a_user_cannot_set_axis_speeds_by_hand() -> None:
    with pytest.raises(ValidationError, match="axis_speed"):
        _setup(axis_speed=100.0)


@pytest.mark.parametrize(
    ("field", "value"),
    [("model", "pf400"), ("geometry", {"l1_mm": 302.0}), ("fitted", {"left": "p1000"})],
)
def test_a_field_that_only_described_a_device_for_drawing_is_refused(field: str, value: JsonValue) -> None:
    with pytest.raises(ValidationError, match=field):
        _setup(**{field: value})


def test_a_real_driver_on_the_built_in_client_is_refused_because_it_has_no_hardware() -> None:
    with pytest.raises(ValidationError, match="lab PC"):
        _setup(client="builtin", real={"type": "human"})


def test_a_real_driver_that_names_no_client_loads_so_the_default_client_can_run_it() -> None:
    setup = _setup(real={"type": "plr", "backend": "PreciseFlex", "connection": {"type": "tcp", "host": "10.0.0.5"}})

    assert setup.client is None
    assert setup.real is not None


def test_the_vendor_sim_is_refused_on_the_built_in_client_because_it_has_no_machine_config() -> None:
    with pytest.raises(ValidationError, match="machine"):
        _setup(client="builtin", sim={"use_vendor_sim": True, "simulator_config": "flex.json"})


@pytest.mark.parametrize("name", ["..", ".", ""])
def test_the_vendor_sim_config_is_refused_when_it_names_a_folder_and_not_a_file(name: str) -> None:
    with pytest.raises(ValidationError, match="file name"):
        _setup(client="lab_pc", sim={"use_vendor_sim": True, "simulator_config": name})


def test_the_vendor_sim_config_is_a_file_name_so_a_server_cannot_point_a_lab_pc_at_any_path() -> None:
    with pytest.raises(ValidationError, match="file name"):
        _setup(client="lab_pc", sim={"use_vendor_sim": True, "simulator_config": "C:/secrets/flex.json"})


@pytest.mark.parametrize(
    "real",
    [
        {"type": "plr", "backend": "STAR", "venus": {"init_protocol": "init.hsl"}},
        {"type": "venus", "backend": "STAR"},
        {"type": "human", "connection": {"type": "usb"}},
    ],
)
def test_a_field_another_driver_type_reads_is_refused(real: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError, match="driver does not read"):
        _setup(client="lab_pc", real=real)


@pytest.mark.parametrize(
    "kind,backend",
    [("shaker", "STARBackend"), ("liquid_handler", "VSpinBackend"), ("centrifuge", "Access2Backend")],
)
def test_a_backend_of_another_kind_is_refused_naming_the_device(kind: str, backend: str) -> None:
    setup = _setup(client="lab_pc", real={"type": "plr", "backend": backend})

    with pytest.raises(ValueError, match=rf"dev_1.*{backend}.*{kind}"):
        check_device_setup("dev_1", kind, setup)


def test_the_server_reads_a_drivers_class_without_building_it() -> None:
    setup = _setup(client="lab_pc", real={"type": "plr", "backend": "OpentronsFlex", "connection": {"type": "tcp", "host": "10.0.0.6"}})

    assert setup.real is not None
    assert real_driver_class("liquid_handler", setup.real) is FlexLiquidHandlerDriver


def test_a_star_is_named_by_its_usb_serial_number() -> None:
    setup = _setup(client="lab_pc", real={"type": "plr", "backend": "STAR", "connection": {"type": "usb", "serial_number": "A1"}})

    driver = build_real_driver(_request("star_1", "liquid_handler", setup))

    assert isinstance(driver, STARLiquidHandlerDriver)
    assert driver._star_backend.io._serial_number == "A1"


@pytest.mark.parametrize(
    ("backend", "connection"),
    [
        ("VSpinBackend", {"type": "usb", "device_id": "FT123"}),
        ("MicroSpinBackend", {"type": "tcp", "host": "10.0.0.9"}),
    ],
)
def test_each_centrifuge_connection_form_is_accepted(backend: str, connection: dict[str, JsonValue]) -> None:
    check_device_setup("spin_1", "centrifuge", _setup(client="lab_pc", real={"type": "plr", "backend": backend, "connection": connection}))


def test_a_connection_field_the_driver_does_not_take_is_refused_naming_the_device_and_the_field() -> None:
    setup = _setup(client="lab_pc", real={"type": "plr", "backend": "VSpinBackend", "connection": {"type": "usb", "serial_number": "X"}})

    with pytest.raises(ValueError, match=r"spin_1.*serial_number"):
        check_device_setup("spin_1", "centrifuge", setup)


def test_a_required_connection_value_left_out_is_refused_naming_the_device_and_the_value() -> None:
    setup = _setup(client="lab_pc", real={"type": "plr", "backend": "MicroSpinBackend"})

    with pytest.raises(ValueError, match=r"spin_1.*host"):
        check_device_setup("spin_1", "centrifuge", setup)


def test_a_kind_with_no_real_driver_is_refused_when_the_setup_loads() -> None:
    setup = _setup(client="lab_pc", real={"type": "plr", "backend": "STAR", "connection": {"type": "usb"}})

    with pytest.raises(ValueError, match=r"washer_1.*plate_washer.*no real driver"):
        check_device_setup("washer_1", "plate_washer", setup)


def test_a_backend_name_that_does_not_exist_is_refused_when_the_setup_loads() -> None:
    setup = _setup(client="lab_pc", real={"type": "plr", "backend": "NoSuchBackend"})

    with pytest.raises(ValueError, match=r"shaker_1.*NoSuchBackend"):
        check_device_setup("shaker_1", "shaker", setup)


def test_the_vendor_sim_is_refused_when_the_setup_loads_for_a_backend_not_reached_at_a_host() -> None:
    setup = _setup(
        client="lab_pc",
        real={"type": "plr", "backend": "STAR"},
        sim={"use_vendor_sim": True, "simulator_config": "star.json"},
    )

    with pytest.raises(ValueError, match=r"star_1.*no vendor simulator"):
        check_device_setup("star_1", "liquid_handler", setup)


def test_the_vendor_sim_is_refused_when_the_setup_loads_for_a_device_naming_no_backend() -> None:
    setup = _setup(client="lab_pc", sim={"use_vendor_sim": True, "simulator_config": "flex.json"})

    with pytest.raises(ValueError, match=r"lh_1.*no vendor simulator"):
        check_device_setup("lh_1", "liquid_handler", setup)


def test_a_liquid_handler_that_models_its_deck_gets_the_chatterbox_in_sim() -> None:
    driver = build_sim_driver(_request("lh_1", "liquid_handler", _setup(sim={"channels": 96}), deck_modeling=True))

    assert isinstance(driver, ChatterboxLiquidHandlerWithProtocolDriver)


def test_a_liquid_handler_that_does_not_model_its_deck_keeps_the_plain_sim() -> None:
    assert isinstance(build_sim_driver(_request("lh_1", "liquid_handler", DeviceSetup())), SimLiquidHandlerWithProtocolDriver)


def test_the_server_knows_a_real_drivers_interfaces_from_its_setup() -> None:
    setup = _setup(client="lab_pc", real={"type": "plr", "backend": "OpentronsFlex", "connection": {"type": "tcp", "host": "10.0.0.6"}})

    assert driver_interfaces("liquid_handler", setup) == FlexLiquidHandlerDriver.interfaces


def test_the_server_knows_a_simulated_devices_interfaces_from_its_setup() -> None:
    assert driver_interfaces("shaker", DeviceSetup()) == SimShakerDriver.interfaces
    assert driver_interfaces("liquid_handler", DeviceSetup(), deck_modeling=True) == ChatterboxLiquidHandlerWithProtocolDriver.interfaces


def test_a_simulated_liquid_handler_can_run_protocols_as_the_topology_may_ask() -> None:
    assert "IProtocolRunner" in driver_interfaces("liquid_handler", DeviceSetup())
    assert "IProtocolRunner" in driver_interfaces("liquid_handler", DeviceSetup(), deck_modeling=True)


def test_an_arm_block_on_a_device_that_is_not_an_arm_is_refused() -> None:
    setup = _setup(client="lab_pc", arm={"gripper_length": 140.0})

    with pytest.raises(ValueError, match=r"shaker_1.*only an arm takes an arm block.*shaker"):
        check_device_setup("shaker_1", "shaker", setup)


@pytest.mark.parametrize("kind", ["transporter", "translator"])
def test_an_arm_block_is_accepted_on_an_arm(kind: str) -> None:
    check_device_setup("arm_1", kind, _setup(arm=_PF400["arm"]))


def test_an_arm_block_naming_a_field_the_arm_does_not_take_is_refused_naming_the_device() -> None:
    misspelt: dict[str, JsonValue] = {**_PF400, "arm": {"gripper_lenght": 140.0}}
    setup = _setup(**misspelt)

    with pytest.raises(ValueError, match=r"(?s)pf400_1.*arm block is not valid.*gripper_lenght"):
        check_device_setup("pf400_1", "transporter", setup)


def test_the_real_pf400_is_built_with_the_gripper_its_arm_block_names() -> None:
    driver = build_real_driver(_request("pf400_1", "transporter", _setup(**_PF400)))

    assert isinstance(driver, PreciseFlexTransporterDriver)
    assert driver._backend.closed_gripper_position == 90.0


def test_a_simulator_config_without_the_vendor_sim_is_refused() -> None:
    with pytest.raises(ValidationError, match="simulator_config.*use_vendor_sim"):
        _setup(client="lab_pc", sim={"simulator_config": "flex.json"})


def test_faults_on_a_device_whose_sim_cannot_inject_them_are_refused() -> None:
    setup = _setup(
        client="lab_pc",
        real={"type": "plr", "backend": "OpentronsFlex", "connection": {"type": "tcp", "host": "10.0.0.6"}},
        sim={"use_vendor_sim": True, "simulator_config": "flex.json", "faults": [{"kind": "raise", "method": "aspirate"}]},
    )

    with pytest.raises(ValueError, match=r"lh_1.*faults"):
        check_device_setup("lh_1", "liquid_handler", setup)
