"""Dynamic PyLabRobot backend lookup by name.

Backend dispatch lives here so consumers (the orca-client device factory) do not
write their own ``__import__('pylabrobot.<sub>')`` calls, which keeps PLR a
transitive dependency of cheshire-drivers only.

The lookup walks PLR's backend categories and returns the first class matching
the name. A category PLR has moved or dropped is skipped rather than fatal: one
missing package must not take down the lookup for every other backend name.
"""

_BACKEND_MODULE_PATHS: tuple[str, ...] = (
    "pylabrobot.shaking",
    "pylabrobot.centrifuge",
    "pylabrobot.sealing",
    "pylabrobot.brooks.precise_flex",
    "pylabrobot.legacy.temperature_controlling.inheco",
    "pylabrobot.legacy.liquid_handling.backends",
)


def get_plr_backend_class(backend_name: str) -> type:
    """Look up a PyLabRobot backend class by name across all backend categories.

    Args:
        backend_name: Class name of the backend (e.g. ``"InhecoThermoShake"``).

    Returns:
        The backend class object.

    Raises:
        ImportError: If no importable PLR backend category exposes a class named
            ``backend_name``.
    """
    for module_path in _BACKEND_MODULE_PATHS:
        try:
            module = __import__(module_path, fromlist=[backend_name])
        except ModuleNotFoundError:
            continue
        if hasattr(module, backend_name):
            return getattr(module, backend_name)
    raise ImportError(
        f"PyLabRobot backend '{backend_name}' not found in any category "
        f"(searched: {', '.join(_BACKEND_MODULE_PATHS)})"
    )
