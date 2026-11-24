"""Every driver class the package ships has the command hook, and hooking its commands leaves the operator surface unchanged."""

import inspect
import json
from typing import ClassVar

import pytest

from cheshire_drivers.driver_hooks import DriverHooks, DriverWithoutHooksError, is_hooked, refuse_unless_it_runs_hooks
from cheshire_drivers.interfaces import ITempGettableDriver
from cheshire_drivers.null_plate_pad import NullPlatePadDriver
from cheshire_drivers.plr.transporter_wrapper import PLRTransporterBackendWrapper
from cheshire_drivers.sims import SimShakerDriver
from tests.command_hook_log import hook_log
from tests.driver_surface_snapshot import SNAPSHOT, package_driver_classes, surface_digests


def without_the_hook(classes: list[type]) -> list[str]:
    """Concrete driver classes without the hook; an abstract mixin is covered by the concrete class that uses it."""
    return [cls.__qualname__ for cls in classes if not inspect.isabstract(cls) and not issubclass(cls, DriverHooks)]


def test_every_concrete_driver_class_under_the_package_has_the_hook() -> None:
    assert without_the_hook(package_driver_classes()) == []


def test_the_class_walk_reaches_sims_wrappers_and_plate_pads() -> None:
    found = set(package_driver_classes())

    assert {SimShakerDriver, PLRTransporterBackendWrapper, NullPlatePadDriver} <= found


def test_a_driver_class_cannot_be_written_without_the_hook() -> None:
    class Bare(ITempGettableDriver):
        async def get_temperature(self) -> float:
            return 20.0

    assert without_the_hook([Bare, SimShakerDriver]) == []
    assert is_hooked(Bare.get_temperature)


class _Forwarder(SimShakerDriver):
    runs_hooks: ClassVar[bool] = False

    async def get_temperature(self) -> float:
        return 20.0


def test_a_driver_that_only_forwards_opts_out_and_its_commands_are_not_hooked() -> None:
    assert not is_hooked(_Forwarder.get_temperature)


def test_an_object_with_no_hook_is_refused_with_a_typed_error() -> None:
    class Duck:
        async def get_temperature(self) -> float:
            return 20.0

    with pytest.raises(DriverWithoutHooksError, match="duck_1: Duck runs no command hooks"):
        refuse_unless_it_runs_hooks("duck_1", Duck())


def test_a_driver_that_opted_out_is_refused_where_commands_run_and_one_with_hooks_is_not() -> None:
    with pytest.raises(DriverWithoutHooksError, match="fwd_1: _Forwarder runs no command hooks"):
        refuse_unless_it_runs_hooks("fwd_1", _Forwarder("fwd"))

    refuse_unless_it_runs_hooks("shaker_1", SimShakerDriver("shaker"))


@pytest.mark.asyncio
async def test_a_forwarder_runs_no_hook_even_for_a_default_it_inherits() -> None:
    forwarder, hooked = _Forwarder("fwd"), SimShakerDriver("hooked")
    forwarder_log, hooked_log = hook_log(forwarder), hook_log(hooked)

    await forwarder.connect()
    await hooked.connect()

    assert forwarder_log == []
    assert hooked_log == [("connect", None)]


def test_hooking_commands_leaves_every_driver_description_byte_identical() -> None:
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    assert surface_digests() == expected
