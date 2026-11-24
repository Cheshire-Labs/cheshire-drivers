"""How long a simulated device command takes on the wall, at the pace of the run it belongs to."""


def wall_seconds(sim_seconds: float, speedup: float | None) -> float:
    """Wall seconds a stretch of simulated time takes at `speedup` (1 is real time); none at no speed-up."""
    return 0.0 if speedup is None else sim_seconds / speedup
