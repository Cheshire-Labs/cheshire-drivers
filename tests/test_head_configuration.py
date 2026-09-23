"""Head configuration is RUNTIME state, not a capability the driver class declares.

Whether channels gang depends on the pipette currently mounted, so the same driver class is ganged
with a multi-channel pipette and independent with a single-channel one. A static `interfaces`
frozenset is read at class level and could only ever describe one of those, which is why this
rides a response model instead.
"""

import pytest

from cheshire_drivers.liquid_handler_models import (
    AspirateRequest,
    AspirateTarget,
    DeckLayoutConfig,
    DispenseRequest,
    DispenseTarget,
    GetHeadConfigurationRequest,
    HeadConfigurationResponse,
    NozzleGroup,
)
from cheshire_drivers.plr.liquid_handler import (
    ChatterboxLiquidHandlerDriver,
    OT2LiquidHandlerDriver,
)
from cheshire_drivers.plr_wrappers import PLRLiquidHandlerWrapper
from pylabrobot.legacy.liquid_handling.backends import OpentronsOT2Backend
from cheshire_drivers.response_lookup import lookup_response_model


class TestGangedVolumeRule:
    def test_one_plunger_refuses_two_volumes(self):
        config = HeadConfigurationResponse(groups=[NozzleGroup(channels=[0, 1, 2, 3])])
        with pytest.raises(ValueError, match="share one plunger"):
            config.require_volumes_honorable({0: 10.0, 1: 10.0, 2: 50.0, 3: 10.0})

    def test_one_plunger_accepts_one_volume_across_its_nozzles(self):
        config = HeadConfigurationResponse(groups=[NozzleGroup(channels=[0, 1, 2, 3])])
        config.require_volumes_honorable({0: 10.0, 1: 10.0, 2: 10.0, 3: 10.0})

    def test_separate_plungers_may_differ(self):
        """Two mounts each with their own plunger genuinely can deliver two volumes at once, and
        the rule must not flatten that into the ganged case."""
        config = HeadConfigurationResponse(
            groups=[NozzleGroup(channels=[0]), NozzleGroup(channels=[1])]
        )
        config.require_volumes_honorable({0: 10.0, 1: 50.0})

    def test_a_partially_addressed_group_is_still_checked(self):
        """Using only some nozzles of a ganged group does not decouple the ones in use."""
        config = HeadConfigurationResponse(groups=[NozzleGroup(channels=[0, 1, 2, 3])])
        with pytest.raises(ValueError, match="share one plunger"):
            config.require_volumes_honorable({0: 10.0, 2: 50.0})


class TestEnforcementAtCommandTime:
    """Reporting the configuration is only half of it. A caller that never reads it must still be
    stopped before the head moves, and stopped by the layer that knows what is mounted.

    A Flex runs on ``FlexLiquidHandlerDriver``, whose own refusals are pinned in
    ``test_opentrons_flex_pipetting.py``; these cover the same guard on the wrapper the
    Chatterbox, STAR and OT-2 drivers share.
    """

    @staticmethod
    def _aspirate(volumes: list[float], use_channels: list[int] | None = None) -> AspirateRequest:
        return AspirateRequest(
            aspirations=[
                AspirateTarget(labware="plate", positions=["A1"] * len(volumes), volumes=volumes)
            ],
            use_channels=use_channels,
        )

    @pytest.mark.asyncio
    async def test_a_channel_the_head_does_not_have_is_refused(self):
        driver = ChatterboxLiquidHandlerDriver(num_channels=4)
        await driver.configure_deck(DeckLayoutConfig(deck_type="FlexDeck", resources=[]))
        with pytest.raises(ValueError, match="not on the mounted head"):
            await driver.aspirate(self._aspirate([10.0, 10.0], use_channels=[0, 9]))

    @pytest.mark.asyncio
    async def test_a_volume_count_that_disagrees_with_use_channels_is_refused(self):
        """Zipping these silently would drop the unmatched tail out of the safety check."""
        driver = ChatterboxLiquidHandlerDriver(num_channels=8)
        await driver.configure_deck(DeckLayoutConfig(deck_type="FlexDeck", resources=[]))
        with pytest.raises(ValueError, match="each volume must name the channel"):
            await driver.aspirate(self._aspirate([10.0, 10.0, 10.0], use_channels=[0, 1]))

    @pytest.mark.asyncio
    async def test_a_repeated_channel_is_refused(self):
        driver = ChatterboxLiquidHandlerDriver(num_channels=8)
        await driver.configure_deck(DeckLayoutConfig(deck_type="FlexDeck", resources=[]))
        with pytest.raises(ValueError, match="repeats a channel"):
            await driver.aspirate(self._aspirate([10.0, 50.0], use_channels=[0, 0]))

    @pytest.mark.asyncio
    async def test_a_dispense_is_checked_the_same_way(self):
        """Enforcement rides both liquid surfaces, not just aspirate."""
        driver = ChatterboxLiquidHandlerDriver(num_channels=8)
        await driver.configure_deck(DeckLayoutConfig(deck_type="FlexDeck", resources=[]))
        request = DispenseRequest(
            dispenses=[
                DispenseTarget(labware="plate", positions=["A1"] * 2, volumes=[10.0, 50.0])
            ],
            use_channels=[0, 0],
        )
        with pytest.raises(ValueError, match="repeats a channel"):
            await driver.dispense(request)

    @pytest.mark.asyncio
    async def test_volumes_are_counted_across_every_target_slice(self):
        """A request carries a LIST of target slices; flattening them wrongly would misalign every
        volume with its channel."""
        driver = ChatterboxLiquidHandlerDriver(num_channels=8)
        await driver.configure_deck(DeckLayoutConfig(deck_type="FlexDeck", resources=[]))
        request = AspirateRequest(
            aspirations=[
                AspirateTarget(labware="a", positions=["A1"] * 4, volumes=[10.0] * 4),
                AspirateTarget(labware="b", positions=["A1"] * 4, volumes=[10.0] * 4),
            ],
            use_channels=[0, 1, 2, 3],
        )
        with pytest.raises(ValueError, match="each volume must name the channel"):
            await driver.aspirate(request)


class TestBackendDispatch:
    """The head-config answer comes off the BACKEND, not the driver subclass, so wrapping a
    backend in the plain wrapper reports exactly what driving it through its own driver would."""

    @pytest.mark.asyncio
    async def test_a_chatterbox_reports_independent_channels(self):
        driver = ChatterboxLiquidHandlerDriver(num_channels=8)
        config = await driver.get_head_configuration(GetHeadConfigurationRequest())
        assert config.independent_volume_count == 8
        assert config.nozzle_count == 8

    @pytest.mark.asyncio
    async def test_the_plain_wrapper_over_an_ot2_backend_reports_what_the_driver_would(self):
        """The footgun this dispatch exists to close: no subclass, same answer."""
        backend = OpentronsOT2Backend(host="localhost")
        backend.left_pipette = {"name": "p300_multi_gen2", "pipetteId": "left-id"}
        backend.right_pipette = None

        wrapper = PLRLiquidHandlerWrapper(backend)
        through_the_driver = OT2LiquidHandlerDriver(host="localhost")
        through_the_driver._backend.left_pipette = backend.left_pipette
        through_the_driver._backend.right_pipette = None

        assert await wrapper.get_head_configuration(
            GetHeadConfigurationRequest()
        ) == await through_the_driver.get_head_configuration(GetHeadConfigurationRequest())


def test_the_response_model_is_registered_for_the_wire():
    """An unregistered readback falls through to EmptyCommandResponse behind a 200 OK, silently
    discarding the payload. Nothing else in the stack catches that."""
    assert (
        lookup_response_model("get_head_configuration", frozenset({"ILiquidHandler"}))
        is HeadConfigurationResponse
    )
