"""The OT-2 driver: the same per-channel motion as the Flex, but no gripper.

The OT-2 has two pipette mounts and no gripper of any kind, so it declares IPipetteMotion and none
of the gripper interfaces. Its channel motion and its head-configuration reporting are the shared
Opentrons surface, so these pin that the OT-2 gets both without re-implementing either.
"""

from unittest.mock import AsyncMock

import pytest

from cheshire_drivers.interfaces import (
    IForceGripperJawDriver,
    IGripperMotionDriver,
    IPipetteMotionDriver,
)
from cheshire_drivers.liquid_handler_models import GetHeadConfigurationRequest
from cheshire_drivers.pipette_motion_models import MoveChannelToRequest
from cheshire_drivers.plr.liquid_handler import OT2LiquidHandlerDriver
from pylabrobot.resources.coordinate import Coordinate


@pytest.fixture
def driver() -> OT2LiquidHandlerDriver:
    d = OT2LiquidHandlerDriver(host="localhost")
    d._ot_backend = AsyncMock()
    return d


class TestDeclaredInterfaces:
    def test_declares_pipette_motion_only(self, driver) -> None:
        assert driver.interfaces == frozenset({"ILiquidHandler", "IPipetteMotion"})
        assert isinstance(driver, IPipetteMotionDriver)

    def test_advertises_no_gripper(self, driver) -> None:
        """The OT-2 has no gripper, so declaring any gripper interface would make `interfaces` lie."""
        assert not isinstance(driver, IGripperMotionDriver)
        assert not isinstance(driver, IForceGripperJawDriver)
        assert not hasattr(driver, "move_gripper_to")


@pytest.mark.asyncio
class TestSharedSurface:
    async def test_move_channel_to_forwards_the_supplied_axes(self, driver) -> None:
        await driver.move_channel_to(MoveChannelToRequest(channel=1, x=10.0, z=30.0))
        driver._ot_backend.move_channel_to.assert_awaited_once_with(1, x=10.0, y=None, z=30.0)

    async def test_an_ot2_multi_is_one_plunger_and_its_nozzles_are_not_yet_addressable(
        self,
    ) -> None:
        """A p300_multi is physically 8 nozzles on 1 plunger, and only the plunger count is
        reported here.

        PyLabRobot has never implemented Opentrons multi-channel: its OT-2 backend counts a
        channel per MOUNT, and every liquid op asserts a single channel. So an 8-nozzle pipette
        is one channel all the way down and no eight-volume request can reach the robot. This
        pins BOTH halves, because the hazard appears only if a later change makes the nozzle
        count real without also enforcing that one plunger delivers one volume.

        The Flex does report real nozzle counts: it runs on pylabrobot.opentrons, not on this
        backend. See test_opentrons_flex_driver.TestHeadConfiguration.
        """
        driver = OT2LiquidHandlerDriver(host="localhost")
        backend = driver._backend
        backend.left_pipette = {"name": "p300_multi_gen2", "pipetteId": "left-id"}
        backend.right_pipette = None

        config = await driver.get_head_configuration(GetHeadConfigurationRequest())

        assert config.independent_volume_count == 1
        assert config.nozzle_count == 1, "the 8 nozzles of an OT-2 multi are not yet addressable"
        with pytest.raises(ValueError, match="not on the mounted head"):
            config.require_volumes_honorable({c: 10.0 for c in range(8)})

    async def test_reports_two_plungers_for_two_single_pipettes(self) -> None:
        driver = OT2LiquidHandlerDriver(host="localhost")
        backend = driver._backend
        backend.left_pipette = {"name": "p300_single_gen2", "pipetteId": "left-id"}
        backend.right_pipette = {"name": "p20_single_gen2", "pipetteId": "right-id"}
        config = await driver.get_head_configuration(GetHeadConfigurationRequest())
        assert config.nozzle_count == 2
        assert config.independent_volume_count == 2

    async def test_each_mount_reports_the_pipette_fitted_and_what_it_holds(self) -> None:
        """A caller planning volumes needs the range, and the mount is where that lives."""
        driver = OT2LiquidHandlerDriver(host="localhost")
        backend = driver._backend
        backend.left_pipette = {"name": "p300_single_gen2", "pipetteId": "left-id"}
        backend.right_pipette = {"name": "p20_single_gen2", "pipetteId": "right-id"}
        config = await driver.get_head_configuration(GetHeadConfigurationRequest())
        assert [(g.pipette_model, g.max_volume_ul) for g in config.groups] == [
            ("p300_single_gen2", 300.0),
            ("p20_single_gen2", 20.0),
        ]
