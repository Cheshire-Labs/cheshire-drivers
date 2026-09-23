"""Factory functions for wrapping PLR backends into cheshire-drivers interfaces.

Escape hatch for PLR backends that don't have a pre-built driver class
in cheshire_drivers.plr. Pass a PLR backend or an existing driver --
if it's already the right interface type, it passes through unchanged.

Example:
    from cheshire_drivers import sealer_driver
    from pylabrobot.legacy.sealing.some_new_backend import NewSealerBackend

    driver = sealer_driver(NewSealerBackend(port="/dev/tty.usb"))
    sealer = Sealer("sealer", driver)
"""

from cheshire_drivers.interfaces import (
    ICentrifugeDriver,
    ISealerDriver,
    IShakerDriver,
    IThermocyclerDriver,
)
from cheshire_drivers.plr_wrappers import (
    PLRCentrifugeBackend,
    PLRCentrifugeBackendWrapper,
    PLRSealerBackend,
    PLRSealerBackendWrapper,
    PLRShakerBackend,
    PLRShakerBackendWrapper,
    PLRThermocyclerBackend,
    PLRThermocyclerBackendWrapper,
)

# transporter_driver lives in cheshire_drivers.plr (transporter_wrapper), not here:
# it needs the optional pylabrobot.legacy.arms backend, not imported eagerly here.


def sealer_driver(backend: ISealerDriver | PLRSealerBackend) -> ISealerDriver:
    """Wrap a PLR sealer backend into an ISealerDriver, or pass through if already one."""
    if isinstance(backend, PLRSealerBackend):
        return PLRSealerBackendWrapper(backend)
    return backend


def shaker_driver(backend: IShakerDriver | PLRShakerBackend) -> IShakerDriver:
    """Wrap a PLR shaker backend into an IShakerDriver, or pass through if already one."""
    if isinstance(backend, PLRShakerBackend):
        return PLRShakerBackendWrapper(backend)
    return backend


def centrifuge_driver(backend: ICentrifugeDriver | PLRCentrifugeBackend) -> ICentrifugeDriver:
    """Wrap a PLR centrifuge backend into an ICentrifugeDriver, or pass through if already one."""
    if isinstance(backend, PLRCentrifugeBackend):
        return PLRCentrifugeBackendWrapper(backend)
    return backend


def thermocycler_driver(
    backend: IThermocyclerDriver | PLRThermocyclerBackend,
) -> IThermocyclerDriver:
    """Wrap a PLR thermocycler backend into an IThermocyclerDriver, or pass through if already one."""
    if isinstance(backend, PLRThermocyclerBackend):
        return PLRThermocyclerBackendWrapper(backend)
    return backend
