"""Pre-built heating-shaker drivers wrapping PyLabRobot backends."""

from cheshire_drivers.plr_wrappers import PLRHeatingShakerBackendWrapper
from pylabrobot.heating_shaking import HeaterShakerChatterboxBackend


class ChatterboxHeatingShakerDriver(PLRHeatingShakerBackendWrapper):
    """Simulated heating-shaker using PyLabRobot's Chatterbox backend.

    Prints all shaker AND temperature operations without physical hardware.
    Satisfies all three cheshire-drivers interfaces (IShaker, ITempSettable,
    ITempGettable) via the stacked-mixin MRO defined on
    PLRHeatingShakerBackendWrapper.
    """

    def __init__(self) -> None:
        super().__init__(HeaterShakerChatterboxBackend())
