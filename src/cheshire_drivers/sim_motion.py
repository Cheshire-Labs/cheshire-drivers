"""Simulated channel and gripper motion for a liquid handler's simulator.

Each mixin stands in for one motion interface of a real liquid handler. It moves nothing and
remembers where it last sent each channel and the gripper, so a read answers with that. A read or
a jog with nothing on record is refused rather than answered from an invented origin.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Optional

from cheshire_drivers.driver_errors import rejected_before_asking
from cheshire_drivers.driver_hooks import DriverHooks
from cheshire_drivers.gripper_models import (
    GetGripperPositionRequest,
    GetGripperRotationRequest,
    GetJawWidthRequest,
    GripperPosition,
    GripWithForceRequest,
    MoveGripperRelativeRequest,
    MoveGripperToRequest,
    ReleaseJawRequest,
    RotateGripperRequest,
    SetJawWidthRequest,
)
from cheshire_drivers.interfaces import (
    IForceGripperJawDriver,
    IGripperMotionDriver,
    IGripperPositionDriver,
    IGripperRotationDriver,
    IPipetteMotionDriver,
    IWidthGripperJawDriver,
)
from cheshire_drivers.pipette_motion_models import (
    ChannelPosition,
    GetChannelPositionRequest,
    MoveChannelRelativeRequest,
    MoveChannelToRequest,
    require_tip_end_datum,
)


class SimulatesSteps(DriverHooks, ABC):
    """What a simulated command needs from its driver: a name to report and a stand-in for the wait."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def _sim(self, message: str) -> None: ...


@dataclass
class SimulatedMotion:
    """Where the simulator last sent each channel and its gripper."""

    channels: dict[int, ChannelPosition] = field(default_factory=dict)
    gripper: Optional[GripperPosition] = None
    jaw_width: Optional[float] = None
    rotation: Optional[float] = None


class RecordsSimulatedMotion(SimulatesSteps):
    _recorded_motion: Optional[SimulatedMotion] = None

    @property
    def _motion(self) -> SimulatedMotion:
        if self._recorded_motion is None:
            self._recorded_motion = SimulatedMotion()
        return self._recorded_motion

    def _nothing_on_record(self, what: str, then: str) -> Exception:
        return rejected_before_asking(NotImplementedError(
            f"{self.name} is simulated and has no {what} on record. {then}"
        ))


class PipetteMotionSimMixin(RecordsSimulatedMotion, IPipetteMotionDriver):
    _channels_reference_tip_end_only: ClassVar[bool] = False
    """True for a robot whose channels report only the tip end, as the Opentrons ones do."""

    def _check_datum(self, request: MoveChannelToRequest | GetChannelPositionRequest) -> None:
        if self._channels_reference_tip_end_only:
            require_tip_end_datum(request.z_reference)

    def _channel_at(self, channel: int) -> ChannelPosition:
        at = self._motion.channels.get(channel)
        if at is None:
            raise self._nothing_on_record(
                f"position for channel {channel}", "Move it to an absolute position with x, y and z first.",
            )
        return at

    def _channel_target(self, request: MoveChannelToRequest) -> ChannelPosition:
        held = self._motion.channels.get(request.channel)
        if held is None:
            if request.x is None or request.y is None or request.z is None:
                raise self._nothing_on_record(
                    f"position for channel {request.channel} to hold its other axes at",
                    "Move it with x, y and z first.",
                )
            return ChannelPosition(
                channel=request.channel, x=request.x, y=request.y, z=request.z, z_reference=request.z_reference,
            )
        return ChannelPosition(
            channel=request.channel,
            x=held.x if request.x is None else request.x,
            y=held.y if request.y is None else request.y,
            z=held.z if request.z is None else request.z,
            z_reference=held.z_reference if request.z is None else request.z_reference,
        )

    async def move_channel_to(self, request: MoveChannelToRequest) -> None:
        self._check_datum(request)
        target = self._channel_target(request)
        await self._sim(f"Driver: {self.name} moving channel {request.channel} to ({target.x}, {target.y}, {target.z})...")
        self._motion.channels[request.channel] = target

    async def move_channel_relative(self, request: MoveChannelRelativeRequest) -> None:
        at = self._channel_at(request.channel)
        await self._sim(f"Driver: {self.name} jogging channel {request.channel}...")
        self._motion.channels[request.channel] = at.model_copy(update={
            "x": at.x + (request.dx or 0.0), "y": at.y + (request.dy or 0.0), "z": at.z + (request.dz or 0.0),
        })

    async def get_channel_position(self, request: GetChannelPositionRequest) -> ChannelPosition:
        self._check_datum(request)
        at = self._channel_at(request.channel)
        if at.z_reference != request.z_reference:
            raise rejected_before_asking(NotImplementedError(
                f"{self.name} is simulated and recorded channel {request.channel} against {at.z_reference}, "
                f"so it cannot read it against {request.z_reference}. Read it with z_reference='{at.z_reference}'."
            ))
        return at


class GripperMotionSimMixin(RecordsSimulatedMotion, IGripperMotionDriver):
    async def move_gripper_to(self, request: MoveGripperToRequest) -> None:
        await self._sim(f"Driver: {self.name} moving its gripper to ({request.x}, {request.y}, {request.z})...")
        self._motion.gripper = GripperPosition(x=request.x, y=request.y, z=request.z)


class GripperPositionSimMixin(RecordsSimulatedMotion, IGripperPositionDriver):
    def _gripper_at(self) -> GripperPosition:
        at = self._motion.gripper
        if at is None:
            raise self._nothing_on_record("gripper position", "Move the gripper to an absolute position first.")
        return at

    async def get_gripper_position(self, request: GetGripperPositionRequest) -> GripperPosition:
        return self._gripper_at()

    async def move_gripper_relative(self, request: MoveGripperRelativeRequest) -> None:
        at = self._gripper_at()
        await self._sim(f"Driver: {self.name} jogging its gripper...")
        self._motion.gripper = GripperPosition(
            x=at.x + (request.dx or 0.0), y=at.y + (request.dy or 0.0), z=at.z + (request.dz or 0.0),
        )


class ForceGripperJawSimMixin(SimulatesSteps, IForceGripperJawDriver):
    async def grip_with_force(self, request: GripWithForceRequest) -> None:
        await self._sim(f"Driver: {self.name} closing its jaw (force={request.force})...")

    async def release_jaw(self, request: ReleaseJawRequest) -> None:
        await self._sim(f"Driver: {self.name} opening its jaw...")


class WidthGripperJawSimMixin(RecordsSimulatedMotion, IWidthGripperJawDriver):
    async def set_jaw_width(self, request: SetJawWidthRequest) -> None:
        await self._sim(f"Driver: {self.name} setting its jaw to {request.width} mm...")
        self._motion.jaw_width = request.width

    async def get_jaw_width(self, request: GetJawWidthRequest) -> float:
        if self._motion.jaw_width is None:
            raise self._nothing_on_record("jaw width", "Set the jaw width first.")
        return self._motion.jaw_width


class GripperRotationSimMixin(RecordsSimulatedMotion, IGripperRotationDriver):
    async def rotate_gripper(self, request: RotateGripperRequest) -> None:
        await self._sim(f"Driver: {self.name} rotating its gripper to {request.angle} degrees...")
        self._motion.rotation = request.angle

    async def get_gripper_rotation(self, request: GetGripperRotationRequest) -> float:
        if self._motion.rotation is None:
            raise self._nothing_on_record("gripper rotation", "Rotate the gripper first.")
        return self._motion.rotation
