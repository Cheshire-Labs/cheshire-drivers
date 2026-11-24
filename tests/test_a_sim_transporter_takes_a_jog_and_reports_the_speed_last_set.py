"""A simulated arm takes a jog on any transporter kind, and reports only a speed something set."""

import pytest

from cheshire_drivers.human_transporter_driver import HumanTransporterDriver
from cheshire_drivers.sims import SimSpeedNotSetError, SimTransporterDriver, SleepSim
from cheshire_drivers.transporter_models import (
    GetJointPositionRequest,
    GetSpeedRequest,
    MoveSingleAxisRelativeRequest,
    MoveSingleAxisRequest,
    SetSpeedRequest,
)
from cheshire_drivers.translator_driver import SimTranslatorDriver


@pytest.mark.asyncio
@pytest.mark.parametrize("driver_cls", [SimTranslatorDriver, HumanTransporterDriver])
async def test_a_sim_translator_and_a_sim_person_accept_both_jogs_and_gain_no_joints(
    driver_cls: type[SimTransporterDriver],
) -> None:
    driver = driver_cls(name="carrier_1", sim_strategy=SleepSim(sim_time=0.0))

    await driver.move_single_axis(MoveSingleAxisRequest(axis="rail", position=100.0))
    await driver.move_single_axis_relative(MoveSingleAxisRelativeRequest(axis="rail", distance=10.0))

    with pytest.raises(NotImplementedError, match="no joints to read"):
        await driver.get_joint_position(GetJointPositionRequest())


@pytest.mark.asyncio
async def test_a_sim_arm_refuses_a_speed_read_until_one_is_set_then_returns_the_one_last_set() -> None:
    driver = SimTransporterDriver(name="pf400_1", sim_strategy=SleepSim(sim_time=0.0))
    with pytest.raises(SimSpeedNotSetError):
        await driver.get_speed(GetSpeedRequest())

    await driver.set_speed(SetSpeedRequest(speed=0.3))
    await driver.set_speed(SetSpeedRequest(speed=0.7))

    assert await driver.get_speed(GetSpeedRequest()) == 0.7
