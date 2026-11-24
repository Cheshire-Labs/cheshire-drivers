"""The executor runs each command_id at most once.

A side-effecting driver command must run at most once, even if its
command_id arrives a second time:

1. In-flight duplicate: a second arrival while the first is still executing
   awaits the same future and returns the same response without re-invoking
   the driver method.
2. Completed cache: a re-arrival after the first response was sent returns
   the cached ResponseMessage without re-invoking the driver method.
3. TTL eviction: after the cache TTL elapses, the same command_id is treated
   as fresh and the driver method runs again.
"""

import asyncio
from typing import ClassVar

import pytest

from cheshire_drivers.command_executor import CommandExecutor, CommandIdempotencyCache
from cheshire_drivers.gateway_protocol import CommandMessage, EffectiveMode
from cheshire_drivers.interfaces import BaseDriver, ITransporterDriver
from cheshire_drivers.shaker_models import ShakeRequest


class _CallCountingShaker(BaseDriver):
    """Counts shake() calls; each call waits on the next gate a test queued."""

    interfaces: ClassVar[frozenset[str]] = frozenset({"IShaker"})

    def __init__(self) -> None:
        self.shake_calls: int = 0
        self._gates: list[asyncio.Event] = []

    async def initialize(self) -> None:
        return

    @property
    def is_initialized(self) -> bool:
        return True

    async def open(self) -> None:
        return

    async def close(self) -> None:
        return

    def queue_gate(self, gate: asyncio.Event) -> None:
        self._gates.append(gate)

    async def shake(self, request: ShakeRequest) -> None:
        self.shake_calls += 1
        if self._gates:
            gate = self._gates.pop(0)
            await gate.wait()


class _OneShaker:
    def __init__(self, driver: _CallCountingShaker) -> None:
        self._driver = driver
        self._lock = asyncio.Lock()

    def driver_for(self, device_name: str, effective_mode: EffectiveMode) -> BaseDriver | ITransporterDriver | None:
        return self._driver if device_name == "test_shaker" else None

    def lock_for(self, device_name: str) -> asyncio.Lock | None:
        return self._lock if device_name == "test_shaker" else None


def _make_executor_with_stub(driver: _CallCountingShaker, cache: CommandIdempotencyCache) -> CommandExecutor:
    return CommandExecutor(_OneShaker(driver), idempotency_cache=cache)


def _shake_command(command_id: str) -> CommandMessage:
    return CommandMessage(
        command_id=command_id,
        device_name="test_shaker",
        command="shake",
        params={"speed": 300.0, "duration": 5.0},
        effective_mode="LIVE",
    )


@pytest.mark.asyncio
async def test_in_flight_duplicate_awaits_single_invocation() -> None:
    """While the first execution is still running, a second arrival with the
    same command_id must NOT invoke the driver again. Both inbound resolve to
    the same single response."""
    driver = _CallCountingShaker()
    gate = asyncio.Event()
    driver.queue_gate(gate)

    cache = CommandIdempotencyCache(ttl_seconds=60.0)
    executor = _make_executor_with_stub(driver, cache)

    cmd = _shake_command("dup_in_flight")

    # Kick off the first execution; it will block on the gate inside shake().
    first_task = asyncio.create_task(executor.execute(cmd))
    # Yield enough turns to let the executor enter shake() and hit the gate.
    for _ in range(10):
        await asyncio.sleep(0)
        if driver.shake_calls == 1:
            break
    assert driver.shake_calls == 1, "first call did not enter the driver"

    # Second arrival of the SAME command_id while the first is still in-flight.
    second_task = asyncio.create_task(executor.execute(cmd))
    # Give the second task scheduler turns; it must NOT start a second shake().
    for _ in range(10):
        await asyncio.sleep(0)
    assert driver.shake_calls == 1, "duplicate command_id triggered a second driver invocation"

    # Release the gate so the original call completes.
    gate.set()

    first_response = await first_task
    second_response = await second_task

    assert driver.shake_calls == 1
    assert first_response.success is True
    assert second_response.success is True
    assert first_response.command_id == "dup_in_flight"
    assert second_response.command_id == "dup_in_flight"
    # The two responses must be the identical payload (the executor satisfies
    # both inbound from the same single execution).
    assert first_response.model_dump() == second_response.model_dump()


@pytest.mark.asyncio
async def test_completed_command_id_returns_cached_response() -> None:
    """After the first execution completes, a re-arrival of the same
    command_id returns the cached ResponseMessage and does NOT invoke the
    driver method a second time."""
    driver = _CallCountingShaker()
    cache = CommandIdempotencyCache(ttl_seconds=60.0)
    executor = _make_executor_with_stub(driver, cache)

    cmd = _shake_command("dup_completed")

    first_response = await executor.execute(cmd)
    assert first_response.success is True
    assert driver.shake_calls == 1

    # The same command_id again. Driver must NOT be invoked again.
    second_response = await executor.execute(cmd)
    assert driver.shake_calls == 1, "completed command_id triggered re-execution"
    assert second_response.model_dump() == first_response.model_dump()


@pytest.mark.asyncio
async def test_ttl_expiry_allows_reuse() -> None:
    """After the cache TTL elapses, the same command_id is treated as fresh
    and the driver method runs again."""
    driver = _CallCountingShaker()
    # Injectable clock so the test does not actually sleep for the TTL.
    now = [1000.0]
    cache = CommandIdempotencyCache(
        ttl_seconds=0.05,
        time_source=lambda: now[0],
    )
    executor = _make_executor_with_stub(driver, cache)

    cmd = _shake_command("reused_after_ttl")

    first_response = await executor.execute(cmd)
    assert first_response.success is True
    assert driver.shake_calls == 1

    # Advance the injected clock beyond TTL.
    now[0] += 10.0

    second_response = await executor.execute(cmd)
    # After eviction, the driver MUST be re-invoked for the same command_id.
    assert driver.shake_calls == 2, "TTL-expired command_id did not allow re-execution"
    assert second_response.success is True


@pytest.mark.asyncio
async def test_distinct_command_ids_run_independently() -> None:
    """De-dup keys ONLY on command_id; distinct ids run the driver each time
    even when command + params are identical."""
    driver = _CallCountingShaker()
    cache = CommandIdempotencyCache(ttl_seconds=60.0)
    executor = _make_executor_with_stub(driver, cache)

    await executor.execute(_shake_command("id_a"))
    await executor.execute(_shake_command("id_b"))

    assert driver.shake_calls == 2


@pytest.mark.asyncio
async def test_original_cancellation_propagates_to_duplicate_awaiters() -> None:
    """If the ORIGINAL caller (the one that got is_new=True from claim()) is
    cancelled mid-execution, duplicate awaiters MUST NOT hang forever. The
    executor's except BaseException arm catches CancelledError and calls
    fail() on the cache, which resolves the shared future with the
    CancelledError. Duplicate awaiters then re-raise the same exception.

    This pins the cancellation cascade contract: cancellation of the
    original execution propagates through the cache to every duplicate
    waiting on the same command_id. Without this, a duplicate
    that arrived while the original was still in-flight would hang on a
    permanently-pending future even though the original task has gone away.
    """
    driver = _CallCountingShaker()
    gate = asyncio.Event()
    driver.queue_gate(gate)

    cache = CommandIdempotencyCache(ttl_seconds=60.0)
    executor = _make_executor_with_stub(driver, cache)

    cmd = _shake_command("cancelled_original")

    # Kick off the first execution; it will block on the gate inside shake().
    first_task = asyncio.create_task(executor.execute(cmd))
    for _ in range(10):
        await asyncio.sleep(0)
        if driver.shake_calls == 1:
            break
    assert driver.shake_calls == 1, "first call did not enter the driver"

    # A duplicate arrives while the first is still in-flight; it gets
    # is_new=False and awaits the shared future.
    second_task = asyncio.create_task(executor.execute(cmd))
    # Yield scheduler turns so the duplicate enters its `await future`.
    for _ in range(10):
        await asyncio.sleep(0)
    assert driver.shake_calls == 1, "duplicate triggered a second driver invocation"

    # Cancel the ORIGINAL task before the gate is released. The executor's
    # except BaseException arm must call cache.fail() with the
    # CancelledError, which resolves the shared future and unblocks the
    # duplicate awaiter with the same exception.
    first_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await first_task

    # The duplicate must NOT hang. It must re-raise the CancelledError that
    # the cache propagated through the shared future. A bounded wait_for
    # guards against the hang regression: if the cancellation cascade ever
    # breaks (e.g. the executor stops calling fail() on CancelledError),
    # this assertion times out loudly instead of hanging the test runner.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(second_task, timeout=1.0)

    # Driver was invoked exactly once across the whole scenario.
    assert driver.shake_calls == 1

    # After cancellation, the entry must be evicted from the cache (fail()
    # pops the entry). A NEW execution with the same command_id is therefore
    # treated as fresh and re-invokes the driver. This pins the recovery
    # path: gateway can safely retry after a cancellation cascade.
    driver_retry_gate = asyncio.Event()
    driver_retry_gate.set()
    driver.queue_gate(driver_retry_gate)
    retry_response = await executor.execute(cmd)
    assert retry_response.success is True
    assert driver.shake_calls == 2
