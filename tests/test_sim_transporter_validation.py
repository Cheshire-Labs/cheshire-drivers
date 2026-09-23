"""Sim transporter validation state machine.

Tests the three logical invariants `SimTransporterDriver` enforces by reading
its synthetic PLR Resource graph: empty pick rejects, occupied place rejects,
and gripper-state (full pick / empty place) rejects. Verifies state mutates
through PLR's `assign_child_resource`/`unassign` API so the validator follows
PLR semantics for free.
"""

import pytest
from pylabrobot.resources.resource import Resource as PLRResource

from cheshire_drivers.move_parameters import SEED_MOVE_PARAMETERS
from cheshire_drivers.sims import (
    SimTransporterDriver,
    SimTransporterValidationError,
    SleepSim,
)
from cheshire_drivers.teachpoints import (
    CartesianCoordinates,
    Teachpoint,
)
from cheshire_drivers.homing_models import HomeRequest
from cheshire_drivers.transporter_models import (
    MoveToCoordsRequest,
    MoveToSafeRequest,
    PickAtCoordsRequest,
    PlaceAtCoordsRequest,
)


def _make_plate(name: str) -> PLRResource:
    return PLRResource(name=name, size_x=127.76, size_y=85.48, size_z=14.0)


def _fast_driver(name: str = "sim_arm") -> SimTransporterDriver:
    """SimTransporterDriver wired with a fast SleepSim (no real waits in tests)."""
    return SimTransporterDriver(name=name, sim_strategy=SleepSim(sim_time=0.0))


def _teachpoint(name: str) -> Teachpoint:
    return Teachpoint(
        position_id=name,
        coordinates=CartesianCoordinates(x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0, roll=0.0),
        orientation="left",
        access_type="vertical",
        vertical_clearance=20.0,
    )


class TestRule1EmptyPick:
    @pytest.mark.asyncio
    async def test_pick_from_empty_position_raises(self) -> None:
        driver = _fast_driver()
        with pytest.raises(SimTransporterValidationError, match="empty"):
            await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

    @pytest.mark.asyncio
    async def test_pick_from_seeded_position_succeeds(self) -> None:
        driver = _fast_driver()
        plate = _make_plate("plate_a")
        driver._seed_resource("nest_1", plate)

        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        assert driver._gripper_payload() is plate
        assert driver._resource_at("nest_1") is None

    @pytest.mark.asyncio
    async def test_pick_after_already_picked_raises(self) -> None:
        driver = _fast_driver()
        driver._seed_resource("nest_1", _make_plate("plate_a"))
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))
        await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=_teachpoint("nest_2"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        with pytest.raises(SimTransporterValidationError, match="empty"):
            await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

    @pytest.mark.asyncio
    async def test_failed_pick_does_not_mutate_state(self) -> None:
        driver = _fast_driver()
        with pytest.raises(SimTransporterValidationError):
            await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        assert driver._gripper_payload() is None
        assert driver._resource_at("nest_1") is None


class TestRule2OccupiedPlace:
    @pytest.mark.asyncio
    async def test_place_to_occupied_position_raises(self) -> None:
        driver = _fast_driver()
        driver._seed_resource("nest_1", _make_plate("plate_a"))
        driver._seed_resource("nest_2", _make_plate("plate_b"))
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        gripper_plate_before = driver._gripper_payload()
        nest_2_plate_before = driver._resource_at("nest_2")

        with pytest.raises(SimTransporterValidationError, match="occupied"):
            await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=_teachpoint("nest_2"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        assert driver._gripper_payload() is gripper_plate_before
        assert driver._resource_at("nest_2") is nest_2_plate_before

    @pytest.mark.asyncio
    async def test_place_to_empty_position_succeeds(self) -> None:
        driver = _fast_driver()
        plate = _make_plate("plate_a")
        driver._seed_resource("nest_1", plate)
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=_teachpoint("nest_2"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        assert driver._gripper_payload() is None
        assert driver._resource_at("nest_2") is plate

    @pytest.mark.asyncio
    async def test_failed_place_does_not_mutate_state(self) -> None:
        driver = _fast_driver()
        plate_a = _make_plate("plate_a")
        plate_b = _make_plate("plate_b")
        driver._seed_resource("nest_1", plate_a)
        driver._seed_resource("nest_2", plate_b)
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        with pytest.raises(SimTransporterValidationError):
            await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=_teachpoint("nest_2"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        assert driver._gripper_payload() is plate_a
        assert driver._resource_at("nest_2") is plate_b


class TestRule3GripperState:
    @pytest.mark.asyncio
    async def test_pick_when_gripper_full_raises(self) -> None:
        driver = _fast_driver()
        driver._seed_resource("nest_1", _make_plate("plate_a"))
        driver._seed_resource("nest_2", _make_plate("plate_b"))
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        with pytest.raises(SimTransporterValidationError, match="gripper already holds"):
            await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_2"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

    @pytest.mark.asyncio
    async def test_place_when_gripper_empty_raises(self) -> None:
        driver = _fast_driver()

        with pytest.raises(SimTransporterValidationError, match="gripper is empty"):
            await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

    @pytest.mark.asyncio
    async def test_full_pick_does_not_disturb_either_position(self) -> None:
        driver = _fast_driver()
        plate_a = _make_plate("plate_a")
        plate_b = _make_plate("plate_b")
        driver._seed_resource("nest_1", plate_a)
        driver._seed_resource("nest_2", plate_b)
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        with pytest.raises(SimTransporterValidationError):
            await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_2"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        assert driver._gripper_payload() is plate_a
        assert driver._resource_at("nest_2") is plate_b


class TestPickPlaceRoundTrip:
    @pytest.mark.asyncio
    async def test_valid_pick_place_sequence_succeeds_end_to_end(self) -> None:
        driver = _fast_driver()
        plate = _make_plate("plate_a")
        driver._seed_resource("nest_a", plate)

        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_a"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))
        await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=_teachpoint("nest_b"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_b"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))
        await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=_teachpoint("nest_a"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        assert driver._gripper_payload() is None
        assert driver._resource_at("nest_a") is plate
        assert driver._resource_at("nest_b") is None

    @pytest.mark.asyncio
    async def test_consecutive_picks_without_place_raise(self) -> None:
        driver = _fast_driver()
        driver._seed_resource("nest_a", _make_plate("plate_a"))
        driver._seed_resource("nest_b", _make_plate("plate_b"))
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_a"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        with pytest.raises(SimTransporterValidationError):
            await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_b"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

    @pytest.mark.asyncio
    async def test_place_onto_pre_seeded_position_raises(self) -> None:
        driver = _fast_driver()
        driver._seed_resource("nest_a", _make_plate("plate_a"))
        driver._seed_resource("nest_b", _make_plate("plate_b"))
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_a"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        with pytest.raises(SimTransporterValidationError, match="occupied"):
            await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=_teachpoint("nest_b"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))


class TestPLRStateCoupling:
    @pytest.mark.asyncio
    async def test_validation_uses_plr_parent_child_state(self) -> None:
        """Mutating the resource graph through PLR's API must be visible to validation."""
        driver = _fast_driver()
        plate = _make_plate("plate_a")
        position = driver._get_or_create_position("nest_1")
        position.assign_child_resource(plate, location=None)

        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        assert driver._gripper_payload() is plate
        assert plate.parent is driver._gripper

    def test_seed_position_rejects_double_seed(self) -> None:
        driver = _fast_driver()
        driver._seed_resource("nest_1", _make_plate("plate_a"))

        with pytest.raises(SimTransporterValidationError, match="already occupied"):
            driver._seed_resource("nest_1", _make_plate("plate_b"))

    def test_seed_position_rejects_duplicate_labware_name(self) -> None:
        """Two labware with the same name can't coexist in one driver's world."""
        driver = _fast_driver()
        driver._seed_resource("nest_1", _make_plate("plate_x"))

        with pytest.raises(SimTransporterValidationError, match="cannot seed 'plate_x'"):
            driver._seed_resource("nest_2", _make_plate("plate_x"))

    def test_gripper_is_plr_resource_starting_empty(self) -> None:
        driver = _fast_driver()
        assert isinstance(driver._gripper, PLRResource)
        assert driver._gripper.children == []
        assert driver._gripper_payload() is None

    @pytest.mark.asyncio
    async def test_gripper_full_state_reflects_plr_children(self) -> None:
        driver = _fast_driver()
        plate = _make_plate("plate_a")
        driver._seed_resource("nest_1", plate)
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        assert driver._gripper.children == [plate]
        assert plate.parent is driver._gripper


class TestPickAtCoordsValidation:
    @pytest.mark.asyncio
    async def test_pick_at_coords_keys_off_position_id(self) -> None:
        driver = _fast_driver()
        plate = _make_plate("plate_a")
        driver._seed_resource("nest_1", plate)
        teachpoint = _teachpoint("nest_1")

        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=teachpoint, handling=SEED_MOVE_PARAMETERS))

        assert driver._gripper_payload() is plate
        assert driver._resource_at("nest_1") is None

    @pytest.mark.asyncio
    async def test_pick_at_coords_empty_position_raises(self) -> None:
        driver = _fast_driver()
        teachpoint = _teachpoint("nest_1")

        with pytest.raises(SimTransporterValidationError, match="empty"):
            await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=teachpoint, handling=SEED_MOVE_PARAMETERS))

    @pytest.mark.asyncio
    async def test_pick_at_coords_when_gripper_full_raises(self) -> None:
        driver = _fast_driver()
        driver._seed_resource("nest_1", _make_plate("plate_a"))
        driver._seed_resource("nest_2", _make_plate("plate_b"))
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        with pytest.raises(SimTransporterValidationError, match="gripper already holds"):
            await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_2"), handling=SEED_MOVE_PARAMETERS))


class TestPlaceAtCoordsValidation:
    @pytest.mark.asyncio
    async def test_place_at_coords_keys_off_position_id(self) -> None:
        driver = _fast_driver()
        plate = _make_plate("plate_a")
        driver._seed_resource("nest_1", plate)
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=_teachpoint("nest_2"), handling=SEED_MOVE_PARAMETERS))

        assert driver._gripper_payload() is None
        assert driver._resource_at("nest_2") is plate

    @pytest.mark.asyncio
    async def test_place_at_coords_when_gripper_empty_raises(self) -> None:
        driver = _fast_driver()

        with pytest.raises(SimTransporterValidationError, match="gripper is empty"):
            await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=_teachpoint("nest_1"), handling=SEED_MOVE_PARAMETERS))

    @pytest.mark.asyncio
    async def test_place_at_coords_to_occupied_position_raises(self) -> None:
        driver = _fast_driver()
        driver._seed_resource("nest_1", _make_plate("plate_a"))
        driver._seed_resource("nest_2", _make_plate("plate_b"))
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        with pytest.raises(SimTransporterValidationError, match="occupied"):
            await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=_teachpoint("nest_2"), handling=SEED_MOVE_PARAMETERS))

    @pytest.mark.asyncio
    async def test_pick_then_place_via_coords_round_trip(self) -> None:
        driver = _fast_driver()
        plate = _make_plate("plate_a")
        driver._seed_resource("nest_a", plate)

        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_a"), handling=SEED_MOVE_PARAMETERS))
        await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=_teachpoint("nest_b"), handling=SEED_MOVE_PARAMETERS))
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_b"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))
        await driver.place_at_coords(PlaceAtCoordsRequest(teachpoint=_teachpoint("nest_a"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        assert driver._resource_at("nest_a") is plate
        assert driver._resource_at("nest_b") is None
        assert driver._gripper_payload() is None


class TestGatewayPathWireShape:
    """`PickAtCoordsRequest` / `PlaceAtCoordsRequest` carry an optional
    `gateway_path: List[Teachpoint]` that the gateway resolves server-side and
    inlines into the wire payload. The on-prem PLR wrapper traverses the
    pre-resolved list instead of doing name lookups against a local store.
    """

    def test_pick_at_coords_accepts_gateway_path_list(self) -> None:
        target = _teachpoint("dest")
        gw_a = _teachpoint("gw_a")
        gw_b = _teachpoint("gw_b")
        request = PickAtCoordsRequest(
            teachpoint=target, labware_type="Plate_96",
            gateway_path=[gw_b, gw_a],
            handling=SEED_MOVE_PARAMETERS,
        )
        assert [wp.position_id for wp in request.gateway_path] == ["gw_b", "gw_a"]

    def test_pick_at_coords_defaults_gateway_path_to_empty(self) -> None:
        request = PickAtCoordsRequest(teachpoint=_teachpoint("dest"), handling=SEED_MOVE_PARAMETERS)
        assert request.gateway_path == []

    def test_pick_at_coords_coerces_dict_gateway_path(self) -> None:
        """Wire payloads carry teachpoint dicts; the validator materializes them."""
        target_dict = _teachpoint("dest").to_dict()
        gw_dict = _teachpoint("gw").to_dict()

        request = PickAtCoordsRequest.model_validate({
            "teachpoint": target_dict,
            "labware_type": "Plate_96",
            "gateway_path": [gw_dict],
            "handling": SEED_MOVE_PARAMETERS.model_dump(),
        })
        assert isinstance(request.teachpoint, Teachpoint)
        assert request.teachpoint.position_id == "dest"
        assert len(request.gateway_path) == 1
        assert isinstance(request.gateway_path[0], Teachpoint)
        assert request.gateway_path[0].position_id == "gw"

    def test_pick_at_coords_rejects_non_list_gateway_path(self) -> None:
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            PickAtCoordsRequest.model_validate({
                "teachpoint": _teachpoint("dest").to_dict(),
                "gateway_path": "not-a-list",
            })

    def test_place_at_coords_accepts_gateway_path_list(self) -> None:
        request = PlaceAtCoordsRequest(
            teachpoint=_teachpoint("dest"),
            labware_type="Plate_96",
            gateway_path=[_teachpoint("gw")],
            handling=SEED_MOVE_PARAMETERS,
        )
        assert request.gateway_path[0].position_id == "gw"


class TestNonValidatingMethods:
    @pytest.mark.asyncio
    async def test_home_does_not_validate_gripper_state(self) -> None:
        driver = _fast_driver()
        driver._seed_resource("nest_1", _make_plate("plate_a"))
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        await driver.home(HomeRequest())

        assert driver._gripper_payload() is not None

    @pytest.mark.asyncio
    async def test_move_to_safe_does_not_validate_gripper_state(self) -> None:
        driver = _fast_driver()
        driver._seed_resource("nest_1", _make_plate("plate_a"))
        await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        await driver.move_to_safe(MoveToSafeRequest())

        assert driver._gripper_payload() is not None

    @pytest.mark.asyncio
    async def test_move_to_position_does_not_touch_world(self) -> None:
        driver = _fast_driver()
        driver._seed_resource("nest_1", _make_plate("plate_a"))

        await driver.move_to_coords(MoveToCoordsRequest(teachpoint=_teachpoint("waypoint_x")))

        assert driver._resource_at("nest_1") is not None
        assert driver._gripper_payload() is None


class TestInstanceIsolation:
    @pytest.mark.asyncio
    async def test_two_transporters_have_independent_worlds(self) -> None:
        driver_a = _fast_driver(name="arm_a")
        driver_b = _fast_driver(name="arm_b")
        driver_a._seed_resource("nest_1", _make_plate("plate_a"))

        with pytest.raises(SimTransporterValidationError, match="empty"):
            await driver_b.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("nest_1"), labware_type="Plate_96", handling=SEED_MOVE_PARAMETERS))

        assert driver_a._resource_at("nest_1") is not None


class TestExternalControlSkipsValidation:
    """Ad-hoc / external-control commands set `external_control=True`. Nothing
    tracks occupancy under external control, so the sim must NOT validate or mutate
    its world graph: pick from empty succeeds, place onto occupied succeeds, and
    the graph is left exactly as it was (a pure no-op, matching real hardware
    which has no occupancy check at all).
    """

    @pytest.mark.asyncio
    async def test_pick_from_empty_succeeds_and_does_not_mutate(self) -> None:
        driver = _fast_driver()

        await driver.pick_at_coords(
            PickAtCoordsRequest(teachpoint=_teachpoint("pad_1"), external_control=True, handling=SEED_MOVE_PARAMETERS)
        )

        assert driver._gripper_payload() is None
        assert driver._resource_at("pad_1") is None

    @pytest.mark.asyncio
    async def test_place_onto_occupied_succeeds_and_does_not_mutate(self) -> None:
        driver = _fast_driver()
        seeded = _make_plate("resident")
        driver._seed_resource("pad_1", seeded)

        await driver.place_at_coords(
            PlaceAtCoordsRequest(teachpoint=_teachpoint("pad_1"), external_control=True, handling=SEED_MOVE_PARAMETERS)
        )

        assert driver._resource_at("pad_1") is seeded
        assert driver._gripper_payload() is None

    @pytest.mark.asyncio
    async def test_pick_does_not_disturb_a_seeded_position(self) -> None:
        driver = _fast_driver()
        resident = _make_plate("resident")
        driver._seed_resource("pad_1", resident)

        await driver.pick_at_coords(
            PickAtCoordsRequest(teachpoint=_teachpoint("pad_1"), external_control=True, handling=SEED_MOVE_PARAMETERS)
        )

        assert driver._resource_at("pad_1") is resident
        assert driver._gripper_payload() is None

    @pytest.mark.asyncio
    async def test_default_request_still_validates(self) -> None:
        """Omitting external_control keeps the occupancy check (the workflow path)."""
        driver = _fast_driver()

        with pytest.raises(SimTransporterValidationError, match="empty"):
            await driver.pick_at_coords(PickAtCoordsRequest(teachpoint=_teachpoint("pad_1"), handling=SEED_MOVE_PARAMETERS))
