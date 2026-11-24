"""Pydantic<->PLR Protocol conversion + wrap-payload validation for thermocycler."""

import pytest
from pydantic import ValidationError

from cheshire_drivers.interfaces import IThermocyclerDriver
from cheshire_drivers.plr import ChatterboxThermocyclerDriver
from cheshire_drivers.plr_wrappers import _to_plr_protocol
from cheshire_drivers.sims import SimThermocyclerDriver
from cheshire_drivers.thermocycler_models import (
    GetBlockCurrentTemperatureRequest,
    GetBlockTargetTemperatureRequest,
    GetLidOpenRequest,
    GetTotalStepCountRequest,
    OpenLidRequest,
    Protocol,
    RunProtocolRequest,
    SetBlockTemperatureRequest,
    Stage,
    Step,
    TemperatureListResponse,
)
from pydantic import JsonValue

from cheshire_drivers.command_table import COMMANDS, DriverKwargs
from pylabrobot.legacy.thermocycling.standard import Protocol as PLRProtocol


def _sample_protocol() -> Protocol:
    return Protocol(
        stages=[
            Stage(
                steps=[
                    Step(temperature=[95.0], hold_seconds=30.0, rate=None),
                    Step(temperature=[60.0], hold_seconds=45.0, rate=2.5),
                ],
                repeats=3,
            ),
            Stage(
                steps=[Step(temperature=[72.0], hold_seconds=300.0, rate=None)],
                repeats=1,
            ),
        ]
    )


def _wrap(command: str, params: dict[str, JsonValue]) -> DriverKwargs:
    found = COMMANDS.find({"IThermocycler"}, command)
    assert found is not None, command
    return found.driver_kwargs(params)


def test_to_plr_protocol_maps_fields_one_to_one() -> None:
    pydantic_protocol = _sample_protocol()
    plr_protocol = _to_plr_protocol(pydantic_protocol)

    assert isinstance(plr_protocol, PLRProtocol)
    assert len(plr_protocol.stages) == 2
    first_stage = plr_protocol.stages[0]
    assert first_stage.repeats == 3
    assert len(first_stage.steps) == 2
    assert first_stage.steps[0].temperature == [95.0]
    assert first_stage.steps[0].hold_seconds == 30.0
    assert first_stage.steps[0].rate is None
    assert first_stage.steps[1].rate == 2.5
    assert plr_protocol.stages[1].steps[0].temperature == [72.0]


def test_wrap_thermocycler_payload_wraps_run_protocol() -> None:
    payload = {
        "protocol": _sample_protocol().model_dump(),
        "block_max_volume": 25.0,
    }
    wrapped = _wrap("run_protocol", payload)
    assert set(wrapped.keys()) == {"request"}
    assert isinstance(wrapped["request"], RunProtocolRequest)
    assert wrapped["request"].block_max_volume == 25.0


def test_wrap_thermocycler_payload_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError, match="bogus"):
        _wrap(
            "set_block_temperature", {"temperature": [95.0], "bogus": 1},
        )


def test_run_protocol_request_rejects_nonpositive_block_max_volume() -> None:
    with pytest.raises(ValidationError):
        RunProtocolRequest(protocol=_sample_protocol(), block_max_volume=0.0)


def test_wrap_thermocycler_payload_wraps_empty_marker_getter() -> None:
    wrapped = _wrap("get_lid_open", {})
    assert set(wrapped.keys()) == {"request"}


@pytest.mark.asyncio
async def test_chatterbox_thermocycler_run_and_getters(capsys) -> None:
    driver = ChatterboxThermocyclerDriver()
    assert isinstance(driver, IThermocyclerDriver)
    assert driver.interfaces == frozenset({"IThermocycler"})

    await driver.initialize()
    await driver.set_block_temperature(SetBlockTemperatureRequest(temperature=[95.0]))
    await driver.run_protocol(
        RunProtocolRequest(protocol=_sample_protocol(), block_max_volume=25.0),
    )

    block = await driver.get_block_current_temperature(
        GetBlockCurrentTemperatureRequest(),
    )
    assert isinstance(block, TemperatureListResponse)

    steps = await driver.get_total_step_count(GetTotalStepCountRequest())
    assert steps.count == 7


@pytest.mark.asyncio
async def test_sim_thermocycler_state_tracks_mutations() -> None:
    driver = SimThermocyclerDriver("thermocycler_1")
    await driver.initialize()

    lid = await driver.get_lid_open(GetLidOpenRequest())
    assert lid.open is True

    await driver.open_lid(OpenLidRequest())
    assert (await driver.get_lid_open(GetLidOpenRequest())).open is True

    await driver.set_block_temperature(SetBlockTemperatureRequest(temperature=[95.0]))
    block = await driver.get_block_current_temperature(
        GetBlockCurrentTemperatureRequest(),
    )
    assert block.temperatures == [95.0]

    await driver.run_protocol(
        RunProtocolRequest(protocol=_sample_protocol(), block_max_volume=25.0),
    )
    steps = await driver.get_total_step_count(GetTotalStepCountRequest())
    assert steps.count == 7


@pytest.mark.asyncio
async def test_sim_rejects_wrong_zone_temperature() -> None:
    driver = SimThermocyclerDriver("thermocycler_1")
    await driver.initialize()
    with pytest.raises(ValueError, match="single zone"):
        await driver.set_block_temperature(
            SetBlockTemperatureRequest(temperature=[95.0, 60.0]),
        )


@pytest.mark.asyncio
async def test_sim_target_getter_raises_when_unset() -> None:
    driver = SimThermocyclerDriver("thermocycler_1")
    await driver.initialize()
    with pytest.raises(RuntimeError, match="not set"):
        await driver.get_block_target_temperature(GetBlockTargetTemperatureRequest())
