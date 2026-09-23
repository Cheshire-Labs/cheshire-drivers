"""Stub the pylabrobot submodules the fork's wheel does not package.

Every module that imports pylabrobot calls `ensure_plr_stubs()` first. This
used to run from the package `__init__`, which only worked because that file
imported the world; it no longer does.

The fork's `pylabrobot/serializer.py` imports `pylabrobot.gui`,
`pylabrobot.testing` and `pylabrobot.tests` unconditionally to look up classes
by name during deserialize, but the published wheel does not package those
submodules (`find_packages()` misses directories without `__init__.py`; in the
fork, `gui/__init__.py` is untracked and `testing/` and `tests/` have no
`__init__.py`). Any code path that triggers `Resource.copy()`, notably PLR's
`drop_resource` calling `rotated()` during deck-internal moves, raises
ModuleNotFoundError.

Stubbing them with empty modules works because PLR's serializer walks them via
`inspect.getmembers(...)`, finds no classes, and falls through to the real
submodules. Remove when the fork ships the missing pieces.
"""

import sys
import types

_MISSING = ("pylabrobot.gui", "pylabrobot.testing", "pylabrobot.tests")


def ensure_plr_stubs() -> None:
    """Register an empty module for each submodule the wheel left out."""
    for missing in _MISSING:
        if missing in sys.modules:
            continue
        try:
            __import__(missing)
        except ModuleNotFoundError:
            sys.modules[missing] = types.ModuleType(missing)
