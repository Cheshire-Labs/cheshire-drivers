"""Lifecycle wiring for a device-owned sim server on PLRLiquidHandlerWrapper.

The Opentrons DEVICE_SIM path boots the vendor robot-server as a side effect of
``configure_deck`` and stops it on driver disposal. This pins that wiring with a
fake sim server (no real vendor process, so it runs in CI):

  * ``configure_deck`` starts the server (the boot rides the command path),
  * ``close`` (the per-op 'close door' command) does NOT stop it -- guards the
    regression where a mid-run close killed the session server,
  * ``_shutdown`` (the disposal hook) stops it -- guards the leak where graceful
    shutdown left the server running.

Nothing here boots the real robot-server: that needs the vendor venv.
"""

import pytest
from pylabrobot.liquid_handling.backends.chatterbox import LiquidHandlerChatterboxBackend

from cheshire_drivers.liquid_handler_models import DeckLayoutConfig
from cheshire_drivers.plr_wrappers import PLRLiquidHandlerWrapper


class _FakeSimServer:
    """Records lifecycle calls; structurally matches SimServerLifecycle."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_configure_deck_starts_sim_server_and_only_shutdown_stops_it() -> None:
    server = _FakeSimServer()
    driver = PLRLiquidHandlerWrapper(
        LiquidHandlerChatterboxBackend(num_channels=8), sim_server=server,
    )

    assert not server.started, "sim server started before any command"

    await driver.configure_deck(DeckLayoutConfig(deck_type="FlexDeck", resources=[]))
    assert server.started, "configure_deck did not start the sim server"

    await driver.close()
    assert not server.stopped, "close() stopped the sim server (must be per-op only)"

    await driver._shutdown()
    assert server.stopped, "_shutdown did not stop the sim server (disposal leak)"
