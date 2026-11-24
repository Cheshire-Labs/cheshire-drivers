"""From a device's setup to its driver class and driver: one table for the server and orca-client."""

import inspect
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias, get_type_hints

from typing_extensions import Self

from cheshire_drivers.command_executor.advertise import interfaces_of_class
from cheshire_drivers.device_setup import (
    DeviceConnection,
    DeviceSetup,
    MachineSetup,
    RealDriverSetup,
    SerialConnection,
    TcpConnection,
    UsbConnection,
    VenusProtocols,
)
from cheshire_drivers.gateway_protocol import AssignedDevice
from cheshire_drivers.human_transporter_driver import HumanTransporterDriver
from cheshire_drivers.interfaces import BaseDriver, ITransporterDriver
from cheshire_drivers.plr import (
    A4SSealerDriver,
    FlexLiquidHandlerDriver,
    OT2LiquidHandlerDriver,
    PreciseFlexTransporterDriver,
    STARLiquidHandlerDriver,
    get_plr_backend_class,
)
from cheshire_drivers.plr.opentrons_sim_server import OpentronsSimServer, OpentronsSimServerSpec
from cheshire_drivers.plr_wrappers import (
    PLRCentrifugeBackendWrapper,
    PLRSealerBackendWrapper,
    PLRShakerBackendWrapper,
    PLRThermocyclerBackendWrapper,
)
from cheshire_drivers.plr.liquid_handler import (
    ChatterboxFlexLiquidHandlerDriver,
    ChatterboxLiquidHandlerWithProtocolDriver,
    ChatterboxOT2LiquidHandlerDriver,
    ChatterboxPLRLiquidHandlerDriver,
    ChatterboxSTARLiquidHandlerDriver,
    PLRLiquidHandlerWrapper,
)
from cheshire_drivers.plr.transporter import precise_flex_from_setup
from cheshire_drivers.plr.transporter_wrapper import PLRTransporterBackendWrapper
from cheshire_drivers.plr.pf400_arm import arm_setup
from cheshire_drivers.sims import (
    SimCentrifugeDriver,
    SimDelidderDriver,
    SimFlexLiquidHandlerDriver,
    SimLiquidHandlerWithProtocolDriver,
    SimOT2LiquidHandlerDriver,
    SimPLRLiquidHandlerDriver,
    SimSTARLiquidHandlerDriver,
    SimPlateWasherDriver,
    SimReaderDriver,
    SimSealerDriver,
    SimShakerDriver,
    SimStorageDriver,
    SimThermocyclerDriver,
    SimTransporterDriver,
    SimWasteDriver,
)
from cheshire_drivers.translator_driver import SimTranslatorDriver
from cheshire_drivers.venus_driver import SimulationVenusProtocolDriver, VenusProtocolDriver

Driver: TypeAlias = BaseDriver | ITransporterDriver

DEFAULT_SIM_CHANNELS = 8
_OPENTRONS_PORT = 31950
_PRECISE_FLEX_PORT = 10100
_LOOPBACK = "127.0.0.1"

SIM_DRIVER_CLASS_BY_KIND: dict[str, type] = {
    "shaker": SimShakerDriver,
    "centrifuge": SimCentrifugeDriver,
    "sealer": SimSealerDriver,
    "transporter": SimTransporterDriver,
    "translator": SimTranslatorDriver,
    "liquid_handler": SimLiquidHandlerWithProtocolDriver,
    "plate_washer": SimPlateWasherDriver,
    "reader": SimReaderDriver,
    "delidder": SimDelidderDriver,
    "storage": SimStorageDriver,
    "waste": SimWasteDriver,
    "thermocycler": SimThermocyclerDriver,
}


@dataclass(frozen=True)
class DriverRequest:
    name: str
    kind: str
    setup: DeviceSetup
    deck_modeling: bool = False
    machine: MachineSetup = field(default_factory=MachineSetup)
    visualize: bool = False

    @classmethod
    def for_device(
        cls, device: AssignedDevice, machine: MachineSetup | None = None, visualize: bool = False,
    ) -> Self:
        return cls(
            name=device.name, kind=device.kind, setup=device.setup, deck_modeling=device.deck_modeling,
            machine=MachineSetup() if machine is None else machine, visualize=visualize,
        )


def _connection_arguments(connection: DeviceConnection | None) -> dict[str, str | int]:
    if connection is None:
        return {}
    fields = connection.model_dump(exclude={"type"})
    return {name: value for name, value in fields.items() if value is not None}


def _connection_of(request: DriverRequest) -> DeviceConnection | None:
    return None if request.setup.real is None else request.setup.real.connection


def _tcp_endpoint(request: DriverRequest, default_port: int) -> tuple[str, int]:
    connection = _connection_of(request)
    if not isinstance(connection, TcpConnection):
        raise ValueError(f"Device {request.name!r} needs a tcp connection with a host.")
    return connection.host, default_port if connection.port is None else connection.port


def _build_ot2(request: DriverRequest) -> Driver:
    host, port = _tcp_endpoint(request, _OPENTRONS_PORT)
    return OT2LiquidHandlerDriver(host, port, visualize=request.visualize)


def _build_flex(request: DriverRequest) -> Driver:
    host, port = _tcp_endpoint(request, _OPENTRONS_PORT)
    return FlexLiquidHandlerDriver(host, port, visualize=request.visualize)


def _build_star(request: DriverRequest) -> Driver:
    connection = _connection_of(request)
    usb = connection if isinstance(connection, UsbConnection) else UsbConnection(type="usb")
    return STARLiquidHandlerDriver(
        serial_number=usb.serial_number, device_address=usb.device_address, visualize=request.visualize,
    )


def _build_a4s(request: DriverRequest) -> Driver:
    connection = _connection_of(request)
    if not isinstance(connection, SerialConnection):
        raise ValueError(f"A4S sealer {request.name!r} needs a serial connection with a port.")
    return A4SSealerDriver(port=connection.port)


def _build_precise_flex(request: DriverRequest) -> Driver:
    host, port = _tcp_endpoint(request, _PRECISE_FLEX_PORT)
    return precise_flex_from_setup(host, port, request.setup.arm)


@dataclass(frozen=True)
class _LiquidHandlerSims:
    """The simulators of one real liquid handler: one plain, one that models its deck."""

    plain: type
    deck: type


_PLR_LIQUID_HANDLER_SIMS = _LiquidHandlerSims(SimPLRLiquidHandlerDriver, ChatterboxPLRLiquidHandlerDriver)


@dataclass(frozen=True)
class _Concrete:
    driver_class: type
    build: Callable[[DriverRequest], Driver]
    has_vendor_sim: bool = False
    liquid_handler_sims: _LiquidHandlerSims | None = None


_STAR = _Concrete(
    STARLiquidHandlerDriver, _build_star,
    liquid_handler_sims=_LiquidHandlerSims(SimSTARLiquidHandlerDriver, ChatterboxSTARLiquidHandlerDriver),
)
_ARM_KINDS = frozenset({"transporter", "translator"})

_PRECISE_FLEX = _Concrete(PreciseFlexTransporterDriver, _build_precise_flex)

# Consulted before get_plr_backend_class, so a mapped name never reaches the bare wrapper.
# "PreciseFlexBackend" and "OpentronsFlexBackend" are absent on purpose: naming one fails at load.
_CONCRETE: dict[tuple[str, str], _Concrete] = {
    ("liquid_handler", "OpentronsOT2Backend"): _Concrete(
        OT2LiquidHandlerDriver, _build_ot2, has_vendor_sim=True,
        liquid_handler_sims=_LiquidHandlerSims(SimOT2LiquidHandlerDriver, ChatterboxOT2LiquidHandlerDriver),
    ),
    ("liquid_handler", "OpentronsFlex"): _Concrete(
        FlexLiquidHandlerDriver, _build_flex, has_vendor_sim=True,
        liquid_handler_sims=_LiquidHandlerSims(SimFlexLiquidHandlerDriver, ChatterboxFlexLiquidHandlerDriver),
    ),
    ("liquid_handler", "STAR"): _STAR,
    ("liquid_handler", "STARBackend"): _STAR,
    ("transporter", "PreciseFlex"): _PRECISE_FLEX,
    ("translator", "PreciseFlex"): _PRECISE_FLEX,
    ("sealer", "A4SBackend"): _Concrete(A4SSealerDriver, _build_a4s),
}

_PLR_WRAPPER_BY_KIND: dict[str, Callable[..., Driver]] = {
    "shaker": PLRShakerBackendWrapper,
    "centrifuge": PLRCentrifugeBackendWrapper,
    "sealer": PLRSealerBackendWrapper,
    "thermocycler": PLRThermocyclerBackendWrapper,
    "transporter": PLRTransporterBackendWrapper,
    "translator": PLRTransporterBackendWrapper,
    "liquid_handler": PLRLiquidHandlerWrapper,
}


def _concrete_for(kind: str, real: RealDriverSetup) -> _Concrete | None:
    return None if real.backend is None else _CONCRETE.get((kind, real.backend))


def real_driver_class(kind: str, real: RealDriverSetup) -> type:
    """The class a real setup builds, read without building it, so the server can see its interfaces."""
    if real.type == "human":
        if kind != "transporter":
            raise ValueError(f"a person can only be a transporter, not a {kind}")
        return HumanTransporterDriver
    if real.type == "venus":
        if kind != "liquid_handler":
            raise ValueError(f"a Venus driver can only be a liquid_handler, not a {kind}")
        return VenusProtocolDriver
    concrete = _concrete_for(kind, real)
    if concrete is not None:
        return concrete.driver_class
    wrapper = _PLR_WRAPPER_BY_KIND.get(kind)
    if wrapper is None:
        raise ValueError(f"kind {kind} has no real driver")
    assert real.backend is not None
    assert isinstance(wrapper, type)
    backend = get_plr_backend_class(real.backend)
    takes = get_type_hints(wrapper.__init__)["backend"]
    if not issubclass(backend, takes):
        raise ValueError(f"backend {real.backend} is not a {kind} backend ({takes.__name__})")
    return wrapper


def _models_a_deck(kind: str, setup: DeviceSetup, deck_modeling: bool) -> bool:
    # Faults fire only on the Sim* classes, so a faulted liquid handler keeps the plain sim.
    return kind == "liquid_handler" and deck_modeling and not setup.sim.faults


def _liquid_handler_sims(real: RealDriverSetup) -> _LiquidHandlerSims:
    concrete = _concrete_for("liquid_handler", real)
    if concrete is None or concrete.liquid_handler_sims is None:
        return _PLR_LIQUID_HANDLER_SIMS
    return concrete.liquid_handler_sims


def sim_driver_class(kind: str, setup: DeviceSetup, deck_modeling: bool = False) -> type:
    """The class `build_sim_driver` builds for this setup, read without building it."""
    real = setup.real
    if setup.sim.use_vendor_sim:
        return _vendor_sim_class(kind, real)
    if real is not None and real.type == "venus":
        return SimulationVenusProtocolDriver
    deck = _models_a_deck(kind, setup, deck_modeling)
    if kind == "liquid_handler" and real is not None:
        sims = _liquid_handler_sims(real)
        return sims.deck if deck else sims.plain
    if deck:
        return ChatterboxLiquidHandlerWithProtocolDriver
    sim_class = SIM_DRIVER_CLASS_BY_KIND.get(kind)
    if sim_class is None:
        raise ValueError(f"no simulator for kind {kind!r}.")
    return sim_class


def sim_driver_interfaces(kind: str, setup: DeviceSetup, deck_modeling: bool = False) -> frozenset[str]:
    """What the device's simulator implements, known from its setup without building anything."""
    return interfaces_of_class(sim_driver_class(kind, setup, deck_modeling))


def driver_interfaces(kind: str, setup: DeviceSetup, deck_modeling: bool = False) -> frozenset[str]:
    """What the device's real driver implements, or its simulator when it has none, known without building anything."""
    if setup.real is not None:
        return interfaces_of_class(real_driver_class(kind, setup.real))
    return sim_driver_interfaces(kind, setup, deck_modeling)


def _connected_class(kind: str, real: RealDriverSetup) -> type | None:
    """The class whose constructor takes this setup's connection values, or None if the driver takes none."""
    if real.type != "plr":
        return None
    concrete = _concrete_for(kind, real)
    if concrete is not None:
        return concrete.driver_class
    assert real.backend is not None
    return get_plr_backend_class(real.backend)


def _check_connection(name: str, kind: str, setup: DeviceSetup, real: RealDriverSetup) -> None:
    connected = _connected_class(kind, real)
    if connected is None:
        return
    parameters = inspect.signature(connected).parameters
    given = _connection_arguments(real.connection)
    refused = sorted(set(given) - set(parameters))
    if refused:
        raise ValueError(f"Device {name!r}: backend {real.backend!r} does not take connection field(s) {refused}.")
    required = {
        parameter for parameter, value in parameters.items()
        if value.default is inspect.Parameter.empty
        and value.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }
    missing = sorted(required - set(given))
    if missing and not (real.connection is None and setup.sim.use_vendor_sim):
        raise ValueError(f"Device {name!r}: backend {real.backend!r} needs connection field(s) {missing}.")


def check_device_setup(name: str, kind: str, setup: DeviceSetup) -> None:
    """Refuse at load a setup that could never build, naming the device and what is wrong."""
    real = setup.real
    if real is not None:
        try:
            real_driver_class(kind, real)
        except (ImportError, ValueError) as refused:
            raise ValueError(f"Device {name!r}: kind {kind}, backend {real.backend!r}: {refused}. It has no real driver.") from refused
        _check_connection(name, kind, setup, real)
    if setup.sim.use_vendor_sim:
        _check_vendor_sim(name, kind, real)
    if setup.sim.faults and (setup.sim.use_vendor_sim or (real is not None and real.type == "venus")):
        raise ValueError(f"Device {name!r}: its simulator cannot inject faults, so sim.faults would do nothing.")
    if setup.arm is None:
        return
    if kind not in _ARM_KINDS:
        raise ValueError(f"Device {name!r}: only an arm takes an arm block, and this is a {kind}.")
    try:
        arm_setup(setup.arm)
    except ValueError as refused:
        raise ValueError(f"Device {name!r}: {refused}") from refused


def _build_venus(cls: type[VenusProtocolDriver | SimulationVenusProtocolDriver], request: DriverRequest) -> Driver:
    assert request.setup.real is not None
    protocols = request.setup.real.venus or VenusProtocols()
    return cls(
        name=request.name,
        init_protocol=protocols.init_protocol,
        picked_protocol=protocols.picked_protocol,
        placed_protocol=protocols.placed_protocol,
        prepare_pick_protocol=protocols.prepare_pick_protocol,
        prepare_place_protocol=protocols.prepare_place_protocol,
        open_protocol=protocols.open_protocol,
        close_protocol=protocols.close_protocol,
        exe_path=request.machine.venus_exe_path,
        methods_folder=request.machine.venus_methods_folder,
    )


def build_real_driver(request: DriverRequest) -> Driver | None:
    """The device's real driver. Building it touches no hardware. None when no real hardware is set up yet."""
    real = request.setup.real
    if real is None or (real.connection is None and request.setup.sim.use_vendor_sim):
        return None
    if real.type == "human":
        return HumanTransporterDriver(request.name)
    if real.type == "venus":
        return _build_venus(VenusProtocolDriver, request)
    concrete = _concrete_for(request.kind, real)
    if concrete is not None:
        return concrete.build(request)
    assert real.backend is not None
    backend = get_plr_backend_class(real.backend)(**_connection_arguments(real.connection))
    if request.kind == "liquid_handler":
        return PLRLiquidHandlerWrapper(backend=backend, visualize=request.visualize)
    return _PLR_WRAPPER_BY_KIND[request.kind](backend)


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as bound:
        bound.bind((_LOOPBACK, 0))
        return int(bound.getsockname()[1])


def _vendor_sim_class(kind: str, real: RealDriverSetup | None) -> type:
    """A vendor simulator is the Opentrons robot-server, so only an Opentrons backend has one."""
    concrete = None if real is None else _concrete_for(kind, real)
    if concrete is None or not concrete.has_vendor_sim:
        raise ValueError(f"backend {None if real is None else real.backend!r} has no vendor simulator.")
    return concrete.driver_class


def _check_vendor_sim(name: str, kind: str, real: RealDriverSetup | None) -> None:
    try:
        _vendor_sim_class(kind, real)
    except ValueError as refused:
        raise ValueError(f"Device {name!r}: {refused}") from refused


def _vendor_sim(request: DriverRequest, driver_class: type) -> Driver:
    machine = request.machine.opentrons_sim
    if machine is None:
        raise ValueError(f"Device {request.name!r} uses the vendor simulator, but this PC's machine config has no opentrons_sim.")
    assert request.setup.sim.simulator_config is not None
    port = _free_tcp_port()
    server = OpentronsSimServer(
        OpentronsSimServerSpec(
            interpreter=machine.interpreter,
            cwd=machine.cwd,
            simulator_config=str(Path(machine.config_folder) / request.setup.sim.simulator_config),
        ),
        port=port,
    )
    return driver_class(host=_LOOPBACK, port=port, sim_server=server, visualize=request.visualize)


def build_sim_driver(request: DriverRequest) -> Driver:
    """The device's simulated driver, which every device has."""
    sim = request.setup.sim
    try:
        driver_class = sim_driver_class(request.kind, request.setup, request.deck_modeling)
    except ValueError as refused:
        raise ValueError(f"Device {request.name!r}: {refused}") from refused
    if sim.use_vendor_sim:
        return _vendor_sim(request, driver_class)
    if driver_class is SimulationVenusProtocolDriver:
        return _build_venus(SimulationVenusProtocolDriver, request)
    if issubclass(driver_class, ChatterboxPLRLiquidHandlerDriver):
        return driver_class(
            num_channels=sim.channels or DEFAULT_SIM_CHANNELS, visualize=request.visualize, name=request.name,
        )
    return driver_class(request.name, faults=list(sim.faults)) if sim.faults else driver_class(request.name)
