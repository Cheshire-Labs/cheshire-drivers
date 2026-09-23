"""Tests for the driver-side wire budget.

The invariant that matters: a driver's own budget must outlast every command it
declares. The engine owns command timeouts and cancels an overrunning command so
the operator gets the recoverable-timeout abort/mark-complete decision; a driver
that gives up first replaces that decision with a transport error. These guard
the derivation, the refusal of a budget that would fire first, and that every
driver hands the budget to its backend rather than keeping it as decoration.
"""

import pytest
from pydantic import ValidationError

from cheshire_drivers.command_timings import command_timing
from cheshire_drivers.interfaces import BaseDriver
from cheshire_drivers.plr.liquid_handler import OT2LiquidHandlerDriver
from cheshire_drivers.plr.opentrons_flex import FlexLiquidHandlerDriver
from cheshire_drivers.plr.sealer import A4SSealerDriver
from cheshire_drivers.wire_timeouts import (
    WireTimeout,
    covering_seconds,
    declared_maximum_seconds,
    require_covering,
    resolve_wire_timeout,
)
from pylabrobot.opentrons import ChatterboxTransport
from pylabrobot.opentrons.transport import HttpxTransport
from pylabrobot.sealing import A4SBackend

OpentronsDriver = type[OT2LiquidHandlerDriver] | type[FlexLiquidHandlerDriver]
OPENTRONS_DRIVERS: list[OpentronsDriver] = [OT2LiquidHandlerDriver, FlexLiquidHandlerDriver]


class _TwoCommandDriver(BaseDriver):
    """Declares one fast command and one that outlasts everything BaseDriver declares."""

    @command_timing(typical=1.0, max=4.0)
    async def quick(self) -> None: ...

    @command_timing(typical=200.0, max=1000.0)
    async def slow(self) -> None: ...


class TestDerivation:
    def test_the_slowest_command_sets_the_budget(self):
        assert declared_maximum_seconds(_TwoCommandDriver) == 1000.0
        assert covering_seconds(_TwoCommandDriver, margin=1.5) == 1500.0

    @pytest.mark.parametrize("driver_cls", OPENTRONS_DRIVERS)
    def test_the_fastest_declared_command_plays_no_part(
        self, driver_cls: OpentronsDriver
    ):
        """A declared timing bounds a whole command, not one round trip.

        Deriving a per-request budget from the fastest command produced 7.5s on
        both drivers, from `get_channel_position` at max=5. That command is an
        enqueue-and-poll round on both robots, not a bare request/response, and
        the budget it produced then bounded real work inside much slower
        commands.
        """
        driver = driver_cls(host="localhost")

        assert driver.wire_timeout.seconds == covering_seconds(driver_cls)
        assert driver.wire_timeout.seconds == 900.0


class TestRefusal:
    def test_a_budget_under_the_slowest_declared_command_is_refused(self):
        with pytest.raises(ValueError, match="gives up before"):
            require_covering(999.0, _TwoCommandDriver)

    def test_a_budget_matching_the_slowest_declared_command_is_allowed(self):
        """The engine's clock starts first, so an equal budget still fires second."""
        require_covering(1000.0, _TwoCommandDriver)

    def test_an_override_that_would_give_up_first_is_refused(self):
        short = WireTimeout(seconds=999.0, poll_interval_seconds=0.1)

        with pytest.raises(ValueError, match="gives up before"):
            resolve_wire_timeout(short, _TwoCommandDriver, poll_interval=0.1)

    def test_an_override_that_outlasts_the_engine_is_kept_as_given(self):
        generous = WireTimeout(seconds=3600.0, poll_interval_seconds=2.0)

        budget = resolve_wire_timeout(generous, _TwoCommandDriver, poll_interval=0.1)

        assert budget is generous

    def test_no_override_derives_a_budget_and_takes_the_given_poll_rate(self):
        budget = resolve_wire_timeout(None, _TwoCommandDriver, poll_interval=0.25)

        assert budget.seconds == 1500.0
        assert budget.poll_interval_seconds == 0.25


class TestModel:
    @pytest.mark.parametrize("field", ["seconds", "poll_interval_seconds"])
    def test_a_zero_budget_is_refused(self, field: str):
        values = {"seconds": 1.0, "poll_interval_seconds": 1.0, field: 0.0}

        with pytest.raises(ValidationError):
            WireTimeout(**values)

    def test_the_budget_is_frozen(self):
        budget = WireTimeout(seconds=2.0, poll_interval_seconds=0.1)

        with pytest.raises(ValidationError):
            budget.seconds = 5.0


class TestOpentronsDriversAreWiredToIt:
    @pytest.mark.parametrize("driver_cls", OPENTRONS_DRIVERS)
    def test_the_default_budget_outlasts_every_command_the_driver_declares(
        self, driver_cls: OpentronsDriver
    ):
        driver = driver_cls(host="localhost")

        require_covering(driver.wire_timeout.seconds, driver_cls)

    @pytest.mark.parametrize("driver_cls", OPENTRONS_DRIVERS)
    def test_a_budget_under_a_declared_command_is_refused_at_construction(
        self, driver_cls: OpentronsDriver
    ):
        """The 7.5s a fastest-command derivation produced would have passed silently."""
        short = WireTimeout(seconds=7.5, poll_interval_seconds=0.05)

        with pytest.raises(ValueError, match="gives up before"):
            driver_cls(host="localhost", wire_timeout=short)

    def test_the_ot2_backend_gets_the_budget_on_both_of_its_waits(self):
        """The OT-2 backend is the only OT-2 PyLabRobot has, and it hands out no
        transport, so the budget goes on the backend or it reaches nothing."""
        budget = WireTimeout(seconds=1200.0, poll_interval_seconds=0.33)

        driver = OT2LiquidHandlerDriver(host="localhost", wire_timeout=budget)

        backend = driver._ot_backend
        assert backend.request_timeout == 1200.0
        assert backend.command_timeout == 1200.0
        assert backend.status_poll_interval == 0.33

    def test_the_flex_transport_carries_the_budget(self):
        """The transport is the only place the budget can go, so the driver builds it."""
        budget = WireTimeout(seconds=1200.0, poll_interval_seconds=0.33)

        driver = FlexLiquidHandlerDriver(host="localhost", wire_timeout=budget)

        flex = driver._flex
        assert flex is not None
        transport = flex._transport
        assert isinstance(transport, HttpxTransport)
        assert transport.io.serialize()["timeout"] == 1200.0

    def test_the_flex_command_budget_outlasts_the_commands_the_driver_declares(self):
        """A request budget bounds one exchange. A home the robot-server takes minutes
        over is one command over many exchanges, and only this budget covers it."""
        budget = WireTimeout(seconds=1200.0, poll_interval_seconds=0.33)

        driver = FlexLiquidHandlerDriver(host="localhost", wire_timeout=budget)

        flex = driver._flex
        assert flex is not None
        assert flex.command_timeout == 1200.0
        assert flex.status_poll_interval == 0.33

    def test_an_injected_flex_transport_keeps_its_own_budget(self):
        """A transport somebody else built already carries a budget, so the driver
        must not replace it with one of its own."""
        injected = ChatterboxTransport()

        driver = FlexLiquidHandlerDriver(host="localhost", transport=injected)

        flex = driver._flex
        assert flex is not None
        assert flex._transport is injected
        # The command budget is the robot's, not the transport's, so it still applies.
        assert flex.command_timeout == driver.wire_timeout.seconds


class TestSealerIsWiredToIt:
    def test_the_default_budget_outlasts_the_seal_cycle_it_waits_out(self):
        """20s was the old default against a `seal` the engine allows 600s."""
        driver = A4SSealerDriver(port="COM3")

        backend = driver._backend
        assert isinstance(backend, A4SBackend)
        assert backend.timeout == round(covering_seconds(A4SSealerDriver))
        assert backend.timeout >= declared_maximum_seconds(A4SSealerDriver)

    def test_a_budget_under_the_seal_cycle_is_refused(self):
        with pytest.raises(ValueError, match="gives up before"):
            A4SSealerDriver(port="COM3", timeout=20)
