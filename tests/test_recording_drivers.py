"""Tests for the Recording*Driver wrappers in sims.py.

The wrappers capture every method call on an inner driver as a
``RecordedCall`` and delegate. Tests assert the captured shapes match
what the workflow dispatched.
"""

import pytest

from cheshire_drivers import (
    RecordedCall,
    RecordingLiquidHandlerDriver,
    RecordingShakerDriver,
    SimLiquidHandlerDriver,
    SimShakerDriver,
)
from cheshire_drivers.shaker_models import (
    LockPlateRequest,
    ShakeRequest,
    StopShakingRequest,
    UnlockPlateRequest,
)


# ----------------------------------------------------------------------
# RecordingShakerDriver
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recording_shaker_captures_shake_request() -> None:
    inner = SimShakerDriver(name="shaker_1")
    rec = RecordingShakerDriver(inner)

    await rec.shake(ShakeRequest(speed=500, duration=10.0))

    assert rec.calls == [
        RecordedCall(method="shake", args={"speed": 500, "duration": 10.0}),
    ]


@pytest.mark.asyncio
async def test_recording_shaker_delegates_lifecycle_silently() -> None:
    """Lifecycle ops delegate to the inner driver but are NOT recorded.

    Mirrors RecordingLiquidHandlerDriver. Tests asserting on shaker
    behavior care about shake / lock / unlock; init/open/close add noise.
    """
    inner = SimShakerDriver(name="shaker_1")
    rec = RecordingShakerDriver(inner)

    assert rec.is_initialized is False
    await rec.initialize()
    assert rec.is_initialized is True
    # No record produced for the lifecycle op.
    assert rec.calls == []


@pytest.mark.asyncio
async def test_recording_shaker_captures_domain_ops_only() -> None:
    """Lock / shake / stop_shaking / unlock are recorded; stop is not."""
    inner = SimShakerDriver(name="s")
    rec = RecordingShakerDriver(inner)

    await rec.lock_plate(LockPlateRequest())
    await rec.shake(ShakeRequest(speed=100, duration=1.0))
    await rec.stop_shaking(StopShakingRequest())
    await rec.unlock_plate(UnlockPlateRequest())
    await rec.stop()  # lifecycle: silent

    assert [call.method for call in rec.calls] == [
        "lock_plate", "shake", "stop_shaking", "unlock_plate",
    ]


def test_recording_shaker_advertises_ishaker_interface() -> None:
    """Wire-protocol dispatch keys off the `interfaces` ClassVar."""
    inner = SimShakerDriver(name="s")
    rec = RecordingShakerDriver(inner)

    assert "IShaker" in rec.interfaces


# ----------------------------------------------------------------------
# RecordingLiquidHandlerDriver (relocated from testing.py to sims.py)
# ----------------------------------------------------------------------


def test_recording_lh_importable_from_top_level() -> None:
    """After the testing.py -> sims.py move, the public symbol still imports."""
    inner = SimLiquidHandlerDriver(name="lh")
    rec = RecordingLiquidHandlerDriver(inner)
    assert rec.calls == []
    assert "ILiquidHandler" in rec.interfaces
