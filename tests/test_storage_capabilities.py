"""Walk-style guards for the storage wire-call contract.

`IStorageDriver` used to be an empty marker. It now declares `dispense()`
as the device-side wire command for a stacker or hotel that releases one
plate on demand.

The shape is parameterless and returns `None`: a driver knows only how to
advance the source's queue. Per-source extensibility (barcode at
dispense, labware-type hint) is deferred until a real use case asks
for it.
"""

import asyncio

import pytest

from cheshire_drivers import sims
from cheshire_drivers.driver_introspection import (
    derive_capabilities,
    interface_member_names,
)
from cheshire_drivers.interfaces import IStorageDriver


class TestIStorageDriverDispenseAbstract:
    """`IStorageDriver` declares `dispense` as part of the contract."""

    def test_dispense_is_abstract_on_istorage_driver(self) -> None:
        assert "dispense" in IStorageDriver.__abstractmethods__

    def test_dispense_is_listed_in_interface_member_names(self) -> None:
        assert "dispense" in interface_member_names(IStorageDriver)

    def test_dispense_signature_is_parameterless(self) -> None:
        import inspect

        sig = inspect.signature(IStorageDriver.dispense)
        params = [name for name in sig.parameters if name != "self"]
        assert params == [], (
            f"IStorageDriver.dispense must be parameterless; got params={params}"
        )

    def test_dispense_return_annotation_is_none(self) -> None:
        import inspect

        sig = inspect.signature(IStorageDriver.dispense)
        assert sig.return_annotation is None or sig.return_annotation is type(None), (
            f"IStorageDriver.dispense must return None; got {sig.return_annotation}"
        )


class TestSimStorageDriverDispense:
    """`SimStorageDriver.dispense()` is callable and is a no-op."""

    def test_sim_storage_driver_is_instantiable(self) -> None:
        sims.SimStorageDriver(name="stacker_test")

    def test_sim_storage_driver_dispense_returns_none(self) -> None:
        driver = sims.SimStorageDriver(name="stacker_test")
        result = asyncio.run(driver.dispense())
        assert result is None

    def test_sim_waste_driver_inherits_dispense(self) -> None:
        # IWasteDriver(IStorageDriver) so SimWasteDriver inherits the
        # contract too; the no-op semantic is fine for a waste device.
        driver = sims.SimWasteDriver(name="waste_test")
        result = asyncio.run(driver.dispense())
        assert result is None


class TestDispenseInAutoDerivedCapabilities:
    """`dispense` MUST belong to the IStorage contract, NOT the auto-derived
    vendor extras. If `dispense` shows up in `derive_capabilities()` output,
    the abstract declaration was lost through MRO and the wire layer would
    miss the typed contract."""

    def test_dispense_is_not_a_vendor_extra_on_sim_storage(self) -> None:
        caps = derive_capabilities(sims.SimStorageDriver)
        assert "dispense" not in caps, (
            f"`dispense` leaked into auto-derived caps {sorted(caps)} - it "
            f"must live in the IStorageDriver contract, not as a vendor extra"
        )
