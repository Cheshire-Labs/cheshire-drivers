"""Shared test fixtures for cheshire_drivers."""

import asyncio

import pytest


@pytest.fixture
def recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every ``asyncio.sleep`` duration and return instantly.

    Timing tests assert on the *requested* sleep, not measured wall-clock, so
    they stay deterministic and never flake under CI load. The returned list
    grows in call order; ``.clear()`` it between phases you want to isolate.
    """
    durations: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        durations.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return durations
