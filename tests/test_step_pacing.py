"""A stretch of simulated time takes that long over the run's speed-up on the wall, and no time with no speed-up."""

import pytest

from cheshire_drivers.sim_pacing import wall_seconds


@pytest.mark.parametrize(
    ("sim_seconds", "speedup", "expected"),
    [(4.0, 10.0, 0.4), (4.0, 1.0, 4.0), (4.0, 0.5, 8.0), (0.0, 2.0, 0.0), (4.0, None, 0.0)],
)
def test_wall_seconds_is_sim_seconds_over_the_speedup_and_nothing_with_no_speedup(
    sim_seconds: float, speedup: float | None, expected: float,
) -> None:
    assert wall_seconds(sim_seconds, speedup) == pytest.approx(expected)
