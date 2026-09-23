"""Pre-built transporter drivers wrapping PyLabRobot backends."""

from typing import ClassVar

from cheshire_drivers.driver_introspection import VendorSurface
from cheshire_drivers.plr.transporter_wrapper import PLRTransporterBackendWrapper
from pylabrobot.brooks.precise_flex import PreciseFlex


class PreciseFlexTransporterDriver(PLRTransporterBackendWrapper):
    """Driver for the Brooks PreciseFlex robotic arm.

    Wraps PyLabRobot's PreciseFlex behind the ITransporterDriver interface.
    Communicates via TCP socket.

    Args:
        host: IP address or hostname of the PreciseFlex controller.
        port: TCP port number.
        gripper_length: Wrist axis to tool tip distance in mm. Only a fallback:
            bring-up reads the real tool length off the controller.
        gripper_z_offset: Wrist plate to tool tip vertical offset in mm. Always
            taken from here; the controller does not report it.
        closed_gripper_position: Firmware jaw value at the minimum jaw width.
            Depends on the fitted gripper.
        is_dual_gripper: Whether the arm has a dual gripper.
        has_rail: Whether the arm has a linear rail axis. Not currently supported;
            PLRTransporterBackendWrapper rejects a railed backend at construction.
        timeout: how long the controller waits for motor power to come on, in
            seconds. Not a wire budget: PyLabRobot spends it on the `hp` power-on
            command and nowhere else. The arm's real waits are its socket read and
            write budgets and its end-of-motion settle, and none of them is
            reachable from here, so there is no covering budget to set yet.
    """

    # Names the class whose surface is forwarded, so the controller's own
    # commands (power, signals, parameters, taught locations, raw TCS) are
    # advertised as capabilities and listed in the introspection catalog
    # instead of being invisible behind the interface.
    vendor_surfaces: ClassVar[tuple[VendorSurface, ...]] = (
        VendorSurface(path="_backend", type=PreciseFlex),
    )

    # Status queries plus the emergency `stop`; anything absent (motion,
    # power, brakes, writes, raw TCS) dispatches only with an explicit confirm.

    def __init__(
        self,
        host: str,
        port: int = 10100,
        gripper_length: float = 162.0,
        gripper_z_offset: float = 0.0,
        closed_gripper_position: float = 75.5,
        is_dual_gripper: bool = False,
        has_rail: bool = False,
        timeout: int = 20,
    ) -> None:
        super().__init__(
            PreciseFlex(
                host=host,
                port=port,
                gripper_length=gripper_length,
                gripper_z_offset=gripper_z_offset,
                closed_gripper_position=closed_gripper_position,
                is_dual_gripper=is_dual_gripper,
                has_rail=has_rail,
                timeout=timeout,
            )
        )
