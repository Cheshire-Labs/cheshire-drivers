"""Hardware-free proof that joints translate correctly onto the arm's Axis pose.

The PreciseFlex wrapper was validated on live hardware with a list-based joint
model ([rail, base, shoulder, elbow, wrist, gripper]). Upstream PLR replaced that
with a Axis->value dict. These tests pin, without hardware, that every named
joint reaches the same numeric value it did before:

- the conversion maps each joint by NAME, not by position;
- the equivalence-anchor test ties the new dict to the exact old list binding, so
  any future re-order breaks the suite;
- the command-capture tests assert the joint values the wrapper actually emits;
- the has_rail tests guard the landmine that Axis numbering (base=1) and the
  Brooks TCS numbering used by move_one_axis (base=2 with a rail) must never cross.
"""
import pytest
from pydantic import ValidationError

from pylabrobot.brooks.precise_flex import Axis, PreciseFlex

from cheshire_drivers.move_parameters import (
    SEED_MOVE_PARAMETERS,
    MoveParameterPatch,
    MoveParameters,
)
from cheshire_drivers.plr import PLRTransporterBackendWrapper, convert_joint_to_plr_dict
from cheshire_drivers.plr.arm_pick_place import EmptyGripError, UnreachableTargetError
from cheshire_drivers.plr.transporter import PreciseFlexTransporterDriver
from cheshire_drivers.plr.transporter_wrapper import _joint_tuple_to_plr_dict
from cheshire_drivers.teachpoints import Teachpoint, JointCoordinates, CartesianCoordinates
from cheshire_drivers.transporter_models import (
    CloseGripperRequest,
    GetJointPositionRequest,
    InitializeRequest,
    MoveSingleAxisRequest,
    MoveToCoordsRequest,
    OpenGripperRequest,
    PickAtCoordsRequest,
    PlaceAtCoordsRequest,
    SetFreeModeRequest,
)

from tests.arm_backend_mock import RecordingArmBackend

# The old, hardware-validated positional binding: list index -> joint name.
OLD_INDEX_TO_NAME = {0: "rail", 1: "base", 2: "shoulder", 3: "elbow", 4: "wrist", 5: "gripper"}
NAME_TO_AXIS = {
    "rail": Axis.RAIL,
    "base": Axis.BASE,
    "shoulder": Axis.SHOULDER,
    "elbow": Axis.ELBOW,
    "wrist": Axis.WRIST,
    "gripper": Axis.GRIPPER,
}


def test_convert_joint_dict_maps_each_named_joint():
    """Distinct sentinels per joint; a key swap makes an assertion fail."""
    coords = JointCoordinates(rail=1, base=2, shoulder=3, elbow=4, wrist=5, gripper=6)
    d = convert_joint_to_plr_dict(coords)
    assert d[Axis.RAIL] == 1
    assert d[Axis.BASE] == 2
    assert d[Axis.SHOULDER] == 3
    assert d[Axis.ELBOW] == 4
    assert d[Axis.WRIST] == 5
    assert d[Axis.GRIPPER] == 6


def test_dict_matches_old_positional_list_binding():
    """Equivalence anchor: the Axis dict carries the same value per named joint
    that the old [rail, base, shoulder, elbow, wrist, gripper] list carried at
    each index. This encodes 'joints were not scrambled' and breaks on re-order."""
    coords = JointCoordinates(rail=11, base=22, shoulder=33, elbow=44, wrist=55, gripper=66)
    old_list = [coords.rail, coords.base, coords.shoulder, coords.elbow, coords.wrist, coords.gripper]
    d = convert_joint_to_plr_dict(coords)
    for index, name in OLD_INDEX_TO_NAME.items():
        assert d[NAME_TO_AXIS[name]] == old_list[index], f"joint '{name}' mistranslated"


@pytest.mark.asyncio
async def test_get_joint_position_reads_each_named_axis():
    """Reading back a dict must fill each JointCoordinates field from its own key."""
    backend = RecordingArmBackend(has_rail=True, initial_joints=[10, 20, 30, 40, 50, 60])
    wrapper = PLRTransporterBackendWrapper(backend)
    jc = await wrapper.get_joint_position(GetJointPositionRequest())
    assert jc.rail == 10
    assert jc.base == 20
    assert jc.shoulder == 30
    assert jc.elbow == 40
    assert jc.wrist == 50
    assert jc.gripper == 60


@pytest.mark.asyncio
async def test_gripper_commands_work_off_the_grip_position_not_the_axis_stops():
    """Open stands off the calibrated grip position; close goes to it. Driving to the
    stops instead, as this wrapper once did, sweeps the jaws to a physical extreme that
    has nothing to do with the labware, and the servo's overshoot at the far end parks
    the axis outside its limits, where it blocks every later move until the arm is homed.
    Close uses force sensing, so the position is a floor the jaws stop short of on
    contact rather than a width they crush to."""
    backend = RecordingArmBackend(has_rail=False)
    wrapper = PLRTransporterBackendWrapper(backend)

    await wrapper.open_gripper(OpenGripperRequest())
    await wrapper.close_gripper(CloseGripperRequest())

    assert backend.grip_positions == [
        ("open", backend.closed_gripper_position + PLRTransporterBackendWrapper.DEFAULT_JAW_OPENING),
        ("close", backend.closed_gripper_position),
    ]
    assert backend.grip_widths == []


@pytest.mark.asyncio
async def test_an_open_uses_the_same_jaw_opening_a_pick_uses():
    """The opening is a stored per-arm number that a pick already reads. If an open
    resolves it anywhere else, changing it moves the pick and leaves the operator's
    button on the old number, which is two answers to how wide the jaws go."""
    backend = RecordingArmBackend(has_rail=False)
    wrapper = PLRTransporterBackendWrapper(backend)

    await wrapper.open_gripper(OpenGripperRequest(jaw_opening=18.0))

    assert backend.grip_positions == [("open", backend.closed_gripper_position + 18.0)]


@pytest.mark.asyncio
async def test_a_caller_can_name_the_gripper_position_it_wants():
    """The defaults are for an operator pressing a button; a caller holding a taught
    pose names the axis position instead."""
    backend = RecordingArmBackend(has_rail=False)
    wrapper = PLRTransporterBackendWrapper(backend)

    await wrapper.open_gripper(OpenGripperRequest(position=95.0))
    await wrapper.close_gripper(CloseGripperRequest(position=78.0))

    assert backend.grip_positions == [("open", 95.0), ("close", 78.0)]


def test_an_open_that_names_both_a_position_and_an_opening_is_refused():
    """They measure from different places, so a request carrying both does not say
    where the jaws should end up."""
    with pytest.raises(ValidationError):
        OpenGripperRequest(position=95.0, jaw_opening=18.0)


@pytest.mark.asyncio
async def test_joint_space_move_emits_an_axis_pose_that_leaves_the_jaws_alone():
    """A joint-space move sends each named joint's value and no gripper at all: the arm
    merges an omitted axis from its own live position. Reading the jaws back and
    re-commanding them makes a live reading into a taught target, so jaws sitting
    outside their limits fail as a pose to re-teach instead of as recoverable arm state.
    The teachpoint's own gripper value (5) is never a target either."""
    backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77])
    wrapper = PLRTransporterBackendWrapper(backend)
    tp = Teachpoint(
        position_id="j",
        coordinates=JointCoordinates(rail=0, base=100, shoulder=10, elbow=120, wrist=-30, gripper=5),
    )
    await wrapper.move_to_coords(MoveToCoordsRequest(teachpoint=tp))

    moves = [c[1][0] for c in backend.calls if c[0] == "move_to_joint_position"]
    assert len(moves) == 1
    d = moves[0]
    assert isinstance(d, dict)  # a joint move records an Axis pose, not a cartesian target
    assert d[Axis.RAIL] == 0
    assert d[Axis.BASE] == 100
    assert d[Axis.SHOULDER] == 10
    assert d[Axis.ELBOW] == 120
    assert d[Axis.WRIST] == -30
    assert Axis.GRIPPER not in d


@pytest.mark.asyncio
async def test_cartesian_move_sends_a_location_and_approach_direction():
    """The cartesian path hands the arm a tool point plus the approach direction
    (the teachpoint's yaw) and the elbow orientation, never a joint pose. Pitch and
    roll are the arm's own fixed plate-gripping wrist and are not sent."""
    backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77])
    wrapper = PLRTransporterBackendWrapper(backend)
    tp = Teachpoint(
        position_id="c",
        coordinates=CartesianCoordinates(x=100, y=200, z=50, yaw=0, pitch=0, roll=0),
        orientation="right",
    )
    await wrapper.move_to_coords(MoveToCoordsRequest(teachpoint=tp))

    moves = [c[1] for c in backend.calls if c[0] == "move_to_location"]
    assert len(moves) == 1
    location, direction, elbow = moves[0]
    assert (location.x, location.y, location.z) == (100, 200, 50)
    assert direction == 0
    assert elbow == "right"


@pytest.mark.asyncio
async def test_a_pick_reaches_the_taught_point_the_way_the_move_says():
    """The pose comes from the teachpoint and the approach comes from the resolved
    move, which is what keeps a driver from having to know about labware at all."""
    backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77])
    wrapper = PLRTransporterBackendWrapper(backend)
    tp = Teachpoint(
        position_id="p",
        coordinates=CartesianCoordinates(x=100, y=200, z=50, yaw=0, pitch=0, roll=0),
        orientation="right",
        access_type="vertical",
    )
    await wrapper.pick_at_coords(PickAtCoordsRequest(teachpoint=tp, gateway_path=[], handling=SEED_MOVE_PARAMETERS))

    # A vertical site: over the labware standing there, straight down onto the taught
    # point, then up by enough to be out of the nest.
    assert [(m.x, m.y, m.z) for m in backend.moves()] == [
        (100, 200, 50 + 14.35 + 10.0),
        (100, 200, 50.0),
        (100, 200, 50 + 20.0 + 10.0),
    ]
    approaches = {c[1][1:] for c in backend.calls if c[0] == "move_to_location"}
    assert approaches == {(0, "right")}


@pytest.mark.asyncio
async def test_move_single_axis_names_the_axis_the_same_way_with_or_without_a_rail():
    """The old landmine was two numbering schemes: joint poses keyed base=1 while
    single-axis moves wanted the controller's base=2-with-a-rail. There is one Axis
    enum for both now, so a named joint resolves the same either way."""
    for has_rail in (True, False):
        backend = RecordingArmBackend(has_rail=has_rail)
        wrapper = PLRTransporterBackendWrapper(backend)
        await wrapper.move_single_axis(MoveSingleAxisRequest(axis="base", position=42.0))
        calls = [c[1] for c in backend.calls if c[0] == "move_one_axis"]
        assert len(calls) == 1
        axis, position = calls[0]
        assert axis is Axis.BASE
        assert position == 42.0


@pytest.mark.asyncio
async def test_set_free_mode_all_frees_all_axes():
    backend = RecordingArmBackend(has_rail=False)
    wrapper = PLRTransporterBackendWrapper(backend)
    await wrapper.set_free_mode(SetFreeModeRequest(axes="all"))
    assert ("start_freedrive_mode", ((),)) in backend.calls


@pytest.mark.asyncio
async def test_set_free_mode_none_ends_freedrive():
    backend = RecordingArmBackend(has_rail=False)
    wrapper = PLRTransporterBackendWrapper(backend)
    await wrapper.set_free_mode(SetFreeModeRequest(axes="none"))
    assert ("stop_freedrive_mode", ()) in backend.calls


@pytest.mark.asyncio
async def test_set_free_mode_single_axis_names_that_axis():
    backend = RecordingArmBackend(has_rail=False)
    wrapper = PLRTransporterBackendWrapper(backend)
    await wrapper.set_free_mode(SetFreeModeRequest(axes=["base"]))
    assert ("start_freedrive_mode", ((int(Axis.BASE),),)) in backend.calls


def test_preciseflex_driver_rejects_has_rail():
    """The railed wire order is unvalidated on this PLR, so constructing a railed
    driver must fail loudly rather than silently mistranslate joints."""
    with pytest.raises(NotImplementedError, match="has_rail"):
        PreciseFlexTransporterDriver(host="127.0.0.1", has_rail=True)


def test_wrapper_rejects_real_railed_preciseflex_backend():
    """Production wraps the raw backend directly (orca-client's factory does
    PLRTransporterBackendWrapper(backend)), bypassing the driver. So the railed
    guard lives on the base wrapper: a real railed PreciseFlex is rejected
    there, while the mock railed backends used above are exempt by type."""
    railed = PreciseFlex(
        host="127.0.0.1",
        gripper_length=162.0,
        gripper_z_offset=0.0,
        closed_gripper_position=75.5,
        has_rail=True,
    )
    with pytest.raises(NotImplementedError, match="has_rail"):
        PLRTransporterBackendWrapper(railed)


# What the recorder adds for the tests' benefit. Everything else it offers is the
# arm's own surface, and has to still be there.
_RECORDER_ONLY = frozenset({
    "calls",
    "moves",
    "grip_widths",
    "grip_positions",
    "speeds",
    "grip_catches_nothing",
    "discovery_fails",
    "HELD_LABWARE_UNITS",
    "WORK_ENVELOPE",
})


def test_the_recording_arm_offers_nothing_the_real_arm_does_not():
    """A method the driver dropped but the mock kept leaves the wrapper free to call it:
    the suite stays green and the call is an AttributeError on hardware. Anything the
    recorder answers that is not listed above must exist on the arm it stands in for."""
    # An unconnected arm: some of the surface is set in __init__, so the class alone
    # does not carry it.
    arm = PreciseFlex(
        host="127.0.0.1",
        gripper_length=162.0,
        gripper_z_offset=0.0,
        closed_gripper_position=75.5,
    )
    surface = {
        name
        for name in vars(RecordingArmBackend)
        if not name.startswith("_") and name not in _RECORDER_ONLY
    }

    # `dir` rather than `hasattr`: reading a property here would run it, and some of
    # them raise on an arm that has not been set up.
    assert sorted(surface - set(dir(arm))) == []


def test_crossover_constants_map_to_named_axes_and_carry_no_jaw_position():
    """The crossover tuples are [rail, base, shoulder, elbow, wrist, gripper]; the helper
    keys them by name and drops the gripper slot. Its 0.0 would drive the jaws to the
    bottom of the axis, and the arm keeps its own jaw position for an omitted axis."""
    d = _joint_tuple_to_plr_dict(PLRTransporterBackendWrapper.SAFE_LOC)
    assert d[Axis.RAIL] == 0.0
    assert d[Axis.BASE] == 170.0
    assert d[Axis.SHOULDER] == 0.0
    assert d[Axis.ELBOW] == 180.0
    assert d[Axis.WRIST] == -180.0
    assert Axis.GRIPPER not in d


def _cartesian_teachpoint(position_id: str = "p") -> Teachpoint:
    return Teachpoint(
        position_id=position_id,
        coordinates=CartesianCoordinates(x=100, y=200, z=50, yaw=0, pitch=0, roll=0),
        orientation="right",
        access_type="vertical",
    )


def _handling(**overrides: str | float | None) -> MoveParameters:
    return MoveParameterPatch.model_validate(overrides).apply_to(SEED_MOVE_PARAMETERS)


@pytest.mark.asyncio
async def test_the_resolved_move_supplies_the_grip_the_driver_used_to_hardcode():
    """A tip box and a deep-well plate want different jaws and different lifts at the
    same taught position; the driver no longer has a plate width of its own to use."""
    backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77])
    wrapper = PLRTransporterBackendWrapper(backend)

    await wrapper.pick_at_coords(PickAtCoordsRequest(
        teachpoint=_cartesian_teachpoint(),
        handling=_handling(resource_width=85.0, resource_height=43.5, jaw_opening=18.0),
    ))

    grasp = [c[1] for c in backend.calls if c[0] == "set_grasp_data"]
    assert grasp[0][0] == 85.0
    opens = [c[1] for c in backend.calls if c[0] == "move_gripper_joint_position"]
    assert opens[0] == (backend.closed_gripper_position + 18.0, "open")
    assert backend.moves()[-1].z == 50 + 20.0 + 10.0


@pytest.mark.asyncio
async def test_the_resolved_move_supplies_the_margin_that_says_a_grip_took_hold():
    """How far a held resource holds the jaws open is a property of what is being
    gripped, and of the face the jaws close on: a plate turned to its short side needs
    its own number. Left in the driver it was one constant for every labware and
    unreachable from anything an operator or a protocol can set."""
    backend = RecordingArmBackend(has_rail=False)  # a hold of HELD_LABWARE_UNITS = 3.0
    wrapper = PLRTransporterBackendWrapper(backend)

    await wrapper.pick_at_coords(PickAtCoordsRequest(
        teachpoint=_cartesian_teachpoint(),
        handling=_handling(plate_present_margin=2.0),
    ))

    with pytest.raises(EmptyGripError):
        await wrapper.pick_at_coords(PickAtCoordsRequest(
            teachpoint=_cartesian_teachpoint(),
            handling=_handling(plate_present_margin=5.0),
        ))


@pytest.mark.asyncio
async def test_a_place_leaves_over_the_labware_it_just_set_down():
    """The open fingers are beside the labware once it is down, and the next leg is
    sideways, so the way out is measured on the labware and not on the pocket."""
    backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77])
    wrapper = PLRTransporterBackendWrapper(backend)

    await wrapper.place_at_coords(PlaceAtCoordsRequest(
        teachpoint=_cartesian_teachpoint(),
        handling=_handling(resource_height=43.5, travel_margin=12.0, grasp_offset=20.0),
    ))

    assert backend.moves()[-1].z == 50 + 43.5 + 12.0


@pytest.mark.asyncio
async def test_the_approach_comes_from_the_resolved_move_not_the_teachpoint():
    """The site's access config is one layer among several, and the layering is
    settled before the request is sent. A driver that re-read the teachpoint here
    would silently discard whatever the labware or the protocol asked for."""
    backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77])
    wrapper = PLRTransporterBackendWrapper(backend)
    teachpoint = _cartesian_teachpoint()
    teachpoint.vertical_clearance = 20.0

    await wrapper.pick_at_coords(PickAtCoordsRequest(
        teachpoint=teachpoint,
        handling=_handling(access_type="horizontal", clearance=60.0, z_above=15.0,
                           grasp_offset=3.0),
    ))

    # A shelf: stand off 60 along the approach, lifted by z_above, then in and level.
    legs = [(round(m.x, 3), round(m.y, 3), round(m.z, 3)) for m in backend.moves()]
    assert legs[:3] == [(40.0, 200.0, 65.0), (40.0, 200.0, 50.0), (100.0, 200.0, 50.0)]
    # It leaves by z_above and withdraws level, never by the loaded lift.
    assert legs[3:] == [(100.0, 200.0, 65.0), (40.0, 200.0, 65.0)]


@pytest.mark.asyncio
async def test_a_labware_gripped_higher_than_the_taught_one_lifts_the_grip_point():
    """Every teachpoint here was taught on one plate. A taller labware is gripped
    above that z, and the offset is what saves re-teaching the whole deck."""
    backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77])
    wrapper = PLRTransporterBackendWrapper(backend)

    await wrapper.pick_at_coords(PickAtCoordsRequest(
        teachpoint=_cartesian_teachpoint(),
        handling=_handling(z_offset=29.3),
    ))

    grip = backend.moves()[1]
    assert (grip.x, grip.y, grip.z) == (100, 200, 79.3)


@pytest.mark.asyncio
async def test_a_move_with_no_speed_of_its_own_leaves_the_arm_alone():
    backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77])
    wrapper = PLRTransporterBackendWrapper(backend)

    await wrapper.pick_at_coords(PickAtCoordsRequest(
        teachpoint=_cartesian_teachpoint(), handling=SEED_MOVE_PARAMETERS,
    ))

    assert backend.speeds == [None]


@pytest.mark.asyncio
async def test_a_move_that_must_go_slowly_says_so_on_the_move_itself():
    """Not by turning the deployment's speed down and hoping someone turns it back."""
    backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77])
    wrapper = PLRTransporterBackendWrapper(backend)

    await wrapper.pick_at_coords(PickAtCoordsRequest(
        teachpoint=_cartesian_teachpoint(), handling=_handling(speed=20.0),
    ))

    assert backend.speeds == [20.0]


@pytest.mark.asyncio
async def test_a_place_lowers_to_the_same_offset_grip_height_the_pick_used():
    """A pick that lifted the grip point and a place that did not would set the plate
    down at the height of whatever the position was taught with."""
    backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77])
    wrapper = PLRTransporterBackendWrapper(backend)

    await wrapper.place_at_coords(PlaceAtCoordsRequest(
        teachpoint=_cartesian_teachpoint(),
        handling=_handling(z_offset=29.3),
    ))

    release = backend.moves()[1]
    assert (release.x, release.y, release.z) == (100, 200, 79.3)


@pytest.mark.asyncio
async def test_a_pick_is_checked_against_the_envelope_the_arm_read_at_bring_up():
    """The wrapper used to trust its own record of having called initialize, which says
    nothing about whether the arm answered."""
    backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77])
    wrapper = PLRTransporterBackendWrapper(backend)
    await wrapper.initialize(InitializeRequest())

    assert wrapper._work_envelope() == backend.WORK_ENVELOPE

    far = Teachpoint(
        position_id="far",
        coordinates=CartesianCoordinates(x=900, y=0, z=50, yaw=0, pitch=0, roll=0),
        orientation="right",
        access_type="vertical",
    )
    with pytest.raises(UnreachableTargetError):
        await wrapper.pick_at_coords(
            PickAtCoordsRequest(teachpoint=far, handling=SEED_MOVE_PARAMETERS)
        )


@pytest.mark.asyncio
async def test_an_arm_that_came_up_without_reading_its_configuration_says_so():
    """Reading the configuration is best-effort, so bring-up finishing is not proof the
    arm knows its limits, and this is the only reach check a pick gets."""
    backend = RecordingArmBackend(
        has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77], discovery_fails=True
    )
    wrapper = PLRTransporterBackendWrapper(backend)
    await wrapper.initialize(InitializeRequest())

    with pytest.raises(RuntimeError, match="initialize again"):
        await wrapper.pick_at_coords(
            PickAtCoordsRequest(
                teachpoint=_cartesian_teachpoint(), handling=SEED_MOVE_PARAMETERS
            )
        )

    assert backend.moves() == []


@pytest.mark.asyncio
async def test_an_arm_that_has_not_been_brought_up_refuses_nothing_for_reach():
    """Nobody has asked it what it can reach, so there is no annulus to check against
    and inventing one would refuse valid targets."""
    backend = RecordingArmBackend(
        has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77], discovery_fails=True
    )

    assert PLRTransporterBackendWrapper(backend)._work_envelope() is None


@pytest.mark.asyncio
async def test_a_real_pick_passes_over_the_labware_at_its_own_height():
    """A deep-well plate is far taller than the standard microplate the defaults carry,
    and crossing to it any lower runs the open fingers into its side.

    This used to send only a type name and check the driver looked the height up in a
    catalog it shipped. That was wrong about where the number comes from: the lookup
    was keyed by one of the labware's names while the wire carried another, so it
    could miss and answer with the microplate default, silently. The height now
    arrives on the move, resolved by the sender that holds the labware.
    """
    backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77])
    wrapper = PLRTransporterBackendWrapper(backend)

    await wrapper.pick_at_coords(PickAtCoordsRequest(
        teachpoint=_cartesian_teachpoint(),
        labware_type="DeepWell_96_Well",
        handling=_handling(resource_height=39.0, travel_margin=10.0, grasp_offset=20.0),
    ))

    assert backend.moves()[0].z == 50 + 39.0 + 10.0


@pytest.mark.asyncio
async def test_a_labware_the_catalog_does_not_know_keeps_the_height_it_was_sent():
    backend = RecordingArmBackend(has_rail=False, initial_joints=[0, 170, 0, 150, 0, 77])
    wrapper = PLRTransporterBackendWrapper(backend)

    await wrapper.pick_at_coords(PickAtCoordsRequest(
        teachpoint=_cartesian_teachpoint(),
        labware_type="nothing_the_catalog_carries",
        handling=_handling(resource_height=61.0, travel_margin=10.0, grasp_offset=20.0),
    ))

    assert backend.moves()[0].z == 50 + 61.0 + 10.0
