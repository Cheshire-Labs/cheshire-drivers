"""A device's simulator implements exactly the interfaces of its real driver, so sim, check run and live accept the same commands."""

import inspect

import pytest

from cheshire_drivers.device_setup import DeviceSetup, RealDriverSetup, SimDriverSetup
from cheshire_drivers.driver_lookup import (
    _CONCRETE,
    SIM_DRIVER_CLASS_BY_KIND,
    driver_interfaces,
    real_driver_class,
    sim_driver_interfaces,
)
from cheshire_drivers.faults import RaiseFault
from cheshire_drivers.plr.backends import _BACKEND_MODULE_PATHS


def _plr_backend_names() -> set[str]:
    names = {backend for _, backend in _CONCRETE}
    for module_path in _BACKEND_MODULE_PATHS:
        try:
            module = __import__(module_path, fromlist=["_"])
        except ModuleNotFoundError:
            continue
        names |= {name for name, value in vars(module).items() if inspect.isclass(value)}
    return names


def _real_setups() -> dict[str, tuple[str, RealDriverSetup]]:
    """Every kind and real driver setup the lookup accepts, named for the test id."""
    candidates = [RealDriverSetup(type="plr", backend=backend) for backend in sorted(_plr_backend_names())]
    candidates += [RealDriverSetup(type="human"), RealDriverSetup(type="venus")]
    accepted: dict[str, tuple[str, RealDriverSetup]] = {}
    for kind in SIM_DRIVER_CLASS_BY_KIND:
        for real in candidates:
            try:
                real_driver_class(kind, real)
            except (ImportError, ValueError):
                continue
            accepted[f"{kind} {real.backend or real.type}"] = (kind, real)
    return accepted


_REAL_SETUPS = _real_setups()
_SIMULATORS: dict[str, tuple[SimDriverSetup, bool]] = {
    "plain": (SimDriverSetup(), False),
    "modelling its deck": (SimDriverSetup(), True),
    "with faults, modelling its deck": (SimDriverSetup(faults=[RaiseFault(kind="raise", method="aspirate")]), True),
}


def test_the_lookup_finds_a_real_driver_for_every_liquid_handler_backend_cheshire_drivers_names() -> None:
    named = {backend for kind, backend in _CONCRETE if kind == "liquid_handler"}
    assert named <= {real.backend for kind, real in _REAL_SETUPS.values() if kind == "liquid_handler"}


@pytest.mark.parametrize("simulator", sorted(_SIMULATORS))
@pytest.mark.parametrize("device", sorted(_REAL_SETUPS))
def test_a_simulator_implements_exactly_its_real_drivers_interfaces(device: str, simulator: str) -> None:
    kind, real = _REAL_SETUPS[device]
    sim, deck_modeling = _SIMULATORS[simulator]
    setup = DeviceSetup(client="lab_pc", real=real, sim=sim)

    assert sim_driver_interfaces(kind, setup, deck_modeling) == driver_interfaces(kind, setup, deck_modeling)
