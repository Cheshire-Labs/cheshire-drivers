"""PyLabRobot backends whose wire opens a FirstMoveGate before the first command that moves."""

from collections.abc import Callable

from pydantic import JsonValue
from pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_backend import STARBackend
from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import OpentronsOT2Backend

from cheshire_drivers.head_retract import FirstMoveGate
from cheshire_drivers.plr.lift_before_first_move import command_moves_in_xy


def star_command_moves(command: str) -> bool:
    """Hamilton firmware requests start with R or Q; every other command may move something."""
    return not command.startswith(("R", "Q"))


class GatedSTARBackend(STARBackend):
    """The STAR backend, holding an armed lift until the first firmware command that may move."""

    def __init__(
        self, gate: FirstMoveGate, on_jaws_opened: Callable[[], None],
        serial_number: str | None, device_address: int | None, left_side_panel_installed: bool,
    ) -> None:
        super().__init__(
            device_address=device_address,
            serial_number=serial_number,
            left_side_panel_installed=left_side_panel_installed,
        )
        self._gate = gate
        self._on_jaws_opened = on_jaws_opened

    async def send_command(
        self, module: str, command: str, auto_id: bool = True, tip_pattern: list[bool] | None = None,
        write_timeout: int | None = None, read_timeout: int | None = None, wait: bool = True,
        fmt: str | None = None, **kwargs: JsonValue,
    ) -> dict[str, JsonValue] | str | None:
        await self._gate.before(moves=star_command_moves(command))
        result: dict[str, JsonValue] | str | None = await super().send_command(
            module, command, auto_id=auto_id, tip_pattern=tip_pattern, write_timeout=write_timeout,
            read_timeout=read_timeout, wait=wait, fmt=fmt, **kwargs,
        )
        return result

    async def iswap_open_gripper(self, open_position: float | None = None) -> None:
        await super().iswap_open_gripper(open_position=open_position)
        self._on_jaws_opened()


class GatedOT2Backend(OpentronsOT2Backend):
    """The OT-2 backend, holding an armed lift until the first robot-server command that moves the gantry."""

    def __init__(
        self, gate: FirstMoveGate, host: str, port: int,
        request_timeout: float, command_timeout: float, status_poll_interval: float,
    ) -> None:
        super().__init__(
            host=host,
            port=port,
            request_timeout=request_timeout,
            command_timeout=command_timeout,
            status_poll_interval=status_poll_interval,
        )
        self._gate = gate

    async def _command(
        self, command_type: str, params: dict[str, JsonValue],
        timeout: float | None = None, abandon_run_on_timeout: bool = True,
    ) -> dict[str, JsonValue]:
        await self._gate.before(moves=command_moves_in_xy(command_type, params))
        result: dict[str, JsonValue] = await super()._command(
            command_type, params, timeout=timeout, abandon_run_on_timeout=abandon_run_on_timeout,
        )
        return result
