import asyncio
import json
import logging
import os
import tempfile
from typing import Any, Dict, Optional

from cheshire_drivers.driver_errors import DriverError
from cheshire_drivers.interfaces import IProtocolRunnerDriver
from cheshire_drivers.protocol_runner_models import LabwareHandoffRequest, RunProtocolRequest

cheshire_logger = logging.getLogger("cheshire_drivers")

class SimulationVenusProtocolDriver(IProtocolRunnerDriver):
    """
    Simulation of Venus Protocol Driver for testing purposes.
    """

    def __init__(self,
                name: str,
                init_protocol: Optional[str]  = None,
                picked_protocol: Optional[str]  = None,
                placed_protocol: Optional[str]  = None,
                prepare_pick_protocol: Optional[str]  = None,
                prepare_place_protocol: Optional[str]  = None,
                open_protocol: Optional[str] = None,
                close_protocol: Optional[str] = None,
                exe_path: str = r"C:\Program Files (x86)\HAMILTON\Bin\HxRun.exe",
                methods_folder: str = r"C:\Program Files (x86)\HAMILTON\Methods",
                sim_time: float = 0.1):
        self._name = name
        self._sim_time = sim_time
        self._exe_path = exe_path
        self._methods_folder = methods_folder
        self._is_connected = False
        self._is_initialized = False
        self._is_running = False
        self._init_protocol: Optional[str]  = init_protocol
        self._picked_protocol: Optional[str]  = picked_protocol
        self._placed_protocol: Optional[str]  = placed_protocol
        self._prepare_pick_protocol: Optional[str]  = prepare_pick_protocol
        self._prepare_place_protocol: Optional[str]  = prepare_place_protocol
        self._open_protocol: Optional[str] = open_protocol
        self._close_protocol: Optional[str] = close_protocol

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def connect(self) -> None:
        self._is_connected = True

    async def disconnect(self) -> None:
        self._is_connected = False

    async def initialize(self) -> None:
        cheshire_logger.info(f"Initializing Venus Protocol Driver: {self._name}")
        self._is_initialized = True

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def prepare_for_place(self, request: LabwareHandoffRequest) -> None:
        await self._sim_hook(self._prepare_place_protocol, request)

    async def prepare_for_pick(self, request: LabwareHandoffRequest) -> None:
        await self._sim_hook(self._prepare_pick_protocol, request)

    async def notify_picked(self, request: LabwareHandoffRequest) -> None:
        await self._sim_hook(self._picked_protocol, request)

    async def notify_placed(self, request: LabwareHandoffRequest) -> None:
        await self._sim_hook(self._placed_protocol, request)

    async def _sim_hook(self, method: Optional[str], request: LabwareHandoffRequest) -> None:
        if method is None:
            return
        cheshire_logger.info(f"Running {method} for {request.labware_name} at {request.site}")
        await asyncio.sleep(self._sim_time)

    async def execute(self, command: str, options: Dict[str, Any]) -> None:
        cheshire_logger.info(f"Executing command: {command} with options: {options}")
        await asyncio.sleep(self._sim_time)

    async def run_protocol(self, request: RunProtocolRequest) -> None:
        cheshire_logger.info(
            f"Running protocol: {request.protocol_filepath} with params: {request.params}"
        )
        await asyncio.sleep(self._sim_time)

    async def open(self) -> None:
        cheshire_logger.info(f"{self.name} opening...")
        if self._open_protocol:
            cheshire_logger.info(f"Running: {self._open_protocol}")
            await asyncio.sleep(self._sim_time)
        cheshire_logger.info(f"{self.name} opened")

    async def close(self) -> None:
        cheshire_logger.info(f"{self.name} closing...")
        if self._close_protocol:
            cheshire_logger.info(f"Running: {self._close_protocol}")
            await asyncio.sleep(self._sim_time)
        cheshire_logger.info(f"{self.name} closed")

class VenusProtocolDriver(IProtocolRunnerDriver):
    """
    Driver for interfacing with Hamilton Venus protocols.
    """

    def __init__(self,
                name: str,
                init_protocol: Optional[str]  = None,
                picked_protocol: Optional[str]  = None,
                placed_protocol: Optional[str]  = None,
                prepare_pick_protocol: Optional[str]  = None,
                prepare_place_protocol: Optional[str]  = None,
                open_protocol: Optional[str] = None,
                close_protocol: Optional[str] = None,
                exe_path: str = r"C:\Program Files (x86)\HAMILTON\Bin\HxRun.exe",
                methods_folder: str = r"C:\Program Files (x86)\HAMILTON\Methods"):
        self._name = name
        self._exe_path = exe_path
        self._methods_folder = methods_folder
        self._is_connected = False
        self._is_initialized = False
        self._is_running = False
        # The Orca submethod library reads its values from this file.
        self._params_filepath = os.path.join(tempfile.gettempdir(), "CheshireLabs", "Orca", "actionConfig.json")
        self._init_protocol: Optional[str]  = init_protocol
        self._picked_protocol: Optional[str]  = picked_protocol
        self._placed_protocol: Optional[str]  = placed_protocol
        self._prepare_pick_protocol: Optional[str]  = prepare_pick_protocol
        self._prepare_place_protocol: Optional[str]  = prepare_place_protocol
        self._open_protocol: Optional[str] = open_protocol
        self._close_protocol: Optional[str] = close_protocol

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    async def connect(self) -> None:
        self._is_connected = True

    async def disconnect(self) -> None:
        self._is_connected = False

    async def initialize(self) -> None:
        if not os.path.exists(self._exe_path):
            raise FileNotFoundError(f"HxRun.exe was not found at '{self._exe_path}'.")
        os.makedirs(os.path.dirname(self._params_filepath), exist_ok=True)
        if self._init_protocol is not None:
            await self._run_method(self._init_protocol, {"action": "initialize"})
        self._is_initialized = True

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def prepare_for_place(self, request: LabwareHandoffRequest) -> None:
        await self._run_hook(self._prepare_place_protocol, "prepare_for_place", request)

    async def prepare_for_pick(self, request: LabwareHandoffRequest) -> None:
        await self._run_hook(self._prepare_pick_protocol, "prepare_for_pick", request)

    async def notify_picked(self, request: LabwareHandoffRequest) -> None:
        await self._run_hook(self._picked_protocol, "notify_picked", request)

    async def notify_placed(self, request: LabwareHandoffRequest) -> None:
        await self._run_hook(self._placed_protocol, "notify_placed", request)

    async def _run_hook(self, method: Optional[str], action: str, request: LabwareHandoffRequest) -> None:
        if method is None:
            return
        # The Orca library reads these with GetConfigProperty_String, so a missing value is "", never null.
        await self._run_method(method, {
            "action": action,
            "labware_name": request.labware_name,
            "labware_type": request.labware_type,
            "site": request.site or "",
            "barcode": request.barcode or "",
        })

    async def execute(self, command: str, options: Dict[str, Any]) -> None:
        if command == "run_protocol":
            method = options.get("method")
            if method is None:
                raise KeyError("The venus method was not provided in the command options.  'method' must be included with command")
            params = options.get("params", {})
            await self.run_protocol(RunProtocolRequest(protocol_filepath=method, params=params))
        else:
            raise NotImplementedError(f"The action '{command}' is unknown for {self._name} of type {type(self).__name__}")

    async def run_protocol(self, request: RunProtocolRequest) -> None:
        await self._run_method(request.protocol_filepath, {**request.params, "action": "run"})

    async def open(self) -> None:
        if self._open_protocol:
            await self._run_method(self._open_protocol, {"action": "open"})

    async def close(self) -> None:
        if self._close_protocol:
            await self._run_method(self._close_protocol, {"action": "close"})

    async def _run_method(self, method: str, params: Dict[str, Any]) -> None:
        method_path = self._method_inside_the_methods_folder(method)
        if not os.path.exists(method_path):
            raise FileNotFoundError(f"The method '{method}' does not exist in the methods folder '{self._methods_folder}'.")
        self._write_params_file(params)
        await self._execute_protocol(method_path)

    def _method_inside_the_methods_folder(self, method: str) -> str:
        """Resolve a method name under the methods folder, refusing to leave it.

        The name arrives over the wire, and an unchecked one becomes argv to
        HxRun.exe: an absolute path or a `..` hop would run any file on the box.
        """
        folder = os.path.realpath(self._methods_folder)
        resolved = os.path.realpath(os.path.join(folder, method))
        if resolved != folder and not resolved.startswith(folder + os.sep):
            raise ValueError(
                f"The method '{method}' resolves outside the methods folder "
                f"'{self._methods_folder}'."
            )
        return resolved

    async def _execute_protocol(self, hsl_method_path: str) -> None:
        self._is_running = True
        try:
            process = await asyncio.create_subprocess_exec(
                self._exe_path, "-t", hsl_method_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await process.communicate()
        finally:
            self._is_running = False
        if process.returncode != 0:
            raise DriverError(
                f"Venus method '{hsl_method_path}' failed with exit code {process.returncode}: "
                f"{stderr.decode(errors='replace').strip()}"
            )

    def _write_params_file(self, params: Dict[str, Any]) -> None:
        with open(self._params_filepath, "w") as params_file:
            json.dump({"params": params}, params_file)
