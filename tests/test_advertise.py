"""A device's driver is described to the server, and released, the same way by orca-client and a check run."""

import pytest

from cheshire_drivers.command_executor import connect_info, release_driver
from cheshire_drivers.plr import ChatterboxLiquidHandlerWithProtocolDriver
from cheshire_drivers.sims import SimShakerDriver, SimTransporterDriver


def test_a_simulators_description_names_its_interfaces_and_the_commands_it_takes() -> None:
    info = connect_info("shaker_1", "shaker", SimShakerDriver("Sim Shaker"))

    assert info.name == "shaker_1"
    assert info.type == "shaker"
    assert "IShaker" in info.interfaces
    assert "shake" in info.methods


def test_a_deck_simulator_says_it_keeps_deck_state() -> None:
    info = connect_info("lh", "liquid_handler", ChatterboxLiquidHandlerWithProtocolDriver())

    assert info.provides_state is True


@pytest.mark.asyncio
async def test_releasing_an_arm_disconnects_it() -> None:
    """An arm holds power between commands, so releasing it must hand it back."""
    arm = SimTransporterDriver("arm")
    calls: list[str] = []

    async def disconnect() -> None:
        calls.append("disconnect")

    setattr(arm, "disconnect", disconnect)

    await release_driver("arm", arm)

    assert calls == ["disconnect"]


@pytest.mark.asyncio
async def test_releasing_a_device_hands_it_back_and_never_closes_its_door() -> None:
    """`close` is the door, lid or plate-lock command; releasing a driver must not move the device."""
    shaker = SimShakerDriver("Sim Shaker")
    calls: list[str] = []

    async def close() -> None:
        calls.append("close")

    async def disconnect() -> None:
        calls.append("disconnect")

    async def shutdown() -> None:
        calls.append("shutdown")

    setattr(shaker, "close", close)
    setattr(shaker, "disconnect", disconnect)
    setattr(shaker, "_shutdown", shutdown)

    await release_driver("shaker_1", shaker)

    assert calls == ["disconnect", "shutdown"]


@pytest.mark.asyncio
async def test_a_failed_disconnect_still_shuts_the_driver_down() -> None:
    shaker = SimShakerDriver("Sim Shaker")
    calls: list[str] = []

    async def disconnect() -> None:
        raise RuntimeError("link already gone")

    async def shutdown() -> None:
        calls.append("shutdown")

    setattr(shaker, "disconnect", disconnect)
    setattr(shaker, "_shutdown", shutdown)

    await release_driver("shaker_1", shaker)

    assert calls == ["shutdown"]
