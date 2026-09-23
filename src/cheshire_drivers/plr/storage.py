"""Pre-built storage drivers wrapping PyLabRobot backends."""

from cheshire_drivers.plr_wrappers import PLRStorageBackendWrapper
from pylabrobot.storage import IncubatorChatterboxBackend


class ChatterboxStorageDriver(PLRStorageBackendWrapper):
    """Simulated storage device using PyLabRobot's Incubator Chatterbox backend.

    Prints lifecycle operations (setup, door open/close) without physical
    hardware. Useful for integration testing the full driver stack against
    cheshire-drivers' IStorageDriver interface.
    """

    def __init__(self) -> None:
        super().__init__(IncubatorChatterboxBackend())
