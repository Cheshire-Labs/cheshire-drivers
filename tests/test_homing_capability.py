"""Homing is a capability a driver declares, not a thing only arms do.

Bring-up stopped homing, so every device whose axes lose their reference at
power-off needs homing reachable on its own terms. On the Flex it was a vendor
extra, which meant the only route to it was the confirm-gated batch escape
hatch -- an odd place for the command an operator needs before the first move
of the day. `IHomeableDriver` gives it the same standing an arm's home has.
"""

import pytest

from cheshire_drivers.faults import RaiseFault
from cheshire_drivers.driver_introspection import (
    derive_capabilities,
    interface_command_names,
)
from cheshire_drivers.homing_models import HomeRequest
from cheshire_drivers.interfaces import IHomeableDriver, ITransporterDriver
from cheshire_drivers.lh_request_validation import LH_REQUEST_MODELS
from cheshire_drivers.plr.opentrons_flex import FlexLiquidHandlerDriver
from cheshire_drivers.sims import SimLiquidHandlerDriver, SimTransporterDriver


class TestTheCapabilityIsDeclared:
    def test_home_is_the_whole_contract(self) -> None:
        assert interface_command_names(IHomeableDriver) == {"home"}

    def test_an_arm_still_carries_home(self) -> None:
        """Moving `home` onto the shared capability must not cost the transporter
        contract the verb every arm already implements."""
        assert "home" in interface_command_names(ITransporterDriver)
        assert "IHomeable" in ITransporterDriver.interfaces

    def test_a_flex_advertises_homing_as_an_interface(self) -> None:
        assert "IHomeable" in FlexLiquidHandlerDriver.interfaces

    def test_a_flex_no_longer_smuggles_home_in_as_a_vendor_extra(self) -> None:
        """Vendor extras are reached through `batch_execute` with a confirm flag.
        That is the wrong door for the command that makes a first move safe."""
        assert "home" not in derive_capabilities(FlexLiquidHandlerDriver)

    def test_the_wire_knows_what_shape_a_home_takes(self) -> None:
        """The gateway validates and orca-client wraps from this one map, so a liquid
        handler's home crosses the wire only if it is registered here."""
        assert LH_REQUEST_MODELS["home"] == ("request", HomeRequest)


class TestSimParity:
    @pytest.mark.asyncio
    async def test_a_sim_liquid_handler_accepts_a_home(self) -> None:
        """A workflow that homes before its first move has to run with no
        hardware, or sim mode refuses a command the real driver takes."""
        driver = SimLiquidHandlerDriver("sim_lh")

        await driver.home(HomeRequest())

        assert "IHomeable" in driver.interfaces

    @pytest.mark.asyncio
    async def test_a_sim_arm_accepts_a_home(self) -> None:
        driver = SimTransporterDriver("sim_arm")

        await driver.home(HomeRequest())


class TestSimHomingBehavesLikeEveryOtherSimVerb:
    @pytest.mark.asyncio
    async def test_a_sim_home_can_be_made_to_fail(self) -> None:
        """Every other sim mixin runs its methods through the fault hook, which is
        what lets a harness rehearse the failure. Leaving homing out would make the
        one command an operator runs before the first move of the day the one
        command no test can make fail."""
        driver = SimLiquidHandlerDriver(
            "sim_lh", faults=[RaiseFault(method="home", error_type="RuntimeError")],
        )

        with pytest.raises(RuntimeError):
            await driver.home(HomeRequest())

    def test_a_typo_in_a_fault_method_is_still_rejected(self) -> None:
        """The allowlist has to keep catching typos; admitting `home` must not turn
        it into an accept-anything."""
        with pytest.raises(ValueError, match="not a faultable"):
            SimLiquidHandlerDriver(
                "sim_lh", faults=[RaiseFault(method="hom", error_type="RuntimeError")],
            )
