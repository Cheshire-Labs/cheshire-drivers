"""The operator surface `describe_driver` reports for every concrete driver class in the package."""

import hashlib
import importlib
import inspect
import json
import pkgutil
import re
import sys
from pathlib import Path

from pydantic import JsonValue

import cheshire_drivers
from cheshire_drivers import interfaces
from cheshire_drivers.driver_introspection import describe_driver
from cheshire_drivers.null_plate_pad import NullPlatePadDriver

SNAPSHOT = Path(__file__).parent / "snapshots" / "describe_driver_digests.json"


def import_every_module() -> None:
    for module in pkgutil.walk_packages(cheshire_drivers.__path__, prefix="cheshire_drivers."):
        # The mesh generator needs the `models` extra, which the test environment does not install.
        if ".scripts" in module.name or module.name.endswith(".build_meshes"):
            continue
        importlib.import_module(module.name)


def _subclasses(root: type) -> set[type]:
    found: set[type] = set()
    pending = [root]
    while pending:
        for child in pending.pop().__subclasses__():
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def driver_interfaces() -> list[type]:
    return [
        value for name, value in vars(interfaces).items()
        if inspect.isclass(value) and name.startswith("I") and name.endswith("Driver")
        and value.__module__ == interfaces.__name__
    ]


def package_driver_classes() -> list[type]:
    """Every class under `cheshire_drivers`, outside the interfaces module, that subclasses a driver interface."""
    import_every_module()
    classes: set[type] = {NullPlatePadDriver}
    for interface in driver_interfaces():
        classes |= _subclasses(interface)
    return sorted(
        (
            cls for cls in classes
            if cls.__module__.startswith("cheshire_drivers.") and cls.__module__ != interfaces.__name__
        ),
        key=lambda cls: f"{cls.__module__}.{cls.__qualname__}",
    )


# A vendor default of `datetime.now()` is evaluated at import, so its value differs on every run.
_IMPORT_TIME = re.compile(r"datetime\.datetime\([^)]*\)")
# Python 3.10 renders a return type such as `list[int]` as `list`; keep only the outer type so a digest is the
# same on every supported version.
_TYPE_ARGUMENTS = re.compile(r'("returns": "[^"\[]*)\[[^"]*"')


def _rendered(cls: type) -> str:
    described = describe_driver(object.__new__(cls))
    surface: dict[str, JsonValue] = {name: info.model_dump(mode="json") for name, info in described.items()}
    rendered = _IMPORT_TIME.sub("datetime.datetime(<import time>)", json.dumps(surface, sort_keys=True))
    return _TYPE_ARGUMENTS.sub(r'\1"', rendered)


def surface_digests() -> dict[str, str]:
    """sha256 of each concrete driver class's `describe_driver` output, by dotted class name."""
    return {
        f"{cls.__module__}.{cls.__qualname__}": hashlib.sha256(_rendered(cls).encode()).hexdigest()
        for cls in package_driver_classes()
        if not inspect.isabstract(cls)
    }


if __name__ == "__main__":
    SNAPSHOT.parent.mkdir(exist_ok=True)
    SNAPSHOT.write_text(json.dumps(surface_digests(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {SNAPSHOT}\n")
