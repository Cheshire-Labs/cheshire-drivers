"""Stream/chatterbox-categories: smoke + interface-conformance tests for the
new PLR Chatterbox-backed drivers in cheshire_drivers.plr.

Each driver:
  - instantiates without arguments (chatterbox needs no hardware),
  - advertises the expected `interfaces` ClassVar so discovery surfaces it,
  - lifecycle methods (initialize/open/close) print PLR chatterbox output,
  - one domain method per category (where defined on the cheshire interface)
    routes to the underlying PLR backend without raising.
"""

import pytest

from cheshire_drivers.centrifuge_models import CentrifugeRequest
from cheshire_drivers.interfaces import (
    ICentrifugeDriver,
    IReaderDriver,
    IShakerDriver,
    IStorageDriver,
    ITempGettableDriver,
    ITempSettableDriver,
)
from cheshire_drivers.plr import (
    ChatterboxCentrifugeDriver,
    ChatterboxHeatingShakerDriver,
    ChatterboxReaderDriver,
    ChatterboxStorageDriver,
)
from cheshire_drivers.reader_models import ReadRequest
from cheshire_drivers.shaker_models import (
    LockPlateRequest,
    ShakeRequest,
    StopShakingRequest,
    UnlockPlateRequest,
)


# --- Centrifuge --------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatterbox_centrifuge_satisfies_interface_and_lifecycle(capsys):
    driver = ChatterboxCentrifugeDriver()
    assert isinstance(driver, ICentrifugeDriver)
    assert driver.interfaces == frozenset({"ICentrifuge"})
    assert driver.is_initialized is False

    await driver.initialize()
    assert driver.is_initialized is True

    await driver.open()
    await driver.close()
    out = capsys.readouterr().out
    assert "Setting up" in out
    assert "Opening door" in out
    assert "Closing door" in out


@pytest.mark.asyncio
async def test_chatterbox_centrifuge_spin_routes_to_backend(capsys):
    driver = ChatterboxCentrifugeDriver()
    await driver.initialize()
    capsys.readouterr()  # discard setup output

    await driver.centrifuge(CentrifugeRequest(g=1500.0, duration=300.0))
    out = capsys.readouterr().out
    assert "spin" in out.lower()
    assert "1500" in out
    assert "300" in out


# --- Reader -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatterbox_reader_satisfies_interface_and_lifecycle(capsys):
    driver = ChatterboxReaderDriver()
    assert isinstance(driver, IReaderDriver)
    assert driver.interfaces == frozenset({"IReader"})

    await driver.initialize()
    assert driver.is_initialized is True

    await driver.open()
    await driver.close()
    out = capsys.readouterr().out
    assert "plate reader" in out.lower()


@pytest.mark.asyncio
async def test_chatterbox_reader_read_acknowledges_request(capsys):
    driver = ChatterboxReaderDriver()
    await driver.initialize()
    capsys.readouterr()

    await driver.read(ReadRequest(protocol_filepath="/p.protocol", output_filepath="/out.csv"))
    out = capsys.readouterr().out
    # The wrapper docstring documents that PLR's atomic-read API does not
    # align with cheshire's protocol-file shape, so read() prints an
    # acknowledgement rather than producing real measurements.
    assert "/p.protocol" in out
    assert "/out.csv" in out


# --- Storage ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatterbox_storage_satisfies_interface_and_lifecycle(capsys):
    driver = ChatterboxStorageDriver()
    assert isinstance(driver, IStorageDriver)
    assert driver.interfaces == frozenset({"IStorage"})

    await driver.initialize()
    assert driver.is_initialized is True

    await driver.open()
    await driver.close()
    out = capsys.readouterr().out
    assert "incubator" in out.lower()
    assert "Opening door" in out
    assert "Closing door" in out


# --- Heating Shaker -----------------------------------------------------------


@pytest.mark.asyncio
async def test_chatterbox_heating_shaker_advertises_all_three_interfaces(capsys):
    driver = ChatterboxHeatingShakerDriver()
    # Multi-interface conformance: the driver must satisfy IShaker AND
    # ITempSettable AND ITempGettable simultaneously (option (a) stacked
    # mixins from the plan).
    assert isinstance(driver, IShakerDriver)
    assert isinstance(driver, ITempSettableDriver)
    assert isinstance(driver, ITempGettableDriver)
    assert driver.interfaces == frozenset(
        {"IShaker", "ITempSettable", "ITempGettable"}
    )


@pytest.mark.asyncio
async def test_chatterbox_heating_shaker_shake_routes_to_backend(capsys):
    driver = ChatterboxHeatingShakerDriver()
    await driver.initialize()
    capsys.readouterr()

    # Lock plate, shake briefly, unlock. Duration is asyncio.sleep'd inside
    # PLRShakerBackendWrapper.shake; keep it tiny.
    await driver.lock_plate(LockPlateRequest())
    await driver.shake(ShakeRequest(speed=200.0, duration=0.01))
    await driver.unlock_plate(UnlockPlateRequest())
    await driver.stop_shaking(StopShakingRequest())


@pytest.mark.asyncio
async def test_chatterbox_heating_shaker_temperature_round_trip(capsys):
    driver = ChatterboxHeatingShakerDriver()
    await driver.initialize()
    capsys.readouterr()

    # Set then get; the temp controller chatterbox stashes the dummy temperature.
    await driver.set_temperature(37.5)
    measured = await driver.get_temperature()
    assert measured == pytest.approx(37.5)
