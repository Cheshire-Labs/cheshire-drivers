"""Only the raw console needs an operator's approval before it dispatches.

The vendor passthrough advertises up to a few hundred controller commands per
device. Every one of them arrives with a signature and a docstring, so the
catalog already tells an operator what it will do and the surface can show
them that before they send it. `send_command` does not: it is an unrestricted
line to the controller, nothing downstream can check what is on it, and no
catalog entry describes what a given string will do.

So that is where the confirmation is, and nowhere else. Gating ordinary motion
as well is what made an operator confirm every move, and a prompt that appears
on everything is a prompt nobody reads.
"""

import pytest

from cheshire_drivers.driver_introspection import (
    derive_capabilities,
    describe_driver,
    needs_confirmation,
    vendor_backend_members,
)
from cheshire_drivers.plr.transporter import PreciseFlexTransporterDriver

FREE_TO_SEND = [
    "request_power_state",
    "request_version",
    "request_joint_position",
    "is_gripper_closed",
    "nop",
    "park",
    "home_all",
    "move_to_location",
    "move_gripper",
    "move_one_axis",
    "move_rail",
    "pick_up_at_location",
    "drop_at_location",
    "move_to_safe",
    "set_power",
    "power_off_robot",
    "release_brake",
    "start_freedrive_mode",
    "zero_torque",
    "set_parameter",
    "set_base",
    "change_config",
    "recover_from_fault",
]


@pytest.fixture
def catalog() -> dict:
    return describe_driver(PreciseFlexTransporterDriver(host="127.0.0.1"))


@pytest.mark.parametrize("command", FREE_TO_SEND)
def test_a_described_command_dispatches_without_a_round_trip(
    catalog: dict, command: str,
) -> None:
    """Each of these carries a signature and a docstring, so the surface can
    tell the operator what it does instead of interrupting them."""
    assert catalog[command].requires_confirm is False


def test_the_raw_console_demands_confirmation(catalog: dict) -> None:
    assert catalog["send_command"].requires_confirm is True


def test_a_raw_console_under_another_name_is_still_gated() -> None:
    """The STAR spells it `send_raw_command`, so the match is on the verb
    rather than an exact name a new backend might not use."""
    assert needs_confirmation("send_raw_command") is True
    assert needs_confirmation("send_hhs_command") is True


def test_interface_contract_methods_are_not_gated(catalog: dict) -> None:
    """Typed interface verbs keep their own validation; the confirm gate is
    for the vendor passthrough only."""
    assert catalog["home"].requires_confirm is False


def test_the_gate_is_the_exception_and_not_the_rule() -> None:
    """The property the whole change exists for: on the arm with the largest
    passthrough, confirmation is rare enough that seeing one means something."""
    members = vendor_backend_members(PreciseFlexTransporterDriver)
    gated = [name for name in members if needs_confirmation(name)]

    assert len(gated) <= 2, sorted(gated)


def test_the_catalog_classifies_every_advertised_capability() -> None:
    """The gate reads the catalog, so a capability with no entry would
    dispatch unclassified; nothing advertised may be missing from it."""
    driver = PreciseFlexTransporterDriver(host="127.0.0.1")
    catalog = describe_driver(driver)
    assert derive_capabilities(type(driver)) <= set(catalog)


def test_every_opted_in_wrapper_can_dispatch_what_it_advertises() -> None:
    """Advertised-but-uncallable is the original bug: the executor refuses a
    name `hasattr` cannot see, so forwarding must exist on every wrapper that
    names a vendor backend, not just the transporter."""
    from cheshire_drivers.plr.centrifuge import VSpinCentrifugeDriver

    driver = VSpinCentrifugeDriver()
    for command in derive_capabilities(VSpinCentrifugeDriver):
        assert callable(getattr(driver, command)), command


def test_backend_properties_and_classmethods_are_not_advertised() -> None:
    """A property read or a PLR classmethod like `deserialize` can never
    succeed as a wire command; offering the name would only move the failure."""
    from cheshire_drivers.plr.centrifuge import VSpinCentrifugeDriver

    for wrong in ("deserialize", "configuration", "parking_position"):
        assert wrong not in derive_capabilities(VSpinCentrifugeDriver)
    capabilities = derive_capabilities(PreciseFlexTransporterDriver)
    assert "configuration" not in capabilities
    assert "parking_position" not in capabilities


def test_emergency_stop_is_never_confirm_gated() -> None:
    """A stop that waits on a confirm round-trip is inverted safety: the one
    verb that must always dispatch immediately is the recovery one."""
    from cheshire_drivers.plr.centrifuge import VSpinCentrifugeDriver
    from cheshire_drivers.sims import SimCentrifugeDriver

    assert describe_driver(VSpinCentrifugeDriver())["stop"].requires_confirm is False
    pf400 = describe_driver(PreciseFlexTransporterDriver(host="127.0.0.1"))
    assert pf400["stop"].requires_confirm is False
    sim = describe_driver(SimCentrifugeDriver(name="sim_centrifuge_1"))
    assert sim["stop"].requires_confirm is False


def test_no_driver_gates_anything_but_its_console() -> None:
    """Widening was per-driver work before; now there is nothing per-driver to
    get wrong, and this is what proves it stayed that way."""
    from cheshire_drivers.plr.centrifuge import VSpinCentrifugeDriver
    from cheshire_drivers.plr.liquid_handler import (
        OT2LiquidHandlerDriver,
        STARLiquidHandlerDriver,
    )
    from cheshire_drivers.plr.sealer import A4SSealerDriver

    for driver_cls in (
        VSpinCentrifugeDriver,
        OT2LiquidHandlerDriver,
        STARLiquidHandlerDriver,
        A4SSealerDriver,
        PreciseFlexTransporterDriver,
    ):
        gated = {
            name for name in vendor_backend_members(driver_cls) if needs_confirmation(name)
        }
        assert all("command" in name for name in gated), (driver_cls.__name__, sorted(gated))
