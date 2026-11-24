"""Opentrons robot-server commands that move the gantry, and a wire that lifts every head before the first one."""

from pydantic import JsonValue
from pylabrobot.opentrons import OpentronsTransport

from cheshire_drivers.head_retract import FirstMoveGate

_MOVES_IN_XY: frozenset[str] = frozenset({
    "aspirate",
    "dispense",
    "blowout",
    "touchTip",
    "pickUpTip",
    "dropTip",
    "liquidProbe",
    "tryLiquidProbe",
    "moveToWell",
    "moveToCoordinates",
    "moveToAddressableArea",
    "moveToAddressableAreaForDropTip",
    "moveLabware",
    "robot/moveTo",
    "robot/moveAxesTo",
    "robot/moveAxesRelative",
})
"""Robot-server command types that can carry the gantry in X or Y, whatever their parameters."""

_ONE_AXIS: frozenset[str] = frozenset({"retractAxis", "moveRelative"})
"""Command types that move one named axis, so they move the gantry only when that axis is x or y."""


def command_moves_in_xy(command_type: str, params: JsonValue) -> bool:
    """True when this robot-server command can carry the gantry in X or Y."""
    if command_type in _ONE_AXIS:
        return isinstance(params, dict) and params.get("axis") in ("x", "y")
    return command_type in _MOVES_IN_XY


def _payload_moves_in_xy(payload: dict[str, JsonValue] | None) -> bool:
    data = None if payload is None else payload.get("data")
    if not isinstance(data, dict):
        return False
    command_type = data.get("commandType")
    return isinstance(command_type, str) and command_moves_in_xy(command_type, data.get("params"))


class LiftBeforeFirstMove:
    """Forwards to the robot's transport, and opens `gate` before a command that moves the gantry."""

    def __init__(self, inner: OpentronsTransport, gate: FirstMoveGate) -> None:
        self.inner = inner
        self._gate = gate

    async def setup(self) -> None:
        await self.inner.setup()

    async def get(self, path: str) -> dict[str, JsonValue]:
        return await self.inner.get(path)

    async def post(self, path: str, json: dict[str, JsonValue] | None = None) -> dict[str, JsonValue]:
        await self._gate.before(moves=_payload_moves_in_xy(json))
        return await self.inner.post(path, json=json)

    async def delete(self, path: str) -> dict[str, JsonValue]:
        return await self.inner.delete(path)

    async def close(self) -> None:
        await self.inner.close()
