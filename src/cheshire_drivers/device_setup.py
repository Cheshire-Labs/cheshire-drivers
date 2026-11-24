"""What one device is and how to reach it: the contents of ``devices/<name>.json``."""

from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from typing_extensions import Self

from cheshire_drivers.faults import FaultSpec

BUILTIN_CLIENT = "builtin"


class _Setup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TcpConnection(_Setup):
    type: Literal["tcp"]
    host: str
    port: int | None = Field(default=None, description="None uses the driver's default port.")


class SerialConnection(_Setup):
    type: Literal["serial"]
    port: str = Field(description="Such as COM3.")


class UsbConnection(_Setup):
    """All fields empty means the first matching instrument found on the bus."""

    type: Literal["usb"]
    serial_number: str | None = None
    device_address: int | None = None
    device_id: str | None = None


DeviceConnection = Annotated[TcpConnection | SerialConnection | UsbConnection, Field(discriminator="type")]


class VenusProtocols(_Setup):
    """Method files relative to the lab PC's Venus methods folder. An unset event does nothing."""

    init_protocol: str | None = None
    picked_protocol: str | None = None
    placed_protocol: str | None = None
    prepare_pick_protocol: str | None = None
    prepare_place_protocol: str | None = None
    open_protocol: str | None = None
    close_protocol: str | None = None


class RealDriverSetup(_Setup):
    type: Literal["plr", "venus", "human"]
    backend: str | None = Field(default=None, description="A PyLabRobot backend class or a device name the lookup maps.")
    connection: DeviceConnection | None = None
    venus: VenusProtocols | None = None

    @model_validator(mode="after")
    def _plr_names_a_backend(self) -> Self:
        if self.type == "plr" and not self.backend:
            raise ValueError("a 'plr' driver needs a backend")
        return self

    @model_validator(mode="after")
    def _sets_only_what_its_driver_reads(self) -> Self:
        read_by = {"plr": {"backend", "connection"}, "venus": {"venus"}, "human": set()}[self.type]
        given = {"backend": self.backend, "connection": self.connection, "venus": self.venus}
        unread = sorted(name for name, value in given.items() if value is not None and name not in read_by)
        if unread:
            raise ValueError(f"a '{self.type}' driver does not read {unread}")
        return self


def _is_plain_file_name(name: str) -> bool:
    # "." and ".." pass the name check because each is its own last path part.
    if name in ("", ".", ".."):
        return False
    return PureWindowsPath(name).name == name and PurePosixPath(name).name == name


class SimDriverSetup(_Setup):
    channels: Literal[1, 8, 96] | None = None
    faults: list[FaultSpec] = Field(default_factory=list)
    use_vendor_sim: bool = False
    simulator_config: str | None = Field(
        default=None, description="Instrument-config file name, in the lab PC's vendor-sim config folder.",
    )

    @model_validator(mode="after")
    def _vendor_sim_names_one_file(self) -> Self:
        if self.use_vendor_sim and self.simulator_config is None:
            raise ValueError("use_vendor_sim needs a simulator_config file name")
        if not self.use_vendor_sim and self.simulator_config is not None:
            raise ValueError("simulator_config is read only with use_vendor_sim")
        name = self.simulator_config
        if name is not None and not _is_plain_file_name(name):
            raise ValueError(f"simulator_config must be a file name, not a path: {name!r}")
        return self


class DeviceSetup(_Setup):
    client: str | None = Field(default=None, description="The orca-client that runs this device. None means the server's default client.")
    arm: dict[str, JsonValue] | None = Field(
        default=None, description="The gripper bolted to an arm, which its controller cannot report (plr/pf400_arm.py).",
    )
    real: RealDriverSetup | None = None
    sim: SimDriverSetup = Field(default_factory=SimDriverSetup)
    always_sim: bool = False

    @model_validator(mode="after")
    def _built_in_client_has_only_plain_sims(self) -> Self:
        if self.client != BUILTIN_CLIENT:
            return self
        if self.real is not None:
            raise ValueError("a real driver needs 'client' to name a lab PC")
        if self.sim.use_vendor_sim:
            raise ValueError("use_vendor_sim needs 'client' to name a lab PC, whose machine config says where the simulator is")
        return self


class OpentronsSimMachine(_Setup):
    """Where a vendor Opentrons simulator is on this PC."""

    interpreter: str = Field(description="A Python that can import robot_server and uvicorn.")
    cwd: str = Field(description="The robot-server working directory.")
    config_folder: str = Field(description="Folder of instrument-config files that setups name.")


class MachineSetup(_Setup):
    """Programs and folders on one lab PC. Never sent by a server, so a server cannot choose what a PC starts."""

    venus_exe_path: str = r"C:\Program Files (x86)\HAMILTON\Bin\HxRun.exe"
    venus_methods_folder: str = r"C:\Program Files (x86)\HAMILTON\Methods"
    opentrons_sim: OpentronsSimMachine | None = None
