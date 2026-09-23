"""A vendor object held one level down still has to reach the operator.

The first passthrough advertised one wrapped object, reached through a single
`_backend` attribute. That covers a PLR wrapper and nothing else. The Flex
driver holds `OpentronsFlex`, and the commands an operator reaches for during a
recovery live one level below it: the jaws on `flex.gripper`, the in-place tip
and blow-out recovery on a mount. None of that was advertised, so the gate
refused it, the catalog never showed it, and `unsafe/ungripLabware` was
reachable only from the robot's touchscreen.

A driver now declares every vendor object it forwards to, with the path to
reach it and a prefix that keeps two objects' `move_to` apart on the wire.
"""

import inspect

import pytest

from cheshire_drivers.driver_introspection import (
    derive_capabilities,
    describe_driver,
    interface_command_names,
    vendor_backend_members,
    vendor_command_sources,
    vendor_surfaces,
    VendorSurface,
)
from cheshire_drivers.interfaces import ILiquidHandlerDriver
from cheshire_drivers.plr.opentrons_flex import FlexLiquidHandlerDriver
from cheshire_drivers.plr_wrappers import VendorSurfaceForwarding
from pylabrobot.opentrons.flex_gripper import FlexGripper
from pylabrobot.opentrons.flex_head import FlexHead1, FlexHead8
from cheshire_drivers.plr.transporter import PreciseFlexTransporterDriver


ROBOT_COMMANDS = [
    "stop",
    "move_axes_to",
    "move_axes_relative",
    "retract_axis",
    "set_rail_lights",
    "set_status_bar",
    "add_comment",
    "reload_labware",
]

GRIPPER_COMMANDS = [
    "gripper.ungrip",
    "gripper.grip",
    "gripper.open_jaw",
    "gripper.move_to",
    "gripper.move_labware",
]

MOUNT_COMMANDS = [
    "left.unsafe_drop_tip_in_place",
    "left.unsafe_blow_out_in_place",
    "left.configure_for_volume",
    "left.get_tip_presence",
    "right.unsafe_drop_tip_in_place",
    "right.move_relative",
]


@pytest.fixture
def flex() -> FlexLiquidHandlerDriver:
    """A driver that has never reached a robot.

    Advertisement happens at the orca-client handshake, before anything is
    connected, so what a driver offers cannot depend on discovering hardware.
    """
    return FlexLiquidHandlerDriver(host="127.0.0.1")


@pytest.fixture
def fitted(flex: FlexLiquidHandlerDriver) -> FlexLiquidHandlerDriver:
    """A driver whose robot has answered: gripper on, a head on each mount.

    The mounts are built without running a head's __init__ because nothing here
    calls one; only attribute resolution is under test.
    """
    robot = flex._flex
    robot.gripper = FlexGripper(robot, "gripperV1")
    robot.left = object.__new__(FlexHead1)
    robot.right = object.__new__(FlexHead8)
    return flex


@pytest.mark.parametrize("command", ROBOT_COMMANDS + GRIPPER_COMMANDS + MOUNT_COMMANDS)
def test_vendor_command_is_advertised_as_a_capability(command: str) -> None:
    """The command gate checks this set, so a missing name is a refused command."""
    assert command in derive_capabilities(FlexLiquidHandlerDriver)


@pytest.mark.parametrize("command", ROBOT_COMMANDS + GRIPPER_COMMANDS + MOUNT_COMMANDS)
def test_vendor_command_is_reachable_on_the_driver(
    fitted: FlexLiquidHandlerDriver, command: str
) -> None:
    """orca-client dispatches with getattr, so a name it cannot resolve is a
    command that dies at the executor no matter what the gate allowed."""
    assert callable(getattr(fitted, command))


def test_the_jaws_can_be_released_by_the_recovery_command(
    fitted: FlexLiquidHandlerDriver,
) -> None:
    """`release_jaw` sends robot/openGripperJaw. When the gripper is holding a
    plate the Flex only answers unsafe/ungripLabware, which is `ungrip`."""
    assert "gripper.ungrip" in derive_capabilities(FlexLiquidHandlerDriver)
    assert callable(getattr(fitted, "gripper.ungrip"))


def test_the_catalog_explains_how_to_call_a_nested_command(
    flex: FlexLiquidHandlerDriver,
) -> None:
    """The operator surface reads this catalog; a name with no signature and no
    docstring is not usable by a person or an assistant."""
    entry = describe_driver(flex)["gripper.grip"]

    assert entry.params is not None
    assert "force" in entry.params
    assert entry.docstring is not None


def test_two_vendor_objects_with_the_same_verb_stay_apart(
    flex: FlexLiquidHandlerDriver,
) -> None:
    """The gripper and a mount both define `move_to`. Un-prefixed they would be
    one wire name that reaches whichever object was declared first."""
    capabilities = derive_capabilities(FlexLiquidHandlerDriver)

    assert "gripper.move_to" in capabilities
    assert "left.move_to" in capabilities


def test_an_interface_verb_is_not_shadowed_by_the_vendor_one_underneath(
    flex: FlexLiquidHandlerDriver,
) -> None:
    """`stop` and `home` exist on the robot as well. A typed driver verb always
    wins, or a submitted workflow would reach the raw one by accident."""
    for command in sorted(interface_command_names(ILiquidHandlerDriver)):
        assert command not in vendor_backend_members(FlexLiquidHandlerDriver)

    assert getattr(flex, "move_plate").__qualname__.startswith("FlexLiquidHandlerDriver")


def test_a_missing_vendor_object_says_which_hardware_is_absent(
    flex: FlexLiquidHandlerDriver,
) -> None:
    """The gripper is optional hardware and is only discovered at connect. A
    dispatch before then must name the mount, not raise a bare AttributeError
    an operator has to decode."""
    with pytest.raises(AttributeError, match="gripper"):
        getattr(flex, "gripper.ungrip")()


def test_a_single_wrapped_backend_still_advertises_its_surface() -> None:
    """The PF400 declares one vendor object with no prefix. Generalizing to
    several must not rename the surface that already ships."""
    capabilities = derive_capabilities(PreciseFlexTransporterDriver)

    for command in ("set_power", "send_command", "request_version", "park"):
        assert command in capabilities


def test_a_sync_vendor_method_is_not_advertised() -> None:
    """The executor refuses a command that is not a coroutine, so advertising
    one puts a name in the operator's catalog that always answers "not async".
    A Flex mount carries three of them."""
    advertised = vendor_backend_members(FlexLiquidHandlerDriver)

    assert "left.unsafe_drop_tip_in_place" in advertised
    for sync_member in ("get_mounted_tips", "default_flow_rates"):
        assert not inspect.iscoroutinefunction(getattr(FlexHead1, sync_member))
        assert f"left.{sync_member}" not in advertised


def test_every_advertised_vendor_command_can_actually_be_dispatched() -> None:
    """Whatever a driver forwards, the executor has to be able to await it."""
    for driver_cls in (FlexLiquidHandlerDriver, PreciseFlexTransporterDriver):
        sources = vendor_command_sources(driver_cls)
        assert sources, f"{driver_cls.__name__} advertises nothing"
        not_awaitable = [
            wire_name for wire_name, (surface, member) in sources.items()
            if not inspect.iscoroutinefunction(getattr(surface.type, member, None))
        ]
        assert not_awaitable == [], driver_cls.__name__


def test_the_catalog_describes_the_head_that_is_fitted(
    fitted: FlexLiquidHandlerDriver,
) -> None:
    """A mount is declared once per head that can sit on it, so the declared
    type is a guess until the robot answers. The two heads do not take the same
    arguments -- a 1-channel `liquid_probe` takes a well, an 8-channel one takes
    a plate and a column -- and this catalog is what an operator reads before
    calling one during a recovery.
    """
    catalog = describe_driver(fitted)

    assert list(catalog["left.liquid_probe"].params) == ["target"]
    assert list(catalog["right.liquid_probe"].params) == ["plate", "column"]


def test_an_absent_head_is_still_described_from_the_declaration(
    flex: FlexLiquidHandlerDriver,
) -> None:
    """Nothing is fitted before the handshake, and the catalog still has to say
    what the commands take."""
    catalog = describe_driver(flex)

    assert "left.liquid_probe" in catalog


def test_vendor_surfaces_declared_as_a_list_still_counts() -> None:
    """A tuple-only check dropped a list on the floor and returned none of
    them, so the driver advertised no vendor commands at all: not advertised,
    not cataloged, not callable, and nothing said anywhere."""
    class ListDeclaring:
        vendor_surfaces = [VendorSurface(path="_backend", type=FlexGripper)]

    assert vendor_surfaces(ListDeclaring) == (
        VendorSurface(path="_backend", type=FlexGripper),
    )


def test_one_surface_without_its_trailing_comma_is_refused() -> None:
    """A VendorSurface is a NamedTuple, so a missing comma makes it a sequence
    of its own three fields and every one of them gets dropped."""
    class MissingComma:
        vendor_surfaces = VendorSurface(path="_backend", type=FlexGripper)

    with pytest.raises(TypeError, match="Add the trailing comma"):
        vendor_surfaces(MissingComma)


def test_a_non_sequence_declaration_is_refused() -> None:
    class NotASequence:
        vendor_surfaces = 42

    with pytest.raises(TypeError, match="must be a sequence of VendorSurface"):
        vendor_surfaces(NotASequence)


def test_a_non_surface_entry_is_refused() -> None:
    class BadEntry:
        vendor_surfaces = (VendorSurface(path="_backend", type=FlexGripper), "oops")

    with pytest.raises(TypeError, match="not VendorSurface"):
        vendor_surfaces(BadEntry)


def test_an_unprefixed_miss_says_what_is_missing() -> None:
    """A driver with only prefixed surfaces used to answer a bare name with
    `declares no vendor surface named ''`, which is not a sentence an
    operator can act on."""
    class OnlyPrefixed(VendorSurfaceForwarding):
        vendor_surfaces = (
            VendorSurface(path="_jaws", type=FlexGripper, prefix="gripper"),
        )

    with pytest.raises(AttributeError, match="unprefixed name"):
        getattr(OnlyPrefixed(), "no_such_command_anywhere")
