from abc import ABC

from cheshire_drivers.interfaces import IProtocolRunnerDriver
from cheshire_drivers.protocol_runner_models import LabwareHandoffRequest


class IgnoresLabwareHandoff(IProtocolRunnerDriver, ABC):
    """A protocol runner with nothing to do when labware is handed to or from it."""

    async def prepare_for_place(self, request: LabwareHandoffRequest) -> None:
        return None

    async def notify_placed(self, request: LabwareHandoffRequest) -> None:
        return None

    async def prepare_for_pick(self, request: LabwareHandoffRequest) -> None:
        return None

    async def notify_picked(self, request: LabwareHandoffRequest) -> None:
        return None
