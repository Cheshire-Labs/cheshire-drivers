"""Pre-built sealer drivers wrapping PyLabRobot backends."""

from typing import ClassVar

from cheshire_drivers.driver_introspection import VendorSurface
from cheshire_drivers.plr_wrappers import PLRSealerBackendWrapper
from cheshire_drivers.wire_timeouts import covering_seconds, require_covering
from pylabrobot.sealing import A4SBackend


class A4SSealerDriver(PLRSealerBackendWrapper):
    """Driver for the A4S plate sealer.

    Wraps PyLabRobot's A4SBackend behind the ISealerDriver interface.
    Communicates via serial port.

    Args:
        port: Serial port path (e.g., "/dev/tty.usbserial-0001" or "COM3").
        timeout: how long to wait on the serial link before giving up, in seconds.
            The A4S has one such budget and it bounds a single response read and a
            whole wait-for-state loop alike, including the loop that waits out a
            seal cycle. Defaults to a budget that outlasts every command this
            driver declares, so the engine's abort fires first; a shorter one is
            refused.
    """

    # Forwarded so the sealer's own commands are advertised and cataloged.
    vendor_surfaces: ClassVar[tuple[VendorSurface, ...]] = (
        VendorSurface(path="_backend", type=A4SBackend),
    )

    def __init__(self, port: str, timeout: int | None = None) -> None:
        if timeout is None:
            timeout = round(covering_seconds(type(self)))
        require_covering(timeout, type(self))
        super().__init__(A4SBackend(port=port, timeout=timeout))
