"""The interfaces read for a device's simulator are the ones of the class `build_sim_driver` builds, in every case."""

import pytest
from pydantic import JsonValue

from cheshire_drivers.command_executor.advertise import interfaces_of, release_driver
from cheshire_drivers.device_setup import DeviceSetup, MachineSetup, OpentronsSimMachine
from cheshire_drivers.driver_lookup import (
    SIM_DRIVER_CLASS_BY_KIND,
    DriverRequest,
    build_sim_driver,
    sim_driver_class,
    sim_driver_interfaces,
)

_LAB_PC: dict[str, JsonValue] = {"client": "lab_pc"}
_STAR: dict[str, JsonValue] = {**_LAB_PC, "real": {"type": "plr", "backend": "STAR"}}
_OT2: dict[str, JsonValue] = {
    **_LAB_PC, "real": {"type": "plr", "backend": "OpentronsOT2Backend", "connection": {"type": "tcp", "host": "10.0.0.5"}},
}
_FLEX: dict[str, JsonValue] = {
    **_LAB_PC, "real": {"type": "plr", "backend": "OpentronsFlex", "connection": {"type": "tcp", "host": "10.0.0.6"}},
}
_VANTAGE: dict[str, JsonValue] = {**_LAB_PC, "real": {"type": "plr", "backend": "VantageBackend"}}
_VENDOR_SIM: dict[str, JsonValue] = {"use_vendor_sim": True, "simulator_config": "robot.json"}
_FAULT: list[JsonValue] = [{"kind": "raise", "method": "aspirate"}]
_MACHINE = MachineSetup(opentrons_sim=OpentronsSimMachine(interpreter="python", cwd=".", config_folder="configs"))

_CASES: dict[str, tuple[str, dict[str, JsonValue], bool]] = {
    **{f"plain {kind}": (kind, {}, False) for kind in SIM_DRIVER_CLASS_BY_KIND},
    "shaker with faults": ("shaker", {"sim": {"faults": [{"kind": "raise", "method": "shake"}]}}, False),
    "liquid handler modelling its deck": ("liquid_handler", {}, True),
    "liquid handler modelling its deck, with faults": ("liquid_handler", {"sim": {"faults": _FAULT}}, True),
    "real STAR": ("liquid_handler", _STAR, False),
    "real STAR modelling its deck": ("liquid_handler", _STAR, True),
    "always_sim STAR": ("liquid_handler", {**_STAR, "always_sim": True}, False),
    "real OT2": ("liquid_handler", _OT2, False),
    "real OT2 modelling its deck": ("liquid_handler", _OT2, True),
    "real Flex": ("liquid_handler", _FLEX, False),
    "real Flex modelling its deck": ("liquid_handler", _FLEX, True),
    "real Vantage": ("liquid_handler", _VANTAGE, False),
    "real Vantage modelling its deck": ("liquid_handler", _VANTAGE, True),
    "OT2 on the vendor simulator": ("liquid_handler", {**_OT2, "sim": _VENDOR_SIM}, False),
    "Flex on the vendor simulator": ("liquid_handler", {**_FLEX, "sim": _VENDOR_SIM}, True),
    "Venus": ("liquid_handler", {**_LAB_PC, "real": {"type": "venus"}}, False),
    "human arm": ("transporter", {**_LAB_PC, "real": {"type": "human"}}, False),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_CASES))
async def test_the_simulators_interfaces_are_the_class_build_sim_driver_builds(case: str) -> None:
    kind, fields, deck_modeling = _CASES[case]
    setup = DeviceSetup.model_validate(fields)
    built = build_sim_driver(DriverRequest(name="dev", kind=kind, setup=setup, deck_modeling=deck_modeling, machine=_MACHINE))
    try:
        assert type(built) is sim_driver_class(kind, setup, deck_modeling)
        assert sim_driver_interfaces(kind, setup, deck_modeling) == interfaces_of(built)
    finally:
        await release_driver("dev", built)


def test_a_backend_with_no_vendor_simulator_is_refused_by_both_naming_the_backend() -> None:
    setup = DeviceSetup.model_validate({**_STAR, "sim": _VENDOR_SIM})
    with pytest.raises(ValueError, match="backend 'STAR' has no vendor simulator"):
        sim_driver_class("liquid_handler", setup)
    with pytest.raises(ValueError, match="Device 'dev': backend 'STAR' has no vendor simulator"):
        build_sim_driver(DriverRequest(name="dev", kind="liquid_handler", setup=setup, machine=_MACHINE))
