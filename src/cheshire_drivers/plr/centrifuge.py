"""Pre-built centrifuge drivers wrapping PyLabRobot backends."""

from typing import ClassVar, Optional

from cheshire_drivers.driver_introspection import VendorSurface
from cheshire_drivers.plr_wrappers import PLRCentrifugeBackendWrapper
from pylabrobot.centrifuge import VSpinBackend
from pylabrobot.legacy.centrifuge.chatterbox import CentrifugeChatterboxBackend


class VSpinCentrifugeDriver(PLRCentrifugeBackendWrapper):
    """Driver for the Agilent VSpin centrifuge.

    Wraps PyLabRobot's VSpinBackend behind the ICentrifugeDriver interface.
    Communicates via FTDI USB.

    Args:
        device_id: FTDI device identifier. If None, uses the first available device.
    """

    # Forwarded so the VSpin's own commands are advertised and cataloged.
    vendor_surfaces: ClassVar[tuple[VendorSurface, ...]] = (
        VendorSurface(path="_backend", type=VSpinBackend),
    )

    def __init__(self, device_id: Optional[str] = None) -> None:
        super().__init__(VSpinBackend(device_id=device_id))


class ChatterboxCentrifugeDriver(PLRCentrifugeBackendWrapper):
    """Simulated centrifuge using PyLabRobot's Chatterbox backend.

    Prints all centrifuge operations without physical hardware. Useful for
    integration testing the full driver stack against the cheshire-drivers
    ICentrifugeDriver interface without instantiating real hardware.
    """

    def __init__(self) -> None:
        super().__init__(CentrifugeChatterboxBackend())
