"""Pipetting, gripper and motion behavior of FlexLiquidHandlerDriver.

Cheshire requests carry arbitrary per-well position lists; a Flex head is a
fixed nozzle block on one plunger. These pin the mapping between the two: which
head runs which slice, which shapes are refused (and that a refusal reaches no
wire), how flow rate / offset / liquid height reach the robot, and that every
mutating op reports the state its trackers actually hold.
"""

from typing import Iterator, Sequence, Tuple

import pytest
from pydantic import ValidationError

from cheshire_drivers.gripper_models import (
    GripWithForceRequest,
    MoveGripperToRequest,
    ReleaseJawRequest,
)
from cheshire_drivers.liquid_handler_models import (
    AddDeckLabwareRequest,
    Aspirate96Request,
    AspirateRequest,
    AspirateTarget,
    DeckLayoutConfig,
    DeckResourceConfig,
    DiscardTipsRequest,
    Dispense96Request,
    DispenseRequest,
    DispenseTarget,
    DropTips96Request,
    DropTipsRequest,
    GetDeckStateRequest,
    LabwareStateResponse,
    LabwareWellState,
    LiquidProbeRequest,
    MixParamsModel,
    MixRequest,
    MovePlateRequest,
    PickUpTips96Request,
    PickUpTipsRequest,
    PipettingParameters,
    ReconcileDeckOccupancyRequest,
    ResetDeckLabwareRequest,
    ReturnTips96Request,
    TROUGH_WELL_ID,
    TipPick,
)
from cheshire_drivers.interfaces import (
    IForceGripperJawDriver,
    IGripperMotionDriver,
    IGripperPositionDriver,
    ILiquidProbeDriver,
    IPipetteMotionDriver,
    IWidthGripperJawDriver,
)
from cheshire_drivers.pipette_motion_models import (
    ChannelPosition,
    GetChannelPositionRequest,
    MoveChannelRelativeRequest,
    MoveChannelToRequest,
)
from cheshire_drivers.plr.opentrons_flex import (
    DEFAULT_PIPETTING_HEIGHT_MM,
    FlexLiquidHandlerDriver,
    _undo_partial_staging,
    derive_labware_state,
)
from pylabrobot.opentrons import ChatterboxTransport, OpentronsError, OpentronsFlex
from pylabrobot.opentrons.flex_head import FlexHead8
from pylabrobot.resources import Plate, TipRack, does_tip_tracking, does_volume_tracking
from pylabrobot.resources.errors import (
    HasTipError,
    TooLittleLiquidError,
    TooLittleVolumeError,
)
from pylabrobot.resources.tip_tracker import set_tip_tracking
from pylabrobot.resources.trough import Trough
from pylabrobot.resources.volume_tracker import set_volume_tracking

_EIGHT_CHANNEL = ("p50_multi_flex", 8, 1.0, 50.0, "left")
_EIGHT_CHANNEL_RIGHT = ("p1000_multi_flex", 8, 5.0, 1000.0, "right")
_SINGLE_CHANNEL = ("p1000_single_flex", 1, 1.0, 1000.0, "right")
_NINETY_SIX = ("p1000_96", 96, 1.0, 1000.0, "left")

_FLEX_LAYOUT = DeckLayoutConfig(deck_type="FlexDeck", resources=[])

_RACK_REF = "flex_96_tiprack_50ul"
_COLUMN_1 = [f"{row}1" for row in "ABCDEFGH"]
_COLUMN_2 = [f"{row}2" for row in "ABCDEFGH"]
_LEFT_CHANNELS = list(range(8))
_RIGHT_CHANNELS = list(range(8, 16))
_PLATE_SEED_UL = 100.0
_TROUGH_SEED_UL = 50_000.0
_SAVED_POSITION = {"x": 12.5, "y": 34.0, "z": 56.5}

_RACK = DeckResourceConfig(name="rack_1", catalog_ref=_RACK_REF, parent_id="C1", site_index=0)
_RACK_MISSING_H1 = DeckResourceConfig(
    name="rack_1",
    catalog_ref=_RACK_REF,
    parent_id="C1",
    site_index=0,
    well_state=LabwareWellState(tips={"H1": False}),
)
_RACK_COLUMN_3_FRONT_TWO_GONE = DeckResourceConfig(
    name="rack_1",
    catalog_ref=_RACK_REF,
    parent_id="C1",
    site_index=0,
    well_state=LabwareWellState(tips={"G3": False, "H3": False}),
)
_RACK_COLUMN_1_MIDDLE_GAP = DeckResourceConfig(
    name="rack_1",
    catalog_ref=_RACK_REF,
    parent_id="C1",
    site_index=0,
    well_state=LabwareWellState(tips={"D1": False}),
)
_RACK_BACK_ROW = DeckResourceConfig(
    name="rack_1", catalog_ref=_RACK_REF, parent_id="A1", site_index=0
)
_RACK_IN_D1 = DeckResourceConfig(
    name="rack_2", catalog_ref=_RACK_REF, parent_id="D1", site_index=0
)
_RACK_IN_D2 = DeckResourceConfig(
    name="rack_2", catalog_ref=_RACK_REF, parent_id="D2", site_index=0
)
_PLATE_IN_D2 = DeckResourceConfig(
    name="plate_2",
    catalog_ref="Cor_96_wellplate_360ul_Fb",
    parent_id="D2",
    site_index=0,
    well_state=LabwareWellState(
        volumes={f"{row}{column}": _PLATE_SEED_UL for column in range(1, 13) for row in "ABCDEFGH"}
    ),
)
_FRONT_ROW_PLATE = DeckResourceConfig(
    name="plate_2",
    catalog_ref="Cor_96_wellplate_360ul_Fb",
    parent_id="D3",
    site_index=0,
    well_state=LabwareWellState(
        volumes={f"{row}{column}": _PLATE_SEED_UL for column in range(1, 13) for row in "ABCDEFGH"}
    ),
)
_RACK_COLUMN_1_ONLY = DeckResourceConfig(
    name="rack_1",
    catalog_ref=_RACK_REF,
    parent_id="C1",
    site_index=0,
    well_state=LabwareWellState(
        tips={
            f"{row}{column}": column == 1
            for column in range(1, 13)
            for row in "ABCDEFGH"
        }
    ),
)
_PLATE = DeckResourceConfig(
    name="plate_1",
    catalog_ref="Cor_96_wellplate_360ul_Fb",
    parent_id="C2",
    site_index=0,
    well_state=LabwareWellState(
        volumes={f"{row}{column}": _PLATE_SEED_UL for column in range(1, 13) for row in "ABCDEFGH"}
    ),
)
_TROUGH = DeckResourceConfig(
    name="trough_1",
    catalog_ref="hamilton_1_trough_200ml_Vb",
    parent_id="C3",
    site_index=0,
    well_state=LabwareWellState(volumes={TROUGH_WELL_ID: _TROUGH_SEED_UL}),
)


@pytest.fixture(autouse=True)
def restore_tracking_globals() -> Iterator[None]:
    """configure_deck flips PLR's tracking globals on; put them back for the rest of the suite."""
    volume, tips = does_volume_tracking(), does_tip_tracking()
    yield
    set_volume_tracking(volume)
    set_tip_tracking(tips)


async def _bench(
    *pipettes: Tuple[str, int, float, float, str],
    gripper: bool = False,
    saved_position: dict[str, float] | None = None,
    rack: DeckResourceConfig = _RACK,
    extra: Sequence[DeckResourceConfig] = (),
    liquid_probe_z: float | None = None,
) -> Tuple[FlexLiquidHandlerDriver, ChatterboxTransport]:
    """A configured Flex carrying a tip rack, a seeded plate and a seeded trough."""
    transport = ChatterboxTransport(
        pipettes=list(pipettes) or [_EIGHT_CHANNEL],
        gripper=gripper,
        saved_position=saved_position,
        liquid_probe_z=liquid_probe_z,
    )
    driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)
    await driver.configure_deck(_FLEX_LAYOUT)
    await driver.initialize()
    await driver.reconcile_deck_occupancy(
        ReconcileDeckOccupancyRequest(resources=[rack, _PLATE, _TROUGH, *extra])
    )
    return driver, transport


def _flex_of(driver: FlexLiquidHandlerDriver) -> OpentronsFlex:
    flex = driver._flex
    assert flex is not None
    return flex


def _plate_of(driver: FlexLiquidHandlerDriver) -> Plate:
    plate = _flex_of(driver).deck.get_resource("plate_1")
    assert isinstance(plate, Plate)
    return plate


def _left_head_of(driver: FlexLiquidHandlerDriver) -> FlexHead8:
    head = _flex_of(driver).left
    assert isinstance(head, FlexHead8)
    return head


def _right_head_of(driver: FlexLiquidHandlerDriver) -> FlexHead8:
    head = _flex_of(driver).right
    assert isinstance(head, FlexHead8)
    return head


def _rack_of(driver: FlexLiquidHandlerDriver) -> TipRack:
    rack = _flex_of(driver).deck.get_resource("rack_1")
    assert isinstance(rack, TipRack)
    return rack


def _trough_of(driver: FlexLiquidHandlerDriver) -> Trough:
    trough = _flex_of(driver).deck.get_resource("trough_1")
    assert isinstance(trough, Trough)
    return trough


def _types(commands: list[dict]) -> list[str]:
    return [command["commandType"] for command in commands]


def _of_type(commands: list[dict], command_type: str) -> list[dict]:
    return [command for command in commands if command["commandType"] == command_type]


async def _pick_up_column(driver: FlexLiquidHandlerDriver) -> None:
    await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=_COLUMN_1)]))


async def _pick_up_one_tip(driver: FlexLiquidHandlerDriver, position: str) -> None:
    await driver.pick_up_tips(PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=[position])]))


async def _pick_up_column_on(
    driver: FlexLiquidHandlerDriver, channels: list[int], positions: list[str]
) -> None:
    """Pick a column with the mount that owns these channels."""
    await driver.pick_up_tips(
        PickUpTipsRequest(
            picks=[TipPick(tip_rack="rack_1", positions=positions)], use_channels=channels
        )
    )


async def _return_column(
    driver: FlexLiquidHandlerDriver, positions: list[str]
) -> LabwareStateResponse:
    return await driver.drop_tips(
        DropTipsRequest(to_waste=False, drops=[TipPick(tip_rack="rack_1", positions=positions)])
    )


def _column_aspirate(
    flow_rates: list[float] | None = None,
    offsets_z: list[float] | None = None,
    parameters: PipettingParameters | None = None,
) -> AspirateRequest:
    """A 20 uL aspirate of column 1, the shape most of these tests vary around."""
    return AspirateRequest(
        aspirations=[AspirateTarget(labware="plate_1", positions=_COLUMN_1, volumes=[20.0] * 8)],
        flow_rates=flow_rates,
        offsets_z=offsets_z,
        parameters=parameters or PipettingParameters(),
    )


class TestSliceToHeadMapping:
    @pytest.mark.asyncio
    async def test_a_full_column_is_one_command_on_the_eight_channel_head(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.aspirate(_column_aspirate())

        aspirates = _of_type(transport.commands[before:], "aspirate")
        assert len(aspirates) == 1
        assert aspirates[0]["params"]["wellName"] == "A1"
        assert aspirates[0]["params"]["volume"] == 20.0

    @pytest.mark.asyncio
    async def test_a_column_further_along_the_plate_anchors_on_its_own_a_row_well(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.aspirate(
            AspirateRequest(
                aspirations=[
                    AspirateTarget(
                        labware="plate_1",
                        positions=[f"{row}5" for row in "ABCDEFGH"],
                        volumes=[15.0] * 8,
                    )
                ]
            )
        )

        assert _of_type(transport.commands[before:], "aspirate")[0]["params"]["wellName"] == "A5"

    @pytest.mark.asyncio
    async def test_a_single_well_goes_to_the_single_channel_mount_when_one_is_there(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, _SINGLE_CHANNEL)
        await _pick_up_one_tip(driver, "A1")
        before = len(transport.commands)

        await driver.aspirate(
            AspirateRequest(
                aspirations=[AspirateTarget(labware="plate_1", positions=["B4"], volumes=[9.0])]
            )
        )

        right = _flex_of(driver).right
        assert right is not None
        aspirates = _of_type(transport.commands[before:], "aspirate")
        assert [c["params"]["wellName"] for c in aspirates] == ["B4"]
        assert aspirates[0]["params"]["pipetteId"] == right.pipette_id

    @pytest.mark.asyncio
    async def test_a_single_well_cherry_picks_one_nozzle_of_the_eight_channel_head(self) -> None:
        """With no single-channel mount, the 8-channel head drives one nozzle (SINGLE layout)
        rather than refusing the well outright. A ganged head can isolate only an end nozzle, so
        the anchor is A1, and the tip has to come off the front of a full column: the seven idle
        nozzles hang past the rack edge there instead of over spots that still hold tips."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = len(transport.commands)

        await _pick_up_one_tip(driver, "H1")
        await driver.aspirate(
            AspirateRequest(
                aspirations=[AspirateTarget(labware="plate_1", positions=["C5"], volumes=[7.0])]
            )
        )

        new = transport.commands[before:]
        layouts = _of_type(new, "configureNozzleLayout")
        assert layouts[0]["params"]["configurationParams"] == {
            "style": "SINGLE",
            "primaryNozzle": "A1",
        }
        assert _of_type(new, "aspirate")[0]["params"]["wellName"] == "C5"

    @pytest.mark.asyncio
    async def test_a_cherry_pick_is_addressed_by_the_anchor_channel(self) -> None:
        """The tip rides the anchor nozzle, so the channel that names a cherry-pick is the head's
        first one whatever row the well is in."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_one_tip(driver, "H1")
        before = len(transport.commands)

        await driver.aspirate(
            AspirateRequest(
                aspirations=[AspirateTarget(labware="plate_1", positions=["C5"], volumes=[5.0])],
                use_channels=[0],
            )
        )

        assert _of_type(transport.commands[before:], "aspirate")[0]["params"]["wellName"] == "C5"

    @pytest.mark.asyncio
    async def test_a_trough_slice_draws_the_whole_head_from_the_single_pool(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        response = await driver.aspirate(
            AspirateRequest(
                aspirations=[
                    AspirateTarget(labware="trough_1", positions=None, volumes=[10.0] * 8)
                ]
            )
        )

        aspirate = _of_type(transport.commands[before:], "aspirate")[0]
        assert aspirate["params"]["wellName"] == TROUGH_WELL_ID
        assert aspirate["params"]["volume"] == 10.0
        # One cavity, eight nozzles: the pool falls by the summed volume.
        assert response.labware_state["trough_1"].volumes == {
            TROUGH_WELL_ID: _TROUGH_SEED_UL - 80.0
        }

    @pytest.mark.asyncio
    async def test_use_channels_names_the_mount_that_runs_the_slice(self) -> None:
        """Channels are numbered left mount first, so 8-15 is the right mount's single channel."""
        driver, transport = await _bench(_EIGHT_CHANNEL, _SINGLE_CHANNEL)
        await _pick_up_one_tip(driver, "A1")
        before = len(transport.commands)

        await driver.aspirate(
            AspirateRequest(
                aspirations=[AspirateTarget(labware="plate_1", positions=["A2"], volumes=[5.0])],
                use_channels=[8],
            )
        )

        right = _flex_of(driver).right
        assert right is not None
        assert _of_type(transport.commands[before:], "aspirate")[0]["params"]["pipetteId"] == (
            right.pipette_id
        )


class TestRefusedShapes:
    """Every refusal must land before the wire, so a rejected op moves no hardware."""

    @pytest.mark.asyncio
    async def test_a_partial_column_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="no mounted head pipettes 3 positions"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(
                            labware="plate_1", positions=["A1", "C1", "E1"], volumes=[5.0] * 3
                        )
                    ]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_eight_positions_that_are_not_one_column_are_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)
        scattered = ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "A2"]

        with pytest.raises(ValueError, match="one column of 'plate_1' in row order"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(labware="plate_1", positions=scattered, volumes=[5.0] * 8)
                    ]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_volumes_that_differ_across_a_column_are_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="one plunger"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(
                            labware="plate_1",
                            positions=_COLUMN_1,
                            volumes=[10.0, 20.0] + [10.0] * 6,
                        )
                    ]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_more_positions_than_the_head_has_nozzles_are_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)
        twelve = [f"A{column}" for column in range(1, 13)]

        with pytest.raises(ValueError, match="no mounted head pipettes 12 positions"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(labware="plate_1", positions=twelve, volumes=[5.0] * 12)
                    ]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_ninety_six_positions_are_pointed_at_the_ninety_six_head_surface(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)
        every_well = [f"{row}{column}" for column in range(1, 13) for row in "ABCDEFGH"]

        with pytest.raises(ValueError, match="aspirate96"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(
                            labware="plate_1", positions=every_well, volumes=[5.0] * 96
                        )
                    ]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_slices_spanning_two_labware_are_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="one Flex command names one labware"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(labware="plate_1", positions=["A1"], volumes=[5.0]),
                        AspirateTarget(labware="trough_1", positions=None, volumes=[5.0]),
                    ]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_use_channels_spanning_two_mounts_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, _SINGLE_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="does not sit on one mounted head"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(
                            labware="plate_1", positions=_COLUMN_1, volumes=[5.0] * 8
                        )
                    ],
                    use_channels=[1, 2, 3, 4, 5, 6, 7, 8],
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_use_channels_naming_a_nozzle_the_head_lacks_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="does not sit on one mounted head"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(labware="trough_1", positions=None, volumes=[5.0] * 8)
                    ],
                    use_channels=[0, 1, 2, 3, 4, 5, 6, 9],
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_use_channels_naming_the_ninety_six_head_is_pointed_at_its_own_ops(self) -> None:
        driver, transport = await _bench(_NINETY_SIX)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="names the 96 head"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(labware="plate_1", positions=_COLUMN_1, volumes=[5.0] * 8)
                    ],
                    use_channels=list(range(8)),
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_an_eight_channel_slice_on_a_ninety_six_only_robot_is_refused(self) -> None:
        driver, transport = await _bench(_NINETY_SIX)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="no mounted head pipettes 8 positions"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(labware="plate_1", positions=_COLUMN_1, volumes=[5.0] * 8)
                    ]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_use_channels_naming_a_nozzle_the_head_cannot_isolate_is_refused(self) -> None:
        """A single-tip op runs on an END nozzle of the head, so naming the channel that matches
        the well's row asks for a nozzle the head cannot drive alone."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="'C5' is channel 0"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[AspirateTarget(labware="plate_1", positions=["C5"], volumes=[5.0])],
                    use_channels=[2],
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_container_slice_that_does_not_drive_the_whole_head_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="drives the whole head"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[AspirateTarget(labware="trough_1", positions=None, volumes=[5.0])]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_positions_on_a_trough_are_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(TypeError, match="single-cavity"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(labware="trough_1", positions=_COLUMN_1, volumes=[5.0] * 8)
                    ]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_plate_addressed_as_one_pool_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(TypeError, match="itemized"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[AspirateTarget(labware="plate_1", positions=None, volumes=[5.0] * 8)]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_use_channels_that_does_not_cover_every_position_is_refused(self) -> None:
        """Nothing upstream checks the list against the slice, and without this guard the
        head would drive all 8 nozzles for a request that named 3."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="names 3 channels but the request carries 8"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(labware="plate_1", positions=_COLUMN_1, volumes=[5.0] * 8)
                    ],
                    use_channels=[0, 1, 2],
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_use_channels_that_repeats_a_channel_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="repeats a channel"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(labware="plate_1", positions=_COLUMN_1, volumes=[5.0] * 8)
                    ],
                    use_channels=[0, 0, 1, 2, 3, 4, 5, 6],
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_channel_named_partial_column_is_refused(self) -> None:
        """Naming the channels does not make a 3-well slice expressible: the head pipettes
        one well or all eight."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="cannot serve 3 positions"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(
                            labware="plate_1", positions=["A1", "B1", "C1"], volumes=[5.0] * 3
                        )
                    ],
                    use_channels=[0, 1, 2],
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_position_that_is_not_a_well_id_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="has no well 'Z1'"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(labware="plate_1", positions=["Z1"], volumes=[5.0])
                    ],
                    use_channels=[0],
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_position_that_is_not_a_well_id_is_refused_without_use_channels(self) -> None:
        """The position is read whether or not the caller names a channel, so a mistyped well is
        refused as one rather than as whatever the head happens to be holding."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="has no well 'Z1'"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(labware="plate_1", positions=["Z1"], volumes=[5.0])
                    ]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize("operation", ["aspirate", "dispense", "mix"])
    async def test_a_liquid_op_with_no_tips_mounted_is_refused(self, operation: str) -> None:
        """The robot moves the nozzles to the well before it checks for a tip, so a bare-nozzle
        op is a doomed trip into the labware. It is refused before any wire command."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)
        requests = {
            "aspirate": lambda: driver.aspirate(_column_aspirate()),
            "dispense": lambda: driver.dispense(
                DispenseRequest(
                    dispenses=[
                        DispenseTarget(labware="plate_1", positions=_COLUMN_1, volumes=[20.0] * 8)
                    ]
                )
            ),
            "mix": lambda: driver.mix(
                MixRequest(labware="plate_1", positions=_COLUMN_1, volume=10.0, repetitions=2)
            ),
        }

        with pytest.raises(ValueError, match="holds no tips"):
            await requests[operation]()
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_move_plate_without_a_gripper_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="needs the Flex gripper"):
            await driver.move_plate(MovePlateRequest(plate="plate_1", to_position="D1-slot"))
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_carrier_style_destination_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="'<slot>-slot'"):
            await driver.move_plate(MovePlateRequest(plate="plate_1", to_position="carrier_1-2"))
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_an_op_on_a_bare_driver_names_what_it_actually_needs(self) -> None:
        """Ops that address deck contents need a deck. Channel motion needs a
        mounted head, which is initialize, so pointing it at configure_deck would
        send the caller to the wrong step."""
        transport = ChatterboxTransport(pipettes=[_EIGHT_CHANNEL], gripper=True)
        driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)

        with pytest.raises(RuntimeError, match="configure_deck"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[AspirateTarget(labware="plate_1", positions=["A1"], volumes=[5.0])]
                )
            )
        with pytest.raises(RuntimeError, match="configure_deck"):
            await driver.move_plate(MovePlateRequest(plate="plate_1", to_position="D1-slot"))
        with pytest.raises(ValueError, match="no pipette is mounted"):
            await driver.move_channel_to(MoveChannelToRequest(channel=0, z=1.0))
        assert transport.commands == []

    @pytest.mark.asyncio
    async def test_the_ninety_six_ops_refuse_a_robot_without_that_head(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="needs a 96-channel head"):
            await driver.pick_up_tips96(PickUpTips96Request(tip_rack="rack_1"))
        assert transport.commands == before


class TestParameterThreading:
    @pytest.mark.asyncio
    async def test_flow_rate_and_z_offset_ride_the_aspirate_command(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.aspirate(_column_aspirate(flow_rates=[42.0] * 8, offsets_z=[2.5] * 8))

        params = _of_type(transport.commands[before:], "aspirate")[0]["params"]
        assert params["flowRate"] == 42.0
        assert params["wellLocation"] == {"origin": "bottom", "offset": {"x": 0, "y": 0, "z": 2.5}}

    @pytest.mark.asyncio
    async def test_offsets_z_is_the_height_itself_not_a_shift_off_the_default_clearance(
        self,
    ) -> None:
        """offsets_z states where the tip end goes above the well floor, so 0.0 means the floor
        and nothing is added to it. Omitting it leaves the robot's own bottom clearance."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)

        before = len(transport.commands)
        await driver.aspirate(_column_aspirate(offsets_z=[0.0] * 8))
        asked_for_the_floor = _of_type(transport.commands[before:], "aspirate")[0]["params"]

        before = len(transport.commands)
        await driver.aspirate(_column_aspirate())
        asked_for_nothing = _of_type(transport.commands[before:], "aspirate")[0]["params"]

        assert asked_for_the_floor["wellLocation"]["offset"]["z"] == 0.0
        assert asked_for_nothing["wellLocation"]["offset"]["z"] > 0.0

    @pytest.mark.asyncio
    async def test_the_resolved_rate_and_height_reach_the_wire(self) -> None:
        """The layers are folded before the wire, so the driver reads numbers
        rather than deciding between a liquid class and a technique."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.aspirate(
            _column_aspirate(parameters=PipettingParameters(flow_rate=12.0, height=3.0))
        )

        params = _of_type(transport.commands[before:], "aspirate")[0]["params"]
        assert params["flowRate"] == 12.0
        assert params["wellLocation"]["offset"]["z"] == 3.0

    @pytest.mark.asyncio
    async def test_a_dispense_reads_the_record_it_was_given(self) -> None:
        """A dispense carries its own resolved record. Direction lives in which
        profile the caller resolved with, not in halves of one object here."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.dispense(
            DispenseRequest(
                dispenses=[
                    DispenseTarget(labware="plate_1", positions=_COLUMN_1, volumes=[20.0] * 8)
                ],
                parameters=PipettingParameters(flow_rate=33.0, height=4.0),
            )
        )

        params = _of_type(transport.commands[before:], "dispense")[0]["params"]
        assert params["flowRate"] == 33.0
        assert params["wellLocation"]["offset"]["z"] == 4.0

    @pytest.mark.asyncio
    async def test_an_unnamed_height_pipettes_at_the_default_clearance(self) -> None:
        """Nobody naming a height gets DEFAULT_PIPETTING_HEIGHT_MM, not the robot's own 1 mm,
        which sits on the floor of anything deeper than a well."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.aspirate(_column_aspirate())
        await driver.dispense(
            DispenseRequest(
                dispenses=[
                    DispenseTarget(labware="plate_1", positions=_COLUMN_1, volumes=[20.0] * 8)
                ]
            )
        )

        sent = transport.commands[before:]
        for command_type in ("aspirate", "dispense"):
            params = _of_type(sent, command_type)[0]["params"]
            assert params["wellLocation"]["offset"]["z"] == DEFAULT_PIPETTING_HEIGHT_MM

    @pytest.mark.asyncio
    async def test_a_cherry_picked_nozzle_takes_a_pipetting_height(self) -> None:
        """One nozzle of the 8-channel head pipettes at the height it was given, same as a
        whole column does."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_one_tip(driver, "H1")
        before = len(transport.commands)

        await driver.aspirate(
            AspirateRequest(
                aspirations=[AspirateTarget(labware="plate_1", positions=["A5"], volumes=[5.0])],
                offsets_z=[2.0],
            )
        )

        params = _of_type(transport.commands[before:], "aspirate")[0]["params"]
        assert params["wellLocation"]["offset"]["z"] == 2.0

    @pytest.mark.asyncio
    async def test_per_channel_offsets_z_outrank_the_resolved_height(self) -> None:
        """A per-channel list is narrower than the record resolved for the step,
        so it is the one the wire carries."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.aspirate(
            _column_aspirate(
                offsets_z=[9.0] * 8, parameters=PipettingParameters(height=6.5)
            )
        )

        params = _of_type(transport.commands[before:], "aspirate")[0]["params"]
        assert params["wellLocation"]["offset"]["z"] == 9.0

    @pytest.mark.asyncio
    async def test_a_flow_rate_that_differs_across_channels_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="one flow rate"):
            await driver.aspirate(_column_aspirate(flow_rates=[10.0] * 7 + [20.0]))
        assert transport.commands == before


class TestMixAndBlowOut:
    @pytest.mark.asyncio
    async def test_a_mix_is_aspirate_dispense_cycles_in_place(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.mix(
            MixRequest(labware="plate_1", positions=_COLUMN_1, volume=15.0, repetitions=3)
        )

        new = transport.commands[before:]
        liquid_ops = [c for c in new if c["commandType"] in ("aspirate", "dispense")]
        assert _types(liquid_ops) == ["aspirate", "dispense"] * 3
        assert {c["params"]["wellName"] for c in liquid_ops} == {"A1"}
        assert {c["params"]["volume"] for c in liquid_ops} == {15.0}

    @pytest.mark.asyncio
    async def test_a_trough_mix_takes_its_channel_count_from_use_channels(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.mix(
            MixRequest(
                labware="trough_1",
                positions=None,
                volume=10.0,
                repetitions=2,
                use_channels=list(range(8)),
            )
        )

        liquid_ops = [
            c for c in transport.commands[before:] if c["commandType"] in ("aspirate", "dispense")
        ]
        assert _types(liquid_ops) == ["aspirate", "dispense"] * 2
        # Each cycle draws and returns the same 8 x 10 uL, so the pool ends where it started.
        assert _trough_of(driver).tracker.get_used_volume() == _TROUGH_SEED_UL

    @pytest.mark.asyncio
    async def test_mix_before_runs_its_cycles_ahead_of_the_aspirate(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.aspirate(
            _column_aspirate(
                parameters=PipettingParameters(
                    mix=MixParamsModel(volume=5.0, repetitions=2)
                )
            )
        )

        liquid_ops = [
            c for c in transport.commands[before:] if c["commandType"] in ("aspirate", "dispense")
        ]
        assert _types(liquid_ops) == ["aspirate", "dispense", "aspirate", "dispense", "aspirate"]
        assert [c["params"]["volume"] for c in liquid_ops] == [5.0, 5.0, 5.0, 5.0, 20.0]

    @pytest.mark.asyncio
    async def test_mix_after_and_blow_out_follow_the_dispense(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.dispense(
            DispenseRequest(
                dispenses=[
                    DispenseTarget(labware="plate_1", positions=_COLUMN_1, volumes=[20.0] * 8)
                ],
                parameters=PipettingParameters(
                    mix=MixParamsModel(volume=5.0, repetitions=1),
                    blow_out=True,
                    blow_out_flow_rate=17.0,
                ),
            )
        )

        new = transport.commands[before:]
        sequence = [
            c
            for c in new
            if c["commandType"] in ("aspirate", "dispense", "blowOutInPlace")
        ]
        assert _types(sequence) == ["dispense", "aspirate", "dispense", "blowOutInPlace"]
        assert sequence[-1]["params"]["flowRate"] == 17.0

    @pytest.mark.asyncio
    async def test_a_named_air_volume_still_blows_out_here(self) -> None:
        """The Flex drives its plunger to one blow-out position, so a named air volume
        has no wire field and goes unread.

        It is not refused. A head that expels air the tip took in needs the volume
        named, so refusing it here would leave no blow-out setting that one profile
        could carry to both machines."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.dispense(
            DispenseRequest(
                dispenses=[
                    DispenseTarget(labware="plate_1", positions=_COLUMN_1, volumes=[20.0] * 8)
                ],
                parameters=PipettingParameters(
                    blow_out=True, blow_out_volume=10.0, blow_out_flow_rate=17.0
                ),
            )
        )

        blow_outs = [c for c in transport.commands[before:] if c["commandType"] == "blowOutInPlace"]
        assert len(blow_outs) == 1
        assert blow_outs[0]["params"]["flowRate"] == 17.0
        assert "volume" not in blow_outs[0]["params"]


class TestTips:
    @pytest.mark.asyncio
    async def test_a_column_pickup_clears_only_that_columns_spots(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = len(transport.commands)

        response = await driver.pick_up_tips(
            PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=_COLUMN_1)])
        )

        pickups = _of_type(transport.commands[before:], "pickUpTip")
        assert [c["params"]["wellName"] for c in pickups] == ["A1"]
        tips = response.labware_state["rack_1"].tips
        assert tips is not None
        assert [tips[position] for position in _COLUMN_1] == [False] * 8
        assert tips["A2"] is True

    @pytest.mark.asyncio
    async def test_a_single_pickup_anchors_on_a1_whatever_row_the_spot_is_in(self) -> None:
        """The robot can isolate only an end nozzle, so a spot in row F is reached by carrying the
        A1 nozzle to it, not by driving the F nozzle. G3 and H3 are already gone here, which is
        what puts the seven idle nozzles over empty ground and makes row F reachable at all."""
        driver, transport = await _bench(_EIGHT_CHANNEL, rack=_RACK_COLUMN_3_FRONT_TWO_GONE)
        before = len(transport.commands)

        await _pick_up_one_tip(driver, "F3")

        new = transport.commands[before:]
        assert _of_type(new, "configureNozzleLayout")[0]["params"]["configurationParams"] == {
            "style": "SINGLE",
            "primaryNozzle": "A1",
        }
        assert _of_type(new, "pickUpTip")[0]["params"]["wellName"] == "F3"
        state = await driver.get_deck_state(GetDeckStateRequest())
        assert state.tips_mounted == [True] + [False] * 7

    @pytest.mark.asyncio
    async def test_a_single_channel_mount_picks_its_one_spot(self) -> None:
        driver, transport = await _bench(_SINGLE_CHANNEL)
        before = len(transport.commands)

        await _pick_up_one_tip(driver, "D2")

        new = transport.commands[before:]
        assert _of_type(new, "configureNozzleLayout") == []
        assert _of_type(new, "pickUpTip")[0]["params"]["wellName"] == "D2"

    @pytest.mark.asyncio
    async def test_dropping_a_column_back_restores_the_racks_spots(self) -> None:
        driver, _ = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)

        response = await driver.drop_tips(
            DropTipsRequest(
                to_waste=False, drops=[TipPick(tip_rack="rack_1", positions=_COLUMN_1)]
            )
        )

        tips = response.labware_state["rack_1"].tips
        assert tips is not None and all(tips.values())

    @pytest.mark.asyncio
    async def test_a_waste_drop_uses_the_trash_sequence_and_keeps_the_rack_empty(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        response = await driver.drop_tips(DropTipsRequest(to_waste=True))

        # The head confirms the tips actually left, and it asks with a run command: reading the
        # sensor over GET /instruments re-caches the pipettes and wipes the run's attached tip.
        assert _types(transport.commands[before:]) == [
            "moveToAddressableAreaForDropTip",
            "dropTipInPlace",
            "getTipPresence",
        ]
        tips = response.labware_state["rack_1"].tips
        assert tips is not None
        assert [tips[position] for position in _COLUMN_1] == [False] * 8

    @pytest.mark.asyncio
    async def test_discarding_a_cherry_picked_tip_restores_the_all_nozzle_layout(self) -> None:
        """A single mounted tip means the head is in SINGLE mode, and only the single-tip drop
        puts the layout back for the next column op."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_one_tip(driver, "H1")
        before = len(transport.commands)

        await driver.discard_tips(DiscardTipsRequest())

        new = transport.commands[before:]
        assert _types(new)[:2] == ["moveToAddressableAreaForDropTip", "dropTipInPlace"]
        assert _of_type(new, "configureNozzleLayout")[0]["params"]["configurationParams"] == {
            "style": "ALL"
        }

    @pytest.mark.asyncio
    async def test_returning_one_tip_to_a_rack_is_refused_on_the_eight_channel_head(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_one_tip(driver, "H1")
        before = list(transport.commands)

        with pytest.raises(ValueError, match="returns a whole column to a rack"):
            await driver.drop_tips(
                DropTipsRequest(to_waste=False, drops=[TipPick(tip_rack="rack_1", positions=["H1"])])
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_dropping_with_no_tips_mounted_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="nothing to drop"):
            await driver.drop_tips(DropTipsRequest(to_waste=True))
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_pickup_spanning_two_racks_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="one Flex command names one tip rack"):
            await driver.pick_up_tips(
                PickUpTipsRequest(
                    picks=[
                        TipPick(tip_rack="rack_1", positions=_COLUMN_1),
                        TipPick(tip_rack="rack_2", positions=_COLUMN_1),
                    ]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_pickup_naming_labware_that_is_not_a_rack_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(TypeError, match="not a tip rack"):
            await driver.pick_up_tips(
                PickUpTipsRequest(picks=[TipPick(tip_rack="plate_1", positions=_COLUMN_1)])
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_returning_tips_with_none_mounted_is_refused(self) -> None:
        """Distinct from the waste-drop refusal: this one reaches the rack path first."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="holds no tips"):
            await driver.drop_tips(
                DropTipsRequest(
                    to_waste=False, drops=[TipPick(tip_rack="rack_1", positions=_COLUMN_1)]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_discarding_some_of_a_heads_tips_is_refused_rather_than_discarding_all(
        self,
    ) -> None:
        """One ejector stroke clears every nozzle, so serving a three-channel request would
        throw away five tips the caller never named."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="drops every tip the head holds at once"):
            await driver.discard_tips(DiscardTipsRequest(use_channels=[3]))

        assert transport.commands == before
        assert all(tip is not None for tip in _left_head_of(driver).get_mounted_tips())

    @pytest.mark.asyncio
    async def test_discarding_every_channel_the_head_holds_is_served(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.discard_tips(DiscardTipsRequest(use_channels=list(range(8))))

        assert _types(transport.commands[before:])[:2] == [
            "moveToAddressableAreaForDropTip",
            "dropTipInPlace",
        ]
        assert all(tip is None for tip in _left_head_of(driver).get_mounted_tips())


class TestDenserThanEightRowPlates:
    """A 384-well plate has rows A-P, and the 8 nozzles sit at a 9 mm pitch, so they cover
    every SECOND row of it: one physical column holds two interleaved sets of eight. Wells
    and column sets are read off the plate's own grid, not a fixed A-H alphabet."""

    _PLATE_384 = DeckResourceConfig(
        name="plate_384",
        catalog_ref="BioRad_384_wellplate_50uL_Vb",
        parent_id="B2",
        site_index=0,
        well_state=LabwareWellState(
            volumes={
                f"{row}{column}": _PLATE_SEED_UL
                for column in range(1, 25)
                for row in "ABCDEFGHIJKLMNOP"
            }
        ),
    )
    _REAR_SET = [f"{row}1" for row in "ACEGIKMO"]
    _FRONT_SET = [f"{row}1" for row in "BDFHJLNP"]

    @pytest.mark.asyncio
    async def test_a_well_below_row_h_is_addressable_on_a_single_channel_mount(self) -> None:
        driver, transport = await _bench(
            _EIGHT_CHANNEL, _SINGLE_CHANNEL, extra=[self._PLATE_384]
        )
        await _pick_up_one_tip(driver, "A1")
        before = len(transport.commands)

        await driver.aspirate(
            AspirateRequest(
                aspirations=[AspirateTarget(labware="plate_384", positions=["I5"], volumes=[5.0])],
                use_channels=[8],
            )
        )

        aspirates = _of_type(transport.commands[before:], "aspirate")
        assert [command["params"]["wellName"] for command in aspirates] == ["I5"]

    @pytest.mark.asyncio
    async def test_the_rear_row_set_is_one_column_stroke(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, extra=[self._PLATE_384])
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.aspirate(
            AspirateRequest(
                aspirations=[
                    AspirateTarget(labware="plate_384", positions=self._REAR_SET, volumes=[5.0] * 8)
                ]
            )
        )

        aspirates = _of_type(transport.commands[before:], "aspirate")
        assert [command["params"]["wellName"] for command in aspirates] == ["A1"]

    @pytest.mark.asyncio
    async def test_the_interleaved_row_set_anchors_on_its_own_first_row(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, extra=[self._PLATE_384])
        await _pick_up_column(driver)
        before = len(transport.commands)

        await driver.aspirate(
            AspirateRequest(
                aspirations=[
                    AspirateTarget(labware="plate_384", positions=self._FRONT_SET, volumes=[5.0] * 8)
                ]
            )
        )

        aspirates = _of_type(transport.commands[before:], "aspirate")
        assert [command["params"]["wellName"] for command in aspirates] == ["B1"]

    @pytest.mark.asyncio
    async def test_eight_adjacent_rows_are_refused_because_the_nozzles_skip_one(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, extra=[self._PLATE_384])
        await _pick_up_column(driver)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="ACEGIKMO or BDFHJLNP"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(
                            labware="plate_384", positions=_COLUMN_1, volumes=[5.0] * 8
                        )
                    ]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_well_the_plate_does_not_have_is_refused_pre_wire(self) -> None:
        driver, transport = await _bench(
            _EIGHT_CHANNEL, _SINGLE_CHANNEL, extra=[self._PLATE_384]
        )
        await _pick_up_one_tip(driver, "A1")
        before = list(transport.commands)

        with pytest.raises(ValueError, match="no well 'Q1'"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[
                        AspirateTarget(labware="plate_384", positions=["Q1"], volumes=[5.0])
                    ],
                    use_channels=[8],
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_ninety_six_plate_still_refuses_a_row_it_does_not_have(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, _SINGLE_CHANNEL)
        await _pick_up_one_tip(driver, "A1")
        before = list(transport.commands)

        with pytest.raises(ValueError, match="no well 'I5'"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[AspirateTarget(labware="plate_1", positions=["I5"], volumes=[5.0])],
                    use_channels=[8],
                )
            )
        assert transport.commands == before


class TestCherryPickAnchor:
    """A single-tip op drives one END nozzle of the ganged head, and which end it is decides
    where the idle seven hang for the rest of that tip's life. So the caller names it, via
    use_channels, and an unnamed one is the rear nozzle rather than whichever end happens to
    fit. The driver records the channel the head actually used rather than assuming one."""

    _FRONT_ROW_RACK = DeckResourceConfig(
        name="rack_1",
        catalog_ref=_RACK_REF,
        parent_id="D1",
        site_index=0,
        well_state=LabwareWellState(tips={f"{row}1": False for row in "ABCDE"}),
    )

    @pytest.mark.asyncio
    async def test_a_back_slot_pick_rides_the_rear_nozzles_channel(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)

        await _pick_up_one_tip(driver, "H5")

        layouts = _of_type(transport.commands, "configureNozzleLayout")
        assert layouts[-1]["params"]["configurationParams"]["primaryNozzle"] == "A1"
        assert _left_head_of(driver).get_mounted_tips()[0] is not None

    @pytest.mark.asyncio
    async def test_a_front_row_pick_is_refused_until_the_front_channel_is_named(self) -> None:
        # The default rear anchor puts the pipette body past the robot's front limit here.
        # Silently swapping to the other end would move the idle seven to the far side.
        # Rows A-E of this column are already empty, so the front anchor's idle nozzles,
        # which hang behind it, have nothing under them.
        driver, transport = await _bench(_EIGHT_CHANNEL, rack=self._FRONT_ROW_RACK)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="outside the robot"):
            await _pick_up_one_tip(driver, "F1")
        assert transport.commands == before

        await _pick_up_column_on(driver, [7], ["F1"])

        layouts = _of_type(transport.commands, "configureNozzleLayout")
        assert layouts[-1]["params"]["configurationParams"]["primaryNozzle"] == "H1"
        assert _left_head_of(driver).get_mounted_tips()[7] is not None

    @pytest.mark.asyncio
    async def test_the_recorded_spot_follows_the_channel_the_head_used(self) -> None:
        """A return reads this record to find the rack, so a wrong channel loses the tip."""
        driver, _ = await _bench(_EIGHT_CHANNEL, rack=self._FRONT_ROW_RACK)

        await _pick_up_column_on(driver, [7], ["F1"])

        carried = {held.channel: held.position for held in driver._mounted_tips}
        assert carried == {7: "F1"}

    @pytest.mark.asyncio
    async def test_naming_the_front_channel_is_accepted(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, rack=self._FRONT_ROW_RACK)

        await driver.pick_up_tips(
            PickUpTipsRequest(
                picks=[TipPick(tip_rack="rack_1", positions=["F1"])], use_channels=[7]
            )
        )

        layouts = _of_type(transport.commands, "configureNozzleLayout")
        assert layouts[-1]["params"]["configurationParams"]["primaryNozzle"] == "H1"
        assert _left_head_of(driver).get_mounted_tips()[7] is not None

    @pytest.mark.asyncio
    async def test_naming_a_channel_that_cannot_reach_the_slot_is_refused_pre_wire(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, rack=self._FRONT_ROW_RACK)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="outside the robot's reach"):
            await driver.pick_up_tips(
                PickUpTipsRequest(
                    picks=[TipPick(tip_rack="rack_1", positions=["F1"])], use_channels=[0]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_naming_a_middle_channel_is_refused_naming_both_anchors(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match=r"is channel 0, 7"):
            await driver.pick_up_tips(
                PickUpTipsRequest(
                    picks=[TipPick(tip_rack="rack_1", positions=["C5"])], use_channels=[3]
                )
            )
        assert transport.commands == before



class TestGangedSingleTipReach:
    """Which single spots an 8-channel head can take, and why.

    The eight nozzles sit on a fixed 9 mm pitch and descend together, so a "single" pickup is
    only single because the other seven have nothing under them. That makes the reachable set
    a function of what the rack still holds: the rear (A1) anchor hangs its idle seven toward
    the FRONT of the column, so it can only take a spot with empty ground in front of it; the
    front (H1) anchor hangs them toward the BACK. A column is therefore consumed from one end
    inward, not in arbitrary order. A single-channel pipette has none of this and picks freely.
    """

    @pytest.mark.asyncio
    async def test_one_nozzle_takes_the_mid_column_spot_eight_ganged_ones_cannot(self) -> None:
        """The same request against the two head shapes, which is the whole point: a
        one-nozzle pipette has nothing hanging off it, so D1 is an ordinary pick and no
        layout command is needed. The 8-channel head refuses the identical request."""
        driver, transport = await _bench(_SINGLE_CHANNEL)
        before = len(transport.commands)

        await _pick_up_one_tip(driver, "D1")

        new = transport.commands[before:]
        assert _of_type(new, "configureNozzleLayout") == []
        assert _of_type(new, "pickUpTip")[0]["params"]["wellName"] == "D1"

        ganged, ganged_transport = await _bench(_EIGHT_CHANNEL)
        before = list(ganged_transport.commands)
        with pytest.raises(ValueError, match="ganged and descend together"):
            await _pick_up_one_tip(ganged, "D1")
        assert ganged_transport.commands == before

    @pytest.mark.asyncio
    async def test_a_full_column_yields_only_its_front_spot_to_the_rear_anchor(self) -> None:
        """H1 hangs the idle seven past the front edge of the rack. One row back, they sit over
        B1 through H1, which still hold tips, so that pick would strip the column."""
        driver, transport = await _bench(_EIGHT_CHANNEL)

        await _pick_up_one_tip(driver, "H1")
        assert _of_type(transport.commands, "pickUpTip")[-1]["params"]["wellName"] == "H1"

        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)
        with pytest.raises(ValueError, match="ganged and descend together"):
            await _pick_up_one_tip(driver, "G1")
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_full_column_yields_only_its_back_spot_to_the_front_anchor(self) -> None:
        """The mirror image: naming channel 7 anchors on H1, whose idle seven hang behind it."""
        driver, transport = await _bench(_EIGHT_CHANNEL)

        await _pick_up_column_on(driver, [7], ["A1"])
        assert _of_type(transport.commands, "pickUpTip")[-1]["params"]["wellName"] == "A1"

        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)
        with pytest.raises(ValueError, match="ganged and descend together"):
            await _pick_up_column_on(driver, [7], ["B1"])
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_refusal_points_at_the_end_that_would_have_worked(self) -> None:
        """An operator reading this needs to know the other anchor is open, not just that this
        one is not. The head never swaps ends on its own: that would move the idle seven to
        the far side of the tip for the rest of its life."""
        driver, _ = await _bench(_EIGHT_CHANNEL)

        with pytest.raises(ValueError, match="Anchor on H1 to hang them off the opposite edge"):
            await _pick_up_one_tip(driver, "A1")

    @pytest.mark.asyncio
    async def test_a_column_is_cherry_picked_from_the_front_inward(self) -> None:
        """The working single-tip pattern on a ganged head: each pick empties the ground the
        next one's idle nozzles need. H1, then G1, then F1, all on the rear anchor."""
        driver, transport = await _bench(_EIGHT_CHANNEL)

        for spot in ("H1", "G1", "F1"):
            await _pick_up_one_tip(driver, spot)
            await driver.discard_tips(DiscardTipsRequest())

        assert [c["params"]["wellName"] for c in _of_type(transport.commands, "pickUpTip")] == [
            "H1",
            "G1",
            "F1",
        ]

    @pytest.mark.asyncio
    async def test_a_column_is_cherry_picked_from_the_back_inward_on_the_front_anchor(
        self,
    ) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)

        for spot in ("A1", "B1", "C1"):
            await _pick_up_column_on(driver, [7], [spot])
            await driver.discard_tips(DiscardTipsRequest())

        layouts = _of_type(transport.commands, "configureNozzleLayout")
        assert layouts[0]["params"]["configurationParams"]["primaryNozzle"] == "H1"
        assert [c["params"]["wellName"] for c in _of_type(transport.commands, "pickUpTip")] == [
            "A1",
            "B1",
            "C1",
        ]

    @pytest.mark.asyncio
    async def test_a_gap_in_the_middle_of_a_column_frees_no_spot_beside_it(self) -> None:
        """D1 is empty here, and it changes nothing: an anchor on C1 still leaves six idle
        nozzles over E1 through H1. Clearance is about all seven idle positions, not the
        neighbour. This is the case that looks pickable and is not."""
        for spot in ("C1", "E1"):
            driver, transport = await _bench(_EIGHT_CHANNEL, rack=_RACK_COLUMN_1_MIDDLE_GAP)
            before = list(transport.commands)

            with pytest.raises(ValueError, match="ganged and descend together"):
                await _pick_up_one_tip(driver, spot)
            assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_refusal_says_so_plainly_when_neither_end_would_have_worked(self) -> None:
        """Steering the operator to the other anchor is wrong when that one is blocked too.
        The fallback names the two ways out: clear the neighbours, or take the column."""
        driver, _ = await _bench(_EIGHT_CHANNEL, rack=_RACK_COLUMN_1_MIDDLE_GAP)

        with pytest.raises(ValueError, match="or use pick_up_tips for the whole column"):
            await _pick_up_one_tip(driver, "C1")

    @pytest.mark.asyncio
    async def test_a_rack_on_the_back_row_can_only_be_cherry_picked_from_one_end(self) -> None:
        """Reach narrows the rule further at the deck edges. On a back-row slot the front
        anchor cannot reach the rack at all, so a column there is consumed front-to-back
        only: there is no mirror-image route, whatever the tip state says."""
        driver, _ = await _bench(_EIGHT_CHANNEL, rack=_RACK_BACK_ROW)
        await _pick_up_one_tip(driver, "H1")

        driver, _ = await _bench(_EIGHT_CHANNEL, rack=_RACK_BACK_ROW)
        with pytest.raises(ValueError, match="outside the robot's reach"):
            await _pick_up_column_on(driver, [7], ["A1"])

    @pytest.mark.asyncio
    async def test_a_carried_tip_is_refused_at_a_well_its_anchor_cannot_reach(self) -> None:
        """The pickup only proved the anchor reached the RACK. A front-row plate can still
        sit outside that same anchor's band, and the head cannot swap ends mid-tip."""
        driver, _ = await _bench(_EIGHT_CHANNEL, extra=[_FRONT_ROW_PLATE])
        await _pick_up_one_tip(driver, "H1")

        await driver.aspirate(
            AspirateRequest(
                aspirations=[AspirateTarget(labware="plate_2", positions=["A5"], volumes=[5.0])]
            )
        )
        with pytest.raises(ValueError, match="outside the robot's reach"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[AspirateTarget(labware="plate_2", positions=["H5"], volumes=[5.0])]
                )
            )

    @pytest.mark.asyncio
    async def test_the_ganged_rule_needs_the_tip_tracking_configure_deck_turns_on(self) -> None:
        """Clearance reads the rack's tip trackers, so with tracking off every position
        reads as full forever and the guard cannot run. Nothing else pins that a
        configured deck leaves tracking on, which is the rule's precondition."""
        await _bench(_EIGHT_CHANNEL)

        assert does_tip_tracking()

    @pytest.mark.asyncio
    async def test_a_gap_in_the_middle_leaves_both_ends_of_the_column_pickable(self) -> None:
        """The ends stay open because their idle nozzles hang off the rack, not because of
        anything the gap did."""
        driver, transport = await _bench(_EIGHT_CHANNEL, rack=_RACK_COLUMN_1_MIDDLE_GAP)
        await _pick_up_one_tip(driver, "H1")
        assert _of_type(transport.commands, "pickUpTip")[-1]["params"]["wellName"] == "H1"

        driver, transport = await _bench(_EIGHT_CHANNEL, rack=_RACK_COLUMN_1_MIDDLE_GAP)
        await _pick_up_column_on(driver, [7], ["A1"])
        assert _of_type(transport.commands, "pickUpTip")[-1]["params"]["wellName"] == "A1"

    @pytest.mark.asyncio
    async def test_an_offset_anchor_reports_the_spot_it_took_not_the_row_it_matches(self) -> None:
        """The tip rides channel 0 whichever row it came from. A driver lining channels up with
        rows would record this F3 tip against A3 and lose it on the way back."""
        driver, _ = await _bench(_EIGHT_CHANNEL, rack=_RACK_COLUMN_3_FRONT_TWO_GONE)

        await _pick_up_one_tip(driver, "F3")

        assert {held.channel: held.position for held in driver._mounted_tips} == {0: "F3"}
        state = await driver.get_deck_state(GetDeckStateRequest())
        assert state.tips_mounted == [True] + [False] * 7

    @pytest.mark.asyncio
    async def test_an_offset_anchor_pipettes_the_well_the_caller_named(self) -> None:
        """Carrying the tip to a well four columns over does not re-align it to row A."""
        driver, transport = await _bench(_EIGHT_CHANNEL, rack=_RACK_COLUMN_3_FRONT_TWO_GONE)
        await _pick_up_one_tip(driver, "F3")
        before = len(transport.commands)

        await driver.aspirate(
            AspirateRequest(
                aspirations=[AspirateTarget(labware="plate_1", positions=["F7"], volumes=[6.0])]
            )
        )

        assert _of_type(transport.commands[before:], "aspirate")[0]["params"]["wellName"] == "F7"


class TestNeighbouringSlots:
    """The seven idle nozzles reach about 63 mm into the next slot's airspace, so what sits
    beside the target matters as much as what sits under it. A rack there is refused
    outright: the idle nozzles would come down on its tips. This is the ganged head's
    hardware-damaging failure mode, as opposed to merely stripping a column.
    """

    @pytest.mark.asyncio
    async def test_a_rack_in_the_next_slot_blocks_a_single_pick(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, extra=[_RACK_IN_D1])
        before = list(transport.commands)

        with pytest.raises(ValueError, match="Collision risk"):
            await _pick_up_one_tip(driver, "H1")
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_rack_in_the_next_slot_blocks_a_single_nozzle_aspirate(self) -> None:
        """Same airspace, later in the run: the pick was fine because nothing was beside
        the rack, and the plate the tip is carried to has a rack next to it."""
        driver, transport = await _bench(_EIGHT_CHANNEL, extra=[_RACK_IN_D2])
        await _pick_up_one_tip(driver, "H1")
        before = list(transport.commands)

        with pytest.raises(ValueError, match="Collision risk"):
            await driver.aspirate(
                AspirateRequest(
                    aspirations=[AspirateTarget(labware="plate_1", positions=["C5"], volumes=[5.0])]
                )
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_short_plate_in_the_next_slot_does_not_block(self) -> None:
        """Only labware the idle nozzles would actually hit counts. Refusing every occupied
        neighbour would make single-nozzle work impossible on a normally loaded deck."""
        driver, transport = await _bench(_EIGHT_CHANNEL, extra=[_PLATE_IN_D2])
        await _pick_up_one_tip(driver, "H1")
        before = len(transport.commands)

        await driver.aspirate(
            AspirateRequest(
                aspirations=[AspirateTarget(labware="plate_1", positions=["C5"], volumes=[5.0])]
            )
        )

        assert _of_type(transport.commands[before:], "aspirate")[0]["params"]["wellName"] == "C5"

    @pytest.mark.asyncio
    async def test_a_full_column_pick_is_untouched_by_a_neighbouring_rack(self) -> None:
        """All eight nozzles are over their own rack, so nothing reaches next door."""
        driver, transport = await _bench(_EIGHT_CHANNEL, extra=[_RACK_IN_D1])
        before = len(transport.commands)

        await _pick_up_column(driver)

        assert _of_type(transport.commands[before:], "pickUpTip")[0]["params"]["wellName"] == "A1"


class TestTheSpotsAHeadIsCarrying:
    """A rebuilt rack starts from the declared layout, so it counts as racked every tip a head
    is still holding. The driver clears those spots from its own record of where each channel's
    tip came from, which is why that record has to match the tips the head physically took."""

    @pytest.mark.asyncio
    async def test_a_column_pick_over_an_empty_spot_leaves_that_spot_racked(self) -> None:
        """The head took seven tips, so a rebuild must not blank the eighth spot it took none
        from: another mount can have returned a tip there."""
        driver, _ = await _bench(_EIGHT_CHANNEL, rack=_RACK_MISSING_H1)
        await _pick_up_column(driver)

        response = await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_RACK, _PLATE, _TROUGH])
        )

        tips = response.labware_state["rack_1"].tips
        assert tips is not None
        assert [tips[position] for position in _COLUMN_1[:7]] == [False] * 7
        assert tips["H1"] is True

    @pytest.mark.asyncio
    async def test_a_cherry_picked_tip_is_remembered_at_the_spot_it_came_from(self) -> None:
        """A cherry-picked tip rides the anchor nozzle (channel 0) whatever row its spot is in, so
        the driver has to remember the spot the request named. Lining channels up with rows would
        empty A1 here, and leave H1 filled with a tip that is not there."""
        driver, _ = await _bench(_EIGHT_CHANNEL)
        await _pick_up_one_tip(driver, "H1")

        response = await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_RACK, _PLATE, _TROUGH])
        )

        tips = response.labware_state["rack_1"].tips
        assert tips is not None
        assert tips["H1"] is False
        assert tips["A1"] is True

    @pytest.mark.asyncio
    async def test_a_ninety_six_pick_over_a_part_full_rack_only_carries_what_it_took(self) -> None:
        driver, _ = await _bench(_NINETY_SIX, rack=_RACK_COLUMN_1_ONLY)
        await driver.pick_up_tips96(PickUpTips96Request(tip_rack="rack_1"))

        response = await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_RACK, _PLATE, _TROUGH])
        )

        tips = response.labware_state["rack_1"].tips
        assert tips is not None
        assert [tips[position] for position in _COLUMN_1] == [False] * 8
        assert tips["A2"] is True

    @pytest.mark.asyncio
    async def test_a_rack_rebuilt_after_a_deck_reset_does_not_refill_the_carried_spots(
        self,
    ) -> None:
        """A panic clear-all ejects no tips, so the head still holds the column it picked and
        the rack it goes back to must come back with those spots empty."""
        driver, _ = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)

        await driver.reset_deck_labware(ResetDeckLabwareRequest())
        await driver.add_deck_labware(
            AddDeckLabwareRequest(name="rack_1", catalog_ref=_RACK_REF, at="C1-slot")
        )

        state = await driver.get_deck_state(GetDeckStateRequest())
        assert [(rack.tips_remaining, rack.total_tips) for rack in state.tip_racks] == [(88, 96)]
        returned = await _return_column(driver, _COLUMN_1)
        tips = returned.labware_state["rack_1"].tips
        assert tips is not None and all(tips.values())

    @pytest.mark.asyncio
    async def test_a_rack_that_left_the_reconciled_set_and_came_back_does_not_refill_them(
        self,
    ) -> None:
        driver, _ = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)

        await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_PLATE, _TROUGH])
        )
        response = await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_RACK, _PLATE, _TROUGH])
        )

        tips = response.labware_state["rack_1"].tips
        assert tips is not None
        assert [tips[position] for position in _COLUMN_1] == [False] * 8
        returned = await _return_column(driver, _COLUMN_1)
        back = returned.labware_state["rack_1"].tips
        assert back is not None and all(back.values())


class TestTwoMountsOfTheSameShape:
    """Two 8-channel mounts is a stock Flex build (a p50 and a p1000 multi), and a rack slice
    alone cannot say which of them a command means. The driver reads the tips it recorded to
    settle a return, and refuses anything else it cannot pin to one mount."""

    @pytest.mark.asyncio
    async def test_a_return_goes_to_the_mount_that_holds_the_tips(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, _EIGHT_CHANNEL_RIGHT)
        await _pick_up_column_on(driver, _RIGHT_CHANNELS, _COLUMN_1)
        before = len(transport.commands)

        response = await _return_column(driver, _COLUMN_1)

        drops = _of_type(transport.commands[before:], "dropTip")
        assert [c["params"]["pipetteId"] for c in drops] == [_right_head_of(driver).pipette_id]
        tips = response.labware_state["rack_1"].tips
        assert tips is not None and all(tips.values())

    @pytest.mark.asyncio
    async def test_a_return_names_the_mount_holding_the_column_it_addresses(self) -> None:
        """With both mounts loaded, resolving by channel count alone would return the left
        mount's tips into the right mount's spots and invert the reported rack."""
        driver, transport = await _bench(_EIGHT_CHANNEL, _EIGHT_CHANNEL_RIGHT)
        await _pick_up_column_on(driver, _LEFT_CHANNELS, _COLUMN_1)
        await _pick_up_column_on(driver, _RIGHT_CHANNELS, _COLUMN_2)
        before = len(transport.commands)

        response = await _return_column(driver, _COLUMN_2)

        drops = _of_type(transport.commands[before:], "dropTip")
        assert [c["params"]["pipetteId"] for c in drops] == [_right_head_of(driver).pipette_id]
        assert [c["params"]["wellName"] for c in drops] == ["A2"]
        tips = response.labware_state["rack_1"].tips
        assert tips is not None
        assert [tips[position] for position in _COLUMN_1] == [False] * 8
        assert [tips[position] for position in _COLUMN_2] == [True] * 8

        rebuilt = await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_RACK, _PLATE, _TROUGH])
        )
        after = rebuilt.labware_state["rack_1"].tips
        assert after is not None
        assert [after[position] for position in _COLUMN_1] == [False] * 8
        assert [after[position] for position in _COLUMN_2] == [True] * 8

    @pytest.mark.asyncio
    async def test_a_waste_drop_can_name_the_mount_it_empties(self) -> None:
        driver, _ = await _bench(_EIGHT_CHANNEL, _EIGHT_CHANNEL_RIGHT)
        await _pick_up_column_on(driver, _LEFT_CHANNELS, _COLUMN_1)
        await _pick_up_column_on(driver, _RIGHT_CHANNELS, _COLUMN_2)

        await driver.drop_tips(DropTipsRequest(to_waste=True, use_channels=_RIGHT_CHANNELS))

        assert all(tip is not None for tip in _left_head_of(driver).get_mounted_tips())
        assert all(tip is None for tip in _right_head_of(driver).get_mounted_tips())

    @pytest.mark.asyncio
    async def test_a_liquid_op_naming_no_channels_is_refused_rather_than_run_on_one_mount(
        self,
    ) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, _EIGHT_CHANNEL_RIGHT)
        await _pick_up_column_on(driver, _LEFT_CHANNELS, _COLUMN_1)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="2 mounted heads pipette 8 positions at once"):
            await driver.aspirate(_column_aspirate())
        assert transport.commands == before


class TestNinetySixHead:
    @pytest.mark.asyncio
    async def test_a_pickup_covers_the_whole_rack_in_one_command(self) -> None:
        driver, transport = await _bench(_NINETY_SIX)
        before = len(transport.commands)

        response = await driver.pick_up_tips96(PickUpTips96Request(tip_rack="rack_1"))

        pickups = _of_type(transport.commands[before:], "pickUpTip")
        assert [c["params"]["wellName"] for c in pickups] == ["A1"]
        tips = response.labware_state["rack_1"].tips
        assert tips is not None and not any(tips.values())

    @pytest.mark.asyncio
    async def test_aspirate96_and_dispense96_thread_rate_and_liquid_height(self) -> None:
        driver, transport = await _bench(_NINETY_SIX)
        await driver.pick_up_tips96(PickUpTips96Request(tip_rack="rack_1"))
        before = len(transport.commands)

        response = await driver.aspirate96(
            Aspirate96Request(labware="plate_1", volume=25.0, flow_rate=8.0, liquid_height=4.0)
        )
        await driver.dispense96(Dispense96Request(labware="plate_1", volume=25.0))

        aspirate = _of_type(transport.commands[before:], "aspirate")[0]["params"]
        assert aspirate["wellName"] == "A1"
        assert aspirate["flowRate"] == 8.0
        assert aspirate["wellLocation"]["offset"]["z"] == 4.0
        volumes = response.labware_state["plate_1"].volumes
        assert volumes is not None
        assert set(volumes.values()) == {_PLATE_SEED_UL - 25.0}

    @pytest.mark.asyncio
    async def test_return_tips96_goes_back_to_the_rack_the_tips_came_from(self) -> None:
        driver, transport = await _bench(_NINETY_SIX)
        await driver.pick_up_tips96(PickUpTips96Request(tip_rack="rack_1"))
        before = len(transport.commands)

        response = await driver.return_tips96(ReturnTips96Request())

        drops = _of_type(transport.commands[before:], "dropTip")
        rack_id = _flex_of(driver)._loaded_labware["rack_1"]
        assert [c["params"]["labwareId"] for c in drops] == [rack_id]
        tips = response.labware_state["rack_1"].tips
        assert tips is not None and all(tips.values())

    @pytest.mark.asyncio
    async def test_return_tips96_without_a_remembered_rack_is_refused(self) -> None:
        driver, transport = await _bench(_NINETY_SIX)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="no rack is recorded"):
            await driver.return_tips96(ReturnTips96Request())
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_waste_drop_forgets_the_rack_the_tips_came_from(self) -> None:
        driver, _ = await _bench(_NINETY_SIX)
        await driver.pick_up_tips96(PickUpTips96Request(tip_rack="rack_1"))

        await driver.drop_tips96(DropTips96Request(to_waste=True))

        with pytest.raises(ValueError, match="no rack is recorded"):
            await driver.return_tips96(ReturnTips96Request())

    @pytest.mark.asyncio
    async def test_a_ninety_six_op_naming_a_rack_as_its_target_is_refused(self) -> None:
        driver, transport = await _bench(_NINETY_SIX)
        before = list(transport.commands)

        with pytest.raises(TypeError, match="the 96 head covers"):
            await driver.aspirate96(Aspirate96Request(labware="rack_1", volume=10.0))
        assert transport.commands == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize("to_waste", [True, False])
    async def test_dropping_the_ninety_six_head_with_no_tips_mounted_is_refused(
        self, to_waste: bool
    ) -> None:
        """A drop with nothing seated drives the bare nozzles to the tip-end height the
        robot planned for, so it is refused before any wire command."""
        driver, transport = await _bench(_NINETY_SIX)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="holds no tips"):
            await driver.drop_tips96(
                DropTips96Request(to_waste=to_waste, tip_rack=None if to_waste else "rack_1")
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_return_after_a_deck_reset_names_the_rack_that_left_the_deck(self) -> None:
        """The rack the tips would go back to left the deck, so the return is refused with the
        driver's own message rather than a lookup failure deep in the deck model."""
        driver, _ = await _bench(_NINETY_SIX)
        await driver.pick_up_tips96(PickUpTips96Request(tip_rack="rack_1"))

        await driver.reset_deck_labware(ResetDeckLabwareRequest())

        with pytest.raises(ValueError, match="no longer on the deck"):
            await driver.return_tips96(ReturnTips96Request())

    @pytest.mark.asyncio
    async def test_a_part_full_head_cannot_be_topped_up_from_a_second_rack(self) -> None:
        """One drop puts all 96 tips in one rack, so the driver's return only works while every
        mounted tip came from one. Nothing can break that: a 96 pickup reconfigures the nozzle
        layout, and the robot refuses any reconfiguration while a channel holds a tip."""
        driver, transport = await _bench(_NINETY_SIX, rack=_RACK_COLUMN_1_ONLY)
        await driver.pick_up_tips96(PickUpTips96Request(tip_rack="rack_1"))
        await driver.add_deck_labware(
            AddDeckLabwareRequest(
                name="rack_2",
                catalog_ref=_RACK_REF,
                at="B1-slot",
                well_state=LabwareWellState(
                    tips={
                        f"{row}{column}": column != 1
                        for column in range(1, 13)
                        for row in "ABCDEFGH"
                    }
                ),
            )
        )
        before = list(transport.commands)

        with pytest.raises(OpentronsError, match="cannot change while channel"):
            await driver.pick_up_tips96(PickUpTips96Request(tip_rack="rack_2"))
        assert transport.commands == before

        returned = await driver.return_tips96(ReturnTips96Request())
        tips = returned.labware_state["rack_1"].tips
        assert tips is not None and [tips[position] for position in _COLUMN_1] == [True] * 8

    @pytest.mark.asyncio
    async def test_a_rebuilt_rack_does_not_refill_the_spots_the_ninety_six_head_carries(
        self,
    ) -> None:
        driver, _ = await _bench(_NINETY_SIX)
        await driver.pick_up_tips96(PickUpTips96Request(tip_rack="rack_1"))

        response = await driver.reconcile_deck_occupancy(
            ReconcileDeckOccupancyRequest(resources=[_RACK, _PLATE, _TROUGH])
        )

        tips = response.labware_state["rack_1"].tips
        assert tips is not None and not any(tips.values())
        returned = await driver.return_tips96(ReturnTips96Request())
        back = returned.labware_state["rack_1"].tips
        assert back is not None and all(back.values())


class TestReportedState:
    @pytest.mark.asyncio
    async def test_an_aspirate_reports_what_the_trackers_hold(self) -> None:
        driver, _ = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)

        response = await driver.aspirate(_column_aspirate())

        volumes = response.labware_state["plate_1"].volumes
        assert volumes is not None
        assert [volumes[position] for position in _COLUMN_1] == [_PLATE_SEED_UL - 20.0] * 8
        assert volumes["A2"] == _PLATE_SEED_UL
        # Every well of the plate is reported, not just the addressed column.
        assert set(volumes) == {
            well.get_identifier() for well in _plate_of(driver).get_all_items()
        }

    @pytest.mark.asyncio
    async def test_a_dispense_reports_the_receiving_wells(self) -> None:
        driver, _ = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)

        response = await driver.dispense(
            DispenseRequest(
                dispenses=[
                    DispenseTarget(
                        labware="plate_1",
                        positions=[f"{row}2" for row in "ABCDEFGH"],
                        volumes=[30.0] * 8,
                    )
                ]
            )
        )

        volumes = response.labware_state["plate_1"].volumes
        assert volumes is not None
        assert volumes["A2"] == _PLATE_SEED_UL + 30.0
        assert volumes["A1"] == _PLATE_SEED_UL

    @pytest.mark.asyncio
    async def test_a_tip_pickup_reports_every_spot_on_the_rack(self) -> None:
        driver, _ = await _bench(_EIGHT_CHANNEL)

        response = await driver.pick_up_tips(
            PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=_COLUMN_1)])
        )

        tips = response.labware_state["rack_1"].tips
        assert tips is not None
        assert set(tips) == {spot.get_identifier() for spot in _rack_of(driver).get_all_items()}
        assert [tips[position] for position in _COLUMN_1] == [False] * 8
        assert all(present for spot, present in tips.items() if spot not in _COLUMN_1)


class TestPartialStagingRollback:
    """A head stages one tracker per addressed item and validates as it goes, so an item
    the machine cannot serve leaves the items before it holding an uncommitted delta.
    The driver reports its trackers as observed truth, so that delta must never survive
    the failure."""

    @pytest.mark.asyncio
    async def test_a_column_mix_one_well_cannot_serve_reports_the_volumes_it_started_with(
        self,
    ) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        plate = _plate_of(driver)
        for position in _COLUMN_1:
            plate.get_item(position).tracker.set_volume(20.0)
        plate.get_item("D1").tracker.set_volume(5.0)
        before = len(transport.commands)

        with pytest.raises(TooLittleLiquidError):
            await driver.mix(
                MixRequest(labware="plate_1", positions=_COLUMN_1, volume=10.0, repetitions=2)
            )

        new = transport.commands[before:]
        assert _of_type(new, "aspirate") == []
        assert _of_type(new, "dispense") == []
        volumes = derive_labware_state(_flex_of(driver).deck)["plate_1"].volumes
        assert volumes is not None
        assert [volumes[position] for position in _COLUMN_1] == [20.0] * 3 + [5.0] + [20.0] * 4

    @pytest.mark.asyncio
    async def test_a_column_dispense_one_well_cannot_hold_reports_the_volumes_it_started_with(
        self,
    ) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        plate = _plate_of(driver)
        for position in _COLUMN_1:
            plate.get_item(position).tracker.set_volume(300.0)
        plate.get_item("D1").tracker.set_volume(360.0)
        before = len(transport.commands)

        with pytest.raises(TooLittleVolumeError):
            await driver.dispense(
                DispenseRequest(
                    dispenses=[
                        DispenseTarget(labware="plate_1", positions=_COLUMN_1, volumes=[20.0] * 8)
                    ]
                )
            )

        assert _of_type(transport.commands[before:], "dispense") == []
        volumes = derive_labware_state(_flex_of(driver).deck)["plate_1"].volumes
        assert volumes is not None
        assert [volumes[position] for position in _COLUMN_1] == [300.0] * 3 + [360.0] + [300.0] * 4

    @pytest.mark.asyncio
    async def test_a_plate_wide_aspirate_one_well_cannot_serve_leaves_every_well_where_it_was(
        self,
    ) -> None:
        driver, transport = await _bench(_NINETY_SIX)
        await driver.pick_up_tips96(PickUpTips96Request(tip_rack="rack_1"))
        plate = _plate_of(driver)
        plate.get_item("H12").tracker.set_volume(5.0)
        before = len(transport.commands)

        with pytest.raises(TooLittleLiquidError):
            await driver.aspirate96(Aspirate96Request(labware="plate_1", volume=10.0))

        assert _of_type(transport.commands[before:], "aspirate") == []
        volumes = derive_labware_state(_flex_of(driver).deck)["plate_1"].volumes
        assert volumes is not None
        assert volumes["A1"] == _PLATE_SEED_UL
        assert volumes["H12"] == 5.0

    @pytest.mark.asyncio
    async def test_a_column_return_one_spot_still_holds_reports_the_spots_it_started_with(
        self,
    ) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        await _pick_up_column(driver)
        _rack_of(driver).set_tip_state({"D1": True})
        before = len(transport.commands)

        with pytest.raises(HasTipError):
            await driver.drop_tips(
                DropTipsRequest(
                    to_waste=False, drops=[TipPick(tip_rack="rack_1", positions=_COLUMN_1)]
                )
            )

        assert _of_type(transport.commands[before:], "dropTip") == []
        tips = derive_labware_state(_flex_of(driver).deck)["rack_1"].tips
        assert tips is not None
        assert [tips[position] for position in _COLUMN_1] == [False] * 3 + [True] + [False] * 4

    @pytest.mark.asyncio
    async def test_a_rollback_skips_a_tracker_that_is_off_and_still_undoes_the_rest(self) -> None:
        """A tracker that is off stages nothing and raises if asked to roll back, which would
        abort the undo partway and leave the wells after it reporting liquid that never moved."""
        driver, _ = await _bench(_EIGHT_CHANNEL)
        plate = _plate_of(driver)
        wells = plate.get_all_items()
        wells[0].tracker.disable()

        with pytest.raises(TooLittleLiquidError):
            with _undo_partial_staging(plate):
                for well in wells[1:5]:
                    well.tracker.remove_liquid(30.0)
                raise TooLittleLiquidError("the next well cannot serve 30.0 uL")

        volumes = derive_labware_state(_flex_of(driver).deck)["plate_1"].volumes
        assert volumes is not None
        assert [volumes[well.get_identifier()] for well in wells[1:5]] == [_PLATE_SEED_UL] * 4


class TestMovePlate:
    @pytest.mark.asyncio
    async def test_a_move_reparents_the_deck_and_names_the_destination_slot(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
        before = len(transport.commands)

        await driver.move_plate(MovePlateRequest(plate="plate_1", to_position="D1-slot"))

        moves = _of_type(transport.commands[before:], "moveLabware")
        assert [m["params"]["newLocation"] for m in moves] == [{"slotName": "D1"}]
        assert moves[0]["params"]["strategy"] == "usingGripper"
        deck = _flex_of(driver).deck
        assert deck.slots["C2"] is None
        assert deck.slots["D1"] is not None and deck.slots["D1"].name == "plate_1"

    @pytest.mark.asyncio
    async def test_a_move_can_say_how_far_below_the_top_the_gripper_takes_the_plate(self) -> None:
        """Grip height is a property of the move, not the labware. For labware the robot has
        no vendor definition for, the distance shapes the synthesized definition it uploads."""
        driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)

        await driver.move_plate(
            MovePlateRequest(plate="plate_1", to_position="D1-slot", grip_distance_from_top=5.0)
        )

        plate_defs = [d for d in transport.labware_definitions if d["parameters"]["loadName"].startswith("plate_1")]
        assert plate_defs, [d["parameters"]["loadName"] for d in transport.labware_definitions]
        assert plate_defs[0]["gripHeightFromLabwareBottom"] == pytest.approx(
            plate_defs[0]["dimensions"]["zDimension"] - 5.0
        )

    @pytest.mark.asyncio
    async def test_a_from_position_the_projection_disagrees_with_is_corrected_first(self) -> None:
        """The engine ledger owns where a plate sits, so a from_position that contradicts the
        deck projection re-parents it rather than failing the move."""
        driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
        before = len(transport.commands)

        await driver.move_plate(
            MovePlateRequest(plate="plate_1", to_position="D1-slot", from_position="B1-slot")
        )

        deck = _flex_of(driver).deck
        assert deck.slots["C2"] is None
        assert deck.slots["B1"] is None
        assert deck.slots["D1"] is not None and deck.slots["D1"].name == "plate_1"
        assert _of_type(transport.commands[before:], "moveLabware")[0]["params"][
            "newLocation"
        ] == {"slotName": "D1"}

    @pytest.mark.asyncio
    async def test_a_corrected_from_position_re_registers_the_plate_with_the_robot(self) -> None:
        """The robot grips from the slot it recorded when the labware loaded, so correcting
        only the local projection would send the gripper to the stale slot. The plate goes off
        deck and re-loads at the corrected slot before the move."""
        driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
        await _pick_up_column(driver)
        await driver.aspirate(_column_aspirate())
        before = len(transport.commands)

        await driver.move_plate(
            MovePlateRequest(plate="plate_1", to_position="D1-slot", from_position="B1-slot")
        )

        new = transport.commands[before:]
        assert [m["params"]["newLocation"] for m in _of_type(new, "moveLabware")] == [
            "offDeck",
            {"slotName": "D1"},
        ]
        assert [load["params"]["location"] for load in _of_type(new, "loadLabware")] == [
            {"slotName": "B1"}
        ]

    @pytest.mark.asyncio
    async def test_a_from_position_another_labware_occupies_is_refused_with_the_plate_left_put(
        self,
    ) -> None:
        """Freeing the plate before finding the destination taken would leave it parented
        nowhere, wedging every later op that names it."""
        driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="deck projection has 'rack_1' there"):
            await driver.move_plate(
                MovePlateRequest(plate="plate_1", to_position="D1-slot", from_position="C1-slot")
            )

        assert transport.commands == before
        deck = _flex_of(driver).deck
        assert deck.slots["C2"] is not None and deck.slots["C2"].name == "plate_1"
        state = derive_labware_state(deck)
        assert state["plate_1"].volumes is not None
        # Still addressable, so the retry naming the slot it really sits on goes through.
        await driver.move_plate(
            MovePlateRequest(plate="plate_1", to_position="D1-slot", from_position="C2-slot")
        )
        assert deck.slots["D1"] is not None and deck.slots["D1"].name == "plate_1"


class TestMotion:
    @pytest.mark.asyncio
    async def test_moving_a_channel_holds_the_axes_the_request_leaves_out(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, saved_position=_SAVED_POSITION)
        before = len(transport.commands)

        await driver.move_channel_to(MoveChannelToRequest(channel=0, z=10.0))

        move = _of_type(transport.commands[before:], "moveToCoordinates")[0]["params"]
        assert move["coordinates"] == {"x": 12.5, "y": 34.0, "z": 10.0}
        # Deck-derived now, not a fixed 120: a rack body (99) plus the robot's arc margin
        # (10) is the term that wins on a Flex deck.
        assert move["minimumZHeight"] == 109.0

    @pytest.mark.asyncio
    async def test_the_travel_floor_clears_a_rack_the_deck_model_does_not_show(self) -> None:
        """A rack physically on the bench but not yet loaded, or loaded after this jog, is
        still in the way. The floor never drops to the tallest MODELLED top for that
        reason, so an empty deck arcs no lower than a loaded one."""
        driver, transport = await _bench(_EIGHT_CHANNEL, saved_position=_SAVED_POSITION)
        await driver.reset_deck_labware(ResetDeckLabwareRequest())
        before = len(transport.commands)

        await driver.move_channel_to(MoveChannelToRequest(channel=0, z=10.0))

        move = _of_type(transport.commands[before:], "moveToCoordinates")[0]["params"]
        assert move["minimumZHeight"] == 109.0

    @pytest.mark.asyncio
    async def test_a_jog_is_read_then_move_by_the_delta(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, saved_position=_SAVED_POSITION)
        before = len(transport.commands)

        await driver.move_channel_relative(MoveChannelRelativeRequest(channel=0, dz=-5.0))

        new = transport.commands[before:]
        assert _types(new) == ["savePosition", "moveToCoordinates"]
        assert new[1]["params"]["coordinates"] == {"x": 12.5, "y": 34.0, "z": 51.5}

    @pytest.mark.asyncio
    async def test_reading_a_channel_position_echoes_the_only_datum_the_flex_has(self) -> None:
        driver, _ = await _bench(_EIGHT_CHANNEL, saved_position=_SAVED_POSITION)

        position = await driver.get_channel_position(GetChannelPositionRequest(channel=0))

        assert position == ChannelPosition(
            channel=0, x=12.5, y=34.0, z=56.5, z_reference="tip_end"
        )

    @pytest.mark.asyncio
    async def test_a_hamilton_z_datum_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="tip end"):
            await driver.move_channel_to(
                MoveChannelToRequest(channel=0, z=10.0, z_reference="stop_disk")
            )
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_channel_that_cannot_move_alone_is_refused(self) -> None:
        """The Flex positions a whole pipette by its A-row nozzle, so jogging channel 3 of an
        8-channel head would put a different nozzle at the taught point."""
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="cannot move on its own"):
            await driver.move_channel_to(MoveChannelToRequest(channel=3, z=10.0))
        assert transport.commands == before

    @pytest.mark.asyncio
    async def test_a_channel_no_head_carries_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="no mounted head has channel 8"):
            await driver.get_channel_position(GetChannelPositionRequest(channel=8))
        assert transport.commands == before


class TestGripperMotion:
    @pytest.mark.asyncio
    async def test_the_gripper_moves_on_the_extension_mount(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
        before = len(transport.commands)

        await driver.move_gripper_to(MoveGripperToRequest(x=1.0, y=2.0, z=3.0))

        move = _of_type(transport.commands[before:], "robot/moveTo")[0]["params"]
        assert move == {"mount": "extension", "destination": {"x": 1.0, "y": 2.0, "z": 3.0}}

    @pytest.mark.asyncio
    async def test_the_jaw_closes_to_a_force_and_opens_fully(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
        before = len(transport.commands)

        await driver.grip_with_force(GripWithForceRequest(force=15.0))
        await driver.release_jaw(ReleaseJawRequest())

        new = transport.commands[before:]
        assert _types(new) == ["robot/closeGripperJaw", "robot/openGripperJaw"]
        assert new[0]["params"] == {"force": 15.0}

    @pytest.mark.asyncio
    async def test_gripping_without_a_force_lets_the_robot_apply_its_default(self) -> None:
        """Naming no force must leave the parameter off the wire entirely, so the robot falls
        back to its own default. Sending it as an explicit null or a zero would either be
        rejected or tell the jaw to close on nothing."""
        driver, transport = await _bench(_EIGHT_CHANNEL, gripper=True)
        before = len(transport.commands)

        await driver.grip_with_force(GripWithForceRequest())

        close = _of_type(transport.commands[before:], "robot/closeGripperJaw")[0]
        assert close["params"] == {}

    @pytest.mark.asyncio
    async def test_jaw_control_without_a_gripper_is_refused(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="needs the Flex gripper"):
            await driver.grip_with_force(GripWithForceRequest())
        assert transport.commands == before


class TestDeclaredInterfaces:
    """The split interfaces only pay off if the Flex refuses to advertise what its hardware has no
    command for. The Opentrons API has no gripper position query and no jaw-width command, so
    declaring either would make `interfaces` lie and push the failure to a runtime error."""

    def test_declares_only_what_the_flex_hardware_can_honor(self) -> None:
        driver = FlexLiquidHandlerDriver(host="localhost", transport=ChatterboxTransport())

        assert driver.interfaces == frozenset(
            {
                "ILiquidHandler",
                "ILiquidProbe",
                "IPipetteMotion",
                "IGripperMotion",
                "IForceGripperJaw",
                "IGantryParking",
                "IHomeable",
            }
        )
        assert isinstance(driver, ILiquidProbeDriver)
        assert isinstance(driver, IPipetteMotionDriver)
        assert isinstance(driver, IGripperMotionDriver)
        assert isinstance(driver, IForceGripperJawDriver)

    def test_does_not_advertise_capabilities_the_flex_lacks(self) -> None:
        driver = FlexLiquidHandlerDriver(host="localhost", transport=ChatterboxTransport())

        assert not isinstance(driver, IGripperPositionDriver)
        assert not isinstance(driver, IWidthGripperJawDriver)
        assert not hasattr(driver, "get_gripper_position")
        assert not hasattr(driver, "set_jaw_width")


class TestLiquidProbe:
    """Asking the machine where the liquid is.

    A probe answers in the frame every other height on this driver uses, the
    height above the well's own floor, so the number feeds straight back into
    an aspirate. Finding nothing is an answer (``height=None``), not a failure.
    """

    _PROBE_DECK_Z = 11.0

    @pytest.mark.asyncio
    async def test_a_single_channel_probes_the_named_well(self) -> None:
        driver, transport = await _bench(_SINGLE_CHANNEL, liquid_probe_z=self._PROBE_DECK_Z)
        await driver.pick_up_tips(
            PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=["A1"])])
        )
        well = _plate_of(driver).get_item("B3")
        floor = well.location.z + well.material_z_thickness

        response = await driver.liquid_probe(
            LiquidProbeRequest(labware="plate_1", positions=["B3"])
        )

        assert response.height == pytest.approx(self._PROBE_DECK_Z - floor)
        probes = _of_type(transport.commands, "tryLiquidProbe")
        assert len(probes) == 1
        assert probes[0]["params"]["wellName"] == "B3"

    @pytest.mark.asyncio
    async def test_a_column_probe_reads_the_whole_column_with_one_command(self) -> None:
        driver, transport = await _bench(liquid_probe_z=self._PROBE_DECK_Z)
        await driver.pick_up_tips(
            PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=_COLUMN_1)])
        )

        anchor = _plate_of(driver).get_item("A2")
        floor = anchor.location.z + anchor.material_z_thickness

        response = await driver.liquid_probe(
            LiquidProbeRequest(labware="plate_1", positions=_COLUMN_2)
        )

        # Every well of a plate shares one floor, so this arithmetic cannot tell
        # A2 from any other well. What it does pin is that a deck z comes back as
        # a height above the floor; the wellName assert below is what fixes the anchor.
        assert response.height == pytest.approx(self._PROBE_DECK_Z - floor), (
            "the probe answers in deck space, so the driver owes a height above the floor"
        )
        probes = _of_type(transport.commands, "tryLiquidProbe")
        assert len(probes) == 1, "one plunger, one pressure reading, one command"
        assert probes[0]["params"]["wellName"] == "A2"

    @pytest.mark.asyncio
    async def test_a_cherry_picked_tip_probes_one_well_of_a_ganged_head(self) -> None:
        driver, transport = await _bench(_EIGHT_CHANNEL, liquid_probe_z=self._PROBE_DECK_Z)
        await _pick_up_one_tip(driver, "H1")

        well = _plate_of(driver).get_item("C5")
        floor = well.location.z + well.material_z_thickness

        response = await driver.liquid_probe(
            LiquidProbeRequest(labware="plate_1", positions=["C5"])
        )

        assert response.height == pytest.approx(self._PROBE_DECK_Z - floor), (
            "the probe answers in deck space, so the driver owes a height above the floor"
        )
        probes = _of_type(transport.commands, "tryLiquidProbe")
        assert len(probes) == 1
        assert probes[0]["params"]["wellName"] == "C5"

    @pytest.mark.asyncio
    async def test_a_trough_probe_names_the_single_cavity(self) -> None:
        driver, transport = await _bench(liquid_probe_z=self._PROBE_DECK_Z)
        await driver.pick_up_tips(
            PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=_COLUMN_1)])
        )
        trough = _trough_of(driver)

        response = await driver.liquid_probe(
            LiquidProbeRequest(labware="trough_1", use_channels=_LEFT_CHANNELS)
        )

        assert response.height == pytest.approx(
            self._PROBE_DECK_Z - trough.material_z_thickness
        )
        probes = _of_type(transport.commands, "tryLiquidProbe")
        assert len(probes) == 1
        assert probes[0]["params"]["wellName"] == TROUGH_WELL_ID

    @pytest.mark.asyncio
    async def test_finding_no_liquid_reports_no_height_rather_than_failing(self) -> None:
        driver, transport = await _bench(_SINGLE_CHANNEL)
        await driver.pick_up_tips(
            PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=["A1"])])
        )

        response = await driver.liquid_probe(
            LiquidProbeRequest(labware="plate_1", positions=["B3"])
        )

        assert response.height is None
        assert len(_of_type(transport.commands, "tryLiquidProbe")) == 1

    @pytest.mark.asyncio
    async def test_the_height_is_the_robots_deck_z_minus_the_wells_own_floor(self) -> None:
        """The robot answers in deck space and this surface speaks well-floor
        heights, so the driver subtracts. The floor comes from the definition the
        robot loaded, which is the only one that agrees with where the robot
        thinks the well is."""
        driver, _transport = await _bench(_SINGLE_CHANNEL, liquid_probe_z=self._PROBE_DECK_Z)
        await driver.pick_up_tips(
            PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=["A1"])])
        )
        response = await driver.liquid_probe(
            LiquidProbeRequest(labware="plate_1", positions=["B3"])
        )

        # Read the floor after the probe: the plate is only loaded into the run
        # when an op first touches it, and the definition arrives with that load.
        floor = _flex_of(driver).well_bottom_deck_z(_plate_of(driver), "B3")
        assert floor > 0.0, "a flat-bottom plate's cavity floor sits above its base"
        assert response.height == pytest.approx(self._PROBE_DECK_Z - floor)

    @pytest.mark.asyncio
    async def test_a_probe_the_robot_cannot_frame_is_an_error_not_a_number(self) -> None:
        """If the run cannot say where the well's floor is, there is no honest
        height to report. Returning the raw deck z would look like a height and
        be wrong by the floor."""
        driver, _transport = await _bench(_SINGLE_CHANNEL, liquid_probe_z=self._PROBE_DECK_Z)
        await driver.pick_up_tips(
            PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=["A1"])])
        )
        await driver.liquid_probe(
            LiquidProbeRequest(labware="plate_1", positions=["B3"])
        )
        # The plate is loaded now, so forgetting its definition is the only way
        # to stand in for a run that never reported one.
        _flex_of(driver)._loaded_geometry.clear()

        with pytest.raises(OpentronsError, match="no definition"):
            await driver.liquid_probe(
                LiquidProbeRequest(labware="plate_1", positions=["B3"])
            )

    @pytest.mark.asyncio
    async def test_probing_without_tips_is_refused_before_the_wire(self) -> None:
        driver, transport = await _bench(_SINGLE_CHANNEL, liquid_probe_z=self._PROBE_DECK_Z)
        before = list(transport.commands)

        with pytest.raises(ValueError, match="holds no tips"):
            await driver.liquid_probe(
                LiquidProbeRequest(labware="plate_1", positions=["B3"])
            )

        assert transport.commands == before

    def test_a_container_probe_must_say_which_channels_it_uses(self) -> None:
        with pytest.raises(ValidationError, match="use_channels"):
            LiquidProbeRequest(labware="trough_1")
