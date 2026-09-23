"""Backend-name lookup against the installed PyLabRobot.

The orca-client device factory resolves a config's ``backend`` string through
this, so a PLR re-org that moves a category shows up here rather than at
device-build time on a customer box.
"""

import pytest

from cheshire_drivers.plr import backends
from cheshire_drivers.plr.backends import get_plr_backend_class


@pytest.mark.parametrize(
    "backend_name",
    [
        "LiquidHandlerChatterboxBackend",
        "STAR",
        "STARBackend",
        "PreciseFlex",
        "ShakerChatterboxBackend",
        "A4SBackend",
        "VSpinBackend",
        "InhecoThermoShake",
    ],
)
def test_every_shipped_backend_name_resolves(backend_name: str) -> None:
    """These names appear in shipped example configs and the lab-sim harness."""
    assert isinstance(get_plr_backend_class(backend_name), type)


def test_a_category_plr_no_longer_ships_does_not_break_the_other_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The walk imports each category in turn, so a missing one used to raise before
    the loop ever reached the categories that do exist."""
    monkeypatch.setattr(
        backends,
        "_BACKEND_MODULE_PATHS",
        ("pylabrobot.no_such_category", "pylabrobot.liquid_handling.backends"),
    )

    assert isinstance(get_plr_backend_class("LiquidHandlerChatterboxBackend"), type)


def test_an_unknown_backend_name_names_the_categories_searched() -> None:
    with pytest.raises(ImportError, match="NotARealBackend"):
        get_plr_backend_class("NotARealBackend")
