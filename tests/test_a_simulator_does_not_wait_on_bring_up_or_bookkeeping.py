"""In simulation, bring-up and a command that moves nothing return at once; a command that moves waits its estimate."""

import asyncio
from typing import ClassVar

import pytest
from pydantic import JsonValue

from cheshire_drivers.command_executor import CommandExecutor
from cheshire_drivers.command_timings import (
    collect_command_timings,
    command_timing,
    simulator_does_not_wait,
    simulator_waits_for,
)
from cheshire_drivers.gateway_protocol import CommandMessage, EffectiveMode
from cheshire_drivers.interfaces import BaseDriver, ITransporterDriver
from cheshire_drivers.labware_models import LabwareIdentity
from cheshire_drivers.move_parameters import SEED_MOVE_PARAMETERS
from cheshire_drivers.shaker_models import ShakeRequest
from cheshire_drivers.sims import SimShakerDriver, SimStrategy, SimTransporterDriver
from cheshire_drivers.teachpoints import CartesianCoordinates, Teachpoint
from cheshire_drivers.transporter_models import EnsureSeededRequest, InitializeRequest, PickAtCoordsRequest

pytestmark = pytest.mark.asyncio

_PLATE = LabwareIdentity(labware_id="plate-1", labware_type="sample_plate")


class _NoWaitOfItsOwn(SimStrategy):
    async def sim(self, prompt: str) -> None:
        return


class _OneDevice:
    def __init__(self, driver: BaseDriver | ITransporterDriver) -> None:
        self._driver = driver
        self._lock = asyncio.Lock()

    def driver_for(self, device_name: str, effective_mode: EffectiveMode) -> BaseDriver | ITransporterDriver | None:
        return self._driver

    def lock_for(self, device_name: str) -> asyncio.Lock | None:
        return self._lock


async def _run_in_sim_at_real_time(
    driver: BaseDriver | ITransporterDriver, command: str, params: dict[str, JsonValue], estimate: float,
) -> None:
    response = await CommandExecutor(_OneDevice(driver)).execute(CommandMessage(
        command_id=command, device_name="device_1", command=command, params=params,
        effective_mode="SIM", sim_speedup=1.0, nominal_seconds=estimate,
    ))
    assert response.success, response.error


def _shaker() -> SimShakerDriver:
    return SimShakerDriver(name="shaker_1", sim_strategy=_NoWaitOfItsOwn())


def _arm() -> SimTransporterDriver:
    return SimTransporterDriver(name="arm_1", sim_strategy=_NoWaitOfItsOwn())


def _seeded(position_id: str) -> dict[str, JsonValue]:
    return EnsureSeededRequest(position_id=position_id, labware=_PLATE).model_dump(mode="json")


def _pick(position_id: str) -> dict[str, JsonValue]:
    teachpoint = Teachpoint(
        position_id=position_id,
        coordinates=CartesianCoordinates(x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0, roll=0.0),
        orientation="left",
        access_type="vertical",
        vertical_clearance=20.0,
    )
    return PickAtCoordsRequest(
        teachpoint=teachpoint, labware_type="sample_plate", handling=SEED_MOVE_PARAMETERS,
    ).model_dump(mode="json")


async def test_a_simulated_initialize_does_not_wait(recorded_sleeps: list[float]) -> None:
    await _run_in_sim_at_real_time(_shaker(), "initialize", {}, estimate=30.0)

    assert recorded_sleeps == []


async def test_a_simulated_arm_initialize_does_not_wait(recorded_sleeps: list[float]) -> None:
    await _run_in_sim_at_real_time(_arm(), "initialize", InitializeRequest().model_dump(mode="json"), estimate=30.0)

    assert recorded_sleeps == []


async def test_telling_a_simulated_arm_where_a_plate_is_does_not_wait(recorded_sleeps: list[float]) -> None:
    await _run_in_sim_at_real_time(_arm(), "ensure_seeded", _seeded("pad_1"), estimate=1.0)

    assert recorded_sleeps == []


async def test_a_simulated_shake_still_waits_its_estimate(recorded_sleeps: list[float]) -> None:
    shake = ShakeRequest(speed=500, duration=60).model_dump(mode="json")

    await _run_in_sim_at_real_time(_shaker(), "shake", shake, estimate=60.0)

    assert recorded_sleeps == [pytest.approx(60.0, abs=1.0)]


async def test_a_simulated_arm_pick_still_waits_its_estimate(recorded_sleeps: list[float]) -> None:
    arm = _arm()
    await arm.ensure_seeded(EnsureSeededRequest(position_id="pad_1", labware=_PLATE))

    await _run_in_sim_at_real_time(arm, "pick_at_coords", _pick("pad_1"), estimate=3.0)

    assert recorded_sleeps == [pytest.approx(3.0, abs=1.0)]


class _Marked:
    interfaces: ClassVar[frozenset[str]] = frozenset()

    @simulator_does_not_wait
    @command_timing(typical=1.0, max=5.0)
    async def note(self) -> None:
        return

    @command_timing(typical=1.0, max=5.0)
    async def move(self) -> None:
        return


async def test_the_mark_says_a_simulator_does_not_wait_and_leaves_the_time_limit_alone() -> None:
    assert not simulator_waits_for(_Marked.note)
    assert simulator_waits_for(_Marked.move)
    assert collect_command_timings(_Marked)["note"] == collect_command_timings(_Marked)["move"]
