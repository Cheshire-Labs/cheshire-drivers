"""Every public driver command runs `_before_command` once before it reaches the device."""

import inspect

import pytest
from pydantic import BaseModel
from pylabrobot.resources.resource import Resource as PLRResource

from cheshire_drivers.driver_hooks import current_call_id, is_hooked
from cheshire_drivers.faults import RaiseFault
from cheshire_drivers.human_transporter_driver import HumanTransporterDriver
from cheshire_drivers.move_parameters import SEED_MOVE_PARAMETERS
from cheshire_drivers.shaker_models import ShakeRequest
from cheshire_drivers.sims import BaseSimDriver, SimShakerDriver, SimTransporterDriver, SleepSim
from cheshire_drivers.teachpoints import JointCoordinates, Teachpoint
from cheshire_drivers.translator_driver import SimTranslatorDriver
from cheshire_drivers.transporter_models import PickAtCoordsRequest, PlaceAtCoordsRequest
from tests.command_hook_log import hook_log
from tests.driver_surface_snapshot import package_driver_classes

SHAKE = ShakeRequest(speed=500, duration=3)
NEST = JointCoordinates(rail=0.0, base=40.0, shoulder=60.0, elbow=250.0, wrist=-310.0)


def _shaker() -> SimShakerDriver:
    return SimShakerDriver(name="shaker_1", sim_strategy=SleepSim(sim_time=0.0))


@pytest.mark.parametrize("cls", [c for c in package_driver_classes() if not inspect.isabstract(c)], ids=lambda c: c.__name__)
def test_every_public_command_of_a_driver_class_is_hooked(cls: type) -> None:
    not_hooked = [
        name for name in dir(cls)
        if not name.startswith("_")
        and inspect.iscoroutinefunction(member := inspect.getattr_static(cls, name))
        and not getattr(member, "__isabstractmethod__", False)
        and not is_hooked(member)
    ]
    assert not_hooked == []


@pytest.mark.asyncio
async def test_a_command_runs_the_hook_once_with_its_name_and_its_request() -> None:
    driver = _shaker()
    log = hook_log(driver)

    await driver.shake(SHAKE)

    assert log == [("shake", SHAKE)]


class _Ordered(BaseSimDriver):
    """Records the order of the hook and the command body."""

    def __init__(self) -> None:
        super().__init__(name="ordered", sim_strategy=SleepSim(sim_time=0.0))
        self.order: list[str] = []

    async def _before_command(self, command: str, request: BaseModel | None) -> None:
        self.order.append(f"before {command}")

    async def blink(self) -> None:
        self.order.append("blink")


@pytest.mark.asyncio
async def test_the_hook_runs_before_the_command() -> None:
    driver = _Ordered()

    await driver.blink()

    assert driver.order == ["before blink", "blink"]


class _CallIds(BaseSimDriver):
    def __init__(self) -> None:
        super().__init__(name="ids", sim_strategy=SleepSim(sim_time=0.0))
        self.seen: list[str | None] = []

    async def outer(self) -> None:
        self.seen.append(current_call_id())
        await self.inner()

    async def inner(self) -> None:
        self.seen.append(current_call_id())


@pytest.mark.asyncio
async def test_two_calls_get_two_call_ids_and_none_is_open_between_commands() -> None:
    driver = _CallIds()

    await driver.inner()
    assert current_call_id() is None
    await driver.inner()

    first, second = driver.seen
    assert first is not None and second is not None
    assert first != second


@pytest.mark.asyncio
async def test_a_command_calling_another_command_on_the_same_driver_runs_the_hook_once_under_one_call_id() -> None:
    driver = _CallIds()
    log = hook_log(driver)

    await driver.outer()

    assert log == [("outer", None)]
    outer_id, inner_id = driver.seen
    assert outer_id is not None and inner_id == outer_id


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [SimTranslatorDriver, HumanTransporterDriver])
async def test_an_override_that_calls_super_runs_the_hook_once(cls: type[SimTransporterDriver]) -> None:
    driver = cls(name="arm", sim_strategy=SleepSim(sim_time=0.0))
    driver._seed_resource("pad_1", PLRResource(name="plate", size_x=1, size_y=1, size_z=1))
    await driver.pick_at_coords(PickAtCoordsRequest(
        teachpoint=Teachpoint(position_id="pad_1", coordinates=NEST), handling=SEED_MOVE_PARAMETERS,
    ))
    log = hook_log(driver)
    place = PlaceAtCoordsRequest(
        teachpoint=Teachpoint(position_id="pad_2", coordinates=NEST), handling=SEED_MOVE_PARAMETERS,
    )

    await driver.place_at_coords(place)

    assert log == [("place_at_coords", place)]


class _RefusingHook(_Ordered):
    async def _before_command(self, command: str, request: BaseModel | None) -> None:
        raise RuntimeError("stalled on purpose")


@pytest.mark.asyncio
async def test_a_hook_that_raises_fails_the_command_before_its_body_and_leaves_no_call_open() -> None:
    driver = _RefusingHook()

    with pytest.raises(RuntimeError, match="stalled on purpose"):
        await driver.blink()

    assert driver.order == []
    assert current_call_id() is None


@pytest.mark.asyncio
async def test_an_injected_fault_fails_its_command_and_the_next_command_runs_the_hook_again() -> None:
    driver = SimShakerDriver(
        name="shaker_1", sim_strategy=SleepSim(sim_time=0.0),
        faults=[RaiseFault(method="shake", message="stalled on purpose")],
    )
    log = hook_log(driver)

    with pytest.raises(RuntimeError, match="stalled on purpose"):
        await driver.shake(SHAKE)
    await driver.initialize()

    assert log == [("shake", SHAKE), ("initialize", None)]


def test_wrapping_keeps_the_signature_and_the_coroutine_function() -> None:
    assert list(inspect.signature(SimShakerDriver.shake).parameters) == ["self", "request"]
    assert inspect.iscoroutinefunction(SimShakerDriver.shake)
