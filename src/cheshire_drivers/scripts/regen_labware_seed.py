"""Regenerate cheshire_drivers/labware_seed.json from PyLabRobot.

Walks pylabrobot.resources, instantiates every public factory with
name="seed", classifies the result, extracts geometry, writes a
discriminated-union JSON list consumed by `load_labware_seed()`.

Run after PyLabRobot updates. Output is committed to the repo and
ships in the cheshire-drivers wheel via `package-data` in pyproject.toml.

Usage:
  python -m cheshire_drivers.scripts.regen_labware_seed
"""

import inspect
import json
import sys
import warnings
from pathlib import Path
from typing import Any

from cheshire_drivers._plr_compat import ensure_plr_stubs

ensure_plr_stubs()

import pylabrobot.resources as plr_res
from pylabrobot.resources.carrier import Carrier
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.tip_rack import TipRack
from pylabrobot.resources.trough import Trough
from pylabrobot.resources.tube import Tube

from cheshire_drivers.plr import plates as cd_plates
from cheshire_drivers.plr.labware import (
    PLRPlateAdapter,
    PLRTipRackAdapter,
    PLRTroughAdapter,
)

from cheshire_drivers.labware_seed import (
    CarrierSeedEntry,
    CarrierSiteSeedEntry,
    LabwareSeedEntry,
    PlateSeedEntry,
    TipRackSeedEntry,
    TipSpotSeedEntry,
    TroughSeedEntry,
    TubeSeedEntry,
    WellSeedEntry,
)


_SEED_NAME = "seed"
_DEPRECATED_HINTS = (
    "deprecated",
    "is not currently defined",
    "removed in a future",
    "removed in the future",
    "will be removed",
)


def _vendor_from_module(obj: Any) -> str | None:
    """Infer vendor from the factory's defining module path.

    `pylabrobot.resources.corning.plates.Cor_96_...` -> 'corning'.
    """
    mod = inspect.getmodule(obj)
    if mod is None or mod.__name__ is None:
        return None
    parts = mod.__name__.split(".")
    # pylabrobot.resources.<vendor>.<...>
    if len(parts) >= 3 and parts[0] == "pylabrobot" and parts[1] == "resources":
        return parts[2]
    return None


def _is_deprecated_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(hint in msg for hint in _DEPRECATED_HINTS)


def _well_entry(plate: Plate, well: Any) -> WellSeedEntry:
    return WellSeedEntry(
        identifier=plate.get_child_identifier(well),
        row=plate.get_child_row(well),
        col=plate.get_child_column(well),
        position_x=well.location.x,
        position_y=well.location.y,
        position_z=well.location.z,
        size_x=well.get_size_x(),
        size_y=well.get_size_y(),
        size_z=well.get_size_z(),
        max_volume=float(well.max_volume),
        bottom_type=well.bottom_type.value,
    )


def _plate_entry(labware_type: str, plate: Plate, vendor: str | None) -> PlateSeedEntry:
    wells = [_well_entry(plate, w) for w in plate.get_all_items()]
    return PlateSeedEntry(
        labware_type=labware_type,
        display_name=labware_type,
        vendor=vendor,
        plr_class_name=labware_type,
        num_rows=plate.num_items_y,
        num_cols=plate.num_items_x,
        size_x=plate.get_size_x(),
        size_y=plate.get_size_y(),
        size_z=plate.get_size_z(),
        wells=wells,
    )


def _tip_spot_entry(rack: TipRack, spot: Any) -> TipSpotSeedEntry:
    tip = spot.make_tip()
    return TipSpotSeedEntry(
        identifier=rack.get_child_identifier(spot),
        row=rack.get_child_row(spot),
        col=rack.get_child_column(spot),
        position_x=spot.location.x,
        position_y=spot.location.y,
        position_z=spot.location.z,
        size_x=spot.get_size_x(),
        size_y=spot.get_size_y(),
        size_z=spot.get_size_z(),
        tip_length=float(tip.total_tip_length),
        has_filter=bool(tip.has_filter),
        maximal_volume=float(tip.maximal_volume),
    )


def _tip_rack_entry(labware_type: str, rack: TipRack, vendor: str | None) -> TipRackSeedEntry:
    spots = [_tip_spot_entry(rack, s) for s in rack.get_all_items()]
    return TipRackSeedEntry(
        labware_type=labware_type,
        display_name=labware_type,
        vendor=vendor,
        plr_class_name=labware_type,
        num_rows=rack.num_items_y,
        num_cols=rack.num_items_x,
        size_x=rack.get_size_x(),
        size_y=rack.get_size_y(),
        size_z=rack.get_size_z(),
        tip_spots=spots,
    )


def _trough_entry(labware_type: str, trough: Trough, vendor: str | None) -> TroughSeedEntry:
    return TroughSeedEntry(
        labware_type=labware_type,
        display_name=labware_type,
        vendor=vendor,
        plr_class_name=labware_type,
        size_x=trough.get_size_x(),
        size_y=trough.get_size_y(),
        size_z=trough.get_size_z(),
        max_volume=float(trough.max_volume),
        bottom_type=trough.bottom_type.value,
    )


def _tube_entry(labware_type: str, tube: Tube, vendor: str | None) -> TubeSeedEntry:
    return TubeSeedEntry(
        labware_type=labware_type,
        display_name=labware_type,
        vendor=vendor,
        plr_class_name=labware_type,
        size_x=tube.get_size_x(),
        size_y=tube.get_size_y(),
        size_z=tube.get_size_z(),
        max_volume=float(tube.max_volume),
    )


def _carrier_site_entry(site_id: int | str, site: Any) -> CarrierSiteSeedEntry:
    return CarrierSiteSeedEntry(
        identifier=str(site_id),
        position_x=site.location.x,
        position_y=site.location.y,
        position_z=site.location.z,
        size_x=site.get_size_x(),
        size_y=site.get_size_y(),
        size_z=site.get_size_z(),
    )


def _carrier_entry(labware_type: str, carrier: Carrier, vendor: str | None) -> CarrierSeedEntry:
    sites_map = getattr(carrier, "sites", {}) or {}
    if isinstance(sites_map, dict):
        sites = [_carrier_site_entry(k, v) for k, v in sorted(sites_map.items(), key=lambda kv: kv[0])]
    else:
        sites = [_carrier_site_entry(i, s) for i, s in enumerate(sites_map)]
    return CarrierSeedEntry(
        labware_type=labware_type,
        display_name=labware_type,
        vendor=vendor,
        plr_class_name=labware_type,
        size_x=carrier.get_size_x(),
        size_y=carrier.get_size_y(),
        size_z=carrier.get_size_z(),
        sites=sites,
    )


def _build_entry(labware_type: str, instance: Any, vendor: str | None) -> LabwareSeedEntry | None:
    """Dispatch on PLR returned-type to the matching seed-entry builder."""
    if isinstance(instance, Plate):
        return _plate_entry(labware_type, instance, vendor)
    if isinstance(instance, TipRack):
        return _tip_rack_entry(labware_type, instance, vendor)
    if isinstance(instance, Trough):
        return _trough_entry(labware_type, instance, vendor)
    if isinstance(instance, Tube):
        return _tube_entry(labware_type, instance, vendor)
    if isinstance(instance, Carrier):
        return _carrier_entry(labware_type, instance, vendor)
    return None


def _enumerate_factories() -> list[tuple[str, Any]]:
    """Public callables in pylabrobot.resources that look like factories."""
    out: list[tuple[str, Any]] = []
    for name, obj in inspect.getmembers(plr_res, callable):
        if name.startswith("_"):
            continue
        if inspect.isclass(obj):
            continue
        try:
            sig = inspect.signature(obj)
        except (ValueError, TypeError):
            continue
        params = sig.parameters
        if "name" not in params:
            continue
        required_non_name = [
            p for p in params.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY)
            and p.name not in ("self", "cls", "name")
        ]
        if required_non_name:
            continue
        out.append((name, obj))
    return out


def _enumerate_cheshire_drivers_factories() -> list[tuple[str, Any]]:
    """Public wrapped factories in cheshire_drivers.plr.plates.

    The module is misnamed -- it contains plates, tip racks, and troughs
    all wrapped with `plr_plate_factory`/`plr_tip_rack_factory`/
    `plr_trough_factory` so they return the I*-compatible adapters
    orca-core templates expect. Many of these wrap PLR factories that
    live in nested sub-modules (e.g. `pylabrobot.resources.corning.falcon`)
    and are NOT re-exported at the top of `pylabrobot.resources`, so the
    primary PLR walk misses them.

    The wrapped factories have generic `(*args, **kwargs)` signatures
    (the closure returned by `plr_plate_factory`), so we cannot inspect
    for a `name` parameter to filter; instead we list everything in the
    module's `__all__` and let instantiation with `name="seed"` succeed
    or skip with a real reason.
    """
    out: list[tuple[str, Any]] = []
    for name in getattr(cd_plates, "__all__", []):
        obj = getattr(cd_plates, name, None)
        if obj is None or inspect.isclass(obj) or not callable(obj):
            continue
        if name in ("plr_plate_factory", "plr_tip_rack_factory", "plr_trough_factory"):
            continue
        out.append((name, obj))
    return out


def _unwrap_adapter(adapter: object) -> object:
    """Pull the underlying PLR object out of a cheshire-drivers adapter.

    The seed entry encodes raw PLR geometry, so seed generation needs
    the wrapped PLR object regardless of which adapter type wraps it.
    Returns the adapter unchanged if it is not a recognized wrapper, so
    raw PLR factories from `plr_resources` pass through untouched.
    """
    if isinstance(adapter, PLRPlateAdapter):
        return adapter._plate
    if isinstance(adapter, PLRTipRackAdapter):
        return adapter._rack
    if isinstance(adapter, PLRTroughAdapter):
        return adapter._trough
    return adapter


def regenerate() -> tuple[list[LabwareSeedEntry], list[tuple[str, str]]]:
    """Walk PLR and cheshire_drivers.plr.plates; return (entries, skipped_with_reason)."""
    entries: list[LabwareSeedEntry] = []
    skipped: list[tuple[str, str]] = []
    seen_labware_types: set[str] = set()

    sources: list[tuple[str, list[tuple[str, Any]]]] = [
        ("plr_resources", _enumerate_factories()),
        ("cheshire_drivers.plr.plates", _enumerate_cheshire_drivers_factories()),
    ]

    for source_label, factories in sources:
        for labware_type, factory in sorted(factories):
            if labware_type in seen_labware_types:
                skipped.append((labware_type, "duplicate (covered by earlier source)"))
                continue
            seen_labware_types.add(labware_type)

            vendor = _vendor_from_module(factory)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    instance = factory(name=_SEED_NAME)
            except Exception as exc:
                reason = "deprecated" if _is_deprecated_error(exc) else f"{type(exc).__name__}: {exc}"
                skipped.append((labware_type, f"[{source_label}] {reason}"))
                continue

            instance = _unwrap_adapter(instance)

            try:
                entry = _build_entry(labware_type, instance, vendor)
            except Exception as exc:
                skipped.append((labware_type, f"[{source_label}] geometry_extraction_failed: {type(exc).__name__}: {exc}"))
                continue

            if entry is None:
                skipped.append((labware_type, f"[{source_label}] unhandled_type: {type(instance).__name__}"))
                continue

            entries.append(entry)

    return entries, skipped


def _write_seed(entries: list[LabwareSeedEntry], dest: Path) -> None:
    """Write the seed JSON. Sorts by (category, labware_type) for stable diffs.

    Compact form (one entry per line) keeps wheel size manageable and
    makes diffs review-line-counting friendly. Reviewers check category
    counts via the regen script summary, not byte-level diffs.
    """
    payload = [json.loads(e.model_dump_json()) for e in entries]
    payload.sort(key=lambda d: (d["category"], d["labware_type"]))
    body = "[\n" + ",\n".join(json.dumps(d, sort_keys=True) for d in payload) + "\n]\n"
    dest.write_text(body, encoding="utf-8")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    dest = repo_root / "src" / "cheshire_drivers" / "labware_seed.json"

    print(f"Regenerating {dest}...")
    entries, skipped = regenerate()
    _write_seed(entries, dest)

    by_cat: dict[str, int] = {}
    for e in entries:
        by_cat[e.category] = by_cat.get(e.category, 0) + 1

    print(f"Wrote {len(entries)} entries to {dest}")
    for cat, count in sorted(by_cat.items()):
        print(f"  {cat}: {count}")
    print(f"\nSkipped {len(skipped)} factories.")
    # Print first 20 non-deprecation skips for diagnosis
    real_skips = [(s, r) for s, r in skipped if r != "deprecated"]
    print(f"  ({len([s for s, r in skipped if r == 'deprecated'])} deprecated, "
          f"{len(real_skips)} other)")
    for labware_type, reason in real_skips[:20]:
        print(f"    - {labware_type}: {reason}")
    if len(real_skips) > 20:
        print(f"    ... and {len(real_skips) - 20} more")

    return 0


if __name__ == "__main__":
    sys.exit(main())
