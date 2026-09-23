"""Guard: the installed pylabrobot provides what cheshire-drivers imports from the pinned fork.

An installed pylabrobot behind the pin breaks real usage in ways that surface as
cryptic ImportErrors deep in some import chain: a missing `pylabrobot.opentrons`
(the whole Flex adapter), a missing `pylabrobot.legacy.*` (the wrappers and arm
backends), or a labware catalog missing symbols the seed names. These fail early
instead, naming the pin from pyproject.toml.

The pin is read from pyproject.toml (the single source of truth), so bumping the
pylabrobot dependency needs no change here.
"""

import importlib
import re
from pathlib import Path

import pytest

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _pinned_pylabrobot_ref() -> str:
    text = _PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r"""pylabrobot[^"']*@([^"'@\s]+)["']""", text)
    assert match, "could not find the pinned pylabrobot ref in pyproject.toml"
    return match.group(1)


def _sync_hint(detail: str) -> str:
    pin = _pinned_pylabrobot_ref()
    return (
        f"{detail} The installed pylabrobot is out of sync with the pin in "
        f"pyproject.toml ({pin}). Reinstall the dependency, or point a local "
        f"checkout at the pinned ref: `git -C pylabrobot fetch --tags origin && "
        f"git -C pylabrobot checkout {pin}`."
    )


@pytest.mark.parametrize(
    "module_path",
    [
        "pylabrobot.legacy.arms.backend",
        "pylabrobot.legacy.liquid_handling.backends",
        "pylabrobot.opentrons",
        "pylabrobot.opentrons.flex_head",
        "pylabrobot.resources.opentrons.flex_deck",
    ],
)
def test_every_package_cheshire_drivers_imports_is_present(module_path: str) -> None:
    try:
        importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        pytest.fail(_sync_hint(f"{module_path} is missing ({exc})."))


@pytest.mark.parametrize(
    "module_path, symbol",
    [
        ("pylabrobot.opentrons", "OpentronsFlex"),
        ("pylabrobot.opentrons.flex_head", "FlexHead96"),
        ("pylabrobot.resources.opentrons.flex_deck", "FlexDeck"),
    ],
)
def test_the_flex_symbols_the_adapter_builds_on_are_present(module_path: str, symbol: str) -> None:
    """A tree can carry the package and still predate the classes the adapter drives."""
    module = importlib.import_module(module_path)
    if not hasattr(module, symbol):
        pytest.fail(_sync_hint(f"{module_path} exposes no {symbol}."))


def test_the_flex_deck_reports_its_slots_and_where_the_96_head_discards() -> None:
    """Both adapters read the deck through these: the wrapper detects an Opentrons slot deck
    structurally (a `slots` mapping), and a 96-head discard asks the deck for its trash. A tree
    carrying FlexDeck without them fails deep inside a placement instead of here."""
    from pylabrobot.resources.opentrons.flex_deck import FlexDeck

    deck = FlexDeck()
    for member in ("slots", "slot_locations", "get_slot_holder", "get_trash_area96"):
        if not hasattr(deck, member):
            pytest.fail(_sync_hint(f"FlexDeck exposes no {member}."))
    assert deck.slots["C2"] is None
    assert deck.get_trash_area96() is deck.get_trash_area()


def test_the_ot2_backend_can_move_a_channel_in_one_coordinated_move() -> None:
    """IPipetteMotion on the OT-2 rides these. Chaining move_channel_x/y/z instead would descend
    once per axis, so a tree without them is not something to fall back from."""
    from pylabrobot.legacy.liquid_handling.backends.opentrons_backend import OpentronsOT2Backend

    for member in ("move_channel_to", "get_channel_position"):
        if not hasattr(OpentronsOT2Backend, member):
            pytest.fail(_sync_hint(f"OpentronsOT2Backend exposes no {member}."))


def test_pylabrobot_labware_catalog_importable() -> None:
    """The seed names PLR factories, so a catalog missing one breaks labware creation."""
    try:
        importlib.import_module("cheshire_drivers.plr.plates")
    except ImportError as exc:
        pytest.fail(_sync_hint(f"pylabrobot labware catalog is incomplete ({exc})."))


def test_an_uploaded_tip_rack_well_is_as_deep_as_the_tip_it_seats() -> None:
    """A tree predating the cavity-floor fix uploads depth 0, which collapses the pickup
    target onto the rack base and drives the nozzle a tip length too low."""
    from pylabrobot.opentrons.labware_definitions import build_tip_rack_definition
    from pylabrobot.resources.opentrons import opentrons_96_filtertiprack_200ul

    rack = opentrons_96_filtertiprack_200ul(name="rack")
    well = build_tip_rack_definition(rack)["wells"]["A1"]
    if well["depth"] <= 0:
        pytest.fail(_sync_hint(f"an uploaded tip-rack well has depth {well['depth']}."))
