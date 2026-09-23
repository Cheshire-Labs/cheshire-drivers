"""A wrapper driver's vendor backend has to reach the operator.

A PLR wrapper holds its vendor object as an attribute rather than inheriting
it, so the controller's own surface (power, signals, parameters, taught
locations, raw commands) was reachable from nowhere: not advertised as a
capability, so the command gate refused it; not in the introspection catalog,
so nothing could see it existed; and not on the driver, so it could not be
called even by name. On the PreciseFlex that meant an operator asking for
`hp 0` was told the arm had no power command.
"""

import pytest

from cheshire_drivers.driver_introspection import (
    derive_capabilities,
    describe_driver,
    interface_member_names,
)
from cheshire_drivers.interfaces import ITransporterDriver
from cheshire_drivers.plr.transporter import PreciseFlexTransporterDriver


# One from each family the controller exposes, so a regression in any of them
# is named rather than hidden behind a count.
VENDOR_COMMANDS = [
    "set_power",
    "request_power_state",
    "power_on_robot",
    "power_off_robot",
    "send_command",
    "attach",
    "detach",
    "start_freedrive_mode",
    "stop_freedrive_mode",
    "set_signal",
    "request_signal",
    "set_parameter",
    "request_parameter",
    "request_version",
    "park",
]


@pytest.fixture
def driver() -> PreciseFlexTransporterDriver:
    return PreciseFlexTransporterDriver(host="127.0.0.1")


@pytest.mark.parametrize("command", VENDOR_COMMANDS)
def test_controller_command_is_advertised_as_a_capability(command: str) -> None:
    """The command gate checks this set, so a missing name is a refused command."""
    assert command in derive_capabilities(PreciseFlexTransporterDriver)


@pytest.mark.parametrize("command", VENDOR_COMMANDS)
def test_controller_command_is_callable_on_the_driver(
    driver: PreciseFlexTransporterDriver, command: str,
) -> None:
    """Advertising a command nobody can dispatch would only move the failure."""
    assert callable(getattr(driver, command))


def test_the_catalog_explains_how_to_call_a_forwarded_command(
    driver: PreciseFlexTransporterDriver,
) -> None:
    """The operator surface reads this catalog; a name with no signature or
    docstring is not usable by a person or by an assistant."""
    entry = describe_driver(driver)["set_power"]

    assert entry.params is not None
    assert entry.params["enable"]["type"] == "bool"
    assert entry.params["timeout"]["required"] is False
    assert entry.docstring is not None and "power" in entry.docstring.lower()


def test_a_typed_verb_is_not_shadowed_by_the_raw_one_underneath(
    driver: PreciseFlexTransporterDriver,
) -> None:
    """`home` exists on both the interface and the backend, with different
    signatures. Forwarding must never reach past the interface implementation,
    or the engine's own calls would change shape."""
    entry = describe_driver(driver)["home"]

    assert entry.params is not None
    assert "request" in entry.params


def test_forwarding_leaves_the_interface_contract_alone() -> None:
    """Vendor extras are additive: every interface member is still a member."""
    capabilities = derive_capabilities(PreciseFlexTransporterDriver)
    contract = interface_member_names(ITransporterDriver)

    assert capabilities.isdisjoint(contract)


def test_private_attributes_are_not_forwarded(
    driver: PreciseFlexTransporterDriver,
) -> None:
    """A driver mid-construction looks up `_backend` through here, so an
    underscore name must never be answered by the backend."""
    with pytest.raises(AttributeError):
        getattr(driver, "_not_a_real_private")


def test_a_name_on_neither_side_says_so(driver: PreciseFlexTransporterDriver) -> None:
    with pytest.raises(AttributeError, match="backend"):
        getattr(driver, "definitely_not_a_command")
