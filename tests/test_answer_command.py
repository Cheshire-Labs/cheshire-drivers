"""A command is answered the same by orca-client and by a check run's client inside Orca."""

import asyncio

import pytest

from cheshire_drivers.command_executor import CommandExecutor, answer_command, read_link
from cheshire_drivers.gateway_protocol import CommandMessage, EffectiveMode, ResponseMessage
from cheshire_drivers.interfaces import BaseDriver, ITransporterDriver
from tests.test_executor import _blink, _HeldLamp, _Lamp, _Source, _until

pytestmark = pytest.mark.asyncio


class _Said:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.responses: list[ResponseMessage] = []

    async def respond(self, response: ResponseMessage) -> None:
        self.said.append("response")
        self.responses.append(response)

    async def report_status(self) -> None:
        self.said.append("status")


async def _answer(executor: CommandExecutor, command: CommandMessage, said: _Said) -> None:
    await answer_command(executor, command, respond=said.respond, report_status=said.report_status)


async def _held_until_cancelled() -> None:
    await asyncio.Event().wait()


async def test_status_is_reported_before_the_response() -> None:
    said = _Said()

    await _answer(CommandExecutor(_Source({"SIM": _Lamp()})), _blink("SIM"), said)

    assert said.said == ["status", "response"]
    assert said.responses[0].success


class _BrokenSource(_Source):
    def driver_for(self, device_name: str, effective_mode: EffectiveMode) -> BaseDriver | ITransporterDriver | None:
        raise RuntimeError("the registry is gone")


async def test_an_error_the_executor_does_not_handle_still_gets_a_failed_response() -> None:
    said = _Said()

    await _answer(CommandExecutor(_BrokenSource({})), _blink("SIM"), said)

    assert said.said == ["status", "response"]
    assert (said.responses[0].success, said.responses[0].error_type) == (False, "RuntimeError")


class _StatusThatFails(_Said):
    async def report_status(self) -> None:
        self.said.append("status")
        raise RuntimeError("the tracker is gone")


async def test_a_status_report_that_fails_still_lets_the_response_through() -> None:
    said = _StatusThatFails()

    await _answer(CommandExecutor(_Source({"SIM": _Lamp()})), _blink("SIM"), said)

    assert said.said == ["status", "response"]
    assert said.responses[0].success


async def test_a_status_report_that_fails_after_an_unhandled_error_still_lets_the_failed_response_through() -> None:
    said = _StatusThatFails()

    await _answer(CommandExecutor(_BrokenSource({})), _blink("SIM"), said)

    assert said.said == ["status", "response"]
    assert (said.responses[0].success, said.responses[0].error_type) == (False, "RuntimeError")


async def test_a_command_the_server_cancelled_gets_no_response() -> None:
    lamp = _HeldLamp()
    lamp.body = _held_until_cancelled
    executor = CommandExecutor(_Source({"SIM": lamp}))
    said = _Said()
    command = _blink("SIM")

    answering = asyncio.create_task(_answer(executor, command, said))
    await _until(lambda: executor.cancel(command.command_id))
    await answering

    assert said.said == ["status"]


async def test_a_cancelled_command_whose_status_report_fails_still_ends_quietly() -> None:
    lamp = _HeldLamp()
    lamp.body = _held_until_cancelled
    executor = CommandExecutor(_Source({"SIM": lamp}))
    said = _StatusThatFails()
    command = _blink("SIM")

    answering = asyncio.create_task(_answer(executor, command, said))
    await _until(lambda: executor.cancel(command.command_id))
    await answering

    assert said.said == ["status"]


class _Unreadable(_Lamp):
    @property
    def is_initialized(self) -> bool:
        raise RuntimeError("no answer")


async def test_a_driver_that_cannot_say_its_link_state_reads_as_closed() -> None:
    link = read_link("lamp_1", "SIM", _Unreadable())

    assert (link.is_connected, link.is_initialized) == (False, False)
