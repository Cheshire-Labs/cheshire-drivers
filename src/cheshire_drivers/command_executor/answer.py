"""Answer one wire command and report a driver's link: one copy for orca-client and a check run."""

import asyncio
import logging
from typing import Awaitable, Callable

from cheshire_drivers.command_executor.executor import CommandCancelled, CommandExecutor
from cheshire_drivers.driver_errors import report_failure
from cheshire_drivers.gateway_protocol import CommandMessage, DeviceLinkInfo, DriverMode, ResponseMessage
from cheshire_drivers.interfaces import BaseDriver, ITransporterDriver

logger = logging.getLogger("cheshire_drivers.command_executor")


async def answer_command(
    executor: CommandExecutor,
    command: CommandMessage,
    *,
    respond: Callable[[ResponseMessage], Awaitable[None]],
    report_status: Callable[[], Awaitable[None]],
) -> None:
    """Run the command, report device status, then respond. A failed status report is logged, never blocks it."""
    try:
        response = await executor.execute(command)
    except CommandCancelled:
        logger.info("Command %s cancelled by server; no response sent", command.command_id)
        await _report_status_or_log(report_status, command)
        return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("Error handling command %s: %s", command.command_id, exc, exc_info=True)
        await _report_status_or_log(report_status, command)
        # No outcome on purpose: the error may come from before or after a driver ran, and the server
        # reads a missing outcome as a failure, which is the safe assumption.
        report = report_failure(exc)
        response = ResponseMessage(
            command_id=command.command_id, success=False, result=None,
            error=report.error, error_type=report.error_type,
        )
        await respond(response)
        return
    # Status goes first: the server reads in order, and the response unblocks the caller.
    await _report_status_or_log(report_status, command)
    await respond(response)
    logger.info("Command %s completed: %s", command.command_id, response.success)


async def _report_status_or_log(report_status: Callable[[], Awaitable[None]], command: CommandMessage) -> None:
    try:
        await report_status()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Could not report device status after command %s", command.command_id)


def read_link(device_name: str, mode: DriverMode, driver: BaseDriver | ITransporterDriver) -> DeviceLinkInfo:
    """One driver's link state, reported closed when it cannot answer, so one bad driver cannot blank the rest."""
    try:
        return DeviceLinkInfo(is_connected=driver.is_connected, is_initialized=driver.is_initialized)
    except Exception:
        logger.error("Could not read %s link state for device %r; reporting it closed", mode, device_name, exc_info=True)
        return DeviceLinkInfo(is_connected=False, is_initialized=False)
