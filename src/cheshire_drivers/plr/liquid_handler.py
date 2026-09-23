"""Pre-built liquid handler drivers wrapping PyLabRobot backends."""

import logging
from typing import ClassVar

from cheshire_drivers.driver_introspection import VendorSurface
from cheshire_drivers.gripper_models import (
    GetGripperPositionRequest,
    GetGripperRotationRequest,
    GetJawWidthRequest,
    GripperPosition,
    MoveGripperRelativeRequest,
    MoveGripperToRequest,
    RotateGripperRequest,
    SetJawWidthRequest,
)
from cheshire_drivers.interfaces import (
    IGripperMotionDriver,
    IGripperPositionDriver,
    IGripperRotationDriver,
    ILiquidHandlerWithProtocolDriver,
    IPipetteMotionDriver,
    IWidthGripperJawDriver,
)
from cheshire_drivers.pipette_motion_models import (
    ChannelPosition,
    GetChannelPositionRequest,
    MoveChannelRelativeRequest,
    MoveChannelToRequest,
    ZReference,
    require_tip_end_datum,
)
from cheshire_drivers.plr_wrappers import PLRLiquidHandlerWrapper, SimServerLifecycle
from cheshire_drivers.protocol_runner_models import RunProtocolRequest
from cheshire_drivers.wire_timeouts import WireTimeout, resolve_wire_timeout
from pylabrobot.legacy.liquid_handling.backends.chatterbox import LiquidHandlerChatterboxBackend
from pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_backend import STARBackend
from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import OpentronsOT2Backend

logger = logging.getLogger("cheshire_drivers")


class ChatterboxLiquidHandlerDriver(PLRLiquidHandlerWrapper):
    """Simulated liquid handler using PyLabRobot's Chatterbox backend.

    Prints all pipetting operations without physical hardware.
    Call configure_deck() with a DeckLayoutConfig before initialize().
    """

    def __init__(self, num_channels: int = 8, visualize: bool = False) -> None:
        super().__init__(LiquidHandlerChatterboxBackend(num_channels), visualize=visualize)


class ChatterboxLiquidHandlerWithProtocolDriver(
    ChatterboxLiquidHandlerDriver, ILiquidHandlerWithProtocolDriver
):
    """Deck-modeling sim LH that also satisfies IProtocolRunner.

    The deck-modeling analogue of ``SimLiquidHandlerWithProtocolDriver``: it
    keeps the real Chatterbox deck (``provides_state=True``, every atomic op
    materializes/reconciles labware on a PLR deck tree) and adds a no-op
    ``run_protocol`` so a workflow that drives a deck-modeled handler through
    either atomic ops or a protocol file resolves cleanly under PURE_SIM.

    PLR has no external protocol-file concept, so ``run_protocol`` logs and
    returns; the deck fidelity comes entirely from the atomic-op path.

    Default ``num_channels=8``: a Hamilton STARlet has 8 independent channels.
    PLR rejects an N-well aspirate when ``N > backend.num_channels``, so a sim
    sized to the real head faithfully catches a workflow that pipettes more
    positions than the head can serve. The separate 96-head (``head96``) is
    always sized 96 regardless of this value, so aspirate96/dispense96 work.
    """

    interfaces: ClassVar[frozenset[str]] = frozenset(
        {"ILiquidHandler", "IProtocolRunner"}
    )

    def __init__(self, num_channels: int = 8, visualize: bool = False) -> None:
        super().__init__(num_channels=num_channels, visualize=visualize)

    async def run_protocol(self, request: RunProtocolRequest) -> None:
        logger.info(
            "ChatterboxLiquidHandlerWithProtocolDriver: no-op run_protocol "
            "%s (params=%s); PLR drives the deck via atomic ops.",
            request.protocol_filepath, request.params,
        )


class _PLROpentronsBackendHolder:
    """Carries the backend handle for the pipette motion mixin.

    Declaring the attribute here rather than on the mixin keeps pyright from flagging it as a
    duplicate when several mixins meet in one MRO.
    """

    _ot_backend: OpentronsOT2Backend


class PLROpentronsPipetteMotionMixin(_PLROpentronsBackendHolder, IPipetteMotionDriver):
    """IPipetteMotion backed by the OT-2's per-channel motion surface.

    The Flex has its own motion, on ``FlexLiquidHandlerDriver`` over ``pylabrobot.opentrons``.
    """

    async def move_channel_to(self, request: MoveChannelToRequest) -> None:
        require_tip_end_datum(request.z_reference)
        await self._ot_backend.move_channel_to(
            request.channel, x=request.x, y=request.y, z=request.z
        )

    async def move_channel_relative(self, request: MoveChannelRelativeRequest) -> None:
        current = await self._ot_backend.get_channel_position(request.channel)
        await self._ot_backend.move_channel_to(
            request.channel,
            x=None if request.dx is None else current.x + request.dx,
            y=None if request.dy is None else current.y + request.dy,
            z=None if request.dz is None else current.z + request.dz,
        )

    async def get_channel_position(self, request: GetChannelPositionRequest) -> ChannelPosition:
        require_tip_end_datum(request.z_reference)
        position = await self._ot_backend.get_channel_position(request.channel)
        return ChannelPosition(
            channel=request.channel,
            x=position.x,
            y=position.y,
            z=position.z,
            z_reference="tip_end",
        )


class OT2LiquidHandlerDriver(PLRLiquidHandlerWrapper, PLROpentronsPipetteMotionMixin):
    """Opentrons OT-2 liquid handler: the generic PLR surface plus per-channel motion.

    Declares IPipetteMotion but none of the gripper interfaces: the OT-2 has no gripper, only the
    two pipette mounts. Channel motion is the same shared surface the Flex uses.
    """

    interfaces: ClassVar[frozenset[str]] = frozenset({"ILiquidHandler", "IPipetteMotion"})

    # Forwarded so the robot-server's own commands are advertised and cataloged.
    vendor_surfaces: ClassVar[tuple[VendorSurface, ...]] = (
        VendorSurface(path="_backend", type=OpentronsOT2Backend),
    )

    def __init__(
        self,
        host: str,
        port: int = 31950,
        visualize: bool = False,
        *,
        sim_server: SimServerLifecycle | None = None,
        wire_timeout: WireTimeout | None = None,
    ) -> None:
        """Args:
        host: the robot's address.
        port: the robot-server's port.
        visualize: open PyLabRobot's visualizer alongside the run.
        sim_server: a device-owned simulator to bring up before first contact.
        wire_timeout: how long to wait on the link before giving up. Defaults to
            a budget that outlasts every command this driver declares, so the
            engine's abort fires first. Raise it for a slow or lossy link; one
            that would give up sooner is refused.
        """
        self.wire_timeout = resolve_wire_timeout(wire_timeout, type(self), poll_interval=0.05)
        backend = OpentronsOT2Backend(
            host=host,
            port=port,
            request_timeout=self.wire_timeout.seconds,
            command_timeout=self.wire_timeout.seconds,
            status_poll_interval=self.wire_timeout.poll_interval_seconds,
        )
        super().__init__(backend, visualize=visualize, sim_server=sim_server)
        self._ot_backend = backend


class _PLRStarBackendHolder:
    """Carries the STAR-typed backend handle shared by the Hamilton motion mixins.

    Declaring `_star_backend` once here rather than on each mixin keeps pyright from flagging it as
    a duplicate when the five STAR mixins meet in one MRO.
    """

    _star_backend: STARBackend


class PLRStarPipetteMotionMixin(_PLRStarBackendHolder, IPipetteMotionDriver):
    """IPipetteMotion backed by the STAR pipetting channels.

    Unlike the Opentrons channels, a STAR channel references BOTH z datums: the firmware exposes a
    stop-disk Z (no tip) and a tip/tool-end Z separately, so this honors whichever the request
    names instead of rejecting one.

    A move_channel_to is issued one command per axis, since STAR has no combined primitive. Unlike
    the Opentrons combined move, the axes are not coordinated through a traversal height, and STAR's
    own y-move guard can raise partway through, leaving earlier axes moved. Manual control should
    sequence axes to stay clear of obstacles.
    """

    async def _move_channel_z(self, channel: int, z: float, z_reference: ZReference) -> None:
        if z_reference == "stop_disk":
            await self._star_backend.move_channel_stop_disk_z(channel, z)
        else:
            await self._star_backend.move_channel_tool_z(channel, z)

    async def move_channel_to(self, request: MoveChannelToRequest) -> None:
        if request.x is not None:
            await self._star_backend.move_channel_x(request.channel, request.x)
        if request.y is not None:
            await self._star_backend.move_channel_y(request.channel, request.y)
        if request.z is not None:
            await self._move_channel_z(request.channel, request.z, request.z_reference)

    async def move_channel_relative(self, request: MoveChannelRelativeRequest) -> None:
        if request.dx is not None:
            await self._star_backend.move_channel_x_relative(request.channel, request.dx)
        if request.dy is not None:
            await self._star_backend.move_channel_y_relative(request.channel, request.dy)
        if request.dz is not None:
            # Jog against the probe, not the backend's native z-relative: a delta is datum- and
            # tip-independent, but that path reads the tip bottom and raises when no tip is mounted.
            current_z = await self._star_backend.request_probe_z_position(request.channel)
            await self._star_backend.move_channel_stop_disk_z(
                request.channel, current_z + request.dz
            )

    async def get_channel_position(self, request: GetChannelPositionRequest) -> ChannelPosition:
        x = await self._star_backend.request_x_pos_channel_n(request.channel)
        y = await self._star_backend.request_y_pos_channel_n(request.channel)
        if request.z_reference == "stop_disk":
            z = await self._star_backend.request_probe_z_position(request.channel)
        else:
            z = await self._star_backend.request_tip_bottom_z_position(request.channel)
        return ChannelPosition(
            channel=request.channel, x=x, y=y, z=z, z_reference=request.z_reference
        )


class PLRStarGripperMotionMixin(_PLRStarBackendHolder, IGripperMotionDriver):
    """IGripperMotion backed by the iSWAP, moved one axis at a time to an absolute deck position."""

    async def move_gripper_to(self, request: MoveGripperToRequest) -> None:
        await self._star_backend.move_iswap_x(request.x)
        await self._star_backend.move_iswap_y(request.y)
        await self._star_backend.move_iswap_z(request.z)


class PLRStarGripperPositionMixin(_PLRStarBackendHolder, IGripperPositionDriver):
    """IGripperPosition backed by the iSWAP, which the STAR firmware can query and jog.

    The Flex gripper has neither a position query nor a jog, so this is a genuine STAR specialty
    the Flex driver cannot offer.
    """

    async def get_gripper_position(self, request: GetGripperPositionRequest) -> GripperPosition:
        position = await self._star_backend.request_iswap_position()
        return GripperPosition(x=position.x, y=position.y, z=position.z)

    async def move_gripper_relative(self, request: MoveGripperRelativeRequest) -> None:
        # STAR raises for a single relative step beyond +/-99.9 mm; larger jogs must be split.
        if request.dx is not None:
            await self._star_backend.move_iswap_x_relative(request.dx)
        if request.dy is not None:
            await self._star_backend.move_iswap_y_relative(request.dy)
        if request.dz is not None:
            await self._star_backend.move_iswap_z_relative(request.dz)


class PLRStarWidthGripperJawMixin(_PLRStarBackendHolder, IWidthGripperJawDriver):
    """IWidthGripperJaw backed by the iSWAP, which drives its jaw to a commanded opening in mm.

    This is the width counterpart to the Flex's force jaw: the iSWAP positions its jaw, where the
    Flex only closes until a force is reached.
    """

    async def set_jaw_width(self, request: SetJawWidthRequest) -> None:
        await self._star_backend.iswap_open_gripper(open_position=request.width)

    async def get_jaw_width(self, request: GetJawWidthRequest) -> float:
        return await self._star_backend.iswap_gripper_request_width()


class PLRStarGripperRotationMixin(_PLRStarBackendHolder, IGripperRotationDriver):
    """IGripperRotation backed by the iSWAP rotation drive, an axis no Opentrons gripper has.

    A single angle maps to the rotation drive that turns the whole gripper; the iSWAP's separate
    wrist drive is a further specialty this interface does not yet expose.
    """

    async def rotate_gripper(self, request: RotateGripperRequest) -> None:
        await self._star_backend.iswap_rotate_to_angles(rotation_angle=request.angle)

    async def get_gripper_rotation(self, request: GetGripperRotationRequest) -> float:
        return await self._star_backend.iswap_rotation_drive_request_angle()


class STARLiquidHandlerDriver(
    PLRLiquidHandlerWrapper,
    PLRStarPipetteMotionMixin,
    PLRStarGripperMotionMixin,
    PLRStarGripperPositionMixin,
    PLRStarWidthGripperJawMixin,
    PLRStarGripperRotationMixin,
):
    """Hamilton MLSTAR liquid handler: the generic PLR surface plus the full iSWAP motion surface.

    Declares every gripper interface the iSWAP supports, which is more than the Flex: the iSWAP has
    position feedback (IGripperPosition), a width-positioned jaw (IWidthGripperJaw) and a rotation
    drive (IGripperRotation), where the Flex gripper places blindly and closes only to a force. Its
    channels are independently driven, so head configuration falls to the generic per-channel
    default with no override. Inheritance shadows `interfaces`, so the full set is restated here.
    """

    interfaces: ClassVar[frozenset[str]] = frozenset(
        {
            "ILiquidHandler",
            "IPipetteMotion",
            "IGripperMotion",
            "IGripperPosition",
            "IWidthGripperJaw",
            "IGripperRotation",
        }
    )

    # Forwarded so the MLSTAR firmware surface is advertised and cataloged.
    vendor_surfaces: ClassVar[tuple[VendorSurface, ...]] = (
        VendorSurface(path="_backend", type=STARBackend),
    )

    def __init__(
        self,
        serial_number: str | None = None,
        device_address: int | None = None,
        left_side_panel_installed: bool = False,
        visualize: bool = False,
    ) -> None:
        backend = STARBackend(
            device_address=device_address,
            serial_number=serial_number,
            left_side_panel_installed=left_side_panel_installed,
        )
        super().__init__(backend, visualize=visualize)
        self._star_backend = backend
