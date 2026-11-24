"""The shared command executor runs a wire command on whatever driver its source names.

orca-client and a check run inside Orca both use it, so a device's simulator
answers the same with or without orca-client.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import ClassVar

import pytest

from cheshire_drivers.command_executor import CommandCancelled, CommandExecutor
from cheshire_drivers.driver_errors import InstrumentOutcome
from cheshire_drivers.gateway_protocol import CommandMessage, EffectiveMode
from cheshire_drivers.interfaces import BaseDriver, ITransporterDriver

pytestmark = pytest.mark.asyncio


class _Lamp(BaseDriver):
    """A driver with one vendor command, counting the times it ran."""

    interfaces: ClassVar[frozenset[str]] = frozenset()

    def __init__(self) -> None:
        self.blinks = 0

    async def initialize(self) -> None:
        return

    @property
    def is_initialized(self) -> bool:
        return True

    async def open(self) -> None:
        return

    async def close(self) -> None:
        return

    async def blink(self) -> None:
        self.blinks += 1


class _HeldLamp(_Lamp):
    """A lamp whose blink runs whatever body a test gives it."""

    def __init__(self) -> None:
        super().__init__()
        self.body: Callable[[], Awaitable[None]] = _nothing

    async def blink(self) -> None:
        await self.body()


async def _nothing() -> None:
    return


async def _until(predicate: Callable[[], Awaitable[bool]]) -> None:
    """Drive the loop until ``predicate`` holds; the timeout only stops a real hang."""
    async def run() -> None:
        while not await predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(run(), timeout=5.0)


class _Source:
    """One device, with a driver per mode it has one for."""

    def __init__(self, drivers: dict[str, _Lamp]) -> None:
        self._drivers = drivers
        self._lock = asyncio.Lock()

    def driver_for(self, device_name: str, effective_mode: EffectiveMode) -> BaseDriver | ITransporterDriver | None:
        if device_name != "lamp_1":
            return None
        return self._drivers.get(effective_mode)

    def lock_for(self, device_name: str) -> asyncio.Lock | None:
        return self._lock if device_name == "lamp_1" else None


def _blink(mode: EffectiveMode, device: str = "lamp_1") -> CommandMessage:
    return CommandMessage(
        command_id=f"{device}-{mode}", device_name=device, command="blink", params={},
        effective_mode=mode,
    )


async def test_a_check_run_command_runs_on_the_simulator_its_source_names() -> None:
    sim = _Lamp()
    executor = CommandExecutor(_Source({"CHECK_RUN": sim}))

    response = await executor.execute(_blink("CHECK_RUN"))

    assert response.success, response.error
    assert sim.blinks == 1


async def test_each_mode_runs_on_its_own_driver() -> None:
    sim, live = _Lamp(), _Lamp()
    executor = CommandExecutor(_Source({"SIM": sim, "LIVE": live}))

    await executor.execute(_blink("SIM"))
    assert (sim.blinks, live.blinks) == (1, 0)

    await executor.execute(_blink("LIVE"))
    assert (sim.blinks, live.blinks) == (1, 1)


async def test_an_unknown_device_is_rejected_without_running_anything() -> None:
    executor = CommandExecutor(_Source({"SIM": _Lamp()}))

    response = await executor.execute(_blink("SIM", device="lamp_9"))

    assert (response.success, response.error_type) == (False, "DeviceNotFoundError")
    assert response.instrument_outcome is InstrumentOutcome.REJECTED


@pytest.mark.parametrize("mode", ["SIM", "CHECK_RUN"])
async def test_a_device_with_no_simulator_says_so(mode: EffectiveMode) -> None:
    executor = CommandExecutor(_Source({"LIVE": _Lamp()}))

    response = await executor.execute(_blink(mode))

    assert (response.success, response.error_type) == (False, "SimDriverNotFoundError")


class _ForwardingLamp(_Lamp):
    runs_hooks: ClassVar[bool] = False


async def test_a_driver_that_runs_no_hooks_is_refused_and_runs_nothing() -> None:
    forwarder = _ForwardingLamp()
    executor = CommandExecutor(_Source({"SIM": forwarder}))

    response = await executor.execute(_blink("SIM"))

    assert (response.success, response.error_type) == (False, "DriverWithoutHooksError")
    assert forwarder.blinks == 0


async def test_cancel_in_flight_command_raises_and_sends_no_response() -> None:
    """A server cancel stops the running command; execute raises
    CommandCancelled so the caller suppresses the response."""
    shaker = _HeldLamp()
    executor = CommandExecutor(_Source({"SIM": shaker}))

    started = asyncio.Event()

    async def blocking_shake() -> None:
        started.set()
        await asyncio.sleep(3600)

    shaker.body = blocking_shake

    cmd = CommandMessage(
        command_id="cancel_me",
        device_name="lamp_1",
        command="blink",
        params={},
        effective_mode="SIM",
    )
    task = asyncio.ensure_future(executor.execute(cmd))
    await asyncio.wait_for(started.wait(), timeout=2.0)

    assert await executor.cancel("cancel_me") is True
    with pytest.raises(CommandCancelled):
        await task
    # Cancelling an already-finished / unknown command is a no-op.
    assert await executor.cancel("cancel_me") is False
    assert await executor.cancel("never_existed") is False


async def test_shutdown_cancel_drains_driver_before_releasing_lock() -> None:
    """A non-server cancel (client shutdown cancels the handler task directly)
    must cancel + drain the driver task before the device lock releases, so the
    driver is never left running while the next command acquires the lock."""
    shaker = _HeldLamp()
    executor = CommandExecutor(_Source({"SIM": shaker}))

    started = asyncio.Event()
    driver_cancelled = asyncio.Event()

    async def blocking_shake() -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            driver_cancelled.set()
            raise

    shaker.body = blocking_shake

    cmd = CommandMessage(
        command_id="shutdown_me",
        device_name="lamp_1",
        command="blink",
        params={},
        effective_mode="SIM",
    )
    task = asyncio.ensure_future(executor.execute(cmd))
    await asyncio.wait_for(started.wait(), timeout=2.0)

    # Shutdown: cancel the handler task itself (NOT executor.cancel()).
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The driver task was cancelled + drained before the lock released; without
    # the fix the driver keeps running and this event never fires (the drain
    # happens inside the device-lock block, before it exits).
    await asyncio.wait_for(driver_cancelled.wait(), timeout=2.0)


async def test_cancel_while_waiting_on_device_lock_suppresses_response() -> None:
    """A cancel that arrives while the command is still queued on the device
    lock (before its driver task registers) must suppress the response and
    never invoke the driver. The lock wait can be minutes on real hardware, so
    this is the wide race window, not a micro one.

    Driven through the public API only: a holder command occupies the device
    lock; the second command queues on it; we cancel the second while it waits
    and assert its driver body never runs (call_count stays 1)."""
    shaker = _HeldLamp()
    executor = CommandExecutor(_Source({"SIM": shaker}))

    call_count = 0
    holder_started = asyncio.Event()

    async def blocking_shake() -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            holder_started.set()
        await asyncio.sleep(3600)

    shaker.body = blocking_shake

    holder = CommandMessage(
        command_id="holder", device_name="lamp_1", command="blink", params={}, effective_mode="SIM",
    )
    waiter = CommandMessage(
        command_id="wait_then_cancel", device_name="lamp_1", command="blink", params={}, effective_mode="SIM",
    )

    holder_task = asyncio.ensure_future(executor.execute(holder))
    await asyncio.wait_for(holder_started.wait(), timeout=2.0)  # holds the lock

    waiter_task = asyncio.ensure_future(executor.execute(waiter))
    # Wait until the waiter has claimed the cache (then it is parked on the
    # holder's device lock). Condition-driven, not a wall-clock guess.
    await _until(lambda: executor._idempotency_cache.is_inflight("wait_then_cancel"))

    # Waiter has no driver task yet, but is claimed in the cache, so the cancel
    # tombstones it (returns False: nothing interrupted).
    assert await executor.cancel("wait_then_cancel") is False

    # Free the lock by cancelling the holder; the waiter then acquires it, sees
    # its tombstone, and raises without ever invoking the driver.
    assert await executor.cancel("holder") is True
    with pytest.raises(CommandCancelled):
        await holder_task
    with pytest.raises(CommandCancelled):
        await waiter_task
    assert call_count == 1, "waiter must not run; only the holder's shake executed"


async def test_cancelled_command_replay_is_suppressed() -> None:
    """A server cancel is terminal: a second arrival of the same command_id
    within the idempotency TTL re-raises CommandCancelled rather than
    re-running the cancelled command."""
    shaker = _HeldLamp()
    executor = CommandExecutor(_Source({"SIM": shaker}))

    started = asyncio.Event()
    call_count = 0

    async def blocking_shake() -> None:
        nonlocal call_count
        call_count += 1
        started.set()
        await asyncio.sleep(3600)

    shaker.body = blocking_shake

    cmd = CommandMessage(
        command_id="replay_me",
        device_name="lamp_1",
        command="blink",
        params={},
        effective_mode="SIM",
    )
    task = asyncio.ensure_future(executor.execute(cmd))
    await asyncio.wait_for(started.wait(), timeout=2.0)

    assert await executor.cancel("replay_me") is True
    with pytest.raises(CommandCancelled):
        await task

    # The same command_id arrives again: it must NOT run the cancelled command.
    with pytest.raises(CommandCancelled):
        await executor.execute(cmd)
    assert call_count == 1, "cancelled command must not re-run on replay"


def _paced_blink(
    mode: EffectiveMode, sim_speedup: float | None = 2.0, nominal_seconds: float | None = 0.4,
) -> CommandMessage:
    return _blink(mode).model_copy(update={"sim_speedup": sim_speedup, "nominal_seconds": nominal_seconds})


async def _seconds_to_run(lamp: _Lamp, command: CommandMessage) -> tuple[float, bool]:
    executor = CommandExecutor(_Source({command.effective_mode: lamp}))
    started = time.monotonic()
    response = await executor.execute(command)
    return time.monotonic() - started, response.success


async def test_a_simulated_command_takes_its_budget_over_the_runs_speedup() -> None:
    elapsed, ok = await _seconds_to_run(_Lamp(), _paced_blink("SIM"))

    # 0.4 / 2 is 0.2 s. The floor sits under it because a Windows timer can fire one 16 ms tick early.
    assert ok
    assert 0.15 <= elapsed < 2.0


class _Clock:
    """Stands in for the executor's `time`, so a test says how long the driver call took."""

    def __init__(self) -> None:
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now


async def _run_a_blink_that_takes(
    driver_seconds: float, monkeypatch: pytest.MonkeyPatch,
) -> bool:
    """Run a SIM blink budgeted 0.4 wall seconds whose driver call takes `driver_seconds` on the executor's clock."""
    clock = _Clock()
    monkeypatch.setattr("cheshire_drivers.command_executor.executor.time", clock)
    lamp = _HeldLamp()

    async def take_that_long() -> None:
        clock.now += driver_seconds

    lamp.body = take_that_long
    response = await CommandExecutor(_Source({"SIM": lamp})).execute(_paced_blink("SIM", nominal_seconds=0.8))
    return response.success


async def test_time_the_driver_already_spent_counts_toward_the_budget(
    recorded_sleeps: list[float], monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert await _run_a_blink_that_takes(0.3, monkeypatch)

    assert recorded_sleeps == [pytest.approx(0.1)]


@pytest.mark.parametrize("driver_seconds", [0.4, 5.0])
async def test_a_driver_call_that_used_the_whole_budget_adds_no_wait(
    driver_seconds: float, recorded_sleeps: list[float], monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert await _run_a_blink_that_takes(driver_seconds, monkeypatch)

    assert recorded_sleeps == []


@pytest.mark.parametrize(
    ("mode", "sim_speedup", "nominal_seconds"),
    [("LIVE", 2.0, 60.0), ("CHECK_RUN", 2.0, 60.0), ("SIM", None, 60.0), ("SIM", 2.0, None)],
)
async def test_a_command_no_run_paces_returns_at_once(
    mode: EffectiveMode, sim_speedup: float | None, nominal_seconds: float | None, recorded_sleeps: list[float],
) -> None:
    lamp = _Lamp()
    executor = CommandExecutor(_Source({mode: lamp}))

    response = await executor.execute(_paced_blink(mode, sim_speedup, nominal_seconds))

    assert response.success and lamp.blinks == 1
    assert recorded_sleeps == []


async def test_a_command_that_raises_is_not_waited_on(recorded_sleeps: list[float]) -> None:
    lamp = _HeldLamp()

    async def fail() -> None:
        raise RuntimeError("bulb blown")

    lamp.body = fail
    executor = CommandExecutor(_Source({"SIM": lamp}))

    response = await executor.execute(_paced_blink("SIM", nominal_seconds=60.0))

    assert (response.success, response.error_type) == (False, "RuntimeError")
    assert recorded_sleeps == []
