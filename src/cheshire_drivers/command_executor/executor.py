"""Runs a wire command on a device's driver and builds the wire response.

One copy for every place a driver runs a command: orca-client, and a check run
inside Orca. A device's simulator answers the same either way.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable
from typing import Dict, Optional, Protocol

from pydantic import BaseModel, JsonValue, ValidationError

from cheshire_drivers.driver_hooks import DriverWithoutHooksError, refuse_unless_it_runs_hooks
from cheshire_drivers.sim_pacing import wall_seconds
from cheshire_drivers.command_table import COMMANDS, DeviceCommand, DriverKwargs
from cheshire_drivers.command_timings import simulator_waits_for
from cheshire_drivers.command_responses import EmptyCommandResponse
from cheshire_drivers.driver_errors import InstrumentOutcome, report_failure

from cheshire_drivers.interfaces import BaseDriver, ITransporterDriver
from cheshire_drivers.gateway_protocol import CommandMessage, EffectiveMode, ResponseMessage
from cheshire_drivers.command_executor.idempotency_cache import CommandIdempotencyCache

logger = logging.getLogger("cheshire_drivers.command_executor")


class IDriverSource(Protocol):
    """Where the executor finds a device's driver and the lock that serializes its commands."""

    def driver_for(
        self, device_name: str, effective_mode: EffectiveMode,
    ) -> BaseDriver | ITransporterDriver | None:
        """The driver that answers in this mode, or None when the device has none."""
        ...

    def lock_for(self, device_name: str) -> asyncio.Lock | None:
        """The device's command lock, or None once the device is gone."""
        ...

class CommandCancelled(Exception):
    """Raised inside the executor when an in-flight command is cancelled.

    Distinct from a bare ``asyncio.CancelledError`` (which means the handling
    task itself is being torn down, e.g. on shutdown): ``CommandCancelled``
    means the server asked us to stop this specific command, so the handler
    discards it and sends no response. The command's eventual driver result,
    if any, is dropped.
    """

    def __init__(self, command_id: str) -> None:
        self.command_id = command_id
        super().__init__(f"command {command_id} cancelled by server")


async def _spending_its_budget(
    cmd: CommandMessage, call: Awaitable[BaseModel | JsonValue], waits: bool,
) -> BaseModel | JsonValue:
    """Run a simulated command, then wait out what is left of the time the engine budgets for it.

    The budget is `nominal_seconds` at the run's speed-up, and what the driver already spent counts toward it,
    so a simulator that takes real time is not made slower. A live command, one no run paces, and one marked
    `@simulator_does_not_wait` are not waited on.
    """
    started = time.monotonic()
    result = await call
    if waits and cmd.effective_mode == "SIM" and cmd.sim_speedup is not None and cmd.nominal_seconds:
        left = wall_seconds(cmd.nominal_seconds, cmd.sim_speedup) - (time.monotonic() - started)
        if left > 0:
            await asyncio.sleep(left)
    return result


class CommandExecutor:
    """Executes string-based commands on devices with validation and locking."""

    def __init__(
        self,
        source: IDriverSource,
        idempotency_cache: Optional[CommandIdempotencyCache] = None,
    ):
        """Initialize command executor.

        Args:
            source: Where each device's driver and lock come from
            idempotency_cache: Optional shared cache for command_id
                de-duplication. A command_id that arrives a second time
                never runs the driver again (an in-flight duplicate
                awaits the original; a completed duplicate returns the
                cached ResponseMessage). Defaults to a fresh cache
                with the cache's default TTL.

        Commands run with no client-side duration bound: the engine owns the
        timeout (a long-running command that overruns its expected duration is
        an operator decision, not a client-side failure). The server cancels a
        command it no longer wants via a cancel message, which cancels the
        in-flight task here.
        """
        self._source = source
        self._idempotency_cache = (
            idempotency_cache
            if idempotency_cache is not None
            else CommandIdempotencyCache()
        )
        # In-flight driver tasks keyed by command_id, so a server cancel can
        # stop the running command. ``_cancelled_ids`` distinguishes a
        # server-requested cancel from task teardown when the CancelledError
        # surfaces inside the driver await.
        self._inflight_tasks: Dict[str, asyncio.Task[BaseModel | JsonValue]] = {}
        self._cancelled_ids: set[str] = set()

    async def cancel(self, command_id: str) -> bool:
        """Cancel a command. Returns True if a running driver task was
        interrupted, False otherwise.

        Always records the cancel as a tombstone, even when no driver task is
        in flight yet: a command can sit for minutes waiting on the device
        lock before its task registers, and a cancel arriving in that window
        must still suppress the command's response rather than being lost.
        ``execute`` re-checks the tombstone after acquiring the lock and after
        the driver finishes, so the cancelled command produces no response.
        """
        if not await self._idempotency_cache.is_inflight(command_id):
            # Nothing running under this id (finished, TTL-retained, or
            # unknown): no response to suppress, and tombstoning it would
            # leak since no command execution would ever clear it.
            return False
        self._cancelled_ids.add(command_id)
        task = self._inflight_tasks.get(command_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def execute(self, cmd: CommandMessage) -> ResponseMessage:
        """Execute a command and return response.

        De-duplicates on ``cmd.command_id`` via :class:`CommandIdempotencyCache`:
        in-flight duplicates await the original execution; completed duplicates
        return the cached ResponseMessage without re-invoking the driver.

        Args:
            cmd: Command message from platform

        Returns:
            Response message with result or error
        """
        future, is_new = await self._idempotency_cache.claim(cmd.command_id)
        if not is_new:
            logger.info(
                "Duplicate command_id=%r received; awaiting original execution "
                "(device=%r, command=%r). Driver NOT re-invoked.",
                cmd.command_id, cmd.device_name, cmd.command,
            )
            return await future

        try:
            response = await self._execute_uncached(cmd)
        except CommandCancelled as exc:
            # A server cancel is terminal: retain the entry for the TTL so a second
            # arrival of this command_id re-raises the cancellation and runs nothing.
            await self._idempotency_cache.record_cancelled(cmd.command_id, exc)
            raise
        except BaseException as exc:
            # The original execute_uncached path itself raised (not a driver
            # error reshaped to a ResponseMessage, but something unexpected).
            # Propagate to any duplicate awaiter and re-raise.
            await self._idempotency_cache.fail(cmd.command_id, exc)
            raise

        await self._idempotency_cache.complete(cmd.command_id, response)
        return response

    async def _execute_uncached(self, cmd: CommandMessage) -> ResponseMessage:
        """Run the command without idempotency de-duplication. The public
        :meth:`execute` wraps this in a cache claim so a command_id runs at most once."""
        logger.info(
            f"Executing: {cmd.command} on {cmd.device_name} "
            f"(effective_mode={cmd.effective_mode})"
        )

        # Block private methods (security)
        if cmd.command.startswith('_'):
            logger.warning(f"Attempted to call private method: {cmd.command}")
            return self._error_response(
                cmd.command_id,
                f"Private methods are not allowed: {cmd.command}",
                "SecurityError"
            )

        driver = self._source.driver_for(cmd.device_name, cmd.effective_mode)
        lock = self._source.lock_for(cmd.device_name)
        if driver is None or lock is None:
            if cmd.effective_mode != "LIVE" and lock is not None:
                logger.error("Device %r has no driver for %s commands here", cmd.device_name, cmd.effective_mode)
                return self._error_response(
                    cmd.command_id,
                    f"Device {cmd.device_name} has no driver for {cmd.effective_mode} commands on this client",
                    "SimDriverNotFoundError",
                )
            logger.error(f"Device not found: {cmd.device_name}")
            return self._error_response(
                cmd.command_id,
                f"Device {cmd.device_name} not found",
                "DeviceNotFoundError"
            )

        try:
            refuse_unless_it_runs_hooks(cmd.device_name, driver)
        except DriverWithoutHooksError as refused:
            logger.error("%s", refused)
            return self._error_response(cmd.command_id, str(refused), "DriverWithoutHooksError")

        try:
            entry = COMMANDS.find(getattr(type(driver), "interfaces", frozenset()), cmd.command)
        except ValueError as e:
            # Two of the driver's interfaces define this name differently; nothing ran.
            logger.error(f"Device {cmd.device_name} cannot tell which {cmd.command} to run: {e}")
            return self._error_response(cmd.command_id, str(e), "AmbiguousCommandError")

        # A property IS its own read: `getattr` runs the getter, so the read
        # has to happen where a failing one can be classified.
        if entry is not None and entry.is_property:
            try:
                raw = getattr(driver, cmd.command)
            except AttributeError as e:
                return self._unsupported(cmd, e)
            except Exception as e:
                # Reading a property actuates nothing, so a read that raises
                # cannot have left the instrument part-way through anything.
                # A driver that says otherwise about its own error is believed.
                logger.error(f"Property access {cmd.command} failed: {e}", exc_info=True)
                report = report_failure(e)
                outcome = report.instrument_outcome
                return self._error_response(
                    cmd.command_id,
                    report.error,
                    report.error_type,
                    outcome if outcome.moved_nothing else InstrumentOutcome.REJECTED,
                )
            logger.debug(f"Property {cmd.command} = {raw}")
            return ResponseMessage(
                command_id=cmd.command_id,
                success=True,
                result=self._wrap_result(cmd.command, raw, entry),
            )

        # A forwarding driver says WHY it cannot resolve a name (optional
        # hardware absent, not connected yet); that reason reaches the operator.
        try:
            member = getattr(driver, cmd.command)
        except AttributeError as e:
            return self._unsupported(cmd, e)

        # Verify method is async (for non-property commands)
        method = member
        if not asyncio.iscoroutinefunction(method):
            logger.error(f"Command {cmd.command} is not async on {cmd.device_name}")
            return self._error_response(
                cmd.command_id,
                f"Command {cmd.command} is not async",
                "NotAsyncError"
            )

        # Execute under the per-device lock with NO client-side duration bound:
        # the engine owns the timeout. The driver call runs as a tracked task so
        # a server cancel can stop it.
        async with lock:
            if self._source.driver_for(cmd.device_name, cmd.effective_mode) is driver:
                try:
                    if cmd.command_id in self._cancelled_ids:
                        # Cancelled while waiting for the device lock -- never start
                        # the driver; the server is no longer awaiting a response.
                        raise CommandCancelled(cmd.command_id)
                    try:
                        params = self._deserialize_params(cmd.params, entry)
                    except (ValueError, ValidationError, TypeError) as e:
                        # Its own catch, and not the driver's: wrapping a payload
                        # into a Request touches no hardware, so this is a refusal
                        # however the driver's own failures are classified.
                        logger.error(f"Invalid parameters for {cmd.command}: {e}")
                        return self._error_response(
                            cmd.command_id,
                            f"Invalid parameters: {e}",
                            "ParameterError",
                        )
                    try:
                        call = method(**params)
                    except TypeError as e:
                        # Binding the arguments, not running the body: a coroutine
                        # function raises this at the call and its body never
                        # starts. Outside this catch it read as the driver's own
                        # failure and faulted a device nothing had touched.
                        logger.error(f"Invalid parameters for {cmd.command}: {e}")
                        return self._error_response(
                            cmd.command_id,
                            f"Invalid parameters: {e}",
                            "ParameterError",
                        )
                    driver_task: asyncio.Task[BaseModel | JsonValue] = asyncio.ensure_future(
                        _spending_its_budget(cmd, call, entry is None or simulator_waits_for(entry.function)),
                    )
                    self._inflight_tasks[cmd.command_id] = driver_task
                    try:
                        raw = await driver_task
                    except asyncio.CancelledError:
                        # The await was interrupted -- a server cancel (task already
                        # cancelled) or client shutdown (task NOT cancelled). Stop the
                        # driver task and wait for it to unwind BEFORE the device lock
                        # releases below, so the next command can't start on the same
                        # driver while this call is still physically running.
                        driver_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await driver_task
                        if cmd.command_id in self._cancelled_ids:
                            raise CommandCancelled(cmd.command_id)
                        raise
                    finally:
                        self._inflight_tasks.pop(cmd.command_id, None)

                    if cmd.command_id in self._cancelled_ids:
                        # Cancel landed just as the driver finished -- still suppress
                        # the response so a cancelled command never replies.
                        raise CommandCancelled(cmd.command_id)

                    logger.debug(f"Command {cmd.command_id} completed successfully")
                    try:
                        wrapped = self._wrap_result(cmd.command, raw, entry)
                    except Exception as e:
                        # The command SUCCEEDED and we cannot project its answer.
                        # Nothing is wrong with the instrument, so this must not
                        # fault it; it is our own shape that is broken, and no
                        # amount of asking again fixes that.
                        logger.error(
                            f"Cannot project the result of {cmd.command}: {e}",
                            exc_info=True,
                        )
                        return self._error_response(
                            cmd.command_id,
                            f"Command succeeded; its result could not be read: {e}",
                            "ResultProjectionError",
                        )
                    return ResponseMessage(
                        command_id=cmd.command_id,
                        success=True,
                        result=wrapped,
                    )

                except CommandCancelled:
                    # Server cancelled this command; no response. Re-raise so the
                    # handler suppresses the reply (and the cache awaiter is failed).
                    logger.info("Command %s cancelled by server", cmd.command_id)
                    raise
                except Exception as e:
                    # Past the parameter catch above, so this is the driver's own
                    # failure and the driver says what it left the instrument in.
                    # An error that has not said reads FAILED.
                    logger.error(f"Command {cmd.command_id} failed: {e}", exc_info=True)
                    report = report_failure(e)
                    return self._error_response(
                        cmd.command_id, report.error, report.error_type, report.instrument_outcome,
                    )
                finally:
                    self._cancelled_ids.discard(cmd.command_id)
        if self._source.lock_for(cmd.device_name) is None:
            self._cancelled_ids.discard(cmd.command_id)
            logger.error("Device %r was removed while command %s waited for it", cmd.device_name, cmd.command_id)
            return self._error_response(
                cmd.command_id,
                f"Device {cmd.device_name} was removed by a new assignment while this command waited. It did not run.",
                "DeviceRemovedError",
            )
        # The device was replaced while this command waited, so resolve it again against the new driver.
        return await self._execute_uncached(cmd)

    def _wrap_result(
        self, command: str, raw: BaseModel | JsonValue, entry: DeviceCommand | None,
    ) -> JsonValue:
        """Project the driver's raw return into the typed Response wire shape.

        Pairs each command with its Response model from cheshire-drivers' command table, then:

          * dumps an existing BaseModel via ``model_dump(mode='json')``;
          * sends ``EmptyCommandResponse().model_dump()`` for ``None``
            (ack-only commands) so the wire ``result`` field is always
            populated;
          * projects a primitive (bool / float / int / str) onto the
            Response's single field when the per-op model has one field
            (InitializedResponse.is_initialized,
            TemperatureResponse.temperature, SpeedResponse.speed);
          * validates a dict against the response_cls and dumps it.

        Raises ``ValueError`` (surfaced as ``ResultProjectionError``) on shapes
        that don't match the response model. A vendor extra answers with
        ``EmptyCommandResponse``, so a primitive it returns is an error rather
        than silently dropped.
        """
        response_cls = EmptyCommandResponse if entry is None else entry.response_model

        if isinstance(raw, BaseModel):
            return raw.model_dump(mode="json")
        if raw is None:
            return EmptyCommandResponse().model_dump(mode="json")
        if isinstance(raw, (bool, float, int, str)):
            fields = list(response_cls.model_fields.keys())
            if len(fields) == 1:
                try:
                    return response_cls.model_validate(
                        {fields[0]: raw}
                    ).model_dump(mode="json")
                except ValidationError as exc:
                    raise ValueError(
                        f"Cannot project primitive {raw!r} for {command!r} "
                        f"onto {response_cls.__name__}: {exc}"
                    ) from exc
            raise ValueError(
                f"Primitive driver return {raw!r} for {command!r} cannot "
                f"project onto {response_cls.__name__} "
                f"(expected single-field model, got fields={fields})"
            )
        if isinstance(raw, dict):
            try:
                return response_cls.model_validate(raw).model_dump(mode="json")
            except ValidationError as exc:
                raise ValueError(
                    f"Driver dict for {command!r} did not validate as "
                    f"{response_cls.__name__}: {exc}"
                ) from exc
        raise ValueError(
            f"Unhandled driver return shape {type(raw).__name__} for "
            f"{command!r}; expected None, BaseModel, dict, or primitive"
        )

    def _unsupported(self, cmd: CommandMessage, e: AttributeError) -> ResponseMessage:
        """The driver has no such name, and it says why (optional hardware
        absent, not connected yet). That reason reaches the operator."""
        logger.error(f"Device {cmd.device_name} cannot resolve {cmd.command}: {e}")
        return self._error_response(
            cmd.command_id,
            f"Device {cmd.device_name} does not support command {cmd.command}: {e}",
            "UnsupportedCommandError",
        )

    def _error_response(
        self,
        command_id: str,
        error: str,
        error_type: str,
        outcome: InstrumentOutcome = InstrumentOutcome.REJECTED,
    ) -> ResponseMessage:
        """Create error response message.

        ``REJECTED`` by default because most of these never reach a driver and
        never will: a command this agent does not have, a device it does not
        hold, a payload that will not deserialize. Nothing was actuated, so the
        control plane must not fault the device -- and nothing is gained by
        asking again, which is what separates it from ``REFUSED``. A caller
        that waits on one of these waits out its whole patience budget and then
        reports a cause it never established.

        A failure that came out of a driver call passes what the driver said.
        """
        return ResponseMessage(
            command_id=command_id,
            success=False,
            result=None,
            error=error,
            error_type=error_type,
            instrument_outcome=outcome,
        )

    def _deserialize_params(self, params: Dict[str, JsonValue], entry: DeviceCommand | None) -> DriverKwargs:
        """The driver's keyword arguments, validated against the command's request model.

        A vendor extra's params pass through. Raises ValueError or
        pydantic.ValidationError on invalid payloads; the caller surfaces both as
        ParameterError.
        """
        if entry is None:
            return dict(params)
        return entry.driver_kwargs(params)
