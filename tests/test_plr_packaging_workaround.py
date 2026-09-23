"""Regression guard for the PLR fork packaging workaround.

Background: the pylabrobot fork's `pylabrobot/serializer.py` imports
`pylabrobot.gui`, `pylabrobot.testing`, and `pylabrobot.tests`
unconditionally during deserialize. Upstream PLR's `find_packages()`
silently drops directories that lack `__init__.py`, so those submodules
are not packaged in the published wheel. Any code path that triggers
`Resource.copy()` (notably PLR's `drop_resource -> rotated()` during
deck-internal moves) raises `ModuleNotFoundError`.

The fix is `cheshire_drivers._plr_compat.ensure_plr_stubs`, which seeds empty
modules into `sys.modules` BEFORE PLR's serializer can fail. Every driver
module that imports pylabrobot calls it first. These tests call it the same
way, so they do not depend on which test module happened to import a driver
first.
"""

import importlib
import sys

import pytest

from cheshire_drivers._plr_compat import ensure_plr_stubs


_STUBBED_MODULES = ("pylabrobot.gui", "pylabrobot.testing", "pylabrobot.tests")


@pytest.fixture(autouse=True)
def _stubs_installed() -> None:
    ensure_plr_stubs()


def test_stubbed_plr_submodules_are_resolvable() -> None:
    """After `ensure_plr_stubs()`, the three submodules PLR's serializer
    requires must resolve via sys.modules.

    The workaround accepts either outcome: an implicit namespace package
    (when the directory exists in the fork tree without __init__.py) or
    an explicit empty stub. Either way, `import pylabrobot.gui` must
    succeed for PLR's serializer to walk it via `inspect.getmembers`.
    """
    for name in _STUBBED_MODULES:
        assert name in sys.modules, (
            f"{name} missing from sys.modules. ensure_plr_stubs must seed "
            "empty stubs (or accept implicit namespace packages) for these "
            "PLR submodules."
        )


def test_pylabrobot_serializer_imports_without_error() -> None:
    """The actual failure mode: PLR's `serializer.py:22` does
    `import pylabrobot.gui as gui_module` at module load. If the
    bootstrap is broken, importing the serializer raises
    ModuleNotFoundError. This test pins that path."""

    # Force a fresh re-import to prove the bootstrap survives module reload,
    # not just the first import.
    sys.modules.pop("pylabrobot.serializer", None)
    importlib.import_module("pylabrobot.serializer")


def test_plr_resource_copy_does_not_raise_module_not_found() -> None:
    """End-to-end: the originally-reported breakage is `Resource.copy()`
    -> `Resource.rotated()` exercising serializer's class lookup. Run it
    once to prove the workaround covers the real call path, not just
    the import-time symptom."""
    from pylabrobot.resources import Resource

    res = Resource(name="t", size_x=1.0, size_y=1.0, size_z=1.0)
    copy = res.copy()
    assert copy.name == "t"
