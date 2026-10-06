"""What the Venus driver hands HxRun.exe, and what it does with HxRun's answer.

A fake HxRun stands in for the real one. It records the method it was told to
run and the params file the Orca submethod library reads at that moment, then
exits with the code the test chose.
"""
import os
import pathlib
import stat
import sys
import tempfile

import pytest
from pydantic import BaseModel, JsonValue

from cheshire_drivers.driver_errors import DriverError
from cheshire_drivers.protocol_runner_models import LabwareHandoffRequest, RunProtocolRequest
from cheshire_drivers.protocol_runner_request_validation import wrap_protocol_runner_payload
from cheshire_drivers.venus_driver import SimulationVenusProtocolDriver, VenusProtocolDriver

_FAKE_HXRUN = """
import json, os, pathlib, sys
params = pathlib.Path(os.environ["FAKE_HXRUN_PARAMS"])
record = {"argv": sys.argv[1:], "params": json.loads(params.read_text())}
with open(os.environ["FAKE_HXRUN_LOG"], "a") as log:
    log.write(json.dumps(record) + "\\n")
sys.stderr.write("method aborted by user")
sys.exit(int(os.environ.get("FAKE_HXRUN_EXIT", "0")))
"""


class _HxRunCall(BaseModel):
    argv: list[str]
    params: dict[str, JsonValue]


class _Bench:
    def __init__(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.methods = tmp_path / "Methods"
        self.methods.mkdir()
        # The Orca library reads %TEMP%\CheshireLabs\Orca\actionConfig.json.
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "Temp"))
        (tmp_path / "Temp").mkdir()
        params_file = tmp_path / "Temp" / "CheshireLabs" / "Orca" / "actionConfig.json"
        self.log = tmp_path / "hxrun.log"
        monkeypatch.setenv("FAKE_HXRUN_PARAMS", str(params_file))
        monkeypatch.setenv("FAKE_HXRUN_LOG", str(self.log))
        self.exe = self._write_fake_hxrun(tmp_path)

    def _write_fake_hxrun(self, tmp_path: pathlib.Path) -> pathlib.Path:
        if os.name == "nt":
            script = tmp_path / "fake_hxrun.py"
            script.write_text(_FAKE_HXRUN, encoding="utf-8")
            exe = tmp_path / "HxRun.cmd"
            exe.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
        else:
            exe = tmp_path / "HxRun"
            exe.write_text(f"#!{sys.executable}\n{_FAKE_HXRUN}", encoding="utf-8")
            exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        return exe

    def method(self, name: str) -> str:
        (self.methods / name).write_text("", encoding="utf-8")
        return name

    def argv_for(self, name: str) -> list[str]:
        return ["-t", str((self.methods / name).resolve())]

    def driver(
        self,
        init_protocol: str | None = None,
        picked_protocol: str | None = None,
        placed_protocol: str | None = None,
        prepare_pick_protocol: str | None = None,
        prepare_place_protocol: str | None = None,
        open_protocol: str | None = None,
        close_protocol: str | None = None,
    ) -> VenusProtocolDriver:
        return VenusProtocolDriver(
            "ml_star",
            init_protocol=init_protocol,
            picked_protocol=picked_protocol,
            placed_protocol=placed_protocol,
            prepare_pick_protocol=prepare_pick_protocol,
            prepare_place_protocol=prepare_place_protocol,
            open_protocol=open_protocol,
            close_protocol=close_protocol,
            exe_path=str(self.exe),
            methods_folder=str(self.methods),
        )

    def runs(self) -> list[_HxRunCall]:
        if not self.log.exists():
            return []
        return [_HxRunCall.model_validate_json(line) for line in self.log.read_text().splitlines()]


@pytest.fixture
def bench(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> _Bench:
    return _Bench(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_run_protocol_puts_the_values_under_params_for_the_orca_library(bench: _Bench) -> None:
    driver = bench.driver()
    await driver.initialize()

    await driver.run_protocol(RunProtocolRequest(
        protocol_filepath=bench.method("Stamp.hsl"), params={"waterVol": 30, "dye": "blue"},
    ))

    [run] = bench.runs()
    assert run.argv == bench.argv_for("Stamp.hsl")
    assert run.params == {"params": {"waterVol": 30, "dye": "blue", "action": "run"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["prepare_for_place", "notify_placed", "prepare_for_pick", "notify_picked"])
async def test_a_pick_or_place_hook_runs_its_method_with_the_labware(bench: _Bench, action: str) -> None:
    hook_method = bench.method("Hook.hsl")
    driver = bench.driver(
        prepare_place_protocol=hook_method if action == "prepare_for_place" else None,
        placed_protocol=hook_method if action == "notify_placed" else None,
        prepare_pick_protocol=hook_method if action == "prepare_for_pick" else None,
        picked_protocol=hook_method if action == "notify_picked" else None,
    )
    await driver.initialize()

    await getattr(driver, action)(LabwareHandoffRequest(
        labware_name="plate_1", labware_type="Cos_96_Rd", site="sample_site", barcode="BC-0042",
    ))

    [run] = bench.runs()
    assert run.argv == bench.argv_for("Hook.hsl")
    assert run.params == {"params": {
        "action": action,
        "labware_name": "plate_1",
        "labware_type": "Cos_96_Rd",
        "site": "sample_site",
        "barcode": "BC-0042",
    }}


@pytest.mark.asyncio
async def test_a_hook_with_no_method_configured_runs_nothing(bench: _Bench) -> None:
    driver = bench.driver()
    await driver.initialize()

    await driver.notify_placed(LabwareHandoffRequest(labware_name="plate_1", labware_type="Cos_96_Rd"))

    assert bench.runs() == []


@pytest.mark.asyncio
async def test_a_hook_without_a_site_or_barcode_sends_empty_strings(bench: _Bench) -> None:
    """The Orca library reads every value with GetConfigProperty_String, which cannot take null."""
    driver = bench.driver(placed_protocol=bench.method("Placed.hsl"))
    await driver.initialize()

    await driver.notify_placed(LabwareHandoffRequest(labware_name="plate_1", labware_type="Cos_96_Rd"))

    [run] = bench.runs()
    assert run.params["params"] == {
        "action": "notify_placed", "labware_name": "plate_1", "labware_type": "Cos_96_Rd",
        "site": "", "barcode": "",
    }


@pytest.mark.parametrize("action", ["prepare_for_place", "notify_placed", "prepare_for_pick", "notify_picked"])
def test_a_hook_arriving_over_the_wire_becomes_a_handoff_request(action: str) -> None:
    wrapped = wrap_protocol_runner_payload(
        action, {"labware_name": "plate_1", "labware_type": "Cos_96_Rd", "site": "sample_site"},
    )

    assert wrapped == {"request": LabwareHandoffRequest(
        labware_name="plate_1", labware_type="Cos_96_Rd", site="sample_site",
    )}


@pytest.mark.asyncio
async def test_initialize_runs_the_init_method(bench: _Bench) -> None:
    driver = bench.driver(init_protocol=bench.method("Init.hsl"))

    await driver.initialize()

    [run] = bench.runs()
    assert run.argv == bench.argv_for("Init.hsl")
    assert run.params == {"params": {"action": "initialize"}}
    assert driver.is_initialized


@pytest.mark.asyncio
async def test_open_and_close_run_their_methods(bench: _Bench) -> None:
    driver = bench.driver(open_protocol=bench.method("Open.hsl"), close_protocol=bench.method("Close.hsl"))
    await driver.initialize()

    await driver.open()
    await driver.close()

    assert [(run.argv, run.params) for run in bench.runs()] == [
        (bench.argv_for("Open.hsl"), {"params": {"action": "open"}}),
        (bench.argv_for("Close.hsl"), {"params": {"action": "close"}}),
    ]


@pytest.mark.asyncio
async def test_a_venus_method_that_fails_raises(bench: _Bench, monkeypatch: pytest.MonkeyPatch) -> None:
    driver = bench.driver()
    await driver.initialize()
    monkeypatch.setenv("FAKE_HXRUN_EXIT", "3")

    with pytest.raises(DriverError, match="exit code 3.*method aborted by user"):
        await driver.run_protocol(RunProtocolRequest(protocol_filepath=bench.method("Stamp.hsl")))

    assert not driver.is_running


@pytest.mark.asyncio
async def test_a_method_missing_from_the_methods_folder_is_refused_before_hxrun_runs(
    bench: _Bench,
) -> None:
    driver = bench.driver()
    await driver.initialize()

    with pytest.raises(FileNotFoundError, match="Missing.hsl"):
        await driver.run_protocol(RunProtocolRequest(protocol_filepath="Missing.hsl"))

    assert bench.runs() == []


def test_a_fresh_driver_is_not_connected() -> None:
    assert VenusProtocolDriver("ml_star").is_connected is False
    assert SimulationVenusProtocolDriver("ml_star").is_connected is False
