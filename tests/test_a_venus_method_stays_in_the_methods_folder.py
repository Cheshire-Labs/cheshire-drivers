"""A method name sent over the wire cannot reach outside the methods folder.

The name becomes argv to HxRun.exe, so an absolute path or a `..` hop would
otherwise run any Venus method file on the machine.
"""
import pathlib

import pytest

from cheshire_drivers.venus_driver import VenusProtocolDriver


def _driver(methods_folder: pathlib.Path) -> VenusProtocolDriver:
    return VenusProtocolDriver("ml_star", methods_folder=str(methods_folder))


def test_a_method_in_a_subfolder_resolves(tmp_path: pathlib.Path) -> None:
    (tmp_path / "Cheshire Labs").mkdir()
    method = tmp_path / "Cheshire Labs" / "SimplePlateStamp.hsl"
    method.write_text("", encoding="utf-8")

    resolved = _driver(tmp_path)._method_inside_the_methods_folder(
        str(pathlib.Path("Cheshire Labs") / "SimplePlateStamp.hsl")
    )

    assert pathlib.Path(resolved) == method.resolve()


def test_an_absolute_path_outside_the_folder_is_refused(tmp_path: pathlib.Path) -> None:
    outside = tmp_path.parent / "elsewhere.hsl"
    outside.write_text("", encoding="utf-8")
    methods = tmp_path / "methods"
    methods.mkdir()

    with pytest.raises(ValueError, match="resolves outside the methods folder"):
        _driver(methods)._method_inside_the_methods_folder(str(outside))


def test_a_parent_hop_is_refused(tmp_path: pathlib.Path) -> None:
    methods = tmp_path / "methods"
    methods.mkdir()

    with pytest.raises(ValueError, match="resolves outside the methods folder"):
        _driver(methods)._method_inside_the_methods_folder("../elsewhere.hsl")


@pytest.mark.asyncio
async def test_run_protocol_refuses_before_it_writes_or_spawns(
    tmp_path: pathlib.Path,
) -> None:
    """The refusal lands before the params file is written, so nothing runs."""
    methods = tmp_path / "methods"
    methods.mkdir()
    driver = _driver(methods)
    params_file = pathlib.Path(driver._params_filepath)
    params_file.unlink(missing_ok=True)

    with pytest.raises(ValueError, match="resolves outside the methods folder"):
        await driver.execute(
            "run_protocol", {"method": str(tmp_path / "elsewhere.hsl"), "params": {}}
        )

    assert not params_file.exists()
