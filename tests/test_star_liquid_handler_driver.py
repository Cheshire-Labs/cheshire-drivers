"""The MLSTAR driver: the full iSWAP motion surface, more than any Opentrons robot.

STAR is the driver that exercises the parts of the contract the Opentrons robots cannot: both z
datums (a channel references either the stop disk or the tip end), gripper position feedback, a
width-positioned jaw, and a rotation drive. These pin each mapping onto the STAR backend and pin
that the driver advertises exactly what the iSWAP can do.
"""

from unittest.mock import AsyncMock

import pytest

from cheshire_drivers.gripper_models import (
    GetGripperPositionRequest,
    GetGripperRotationRequest,
    GetJawWidthRequest,
    MoveGripperRelativeRequest,
    MoveGripperToRequest,
    RotateGripperRequest,
    SetJawWidthRequest,
)
from cheshire_drivers.interfaces import (
    IForceGripperJawDriver,
    IGripperMotionDriver,
    IGripperPositionDriver,
    IGripperRotationDriver,
    IPipetteMotionDriver,
    IWidthGripperJawDriver,
)
from cheshire_drivers.pipette_motion_models import (
    GetChannelPositionRequest,
    MoveChannelRelativeRequest,
    MoveChannelToRequest,
)
from cheshire_drivers.plr.liquid_handler import STARLiquidHandlerDriver
from pylabrobot.resources.coordinate import Coordinate


@pytest.fixture
def driver() -> STARLiquidHandlerDriver:
    d = STARLiquidHandlerDriver(serial_number="sim")
    d._star_backend = AsyncMock()
    return d


class TestDeclaredInterfaces:
    def test_declares_the_full_iswap_surface(self, driver) -> None:
        assert driver.interfaces == frozenset(
            {
                "ILiquidHandler",
                "IPipetteMotion",
                "IGripperMotion",
                "IGripperPosition",
                "IWidthGripperJaw",
                "IGripperRotation",
            }
        )
        for iface in (
            IPipetteMotionDriver,
            IGripperMotionDriver,
            IGripperPositionDriver,
            IWidthGripperJawDriver,
            IGripperRotationDriver,
        ):
            assert isinstance(driver, iface)

    def test_uses_a_width_jaw_not_a_force_jaw(self, driver) -> None:
        """The iSWAP positions its jaw to a width; only the Flex closes to a force. Declaring the
        force jaw would misrepresent the hardware."""
        assert not isinstance(driver, IForceGripperJawDriver)
        assert not hasattr(driver, "grip_with_force")


@pytest.mark.asyncio
class TestChannelMotionHonorsBothDatums:
    async def test_a_tip_end_z_drives_the_tool_z(self, driver) -> None:
        await driver.move_channel_to(MoveChannelToRequest(channel=2, z=30.0, z_reference="tip_end"))
        driver._star_backend.move_channel_tool_z.assert_awaited_once_with(2, 30.0)
        driver._star_backend.move_channel_stop_disk_z.assert_not_awaited()

    async def test_a_stop_disk_z_drives_the_stop_disk_z(self, driver) -> None:
        """The Opentrons driver refuses stop_disk; STAR honors it, because its firmware has the
        datum. This is the whole reason z_reference is on the request."""
        await driver.move_channel_to(
            MoveChannelToRequest(channel=0, z=12.0, z_reference="stop_disk")
        )
        driver._star_backend.move_channel_stop_disk_z.assert_awaited_once_with(0, 12.0)
        driver._star_backend.move_channel_tool_z.assert_not_awaited()

    async def test_only_supplied_axes_move(self, driver) -> None:
        await driver.move_channel_to(MoveChannelToRequest(channel=1, x=100.0))
        driver._star_backend.move_channel_x.assert_awaited_once_with(1, 100.0)
        driver._star_backend.move_channel_y.assert_not_awaited()
        driver._star_backend.move_channel_tool_z.assert_not_awaited()

    async def test_xy_relative_jog_uses_the_native_per_axis_deltas(self, driver) -> None:
        await driver.move_channel_relative(MoveChannelRelativeRequest(channel=3, dx=5.0))
        driver._star_backend.move_channel_x_relative.assert_awaited_once_with(3, 5.0)
        driver._star_backend.move_channel_y_relative.assert_not_awaited()

    async def test_z_relative_jogs_against_the_probe_so_it_works_without_a_tip(self, driver) -> None:
        """The native z-relative reads the tip bottom and raises with no tip, but a delta is the
        same carriage move in either datum, so the jog composes against the tip-free probe read."""
        driver._star_backend.request_probe_z_position.return_value = 40.0
        await driver.move_channel_relative(MoveChannelRelativeRequest(channel=3, dz=-2.0))
        driver._star_backend.request_probe_z_position.assert_awaited_once_with(3)
        driver._star_backend.move_channel_stop_disk_z.assert_awaited_once_with(3, 38.0)
        driver._star_backend.move_channel_z_relative.assert_not_awaited()

    async def test_reading_a_tip_end_position_uses_the_tip_bottom(self, driver) -> None:
        driver._star_backend.request_x_pos_channel_n.return_value = 10.0
        driver._star_backend.request_y_pos_channel_n.return_value = 20.0
        driver._star_backend.request_tip_bottom_z_position.return_value = 30.0
        position = await driver.get_channel_position(
            GetChannelPositionRequest(channel=1, z_reference="tip_end")
        )
        assert (position.x, position.y, position.z, position.z_reference) == (
            10.0, 20.0, 30.0, "tip_end",
        )
        driver._star_backend.request_probe_z_position.assert_not_awaited()

    async def test_reading_a_stop_disk_position_uses_the_probe(self, driver) -> None:
        """A stop-disk read must not require a tip, so it reads the probe, not the tip bottom."""
        driver._star_backend.request_x_pos_channel_n.return_value = 1.0
        driver._star_backend.request_y_pos_channel_n.return_value = 2.0
        driver._star_backend.request_probe_z_position.return_value = 3.0
        position = await driver.get_channel_position(
            GetChannelPositionRequest(channel=0, z_reference="stop_disk")
        )
        assert position.z == 3.0 and position.z_reference == "stop_disk"
        driver._star_backend.request_tip_bottom_z_position.assert_not_awaited()


@pytest.mark.asyncio
class TestGripperSpecialties:
    async def test_move_gripper_to_drives_each_iswap_axis(self, driver) -> None:
        await driver.move_gripper_to(MoveGripperToRequest(x=100.0, y=110.0, z=120.0))
        driver._star_backend.move_iswap_x.assert_awaited_once_with(100.0)
        driver._star_backend.move_iswap_y.assert_awaited_once_with(110.0)
        driver._star_backend.move_iswap_z.assert_awaited_once_with(120.0)

    async def test_the_iswap_position_can_be_read(self, driver) -> None:
        """A read the Flex cannot offer: the iSWAP reports where it is."""
        driver._star_backend.request_iswap_position.return_value = Coordinate(5.0, 6.0, 7.0)
        position = await driver.get_gripper_position(GetGripperPositionRequest())
        assert (position.x, position.y, position.z) == (5.0, 6.0, 7.0)

    async def test_the_gripper_can_be_jogged(self, driver) -> None:
        await driver.move_gripper_relative(MoveGripperRelativeRequest(dy=3.0))
        driver._star_backend.move_iswap_y_relative.assert_awaited_once_with(3.0)
        driver._star_backend.move_iswap_x_relative.assert_not_awaited()

    async def test_the_jaw_is_positioned_to_a_width(self, driver) -> None:
        await driver.set_jaw_width(SetJawWidthRequest(width=85.0))
        driver._star_backend.iswap_open_gripper.assert_awaited_once_with(open_position=85.0)

    async def test_the_jaw_width_can_be_read(self, driver) -> None:
        driver._star_backend.iswap_gripper_request_width.return_value = 84.5
        width = await driver.get_jaw_width(GetJawWidthRequest())
        assert width == 84.5

    async def test_the_gripper_rotates_to_an_absolute_angle(self, driver) -> None:
        await driver.rotate_gripper(RotateGripperRequest(angle=90.0))
        driver._star_backend.iswap_rotate_to_angles.assert_awaited_once_with(rotation_angle=90.0)

    async def test_the_rotation_angle_can_be_read(self, driver) -> None:
        driver._star_backend.iswap_rotation_drive_request_angle.return_value = 89.7
        angle = await driver.get_gripper_rotation(GetGripperRotationRequest())
        assert angle == 89.7
