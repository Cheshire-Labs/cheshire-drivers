"""Pick and place composed out of the arm's moves.

The route, the geometry and the plate-present inference used to live in the arm
driver, where nothing above it could vary them per labware. These pin what the
sequence does now that it is built here from the resolved move.
"""

import pytest
from pylabrobot.resources import Coordinate

from cheshire_drivers.move_parameters import (
    SEED_MOVE_PARAMETERS,
    MoveParameterPatch,
    MoveParameters,
)
from cheshire_drivers.plr.arm_pick_place import (
    ArmPickPlace,
    EmptyGripError,
    UnreachableTargetError,
)
from tests.arm_backend_mock import RecordingArmBackend

PAD = Coordinate(200.0, 100.0, 40.0)


def _handling(**overrides: str | float | None) -> MoveParameters:
    return MoveParameterPatch.model_validate(overrides).apply_to(SEED_MOVE_PARAMETERS)


def _legs(backend: RecordingArmBackend) -> list[tuple[float, float, float]]:
    return [(round(m.x, 3), round(m.y, 3), round(m.z, 3)) for m in backend.moves()]


def _verbs(backend: RecordingArmBackend) -> list[str]:
    """The move and jaw calls in order, as "move", "open" or "close"."""
    out = []
    for name, args in backend.calls:
        if name == "move_to_location":
            out.append("move")
        elif name == "move_gripper_joint_position":
            out.append(str(args[1]))
    return out


@pytest.mark.asyncio
async def test_the_jaws_open_before_the_arm_reaches_in():
    """Descending onto a plate with the fingers still closed is how you break one."""
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).pick(PAD, direction=0.0, handling=SEED_MOVE_PARAMETERS)

    assert _verbs(backend) == ["open", "move", "move", "close", "move"]


@pytest.mark.asyncio
async def test_the_jaws_open_a_step_off_the_grip_position_not_to_the_stop():
    """Sweeping to the axis stop is a long move to no purpose, and on a full deck it
    is the move that meets the neighbours."""
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).pick(PAD, direction=0.0, handling=_handling(jaw_opening=14.0))

    opens = [c[1] for c in backend.calls if c[0] == "move_gripper_joint_position"]
    assert opens[0] == (backend.closed_gripper_position + 14.0, "open")


@pytest.mark.asyncio
async def test_the_grip_commands_the_calibrated_position_not_the_axis_floor():
    """Commanding tighter and letting the plate stop the fingers holds a standing
    position error, which the controller reports as an overheating motor."""
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).pick(PAD, direction=0.0, handling=SEED_MOVE_PARAMETERS)

    closes = [c[1] for c in backend.calls if c[0] == "move_gripper_joint_position"][1]
    assert closes == (backend.closed_gripper_position, "close")


@pytest.mark.asyncio
async def test_a_pick_that_caught_nothing_says_so():
    backend = RecordingArmBackend(has_rail=False, grip_catches_nothing=True)

    with pytest.raises(EmptyGripError):
        await ArmPickPlace(backend).pick(PAD, direction=0.0, handling=SEED_MOVE_PARAMETERS)


@pytest.mark.asyncio
async def test_a_pick_that_caught_nothing_leaves_the_nest_before_it_raises():
    """Failing with the arm still down in the nest leaves it there for whatever runs
    next, and the empty jaws do not take the allowance a held labware needs."""
    backend = RecordingArmBackend(has_rail=False, grip_catches_nothing=True)

    with pytest.raises(EmptyGripError):
        await ArmPickPlace(backend).pick(
            PAD,
            direction=0.0,
            handling=_handling(resource_height=99.0, travel_margin=10.0, grasp_offset=20.0),
        )

    assert _legs(backend)[-1] == (200.0, 100.0, 40.0 + 99.0 + 10.0)


@pytest.mark.asyncio
async def test_a_retreat_rises_by_the_depth_of_the_nest():
    """An operator raises grasp_offset because that pocket is deep; a fixed lift leaves
    the labware still inside it when the traverse starts."""
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).pick(
        PAD,
        direction=0.0,
        handling=_handling(travel_margin=10.0, grasp_offset=60.0),
    )

    assert _legs(backend)[-1] == (200.0, 100.0, 40.0 + 60.0 + 10.0)


@pytest.mark.asyncio
async def test_a_retreat_does_not_rise_by_the_height_of_the_labware():
    """What has to be cleared on the way out is the lip of the pad, and a tip box and a
    microplate come off the same pad over the same lip. Rising by the labware's own
    height instead sent a 99 mm rack 129 mm into the air to leave a flat surface."""
    backend = RecordingArmBackend(has_rail=False)

    for height in (14.0, 99.0):
        backend.calls.clear()
        await ArmPickPlace(backend).pick(
            PAD,
            direction=0.0,
            handling=_handling(
                resource_height=height, travel_margin=10.0, grasp_offset=20.0
            ),
        )
        assert _legs(backend)[-1] == (200.0, 100.0, 70.0)


@pytest.mark.asyncio
async def test_a_release_leaves_over_the_labware_it_just_set_down():
    """The bench failure: the arm cleared the pad by the pad's depth, which put the
    open fingers halfway up a 99 mm box, and the next leg was sideways."""
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).place(
        PAD,
        direction=0.0,
        handling=_handling(resource_height=99.0, travel_margin=10.0, grasp_offset=20.0),
    )

    assert _legs(backend)[-1] == (200.0, 100.0, 40.0 + 99.0 + 10.0)


@pytest.mark.asyncio
async def test_a_shelf_is_entered_along_the_approach_and_left_level():
    """There is nothing to traverse over inside a hotel, and a full lift would meet
    the shelf above."""
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).pick(
        Coordinate(300.0, 0.0, 50.0),
        direction=0.0,
        handling=_handling(access_type="horizontal", clearance=40.0, z_above=10.0),
    )

    assert _legs(backend) == [
        (260.0, 0.0, 60.0),
        (260.0, 0.0, 50.0),
        (300.0, 0.0, 50.0),
        (300.0, 0.0, 60.0),
        (260.0, 0.0, 60.0),
    ]


@pytest.mark.asyncio
async def test_a_target_out_of_reach_is_refused_before_anything_moves():
    backend = RecordingArmBackend(has_rail=False)

    with pytest.raises(UnreachableTargetError):
        await ArmPickPlace(backend).pick(
            Coordinate(900.0, 0.0, 50.0),
            direction=0.0,
            handling=SEED_MOVE_PARAMETERS,
            envelope=backend.WORK_ENVELOPE,
        )

    assert backend.calls == []


@pytest.mark.asyncio
async def test_a_retreat_that_would_leave_the_z_travel_is_refused_too():
    """Refusing it only on the way up would mean failing with the labware in the jaws."""
    backend = RecordingArmBackend(has_rail=False)

    with pytest.raises(UnreachableTargetError):
        await ArmPickPlace(backend).pick(
            Coordinate(300.0, 80.0, 395.0),
            direction=0.0,
            handling=_handling(clearance=2.0, grasp_offset=20.0),
            envelope=backend.WORK_ENVELOPE,
        )

    assert backend.calls == []


@pytest.mark.asyncio
async def test_a_shelf_is_measured_by_its_own_lift_not_by_the_standoff():
    """A horizontal site never rises by more than z_above, so one near the top of the
    travel must not be refused for a clearance it only uses horizontally."""
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).place(
        Coordinate(300.0, 80.0, 395.0),
        direction=0.0,
        handling=_handling(access_type="horizontal", clearance=100.0, z_above=3.0),
        envelope=backend.WORK_ENVELOPE,
    )

    assert _legs(backend) == [
        (200.0, 80.0, 398.0),
        (200.0, 80.0, 395.0),
        (300.0, 80.0, 395.0),
        (300.0, 80.0, 398.0),
        (200.0, 80.0, 398.0),
    ]


@pytest.mark.asyncio
async def test_an_arm_that_has_not_read_its_envelope_refuses_nothing_for_reach():
    """Bring-up is what reads the envelope off the controller. Before that there is
    no annulus to check against, and inventing one would refuse valid targets."""
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).pick(
        Coordinate(900.0, 0.0, 50.0), direction=0.0, handling=SEED_MOVE_PARAMETERS
    )

    assert _legs(backend) == [
        (900.0, 0.0, 74.35),
        (900.0, 0.0, 50.0),
        (900.0, 0.0, 80.0),
    ]


@pytest.mark.asyncio
async def test_a_move_with_a_speed_of_its_own_runs_the_whole_sequence_at_it():
    """Every leg, not just the descent: the retreat is the one carrying the labware.

    The scope is entered once and every leg happens inside it, so what pins "the whole
    sequence" is that no leg was recorded outside the scope.
    """
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).pick(PAD, direction=0.0, handling=_handling(speed=20.0))

    names = [name for name, _ in backend.calls]
    assert backend.speeds == [20.0]
    assert names[0] == "at_speed_enter" and names[-1] == "at_speed_exit"
    assert "at_speed_enter" not in names[1:]


@pytest.mark.asyncio
async def test_a_release_never_opens_past_the_end_of_the_gripper_axis():
    """The release opens off where the labware left the jaws, which is already part of
    the way to the stop, so a step off it can reach the end of the axis."""
    backend = RecordingArmBackend(
        has_rail=False,
        initial_joints=[0.0, 170.0, 0.0, 150.0, 0.0, 78.0],
        gripper_joint_range=(69.0, 82.0),
    )

    await ArmPickPlace(backend).place(PAD, direction=0.0, handling=_handling(jaw_opening=14.0))

    opens = [c[1] for c in backend.calls if c[0] == "move_gripper_joint_position"]
    assert opens == [(82.0, "open")]


@pytest.mark.asyncio
async def test_an_arm_that_reports_no_axis_ceiling_opens_where_it_was_asked():
    """Inventing a ceiling would refuse openings the fitted gripper reaches."""
    backend = RecordingArmBackend(
        has_rail=False, initial_joints=[0.0, 170.0, 0.0, 150.0, 0.0, 78.0]
    )

    await ArmPickPlace(backend).place(PAD, direction=0.0, handling=_handling(jaw_opening=14.0))

    opens = [c[1] for c in backend.calls if c[0] == "move_gripper_joint_position"]
    assert opens == [(92.0, "open")]


@pytest.mark.asyncio
async def test_a_place_opens_a_step_off_where_the_labware_held_the_jaws():
    """Not off the closed position: the jaws are already apart, and opening by the step
    from closed would either not let go or sweep into the neighbours."""
    held = RecordingArmBackend.closed_gripper_position + 3.0
    backend = RecordingArmBackend(
        has_rail=False, initial_joints=[0.0, 170.0, 0.0, 150.0, 0.0, held]
    )

    await ArmPickPlace(backend).place(PAD, direction=0.0, handling=_handling(jaw_opening=14.0))

    opens = [c[1] for c in backend.calls if c[0] == "move_gripper_joint_position"]
    assert opens == [(held + 14.0, "open")]


@pytest.mark.asyncio
async def test_a_margin_wider_than_the_hold_reads_the_grip_as_empty():
    """Which face the jaws closed on decides how far a held labware holds them apart,
    which is why the margin is per-labware rather than one number for the arm."""
    backend = RecordingArmBackend(has_rail=False)  # a hold of HELD_LABWARE_UNITS = 3.0

    with pytest.raises(EmptyGripError):
        await ArmPickPlace(backend).pick(
            PAD, direction=0.0, handling=_handling(plate_present_margin=5.0)
        )


@pytest.mark.asyncio
async def test_a_margin_under_the_hold_reads_the_same_grip_as_holding_something():
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).pick(
        PAD, direction=0.0, handling=_handling(plate_present_margin=2.0)
    )

    assert _legs(backend)[-1] == (200.0, 100.0, 40.0 + 20.0 + 10.0)  # loaded: the nest


@pytest.mark.asyncio
async def test_a_vertical_pick_enters_from_above_the_labware_not_the_site_floor():
    """A 99 mm tip rack under a 20 mm clearance: descending to 20 above the base of
    the rack descends through the rack. The entry has to clear what it lands on."""
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).pick(
        PAD,
        direction=0.0,
        handling=_handling(resource_height=99.0, travel_margin=10.0, clearance=20.0),
    )

    assert _legs(backend)[0] == (200.0, 100.0, 40.0 + 99.0 + 10.0)


@pytest.mark.asyncio
async def test_a_vertical_place_descends_from_clear_of_the_nest():
    """A place is a pick run backwards: it arrives holding the labware, which hangs
    below the fingers, so it descends from the height that clears the pocket. What it
    is carrying is already inside the gripper, so its height changes nothing."""
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).place(
        PAD,
        direction=0.0,
        handling=_handling(resource_height=99.0, travel_margin=10.0, grasp_offset=35.0),
    )

    assert _legs(backend)[0] == (200.0, 100.0, 40.0 + 35.0 + 10.0)


@pytest.mark.asyncio
async def test_a_site_can_ask_for_more_room_than_the_labware_needs():
    """`clearance` is the site's floor, not its ceiling: a lid overhanging the nest is
    a reason to enter higher, and nothing about the labware would say so."""
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).pick(
        PAD,
        direction=0.0,
        handling=_handling(resource_height=14.0, travel_margin=10.0, clearance=200.0),
    )

    assert _legs(backend)[0] == (200.0, 100.0, 240.0)


@pytest.mark.asyncio
async def test_a_move_is_refused_for_the_height_it_actually_reaches():
    """The pre-flight measures every leg the move makes. Checking a lower one and
    letting a higher one run means clearing the check and then leaving the Z travel,
    with the labware already in the jaws."""
    backend = RecordingArmBackend(has_rail=False)

    with pytest.raises(UnreachableTargetError):
        await ArmPickPlace(backend).place(
            Coordinate(300.0, 80.0, 350.0),
            direction=0.0,
            handling=_handling(travel_margin=10.0, grasp_offset=60.0),
            envelope=backend.WORK_ENVELOPE,
        )

    assert backend.calls == []


@pytest.mark.asyncio
async def test_the_sites_floor_holds_on_the_way_out_as_well_as_the_way_in():
    """`clearance` is a property of the site, not of which direction the arm is going.
    Honouring it only on the descent also puts the pre-flight above a height the
    retreat never reaches, so a move can clear the check and still leave too low."""
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).pick(
        PAD,
        direction=0.0,
        handling=_handling(travel_margin=10.0, grasp_offset=20.0, clearance=200.0),
    )

    assert _legs(backend)[-1] == (200.0, 100.0, 240.0)


@pytest.mark.asyncio
async def test_a_release_respects_the_sites_floor_too():
    """The same floor on the other empty-jaw leg: leaving after a place."""
    backend = RecordingArmBackend(has_rail=False)

    await ArmPickPlace(backend).place(
        PAD,
        direction=0.0,
        handling=_handling(resource_height=14.0, travel_margin=10.0, clearance=200.0),
    )

    assert _legs(backend)[-1] == (200.0, 100.0, 240.0)
