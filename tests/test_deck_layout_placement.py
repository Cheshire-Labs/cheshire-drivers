"""Carriers-only validation on DeckLayoutConfig.

A deck layout declares carriers (rail set) only. Labware occupancy
(parent_id + site_index) is NOT a layout concern: the engine derives it from
the ledger and projects it onto the driver deck via ReconcileDeckOccupancyRequest
/ add_deck_labware at runtime. Any labware-on-carrier-site entry in a layout is
rejected at config construction, so authors see the problem at topology-build
rather than as a divergence between the PLR deck and the engine ledger.

The mirror image lives on ReconcileDeckOccupancyRequest (test_lh_deck_reset.py),
which keeps carriers OUT of the occupancy wire format.
"""

import pytest
from pydantic import ValidationError

from cheshire_drivers.liquid_handler_models import (
    DeckLayoutConfig,
    DeckResourceConfig,
)


def _carrier(name: str, rail: int = 25) -> DeckResourceConfig:
    return DeckResourceConfig(name=name, catalog_ref="PLT_CAR_L5AC_A00", rail=rail)


def _labware(name: str, parent_id: str, site_index: int = 0) -> DeckResourceConfig:
    return DeckResourceConfig(
        name=name, catalog_ref="Cor_96_wellplate_360ul_Fb",
        parent_id=parent_id, site_index=site_index,
    )


def test_carriers_only_layout_passes() -> None:
    DeckLayoutConfig(
        deck_type="STARlet",
        resources=[
            _carrier("carrier-7", rail=7),
            _carrier("carrier-25", rail=25),
        ],
    )


def test_layout_with_labware_on_site_is_rejected() -> None:
    """The deprecation gate: labware-on-carrier-site in a layout is forbidden.

    Was previously tolerated (test_valid_layout_with_carrier_and_labware_passes,
    now flipped). The carriers-only model rejects it -- occupancy comes from the
    ledger via reconcile_deck_occupancy / add_deck_labware, never the layout.
    """
    with pytest.raises(ValidationError, match="declares labware on a carrier site"):
        DeckLayoutConfig(
            deck_type="STARlet",
            resources=[
                _carrier("carrier-25", rail=25),
                _labware("plate_1", parent_id="carrier-25", site_index=0),
            ],
        )


def test_resource_with_both_rail_and_parent_id_is_rejected() -> None:
    with pytest.raises(ValidationError, match="declares labware on a carrier site"):
        DeckLayoutConfig(
            deck_type="STARlet",
            resources=[
                DeckResourceConfig(
                    name="confused", catalog_ref="x",
                    rail=7, parent_id="other", site_index=0,
                ),
            ],
        )


def test_resource_with_parent_id_but_no_site_index_is_rejected() -> None:
    with pytest.raises(ValidationError, match="declares labware on a carrier site"):
        DeckLayoutConfig(
            deck_type="STARlet",
            resources=[
                _carrier("carrier-25"),
                DeckResourceConfig(
                    name="floating", catalog_ref="x", parent_id="carrier-25",
                ),
            ],
        )


def test_resource_with_site_index_but_no_parent_id_is_rejected() -> None:
    with pytest.raises(ValidationError, match="declares labware on a carrier site"):
        DeckLayoutConfig(
            deck_type="STARlet",
            resources=[
                DeckResourceConfig(name="floating", catalog_ref="x", site_index=0),
            ],
        )


def test_resource_with_neither_rail_nor_parent_is_rejected() -> None:
    with pytest.raises(ValidationError, match="has no rail"):
        DeckLayoutConfig(
            deck_type="STARlet",
            resources=[
                DeckResourceConfig(name="floating", catalog_ref="x"),
            ],
        )


def test_duplicate_resource_names_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate deck resource name"):
        DeckLayoutConfig(
            deck_type="STARlet",
            resources=[
                _carrier("carrier-25", rail=25),
                _carrier("carrier-25", rail=15),
            ],
        )
