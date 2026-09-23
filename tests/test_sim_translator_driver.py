"""SimTranslatorDriver: one carriage serves every taught position.

A translator/shuttle is one physical carriage on a rail; its taught
endpoints are the same pad at two positions. The driver declares that
truth via ``single_carriage`` (read by the engine's reservation layer,
which is the real scheduling gate) and enforces the carriage invariant
on its OWN ops: placing while any other position holds a plate is
physically impossible. Generic arms (``SimTransporterDriver``) keep
``single_carriage=False`` -- their taught positions are independent pads,
even though the arm itself only ever grips one plate at a time.
"""

import pytest
from pylabrobot.resources.resource import Resource as PLRResource

from cheshire_drivers.move_parameters import SEED_MOVE_PARAMETERS
from cheshire_drivers.driver_introspection import (
    derive_capabilities,
    describe_driver,
    interface_command_names,
    interface_member_names,
)
from cheshire_drivers.interfaces import ITransporterDriver
from cheshire_drivers.sims import (
    SimTransporterDriver,
    SimTransporterValidationError,
    SleepSim,
)
from cheshire_drivers.translator_driver import SimTranslatorDriver
from cheshire_drivers.teachpoints import CartesianCoordinates, Teachpoint
from cheshire_drivers.transporter_models import (
    PickAtCoordsRequest,
    PlaceAtCoordsRequest,
)


def _make_plate(name: str) -> PLRResource:
    return PLRResource(name=name, size_x=127.76, size_y=85.48, size_z=14.0)


def _translator(name: str = "translator_1") -> SimTranslatorDriver:
    return SimTranslatorDriver(name=name, sim_strategy=SleepSim(sim_time=0.0))


def _teachpoint(name: str) -> Teachpoint:
    return Teachpoint(
        position_id=name,
        coordinates=CartesianCoordinates(x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0, roll=0.0),
        orientation="left",
        access_type="horizontal",
        vertical_clearance=20.0,
    )


class TestSingleCarriageDeclaration:

    def test_translator_declares_single_carriage(self) -> None:
        assert _translator().single_carriage is True

    def test_generic_arm_does_not(self) -> None:
        """An arm grips one plate at a time too -- that is NOT what
        single_carriage means. It means the POSITION SET is one slot."""
        arm = SimTransporterDriver(name="arm", sim_strategy=SleepSim(sim_time=0.0))
        assert arm.single_carriage is False


class TestCarriageInvariantOnOwnOps:

    @pytest.mark.asyncio
    async def test_place_rejected_while_another_position_occupied(self) -> None:
        driver = _translator()
        driver._seed_resource("t1_end", _make_plate("camper"))
        driver._seed_resource("t1_start", _make_plate("incoming"))
        await driver.pick_at_coords(
            PickAtCoordsRequest(teachpoint=_teachpoint("t1_start"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS)
        )

        with pytest.raises(SimTransporterValidationError, match="single-carriage"):
            await driver.place_at_coords(
                PlaceAtCoordsRequest(teachpoint=_teachpoint("t1_start"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS)
            )

    @pytest.mark.asyncio
    async def test_crossing_pick_then_place_succeeds(self) -> None:
        """The legitimate ride: pick empties the source, so the place at the
        opposite endpoint sees an empty carriage and proceeds."""
        driver = _translator()
        plate = _make_plate("rider")
        driver._seed_resource("t1_start", plate)

        await driver.pick_at_coords(
            PickAtCoordsRequest(teachpoint=_teachpoint("t1_start"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS)
        )
        await driver.place_at_coords(
            PlaceAtCoordsRequest(teachpoint=_teachpoint("t1_end"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS)
        )

        assert driver._resource_at("t1_end") is plate

    @pytest.mark.asyncio
    async def test_same_position_occupied_place_still_rejected(self) -> None:
        """Base per-position validation is inherited, not shadowed."""
        driver = _translator()
        driver._seed_resource("t1_start", _make_plate("resident"))
        driver._seed_resource("t1_end", _make_plate("incoming"))
        await driver.pick_at_coords(
            PickAtCoordsRequest(teachpoint=_teachpoint("t1_end"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS)
        )

        with pytest.raises(SimTransporterValidationError, match="occupied"):
            await driver.place_at_coords(
                PlaceAtCoordsRequest(teachpoint=_teachpoint("t1_start"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS)
            )

    @pytest.mark.asyncio
    async def test_external_control_place_is_noop_not_rejected(self) -> None:
        """An external-control place is a world no-op in the base; the carriage
        guard must defer to it, not reject, even with a sibling occupied."""
        driver = _translator()
        driver._seed_resource("t1_end", _make_plate("camper"))

        await driver.place_at_coords(
            PlaceAtCoordsRequest(
                teachpoint=_teachpoint("t1_start"),
                labware_type="Plate_96",
                external_control=True,
                handling=SEED_MOVE_PARAMETERS,
            )
        )

        assert driver._resource_at("t1_start") is None


class TestSingleCarriageNotExposed:
    """single_carriage is engine-read metadata, never a wire command. It must
    stay off every discoverability/validation surface; otherwise a
    POST .../command {"command": "single_carriage"} would validate."""

    def test_absent_from_introspection_methods_catalog(self) -> None:
        assert "single_carriage" not in describe_driver(_translator())

    def test_absent_from_derived_vendor_capabilities(self) -> None:
        assert "single_carriage" not in derive_capabilities(SimTranslatorDriver)

    def test_properties_excluded_from_invokable_command_set(self) -> None:
        """The capability gate reads interface_command_names, which drops every
        @property. single_carriage and name are interface MEMBERS but not
        invokable COMMANDS."""
        members = interface_member_names(ITransporterDriver)
        commands = interface_command_names(ITransporterDriver)
        assert "single_carriage" in members and "single_carriage" not in commands
        assert "name" in members and "name" not in commands
