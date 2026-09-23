"""Bringing a device up must not move it, and re-connecting must not break it.

Two failures on the PF400 bench drive these tests. `initialize` called PLR's
compound `setup()`, which ends in a home sweep, so one REST call could drive a
plate-gripping arm through its whole envelope. And `connect` on a live arm
swapped in a fresh socket: PLR's socket setup drops the old connection without
closing it, and the PreciseFlex attaches the robot per connection, so the
attached session was orphaned and every later command failed "no robot
attached".

Both are properties of the one live session, not of any single caller, which is
why they are pinned here rather than at whichever surface happened to hit them.
"""

import asyncio
from typing import List

import pytest
from pylabrobot.legacy.liquid_handling.backends.chatterbox import (
    LiquidHandlerChatterboxBackend,
)

from cheshire_drivers.driver_introspection import (
    derive_capabilities,
    interface_command_names,
)
from cheshire_drivers.interfaces import (
    BaseDriver,
    ILiquidHandlerDriver,
    IShakerDriver,
    ITransporterDriver,
)
from cheshire_drivers.liquid_handler_models import DeckLayoutConfig
from cheshire_drivers.plr.liquid_handler import ChatterboxLiquidHandlerDriver
from cheshire_drivers.plr.transporter_wrapper import PLRTransporterBackendWrapper
from cheshire_drivers.plr_wrappers import PLRLiquidHandlerWrapper
from cheshire_drivers.sims import SimShakerDriver, SimTransporterDriver
from cheshire_drivers.homing_models import HomeRequest
from cheshire_drivers.transporter_models import InitializeRequest

from tests.arm_backend_mock import RecordingArmBackend


class _GranularArm(RecordingArmBackend):
    """Arm that splits link, power-on and homing, as the PreciseFlex does."""

    def __init__(self) -> None:
        super().__init__()
        self.lifecycle: List[str] = []

    async def setup(self) -> None:
        self.lifecycle.append("setup")

    async def connect(self) -> None:
        self.lifecycle.append("connect")

    async def initialize(self) -> None:
        self.lifecycle.append("initialize")

    async def disconnect(self) -> None:
        self.lifecycle.append("disconnect")

    async def home(self) -> None:
        self.lifecycle.append("home")


class _CompoundOnlyArm(RecordingArmBackend):
    """Arm offering only PLR's compound lifecycle, which most backends still are."""

    def __init__(self) -> None:
        super().__init__()
        self.lifecycle: List[str] = []

    async def setup(self) -> None:
        self.lifecycle.append("setup")

    async def home(self) -> None:
        self.lifecycle.append("home")


@pytest.mark.asyncio
async def test_initialize_takes_the_granular_verbs_and_never_homes() -> None:
    arm = _GranularArm()
    wrapper = PLRTransporterBackendWrapper(arm)

    await wrapper.initialize(InitializeRequest())

    assert arm.lifecycle == ["connect", "initialize"]
    assert wrapper.is_initialized


@pytest.mark.asyncio
async def test_initialize_falls_back_to_setup_when_the_arm_has_no_granular_verbs() -> None:
    arm = _CompoundOnlyArm()
    wrapper = PLRTransporterBackendWrapper(arm)

    await wrapper.initialize(InitializeRequest())

    assert arm.lifecycle == ["setup"]
    assert wrapper.is_initialized


@pytest.mark.asyncio
async def test_homing_is_reached_only_through_the_home_verb() -> None:
    arm = _GranularArm()
    wrapper = PLRTransporterBackendWrapper(arm)

    await wrapper.home(HomeRequest())

    assert arm.lifecycle == ["home"]


@pytest.mark.asyncio
async def test_connect_is_a_no_op_once_the_link_is_open() -> None:
    """A second connect must not re-link and orphan the attached session."""
    arm = _GranularArm()
    wrapper = PLRTransporterBackendWrapper(arm)

    await wrapper.initialize(InitializeRequest())
    await wrapper.connect()
    await wrapper.connect()

    assert arm.lifecycle == ["connect", "initialize"]


class _DeadSocketArm(_GranularArm):
    """Accepts the connect, then aborts the first read, as a stale socket does."""

    async def request_joint_position(self) -> dict:
        self.lifecycle.append("read")
        raise OSError("[WinError 1236] the network connection was aborted")


@pytest.mark.asyncio
async def test_connect_refuses_a_socket_that_cannot_be_read() -> None:
    """An idle arm accepts a connect and then aborts the first real command.

    Reporting success there hands the caller a link it cannot use and drops
    the failure on whatever ran next, which on the bench was a move.
    """
    arm = _DeadSocketArm()
    wrapper = PLRTransporterBackendWrapper(arm)

    with pytest.raises(OSError):
        await wrapper.connect()

    assert not wrapper.is_connected, "a link that cannot be read is not open"
    assert arm.lifecycle == ["connect", "read", "disconnect"], (
        "the dead socket has to be handed back, or connect no-ops on the "
        "retry and the arm is stranded until someone restarts the client"
    )


@pytest.mark.asyncio
async def test_a_refused_connect_can_be_retried() -> None:
    """The flag is what strands an arm, so a failed connect must clear it."""
    arm = _DeadSocketArm()
    wrapper = PLRTransporterBackendWrapper(arm)

    with pytest.raises(OSError):
        await wrapper.connect()
    with pytest.raises(OSError):
        await wrapper.connect()

    assert arm.lifecycle.count("connect") == 2, (
        "the second attempt must really re-open rather than return the "
        f"first attempt's state; saw {arm.lifecycle}"
    )


@pytest.mark.asyncio
async def test_a_healthy_connect_proves_the_link_once() -> None:
    arm = _GranularArm()
    wrapper = PLRTransporterBackendWrapper(arm)

    await wrapper.connect()

    assert wrapper.is_connected
    assert arm.lifecycle == ["connect"], (
        f"connect must open the link once and move nothing; saw {arm.lifecycle}"
    )


@pytest.mark.asyncio
async def test_disconnect_lets_the_link_be_opened_again() -> None:
    """And it clears bring-up, so the arm is not left looking ready."""
    arm = _GranularArm()
    wrapper = PLRTransporterBackendWrapper(arm)

    await wrapper.initialize(InitializeRequest())
    assert wrapper.is_initialized, "otherwise the assertion below proves nothing"

    await wrapper.disconnect()
    assert not wrapper.is_initialized

    await wrapper.connect()

    assert arm.lifecycle == ["connect", "initialize", "disconnect", "connect"]


class _ArmThatFailsHandback(_GranularArm):
    """Arm whose link has already died, so handing it back raises."""

    async def disconnect(self) -> None:
        self.lifecycle.append("disconnect")
        raise ConnectionResetError("link already gone")


@pytest.mark.asyncio
async def test_a_failed_handback_still_frees_the_link() -> None:
    """Otherwise the arm is unreachable until someone restarts the client.

    `connect` no-ops while the wrapper thinks the link is open, so a disconnect
    that raises on the way out would leave that belief set with no live link
    behind it, and nothing short of a restart could reopen it.
    """
    arm = _ArmThatFailsHandback()
    wrapper = PLRTransporterBackendWrapper(arm)

    await wrapper.initialize(InitializeRequest())
    with pytest.raises(ConnectionResetError):
        await wrapper.disconnect()

    assert not wrapper.is_initialized
    await wrapper.connect()

    assert arm.lifecycle == ["connect", "initialize", "disconnect", "connect"]


@pytest.mark.parametrize(
    "interface",
    [BaseDriver, ITransporterDriver, ILiquidHandlerDriver, IShakerDriver],
    ids=lambda i: i.__name__,
)
def test_every_device_interface_admits_connect_and_disconnect(interface: type) -> None:
    """The capability gate must accept the verbs on any device, not just arms.

    The gate resolves a command against the interfaces a driver advertises, so a
    verb the driver really implements is refused unless its interface declares
    it. `connect`/`disconnect` are concrete defaults on `BaseDriver`, and
    concrete members do not propagate into a subclass's `__abstractmethods__`.
    """
    commands = interface_command_names(interface)
    assert {"connect", "disconnect"} <= commands


def test_lifecycle_verbs_are_contract_not_auto_derived_vendor_extras() -> None:
    """Promoting the verbs must take them out of the auto-derived extras.

    A name in both sets is ambiguous: the extras path skips the interface gate,
    which is how connect stayed reachable as an untyped passthrough.
    """
    extras = derive_capabilities(PLRTransporterBackendWrapper)
    assert not ({"connect", "disconnect"} & set(extras))


@pytest.mark.asyncio
async def test_the_device_link_is_observable_on_its_own() -> None:
    """`is_connected` must track the link, not bring-up.

    There are two connections in this system and both have to be knowable: the
    on-prem client's link to the cloud, and each device's link to its own
    hardware. This is the second one. If it merely mirrored `is_initialized`,
    an arm that is linked but not powered up, or released but still owned by a
    live client, would be indistinguishable from one that is ready.
    """
    arm = _GranularArm()
    wrapper = PLRTransporterBackendWrapper(arm)
    assert wrapper.is_connected is False

    await wrapper.connect()
    assert wrapper.is_connected is True
    assert wrapper.is_initialized is False  # linked, not yet brought up

    await wrapper.initialize(InitializeRequest())
    assert (wrapper.is_connected, wrapper.is_initialized) == (True, True)

    await wrapper.disconnect()
    assert (wrapper.is_connected, wrapper.is_initialized) == (False, False)


@pytest.mark.asyncio
async def test_a_backend_with_no_separate_link_still_reports_the_link_it_has() -> None:
    """Without granular verbs the link has no life of its own, so it tracks bring-up.

    `setup()` opens the link on its way through, so an arm that came up the
    compound way IS linked. Reporting False there would grey the device out in
    a UI while it is happily taking commands.
    """
    wrapper = PLRTransporterBackendWrapper(_CompoundOnlyArm())
    assert wrapper.is_connected is False

    await wrapper.initialize(InitializeRequest())

    assert wrapper.is_connected is True
    assert wrapper.is_initialized is True


@pytest.mark.asyncio
async def test_a_simulated_device_reports_the_link_bring_up_gave_it() -> None:
    """A simulator has no session of its own, so it takes the default too.

    It used to gate the link on its own flag that only the `connect` verb
    moved, while bring-up left it closed. Orca brings devices up with
    `initialize` and nothing else, so every simulated device on a running bench
    reported itself disconnected while it answered commands.
    """
    shaker = SimShakerDriver("shaker_1")
    assert shaker.is_connected is False

    await shaker.initialize()

    assert (shaker.is_connected, shaker.is_initialized) == (True, True)


@pytest.mark.asyncio
async def test_every_simulated_device_answers_the_link_the_same_way() -> None:
    """The two sim base classes used to disagree after identical bring-up.

    A shaker read closed and an arm read open, so what "the link is open" meant
    depended on which sim class a device happened to be built from.
    """
    shaker = SimShakerDriver("shaker_1")
    arm = SimTransporterDriver("arm_1")

    await shaker.initialize()
    await arm.initialize(InitializeRequest())

    assert shaker.is_connected is True
    assert arm.is_connected is True


def test_every_wire_readable_property_has_somewhere_to_land() -> None:
    """A readable property needs a response model with exactly one field.

    The executor projects a bare bool onto the model's single field. A name in
    the readable set with no model falls through to `EmptyCommandResponse`,
    which has nothing to hold it, and the read fails at the last hop instead of
    being refused up front. This guard is why the set and the models cannot
    drift apart again.
    """
    from cheshire_drivers.command_responses import EmptyCommandResponse
    from cheshire_drivers.response_lookup import (
        BASE_RESPONSE_MODELS,
        WIRE_READABLE_PROPERTIES,
    )

    for name in WIRE_READABLE_PROPERTIES:
        model = BASE_RESPONSE_MODELS.get(name)
        assert model is not None, f"{name} is readable but has no response model"
        assert model is not EmptyCommandResponse, f"{name} projects onto nothing"
        assert list(model.model_fields) == [name], (
            f"{name} must land on a single field of the same name"
        )


def test_readable_properties_are_real_properties_on_the_contract() -> None:
    """Guards the other direction: a name here must actually BE a property.

    Listing a regular method would tell the executor to fetch it as an
    attribute instead of calling it, and the wire would carry a bound method.
    """
    import inspect

    from cheshire_drivers.response_lookup import WIRE_READABLE_PROPERTIES

    for name in WIRE_READABLE_PROPERTIES:
        member = inspect.getattr_static(BaseDriver, name, None)
        assert isinstance(member, property), f"{name} is not a property on BaseDriver"


async def _configured_liquid_handler() -> ChatterboxLiquidHandlerDriver:
    driver = ChatterboxLiquidHandlerDriver()
    await driver.configure_deck(DeckLayoutConfig(deck_type="STARLet", resources=[]))
    return driver


@pytest.mark.asyncio
async def test_a_liquid_handler_that_gave_up_mid_command_can_be_brought_back_up() -> None:
    """A backend that latches a refusal needs a route back, and it has to be reachable.

    A driver whose link died mid-command refuses everything after it until its
    backend is set up again. `initialize()` returns on its own flag and nothing
    ever cleared that flag, so the only route back was restarting the on-prem
    client. Handing the device back is what clears it.
    """
    driver = await _configured_liquid_handler()
    assert (driver.is_initialized, driver.is_connected) == (True, True)

    await driver.disconnect()
    assert (driver.is_initialized, driver.is_connected) == (False, False)

    await driver.initialize()

    assert (driver.is_initialized, driver.is_connected) == (True, True)


@pytest.mark.asyncio
async def test_bringing_a_live_liquid_handler_up_again_still_does_nothing() -> None:
    """Which is why the way back is `disconnect`, not an `initialize` that always sets up.

    PyLabRobot refuses a second `setup()` on a handler already up, and orca's
    bring-up walk calls `configure_deck` (which sets it up) and then
    `initialize`. An initialize that dropped its guard would raise on every boot.
    """
    driver = await _configured_liquid_handler()

    await driver.initialize()

    assert driver.is_initialized
    handler = driver._lh
    assert handler is not None
    with pytest.raises(RuntimeError, match="setup has already finished"):
        await handler.setup()


@pytest.mark.asyncio
async def test_handing_back_a_liquid_handler_that_never_came_up_is_harmless() -> None:
    """Several surfaces drive the same device, so a redundant hand-back must not raise."""
    driver = ChatterboxLiquidHandlerDriver()

    await driver.disconnect()

    assert not driver.is_initialized


class _StopHangsOnce(LiquidHandlerChatterboxBackend):
    """Handler whose first hand-back never answers, the way a dead link's does."""

    def __init__(self, num_channels: int = 8) -> None:
        super().__init__(num_channels)
        self.entered_stop = asyncio.Event()
        self.stop_attempts = 0

    async def stop(self) -> None:
        self.stop_attempts += 1
        self.entered_stop.set()
        if self.stop_attempts == 1:
            await asyncio.sleep(3600)
        await super().stop()


@pytest.mark.asyncio
async def test_a_cancelled_hand_back_leaves_the_handler_recoverable() -> None:
    """The cancel is the normal outcome on the link this recovery exists for.

    `disconnect` is declared at 15s while its `stop` waits on the wire budget, so
    the engine cancels it as a matter of course. PyLabRobot clears its own setup
    flag after the await, so a driver holding a copy cleared in a `finally` came
    out saying "down" while PyLabRobot said "up": `initialize` then raised
    forever and `disconnect` no-opped forever.
    """
    backend = _StopHangsOnce()
    driver = PLRLiquidHandlerWrapper(backend)
    await driver.configure_deck(DeckLayoutConfig(deck_type="STARLet", resources=[]))

    cut_short = asyncio.create_task(driver.disconnect())
    await backend.entered_stop.wait()
    cut_short.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cut_short

    assert driver.is_initialized  # nothing came down, and the driver says so

    await driver.disconnect()
    assert not driver.is_initialized

    await driver.initialize()
    assert driver.is_initialized
    assert backend.stop_attempts == 2
