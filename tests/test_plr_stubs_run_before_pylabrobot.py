"""Every module that imports pylabrobot calls `ensure_plr_stubs()` first.

The fork's wheel omits `pylabrobot.gui`, `pylabrobot.testing` and
`pylabrobot.tests`, which its own serializer imports; see
`cheshire_drivers._plr_compat`. That used to be handled by the package
`__init__`, which ran on any import because it imported the world. It no
longer does, so each entry point into pylabrobot installs the stubs itself.

A module reached only through `cheshire_drivers/plr/__init__.py` is covered by
that file, since Python runs a package `__init__` before any submodule.
"""

from pathlib import Path

import pytest

from cheshire_source_text import code_lines, code_lines_of, imports_rooted_at, python_files

SRC_ROOT = Path(__file__).parent.parent / "src"

CALL = "ensure_plr_stubs()"


def _installs_stubs(lines: list) -> bool:
    return any(line.text.strip() == CALL for line in lines)


def _first_import_of(lines: list, root: str) -> int | None:
    found = imports_rooted_at(lines, frozenset({root}))
    return found[0].lineno if found else None


def _offenders(files: list[Path]) -> list[str]:
    bad: list[str] = []
    for path in files:
        if path.name == "_plr_compat.py":
            continue
        lines = code_lines_of(path)
        plr_line = _first_import_of(lines, "pylabrobot")
        if plr_line is None:
            continue
        if path.parent.name == "plr" and path.name != "__init__.py":
            continue
        if not _installs_stubs(lines):
            bad.append(f"{path.relative_to(SRC_ROOT)}: pylabrobot at line {plr_line}")
    return bad


def test_the_scan_sees_the_modules_that_import_pylabrobot() -> None:
    """Without this the case below passes on an empty file list."""
    importers = [
        path for path in python_files(SRC_ROOT)
        if _first_import_of(code_lines_of(path), "pylabrobot") is not None
    ]
    assert len(importers) >= 15, importers


def test_every_pylabrobot_entry_point_installs_the_stubs() -> None:
    assert _offenders(python_files(SRC_ROOT)) == []


MISSING_THE_CALL = """
from pylabrobot.resources.plate import Plate

def build() -> Plate:
    return Plate()
"""

HAS_THE_CALL = """
from cheshire_drivers._plr_compat import ensure_plr_stubs

ensure_plr_stubs()

from pylabrobot.resources.plate import Plate

def build() -> Plate:
    return Plate()
"""


@pytest.mark.parametrize(
    "source, caught",
    [(MISSING_THE_CALL, True), (HAS_THE_CALL, False)],
    ids=["positive control", "negative control"],
)
def test_the_scan_fires_on_the_shape_it_claims_to_catch(
    source: str, caught: bool, tmp_path: Path
) -> None:
    lines = code_lines(source)
    assert (_first_import_of(lines, "pylabrobot") is not None) is True
    assert _installs_stubs(lines) is not caught
