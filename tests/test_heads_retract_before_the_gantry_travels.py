"""Every liquid handler lifts all of its heads before its gantry moves in X or Y.

One gantry carries every pipette mount, the 96 head and the gripper, so a head
left low is dragged across the deck by any move of any of them. An operator
command and a run step both reach the driver by command name through the device
agent, so the lift lives in the hook every command passes through.
"""

import importlib
import inspect
import pkgutil
from typing import ClassVar

import pytest
from pydantic import BaseModel

import cheshire_drivers
import cheshire_drivers.plr
from cheshire_drivers.driver_introspection import VendorSurface, vendor_surfaces
from cheshire_drivers.gantry_models import GantryBusyError, ParkGantryRequest
from cheshire_drivers.gripper_models import MoveGripperRelativeRequest
from cheshire_drivers.head_retract import TRAVELLING_COMMANDS, RetractsHeadsBeforeTravel
from cheshire_drivers.interfaces import ILiquidHandlerDriver
from cheshire_drivers.liquid_handler_models import (
    AddDeckLabwareRequest,
    Aspirate96Request,
    AspirateRequest,
    AspirateTarget,
    DiscardTipsRequest,
    Dispense96Request,
    DispenseRequest,
    DispenseTarget,
    DropTips96Request,
    DropTipsRequest,
    LiquidProbeRequest,
    MixRequest,
    MovePlateRequest,
    PickUpTips96Request,
    PickUpTipsRequest,
    ReturnTips96Request,
    TipPick,
)
from cheshire_drivers.pipette_motion_models import MoveChannelRelativeRequest, MoveChannelToRequest
from cheshire_drivers.plr_wrappers import VendorSurfaceForwarding
from cheshire_drivers.sims import SimLiquidHandlerDriver, SimStrategy

pytestmark = pytest.mark.asyncio

_COLUMN = ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1"]
_ASPIRATE = AspirateRequest(aspirations=[AspirateTarget(labware="plate_1", positions=_COLUMN, volumes=[10.0] * 8)])


class _Log(SimStrategy):
    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def sim(self, prompt: str) -> None:
        self._log.append(prompt)


class _SimHandler(SimLiquidHandlerDriver):
    """The sim handler, with its lift written into the same log its commands write to."""

    def __init__(self) -> None:
        self.log: list[str] = []
        super().__init__("lh_sim", sim_strategy=_Log(self.log))

    async def _retract_heads(self, command: str) -> None:
        self.log.append("retract")


_REQUESTS: dict[str, BaseModel] = {
    "aspirate": _ASPIRATE,
    "dispense": DispenseRequest(dispenses=[DispenseTarget(labware="plate_1", positions=_COLUMN, volumes=[10.0] * 8)]),
    "mix": MixRequest(labware="plate_1", positions=_COLUMN, volume=10.0, repetitions=2),
    "liquid_probe": LiquidProbeRequest(labware="plate_1", positions=_COLUMN),
    "pick_up_tips": PickUpTipsRequest(picks=[TipPick(tip_rack="rack_1", positions=_COLUMN)]),
    "drop_tips": DropTipsRequest(to_waste=True),
    "discard_tips": DiscardTipsRequest(),
    "aspirate96": Aspirate96Request(labware="plate_1", volume=10.0),
    "dispense96": Dispense96Request(labware="plate_1", volume=10.0),
    "pick_up_tips96": PickUpTips96Request(tip_rack="rack_1"),
    "drop_tips96": DropTips96Request(),
    "return_tips96": ReturnTips96Request(),
    "move_plate": MovePlateRequest(plate="plate_1", to_position="D4-slot"),
    "park_gantry": ParkGantryRequest(),
}


@pytest.mark.parametrize("command", sorted(_REQUESTS))
async def test_a_sim_handler_lifts_its_heads_before_each_travelling_command(command: str) -> None:
    driver = _SimHandler()
    await driver.add_deck_labware(AddDeckLabwareRequest(name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="A1-slot"))
    driver.log.clear()

    await getattr(driver, command)(_REQUESTS[command])

    assert driver.log[0] == "retract"
    assert driver.log.count("retract") == 1


def test_every_travelling_command_the_sim_offers_is_covered_here() -> None:
    offered = {name for name in TRAVELLING_COMMANDS if hasattr(SimLiquidHandlerDriver, name)}

    assert offered == set(_REQUESTS)


async def test_a_command_that_stays_put_does_not_lift() -> None:
    driver = _SimHandler()

    await driver.add_deck_labware(AddDeckLabwareRequest(name="plate_1", catalog_ref="Cor_96_wellplate_360ul_Fb", at="A1-slot"))

    assert "retract" not in driver.log


class _Gantry:
    """A vendor object a driver forwards to: one command travels, one does not."""

    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def move_xy(self, x: float, y: float) -> None:
        self._log.append("move_xy")

    async def set_lights(self, on: bool) -> None:
        self._log.append("set_lights")


class _Rig(RetractsHeadsBeforeTravel, VendorSurfaceForwarding):
    """A handler reduced to the shared lift: jogs, a vendor surface, and a lift that can refuse."""

    vendor_surfaces: ClassVar[tuple[VendorSurface, ...]] = (
        VendorSurface(path="_gantry", type=_Gantry, prefix="gantry", travels=frozenset({"move_xy"})),
    )

    def __init__(self, refuse: bool = False) -> None:
        self.log: list[str] = []
        self._gantry = _Gantry(self.log)
        self._refuse = refuse

    async def _retract_heads(self, command: str) -> None:
        if self._refuse:
            raise GantryBusyError(f"{command}: the gripper may still hold a plate.")
        self.log.append("retract")

    async def move_channel_to(self, request: MoveChannelToRequest) -> None:
        self.log.append("move_channel_to")

    async def move_channel_relative(self, request: MoveChannelRelativeRequest) -> None:
        self.log.append("move_channel_relative")

    async def move_gripper_relative(self, request: MoveGripperRelativeRequest) -> None:
        self.log.append("move_gripper_relative")

    async def aspirate(self, request: AspirateRequest) -> None:
        self.log.append("aspirate")


_JOG_COMMANDS: dict[type, str] = {
    MoveChannelToRequest: "move_channel_to",
    MoveChannelRelativeRequest: "move_channel_relative",
    MoveGripperRelativeRequest: "move_gripper_relative",
}


@pytest.mark.parametrize("jog", [
    MoveChannelToRequest(channel=0, x=10.0),
    MoveChannelToRequest(channel=0, y=10.0, z=5.0),
    MoveChannelRelativeRequest(channel=0, dx=1.0),
    MoveGripperRelativeRequest(dy=-1.0),
])
async def test_a_jog_in_x_or_y_lifts_first(jog: BaseModel) -> None:
    rig = _Rig()
    command = _JOG_COMMANDS[type(jog)]

    await getattr(rig, command)(jog)

    assert rig.log == ["retract", command]


@pytest.mark.parametrize("jog", [
    MoveChannelToRequest(channel=0, z=5.0),
    MoveChannelRelativeRequest(channel=0, dz=-2.0),
    MoveChannelRelativeRequest(channel=0, dx=0.0, dz=-2.0),
    MoveGripperRelativeRequest(dz=3.0),
])
async def test_a_jog_in_z_alone_does_not_lift(jog: BaseModel) -> None:
    """Lifting first would undo the jog: the operator is lowering into a well on purpose."""
    rig = _Rig()
    command = _JOG_COMMANDS[type(jog)]

    await getattr(rig, command)(jog)

    assert rig.log == [command]


async def test_a_vendor_command_the_driver_says_travels_lifts_first() -> None:
    rig = _Rig()

    await getattr(rig, "gantry.move_xy")(1.0, 2.0)

    assert rig.log == ["retract", "move_xy"]


async def test_a_vendor_command_that_stays_put_does_not_lift() -> None:
    rig = _Rig()

    await getattr(rig, "gantry.set_lights")(True)

    assert rig.log == ["set_lights"]


async def test_a_lift_that_is_not_safe_refuses_the_command() -> None:
    rig = _Rig(refuse=True)

    with pytest.raises(GantryBusyError):
        await rig.aspirate(_ASPIRATE)

    assert rig.log == []


def test_a_vendor_surface_cannot_name_a_travelling_command_it_lacks() -> None:
    class _Typo(_Rig):
        vendor_surfaces: ClassVar[tuple[VendorSurface, ...]] = (
            VendorSurface(path="_gantry", type=_Gantry, prefix="gantry", travels=frozenset({"move_zz"})),
        )

    with pytest.raises(TypeError, match="move_zz"):
        vendor_surfaces(_Typo)


def _concrete_liquid_handlers() -> list[type]:
    for package in (cheshire_drivers, cheshire_drivers.plr):
        for module in pkgutil.iter_modules(package.__path__):
            importlib.import_module(f"{package.__name__}.{module.name}")
    found: list[type] = []
    pending: list[type] = [ILiquidHandlerDriver]
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if cls in found:
            continue
        if not inspect.isabstract(cls) and cls.__module__.startswith("cheshire_drivers."):
            found.append(cls)
    return found


def test_every_liquid_handler_driver_lifts_its_heads_before_travel() -> None:
    handlers = _concrete_liquid_handlers()

    assert len(handlers) >= 3, "the walk found too few drivers to mean anything"
    assert [cls.__name__ for cls in handlers if not issubclass(cls, RetractsHeadsBeforeTravel)] == []
