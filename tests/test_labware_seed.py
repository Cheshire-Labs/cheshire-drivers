"""Tests for the bundled PLR-derived labware seed catalog.

These tests run against the committed `labware_seed.json` artifact, so they
serve double duty: (a) they pin the public shape of `load_labware_seed()`
and `LabwareSeedEntry`, (b) they catch regressions in the regen script
output by checking known-class presence and protocol satisfiability.
"""

import warnings

import pytest
from pylabrobot.resources.plate import Plate

from cheshire_drivers.labware_seed import (
    CarrierSeedEntry,
    PlateSeedEntry,
    TipRackSeedEntry,
    TroughSeedEntry,
    TubeSeedEntry,
    get_carrier_site_identifiers,
    load_labware_seed,
)
from cheshire_drivers.plr.labware_converter import (
    PLRLabwareConverter,
    _HasPlateGeometry,
    _HasWellGeometry,
)


# Stable in-PLR factory names we expect to ship in every seed regeneration.
# If a future PLR drop renames or removes one of these, the regen script
# should grow an explicit migration entry; silent disappearance is the bug
# this guard catches.
_KNOWN_PLATES: frozenset[str] = frozenset({
    "Cor_96_wellplate_360ul_Fb",
    "Cor_96_wellplate_2mL_Vb",
})
_KNOWN_TIP_RACKS: frozenset[str] = frozenset({
    "hamilton_96_tiprack_1000uL",
    "hamilton_96_tiprack_10uL",
})


@pytest.fixture(scope="module")
def seed_entries() -> list:
    """Load once per module; 295 entries × Pydantic validation isn't cheap."""
    return load_labware_seed()


def test_seed_json_loads(seed_entries: list) -> None:
    """`load_labware_seed()` returns a non-empty list of validated entries."""
    assert isinstance(seed_entries, list)
    assert len(seed_entries) >= 100  # baseline; current seed has 295
    valid_kinds = (PlateSeedEntry, TipRackSeedEntry, TroughSeedEntry, TubeSeedEntry, CarrierSeedEntry)
    for entry in seed_entries:
        assert isinstance(entry, valid_kinds), (
            f"unexpected entry type {type(entry).__name__} for labware_type {getattr(entry, 'labware_type', '<?>')}"
        )


def test_seed_has_known_classes(seed_entries: list) -> None:
    """Stable PLR factory names must survive every regen."""
    plate_labware_types = {e.labware_type for e in seed_entries if isinstance(e, PlateSeedEntry)}
    tip_labware_types = {e.labware_type for e in seed_entries if isinstance(e, TipRackSeedEntry)}

    missing_plates = _KNOWN_PLATES - plate_labware_types
    assert not missing_plates, (
        f"plate labware_types missing from seed: {sorted(missing_plates)}. "
        f"Either the PLR factory was renamed/removed (update _KNOWN_PLATES with a comment) "
        f"or the regen script regressed."
    )
    missing_racks = _KNOWN_TIP_RACKS - tip_labware_types
    assert not missing_racks, (
        f"tip rack labware_types missing from seed: {sorted(missing_racks)}"
    )


def test_seed_names_resolve_to_current_plr_factories(seed_entries: list) -> None:
    """Every seed labware_type must name a factory that still exists in the
    pinned PLR (or the cheshire_drivers.plr.plates catalog).

    A PLR bump that renames or drops a factory without a matching seed regen
    leaves a dangling entry that fails to resolve at deployment boot. This is
    the guard for that class of drift; signature-only enumeration (no
    instantiation) keeps it cheap.
    """
    from cheshire_drivers.scripts.regen_labware_seed import (
        _enumerate_cheshire_drivers_factories,
        _enumerate_factories,
    )

    available = {name for name, _ in _enumerate_factories()}
    available |= {name for name, _ in _enumerate_cheshire_drivers_factories()}
    seed_types = {e.labware_type for e in seed_entries}
    dangling = seed_types - available
    assert not dangling, (
        f"seed names factories absent from the pinned PLR: {sorted(dangling)}. "
        f"Regenerate: python -m cheshire_drivers.scripts.regen_labware_seed"
    )


def test_seed_plate_satisfies_iplate_protocol(seed_entries: list) -> None:
    """Every plate entry must satisfy `_HasPlateGeometry` + per-well `_HasWellGeometry`.

    These protocols define the contract `PLRLabwareConverter.to_plr_plate()`
    consumes. Drift here means orca-client's custom-labware reconstruction
    will fail at runtime for labware sourced from this seed.
    """
    plates = [e for e in seed_entries if isinstance(e, PlateSeedEntry)]
    assert plates, "seed has no plate entries"

    for plate in plates:
        assert isinstance(plate, _HasPlateGeometry), (
            f"plate {plate.labware_type!r} does not satisfy _HasPlateGeometry"
        )
        well = plate.well("A1")
        assert isinstance(well, _HasWellGeometry), (
            f"plate {plate.labware_type!r} well A1 does not satisfy _HasWellGeometry"
        )


def test_seed_plate_geometry_round_trips_to_plr(seed_entries: list) -> None:
    """For every plate entry, reconstructing via PLRLabwareConverter produces
    a PLR Plate whose top-level dimensions match the seed (and the original
    PLR factory). This is the geometry-construction correctness gate for
    custom labware.
    """
    converter = PLRLabwareConverter()
    plates = [e for e in seed_entries if isinstance(e, PlateSeedEntry)]

    for plate in plates:
        plr_plate = converter.to_plr_plate(plate)
        assert isinstance(plr_plate, Plate)
        assert plr_plate.get_size_x() == pytest.approx(plate.size_x), plate.labware_type
        assert plr_plate.get_size_y() == pytest.approx(plate.size_y), plate.labware_type
        assert plr_plate.get_size_z() == pytest.approx(plate.size_z), plate.labware_type
        assert plr_plate.num_items_x == plate.num_cols, plate.labware_type
        assert plr_plate.num_items_y == plate.num_rows, plate.labware_type


def test_seed_categories_and_counts(seed_entries: list) -> None:
    """Sanity-check the category mix. Current seed: 99 plate / 88 tip_rack /
    8 trough / 13 tube / 87 carrier. Drift triggers regen review.
    """
    by_cat: dict[str, int] = {}
    for e in seed_entries:
        by_cat[e.category] = by_cat.get(e.category, 0) + 1

    assert by_cat.get("plate", 0) >= 50, f"plate count regressed: {by_cat}"
    assert by_cat.get("tip_rack", 0) >= 80, f"tip_rack count regressed: {by_cat}"
    assert by_cat.get("trough", 0) >= 1, f"trough count regressed: {by_cat}"
    assert by_cat.get("tube", 0) >= 1, f"tube count regressed: {by_cat}"
    assert by_cat.get("carrier", 0) >= 50, f"carrier count regressed: {by_cat}"


def test_seed_labware_types_unique(seed_entries: list) -> None:
    """Every entry has a unique labware_type across categories."""
    labware_types = [e.labware_type for e in seed_entries]
    assert len(labware_types) == len(set(labware_types)), (
        f"duplicate labware_types in seed: {len(labware_types) - len(set(labware_types))} duplicates"
    )


def test_plate_seed_entry_typed_as_has_plate_geometry(seed_entries: list) -> None:
    """A function annotated `_HasPlateGeometry` accepts `PlateSeedEntry` and
    reads `.name` without a type-checker complaint.

    Closes a review gap: `_HasPlateGeometry.name` switched from a
    mutable attribute to a `@property`, and the prior coverage relied on
    runtime `isinstance` only. This test pins the static contract: pyright
    on this file must accept passing a PlateSeedEntry where the protocol
    is expected. Compile-time errors would surface as a type-checker
    failure on the call below, not as a runtime assertion miss.
    """

    def _read_name(p: _HasPlateGeometry) -> str:
        return p.name

    plate = next(e for e in seed_entries if isinstance(e, PlateSeedEntry))
    assert _read_name(plate) == plate.labware_type


def test_get_carrier_site_identifiers_returns_known_plate_carrier_sites() -> None:
    sites = get_carrier_site_identifiers("PLT_CAR_L5AC_A00")
    assert sites == ["0", "1", "2", "3", "4"]


def test_get_carrier_site_identifiers_raises_for_unknown_catalog_ref() -> None:
    with pytest.raises(KeyError, match="Unknown carrier catalog_ref"):
        get_carrier_site_identifiers("does_not_exist_carrier")


def test_get_carrier_site_identifiers_rejects_non_carrier_catalog_ref() -> None:
    plate_ref = "Cor_96_wellplate_360ul_Fb"
    with pytest.raises(KeyError, match="Unknown carrier catalog_ref"):
        get_carrier_site_identifiers(plate_ref)
