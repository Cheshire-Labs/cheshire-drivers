"""Pre-built shaker drivers wrapping PyLabRobot backends."""

from cheshire_drivers.plr_wrappers import PLRShakerBackendWrapper
from pylabrobot.shaking import ShakerChatterboxBackend


class ChatterboxShakerDriver(PLRShakerBackendWrapper):
    """Simulated shaker driver using PyLabRobot's Chatterbox backend.

    No physical hardware required. Useful for integration testing
    with the full driver stack active.
    """

    def __init__(self) -> None:
        super().__init__(ShakerChatterboxBackend())
