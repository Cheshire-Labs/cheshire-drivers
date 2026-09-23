"""Manage a simulated Opentrons robot-server subprocess for DEVICE_SIM.

The Opentrons robot-server IS the device's own simulator: it serves the same HTTP
API as real hardware and runs the same motion planning, driven by the same
``FlexLiquidHandlerDriver``, but over a virtual smoothie instead of real motors.
DEVICE_SIM boots one per Opentrons liquid handler and stops it with the device, so
an operator never starts or stops a server by hand.

The launch spec (which interpreter/cwd runs the server, which instrument config it
loads) is supplied by the caller, so this layer holds no hardcoded paths: a dev
deployment points at the Opentrons monorepo venv, a packaged product at its bundled
robot-server.
"""

import asyncio
import atexit
import os
import subprocess
import urllib.request
import weakref
from dataclasses import dataclass


@dataclass(frozen=True)
class OpentronsSimServerSpec:
    """How to launch a simulated robot-server.

    ``interpreter`` is a Python that can import ``robot_server`` + ``uvicorn``;
    ``simulator_config`` is the instrument-config JSON that selects the mounted
    pipettes and gripper.
    """

    interpreter: str
    cwd: str
    simulator_config: str


# Runs in the robot-server's interpreter (no cheshire_drivers import). The os.sync
# shim is required on Windows, where the robot-server's POSIX os.sync call 500s /health.
_BOOTSTRAP = (
    "import os\n"
    "if not hasattr(os, 'sync'): os.sync = lambda: None\n"
    "import uvicorn\n"
    "uvicorn.run('robot_server.app:app', host=os.environ['_CD_SIM_HOST'],"
    " port=int(os.environ['_CD_SIM_PORT']), ws='wsproto', log_level='warning')\n"
)


class OpentronsSimServer:
    """A robot-server subprocess bound to one liquid handler's DEVICE_SIM lifecycle."""

    def __init__(self, spec: OpentronsSimServerSpec, *, host: str = "127.0.0.1", port: int) -> None:
        self._spec = spec
        self._host = host
        self._port = port
        self._proc: subprocess.Popen[bytes] | None = None
        self._finalizer: weakref.finalize | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    async def start(self, *, timeout: float = 90.0) -> None:
        """Spawn the server and block until ``/health`` answers, or raise on timeout.

        Idempotent: a no-op while the server is already running.
        """
        if self._proc is not None and self._proc.poll() is None:
            return
        env = os.environ.copy()
        env.update(
            ENABLE_VIRTUAL_SMOOTHIE="true",
            OT_ROBOT_SERVER_persistence_directory="automatically_make_temporary",
            OT_ROBOT_SERVER_simulator_configuration_file_path=self._spec.simulator_config,
            OT_API_FF_enableOT3HardwareController="true",
            DEV_ROBOT_NAME="opentrons-dev",
            OT_ROBOT_SERVER_DOT_ENV_PATH="dev-flex.env",
            _CD_SIM_HOST=self._host,
            _CD_SIM_PORT=str(self._port),
        )
        self._proc = subprocess.Popen(
            [self._spec.interpreter, "-c", _BOOTSTRAP],
            cwd=self._spec.cwd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Kill the child if this process exits without a clean stop() (crash, abort).
        self._finalizer = weakref.finalize(self, _terminate, self._proc)
        atexit.register(self.stop)
        await self._await_health(timeout)

    async def _await_health(self, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError(
                    f"Opentrons sim server exited during startup (code {self._proc.returncode})"
                )
            if await asyncio.to_thread(self._health_ok):
                return
            if asyncio.get_running_loop().time() >= deadline:
                self.stop()
                raise TimeoutError(
                    f"Opentrons sim server did not become healthy on {self.base_url} within {timeout}s"
                )
            await asyncio.sleep(1.0)

    def _health_ok(self) -> bool:
        req = urllib.request.Request(
            f"{self.base_url}/health", headers={"opentrons-version": "3"}
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def stop(self) -> None:
        """Terminate the server subprocess; idempotent and safe to call on teardown/abort."""
        if self._finalizer is not None:
            self._finalizer.detach()
            self._finalizer = None
        if self._proc is not None:
            _terminate(self._proc)
            self._proc = None


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
