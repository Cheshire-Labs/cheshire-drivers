from collections.abc import Iterator, Sequence
from typing import Protocol

from cheshire_drivers.device_sites import DEFAULT_SITE
from cheshire_drivers.labware_seed import get_carrier_site_identifiers
from cheshire_drivers.liquid_handler_models import DeckLayoutConfig, DeckResourceConfig
from cheshire_drivers.plr_wrappers import slot_names_for_deck_type


class DeckSiteProvider(Protocol):
    """A placement unit on a liquid-handler deck that exposes its deck-sites."""

    @property
    def name(self) -> str: ...

    def site_identifiers(self) -> Sequence[str]: ...


class RailCarrierUnit:
    """A carrier mounted on a deck rail, with its sites read from the carrier catalog."""

    def __init__(self, config: DeckResourceConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return self._config.name

    def site_identifiers(self) -> Sequence[str]:
        return get_carrier_site_identifiers(self._config.catalog_ref)


class DeckSlotUnit:
    """A built-in slot of a slot deck, with exactly one site."""

    def __init__(self, slot: str) -> None:
        self._slot = slot

    @property
    def name(self) -> str:
        return self._slot

    def site_identifiers(self) -> Sequence[str]:
        return (DEFAULT_SITE,)


def deck_site_units(deck_config: DeckLayoutConfig) -> Iterator[DeckSiteProvider]:
    """The placement units of a layout: its rail carriers, else the built-in slots of its deck type."""
    carriers = [r for r in deck_config.resources if r.rail is not None]
    if carriers:
        for carrier in carriers:
            yield RailCarrierUnit(carrier)
        return
    slot_names = slot_names_for_deck_type(deck_config.deck_type)
    if slot_names is not None:
        for slot in slot_names:
            yield DeckSlotUnit(slot)
