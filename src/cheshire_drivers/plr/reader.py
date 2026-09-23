"""Pre-built plate reader drivers wrapping PyLabRobot backends."""

from cheshire_drivers.plr_wrappers import PLRReaderBackendWrapper
from pylabrobot.plate_reading import PlateReaderChatterboxBackend


class ChatterboxReaderDriver(PLRReaderBackendWrapper):
    """Simulated plate reader using PyLabRobot's Chatterbox backend.

    Prints lifecycle operations (setup/open/close) and acknowledges read
    requests. Note: cheshire-drivers' IReaderDriver routes reads via a
    protocol-file pattern (ReadRequest.protocol_filepath / output_filepath),
    while PLR's plate-reader chatterbox exposes atomic per-measurement methods
    (read_luminescence/read_absorbance/read_fluorescence). The wrapper does
    not bridge the shape gap; see PLRReaderBackendWrapper for details.
    """

    def __init__(self) -> None:
        super().__init__(PlateReaderChatterboxBackend())
