"""Converts IPlate/IWell to PLR objects for PLR-backed devices.

Used by PLR device wrappers (e.g., liquid handler) when they need
native PLR objects for coordinate calculations and backend operations.

Adapter-unwrap branch: input is a PLRPlateAdapter (or matching well /
trough / tip-rack adapter) wrapping an existing PLR object -- return
the wrapped PLR object directly so parent chains, trackers, and
callbacks survive.

Geometry-construction branch: input is a non-PLR IPlate-shaped object
(e.g. a PlateSeedEntry from cheshire_drivers.labware_seed, the shape
orca-client receives over the wire for custom labware). Build a fresh
PLR Plate from the protocol-typed geometry fields.
"""

from typing import Protocol, runtime_checkable

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well, WellBottomType
from pylabrobot.resources.resource import Coordinate
from pylabrobot.resources.trough import Trough
from pylabrobot.resources.tip_rack import TipRack

from cheshire_drivers.plr.labware import PLRPlateAdapter, PLRWellAdapter, PLRTroughAdapter, PLRTipRackAdapter


@runtime_checkable
class _HasWellGeometry(Protocol):
    """Minimal well protocol for geometry extraction."""
    identifier: str
    position_x: float
    position_y: float
    position_z: float
    size_x: float
    size_y: float
    size_z: float
    max_volume: float
    volume: float
    bottom_type: str

    @property
    def row(self) -> int: ...
    @property
    def col(self) -> int: ...


@runtime_checkable
class _HasPlateGeometry(Protocol):
    """Minimal plate protocol for geometry extraction.

    `name` is read-only so frozen Pydantic models (e.g. `PlateSeedEntry`)
    can satisfy the protocol via an aliasing property. Mutable attributes
    (PLR's `Plate.name`) satisfy the read-only requirement implicitly.
    """
    num_rows: int
    num_cols: int
    size_x: float
    size_y: float
    size_z: float

    @property
    def name(self) -> str: ...

    def well(self, identifier: str) -> _HasWellGeometry: ...


class PLRLabwareConverter:
    """Converts IPlate/IWell/ITrough to PLR objects for PLR-backed devices."""

    def to_plr_plate(self, plate: _HasPlateGeometry) -> Plate:
        """Convert an IPlate to a PLR Plate.

        Adapter-unwrap branch (input wraps a PLR Plate already): unwraps
        the adapter, preserving all PLR references (parent chains,
        trackers, callbacks).

        Geometry-construction branch (input is a non-PLR IPlate, e.g. a
        PlateSeedEntry): constructs a new PLR Plate from the protocol-
        typed geometry data.
        """
        if isinstance(plate, PLRPlateAdapter):
            return plate._plate
        return self._construct_plate(plate)

    def to_plr_well(self, well: _HasWellGeometry) -> Well:
        """Convert an IWell to a PLR Well."""
        if isinstance(well, PLRWellAdapter):
            return well._well
        return self._construct_well(well)

    def to_plr_trough(self, trough: object) -> Trough:
        """Convert an ITrough to a PLR Trough.

        V1 requires PLR-typed troughs (wrap with ``PLRTroughAdapter``).
        Non-PLR troughs raise so a topology error surfaces at first
        dispatch rather than silently producing an empty trough.
        """
        if isinstance(trough, PLRTroughAdapter):
            return trough._trough
        raise NotImplementedError(
            f"Cannot convert {type(trough).__name__!r} to a PLR Trough: only "
            f"PLRTroughAdapter is supported in V1. Wrap your trough with "
            f"PLRTroughAdapter at topology construction, or use one of the "
            f"PLR-backed troughs from cheshire_drivers.plr.troughs.",
        )

    def to_plr_tip_rack(self, rack: object) -> TipRack:
        """Convert an ITipRack to a PLR TipRack.

        V1 requires PLR-typed tip racks (wrap with ``PLRTipRackAdapter``).
        Non-PLR tip racks raise so a topology error surfaces at first
        dispatch rather than silently producing an empty rack.
        """
        if isinstance(rack, PLRTipRackAdapter):
            return rack._rack
        raise NotImplementedError(
            f"Cannot convert {type(rack).__name__!r} to a PLR TipRack: only "
            f"PLRTipRackAdapter is supported in V1. Wrap your tip rack with "
            f"PLRTipRackAdapter at topology construction, or use one of the "
            f"PLR-backed tip racks from cheshire_drivers.plr.tip_racks.",
        )

    def _construct_plate(self, plate: _HasPlateGeometry) -> Plate:
        """Build a PLR Plate from IPlate geometry. Used for non-PLR labware."""
        from pylabrobot.resources.utils import create_ordered_items_2d

        ref_well = plate.well("A1")
        bottom = WellBottomType(ref_well.bottom_type) if ref_well.bottom_type != "unknown" else WellBottomType.UNKNOWN

        ordered_items = create_ordered_items_2d(
            Well,
            num_items_x=plate.num_cols,
            num_items_y=plate.num_rows,
            dx=0.0, dy=0.0, dz=0.0,
            item_dx=plate.size_x / plate.num_cols,
            item_dy=plate.size_y / plate.num_rows,
            size_x=ref_well.size_x,
            size_y=ref_well.size_y,
            size_z=ref_well.size_z,
            max_volume=ref_well.max_volume,
            bottom_type=bottom,
        )

        plr_plate = Plate(
            name=plate.name,
            size_x=plate.size_x,
            size_y=plate.size_y,
            size_z=plate.size_z,
            ordered_items=ordered_items,
        )

        # Set well volumes to match interface state
        for row_idx in range(plate.num_rows):
            for col_idx in range(plate.num_cols):
                well_id = f"{chr(65 + row_idx)}{col_idx + 1}"
                iwell = plate.well(well_id)
                if iwell.volume > 0:
                    plr_plate.get_well(well_id).set_volume(iwell.volume)

        return plr_plate

    def _construct_well(self, well: _HasWellGeometry) -> Well:
        """Build a standalone PLR Well from IWell geometry."""
        bottom = WellBottomType(well.bottom_type) if well.bottom_type != "unknown" else WellBottomType.UNKNOWN
        plr_well = Well(
            name=well.identifier,
            size_x=well.size_x,
            size_y=well.size_y,
            size_z=well.size_z,
            max_volume=well.max_volume,
            bottom_type=bottom,
        )
        if well.volume > 0:
            plr_well.set_volume(well.volume)
        return plr_well
