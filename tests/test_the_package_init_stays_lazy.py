"""`import cheshire_drivers.<wire model>` must not load pylabrobot.

The package `__init__` used to import every driver eagerly, so pulling in one
Pydantic model loaded the PLR wrappers, pylabrobot and matplotlib. A consumer
that only speaks the wire (the orca CLI) paid 1.5 seconds per invocation for
types it never used.

`__init__.py` serves names through `__getattr__`; `__init__.pyi` carries the
static types. The two have to agree, or `from cheshire_drivers import X`
silently becomes `Any` everywhere in the org.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

import cheshire_drivers

PKG = Path(cheshire_drivers.__file__).parent

# Modules a wire-only consumer imports. None of them needs a driver.
WIRE_MODULES = [
    "cheshire_drivers.gateway_protocol",
    "cheshire_drivers.liquid_handler_models",
    "cheshire_drivers.move_parameters",
    "cheshire_drivers.teachpoints",
    "cheshire_drivers.driver_errors",
]


def _modules_after_importing(module: str) -> set[str]:
    """Top-level packages loaded by a fresh interpreter importing `module`."""
    probe = (
        f"import sys; import {module}; "
        "print(','.join(sorted({m.split('.')[0] for m in sys.modules})))"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    return set(done.stdout.strip().split(","))


@pytest.mark.parametrize("module", WIRE_MODULES)
def test_a_wire_module_does_not_drag_in_pylabrobot(module: str) -> None:
    loaded = _modules_after_importing(module)
    assert "pylabrobot" not in loaded, module
    assert "matplotlib" not in loaded, module


def test_the_package_itself_imports_nothing_heavy() -> None:
    loaded = _modules_after_importing("cheshire_drivers")
    assert "pylabrobot" not in loaded
    assert "matplotlib" not in loaded


def test_every_exported_name_still_resolves() -> None:
    """__all__ is the promise; __getattr__ has to keep it for every name."""
    unresolvable = [
        name for name in cheshire_drivers.__all__
        if not hasattr(cheshire_drivers, name)
    ]
    assert unresolvable == []


def test_a_name_nobody_exports_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="NoSuchDriver"):
        getattr(cheshire_drivers, "NoSuchDriver")


def _stub_names() -> set[str]:
    text = (PKG / "__init__.pyi").read_text(encoding="utf-8")
    return set(re.findall(r"^from [\w.]+ import (\w+) as \1$", text, re.MULTILINE))


def test_the_type_stub_covers_exactly_what_getattr_serves() -> None:
    """A name in one and not the other is either untyped or a dead import."""
    served = set(cheshire_drivers._EXPORTS)
    assert _stub_names() == served


def test_the_stub_points_each_name_at_the_module_that_serves_it() -> None:
    text = (PKG / "__init__.pyi").read_text(encoding="utf-8")
    stubbed = dict(
        (name, module)
        for module, name in re.findall(
            r"^from ([\w.]+) import (\w+) as \2$", text, re.MULTILINE
        )
    )
    disagreements = {
        name: (module, cheshire_drivers._EXPORTS[name])
        for name, module in stubbed.items()
        if cheshire_drivers._EXPORTS[name] != module
    }
    assert disagreements == {}
