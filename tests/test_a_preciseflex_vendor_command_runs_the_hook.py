"""A vendor command reached through the PreciseFlex's surface runs the command hook like any command."""

import pytest

from cheshire_drivers.plr.transporter import PreciseFlexTransporterDriver
from tests.command_hook_log import hook_log


@pytest.mark.asyncio
async def test_a_vendor_command_reached_through_the_surface_runs_the_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = PreciseFlexTransporterDriver(host="127.0.0.1")
    log = hook_log(driver)

    async def send_command(command: str) -> str:
        return f"echo {command}"

    monkeypatch.setattr(driver._backend, "send_command", send_command)

    assert await driver.send_command("wherej") == "echo wherej"
    assert log == [("send_command", None)]
