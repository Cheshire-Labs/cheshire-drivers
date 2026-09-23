"""Pre-built thermocycler drivers wrapping PyLabRobot backends."""

from cheshire_drivers.plr_wrappers import PLRThermocyclerBackendWrapper
from pylabrobot.thermocycling import ThermocyclerChatterboxBackend


class ChatterboxThermocyclerDriver(PLRThermocyclerBackendWrapper):
    """Simulated thermocycler using PyLabRobot's Chatterbox backend.

    Prints all thermocycler operations without physical hardware. Single-zone
    (`num_zones=1`), so protocols and set-temperature commands pass 1-element
    temperature lists.
    """

    def __init__(self) -> None:
        super().__init__(ThermocyclerChatterboxBackend(num_zones=1))
