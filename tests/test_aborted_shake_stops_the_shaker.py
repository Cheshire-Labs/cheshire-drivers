"""An aborted shake must still stop the shaker.

A timed shake starts the motor and waits out the duration. Aborting a run cancels
that wait. Without a stop on the way out the motor keeps running, with a plate on
it, after the run that asked for it has gone.
"""

import asyncio

import pytest
from cheshire_drivers.shaker_models import ShakeRequest
from cheshire_drivers.plr_wrappers import PLRShakerBackendWrapper

pytestmark = pytest.mark.asyncio


class _RecordingShaker:
    """The three backend calls the wrapper makes, in the order it makes them."""

    supports_locking = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def setup(self) -> None:
        self.calls.append("setup")

    async def stop(self) -> None:
        self.calls.append("stop")

    async def start_shaking(self, speed: float) -> None:
        self.calls.append("start_shaking")

    async def stop_shaking(self) -> None:
        self.calls.append("stop_shaking")


async def test_a_cancelled_shake_still_stops_the_motor() -> None:
    backend = _RecordingShaker()
    shaker = PLRShakerBackendWrapper(backend)

    task = asyncio.create_task(shaker.shake(ShakeRequest(speed=300.0, duration=3600.0)))
    while "start_shaking" not in backend.calls:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert backend.calls == ["start_shaking", "stop_shaking"], (
        "an aborted shake left the motor running"
    )


async def test_a_shake_that_runs_its_duration_still_stops_the_motor() -> None:
    backend = _RecordingShaker()
    shaker = PLRShakerBackendWrapper(backend)

    await shaker.shake(ShakeRequest(speed=300.0, duration=0.0))

    assert backend.calls == ["start_shaking", "stop_shaking"]
