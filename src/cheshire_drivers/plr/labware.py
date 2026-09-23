"""PLR labware adapters and factory wrappers.

Wraps PyLabRobot labware objects behind IPlate/IWell/ITipRack/ITipSpot/ITrough
interfaces. Each adapter is a live view: reads/writes propagate to the underlying
PLR object.
"""

import inspect
from typing import Callable

from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.resources.trough import Trough

from cheshire_drivers.labware_interfaces import IPlate, IWell, ITipRack, ITipSpot, ITrough


class PLRWellAdapter(IWell):
    """Adapts a PLR Well to the IWell protocol. Live view."""

    def __init__(self, plr_well: Well) -> None:
        self._well = plr_well

    @property
    def resource_name(self) -> str:
        return self._well.parent.name

    @property
    def position(self) -> str | None:
        return self._well.get_identifier()

    @property
    def parent_name(self) -> str:
        return self._well.parent.name

    @property
    def identifier(self) -> str:
        return self._well.get_identifier()

    @property
    def row(self) -> int:
        return self._well.get_row()

    @property
    def col(self) -> int:
        return self._well.get_column()

    @property
    def position_x(self) -> float:
        loc = self._well.location
        return loc.x if loc is not None else 0.0

    @property
    def position_y(self) -> float:
        loc = self._well.location
        return loc.y if loc is not None else 0.0

    @property
    def position_z(self) -> float:
        loc = self._well.location
        return loc.z if loc is not None else 0.0

    @property
    def size_x(self) -> float:
        return self._well.get_size_x()

    @property
    def size_y(self) -> float:
        return self._well.get_size_y()

    @property
    def size_z(self) -> float:
        return self._well.get_size_z()

    @property
    def max_volume(self) -> float:
        return self._well.max_volume

    @property
    def volume(self) -> float:
        return self._well.tracker.volume

    def set_volume(self, volume: float) -> None:
        self._well.set_volume(volume)

    @property
    def bottom_type(self) -> str:
        return self._well.bottom_type.value


class PLRPlateAdapter(IPlate):
    """Adapts a PLR Plate to the IPlate protocol. Live view with well caching."""

    def __init__(self, plr_plate: Plate) -> None:
        self._plate = plr_plate
        self._well_cache: dict[str, PLRWellAdapter] = {}

    @property
    def name(self) -> str:
        return self._plate.name

    @property
    def barcode(self) -> str | None:
        bc = self._plate.barcode
        if bc is None:
            return None
        return bc.data if hasattr(bc, "data") else str(bc)

    @property
    def num_rows(self) -> int:
        return self._plate.num_items_y

    @property
    def num_cols(self) -> int:
        return self._plate.num_items_x

    @property
    def size_x(self) -> float:
        return self._plate.get_size_x()

    @property
    def size_y(self) -> float:
        return self._plate.get_size_y()

    @property
    def size_z(self) -> float:
        return self._plate.get_size_z()

    @property
    def has_lid(self) -> bool:
        return self._plate.has_lid()

    def well(self, identifier: str) -> PLRWellAdapter:
        if identifier not in self._well_cache:
            self._well_cache[identifier] = PLRWellAdapter(self._plate.get_well(identifier))
        return self._well_cache[identifier]

    def wells(self, identifiers: list[str]) -> list[PLRWellAdapter]:
        return [self.well(id) for id in identifiers]


class PLRTipSpotAdapter(ITipSpot):
    """Adapts a PLR TipSpot to the ITipSpot protocol."""

    def __init__(self, plr_tip_spot: TipSpot) -> None:
        self._spot = plr_tip_spot

    @property
    def parent_name(self) -> str:
        return self._spot.parent.name

    @property
    def identifier(self) -> str:
        return self._spot.get_identifier()

    @property
    def has_tip(self) -> bool:
        return self._spot.has_tip()

    def set_tip(self, has_tip: bool) -> None:
        if has_tip and not self._spot.has_tip():
            tip = self._spot.make_tip()
            self._spot.tracker.add_tip(tip, origin=self._spot)
        elif not has_tip and self._spot.has_tip():
            self._spot.tracker.remove_tip()


class PLRTipRackAdapter(ITipRack):
    """Adapts a PLR TipRack to the ITipRack protocol."""

    def __init__(self, plr_tip_rack: TipRack) -> None:
        self._rack = plr_tip_rack
        self._spot_cache: dict[str, PLRTipSpotAdapter] = {}

    @property
    def name(self) -> str:
        return self._rack.name

    @property
    def size_z(self) -> float:
        return self._rack.get_size_z()

    @property
    def num_tips(self) -> int:
        return self._rack.num_items

    @property
    def has_tips(self) -> bool:
        return any(spot.has_tip() for spot in self._rack.get_all_items())

    def tip_spot(self, identifier: str) -> PLRTipSpotAdapter:
        if identifier not in self._spot_cache:
            self._spot_cache[identifier] = PLRTipSpotAdapter(self._rack.get_item(identifier))
        return self._spot_cache[identifier]

    def tip_spots(self) -> list[PLRTipSpotAdapter]:
        spots: list[PLRTipSpotAdapter] = []
        for item in self._rack.get_all_items():
            ident = item.get_identifier()
            if ident not in self._spot_cache:
                self._spot_cache[ident] = PLRTipSpotAdapter(item)
            spots.append(self._spot_cache[ident])
        return spots


class PLRTroughAdapter(ITrough):
    """Adapts a PLR Trough to the ITrough protocol. Live view."""

    def __init__(self, plr_trough: Trough) -> None:
        self._trough = plr_trough

    @property
    def resource_name(self) -> str:
        return self._trough.name

    @property
    def position(self) -> str | None:
        return None

    @property
    def name(self) -> str:
        return self._trough.name

    @property
    def size_x(self) -> float:
        return self._trough.get_size_x()

    @property
    def size_y(self) -> float:
        return self._trough.get_size_y()

    @property
    def size_z(self) -> float:
        return self._trough.get_size_z()

    @property
    def max_volume(self) -> float:
        return self._trough.max_volume

    @property
    def volume(self) -> float:
        return self._trough.tracker.volume

    def set_volume(self, volume: float) -> None:
        self._trough.tracker.set_volume(volume)


# --- Factory wrappers ---

def plr_plate_factory(plr_factory: Callable[..., Plate]) -> Callable[..., PLRPlateAdapter]:
    """Wrap a PLR plate factory so it returns IPlate (via PLRPlateAdapter)."""
    def wrapper(*args: object, **kwargs: object) -> PLRPlateAdapter:
        plr_plate = plr_factory(*args, **kwargs)
        return PLRPlateAdapter(plr_plate)
    return wrapper


def plr_tip_rack_factory(plr_factory: Callable[..., TipRack]) -> Callable[..., PLRTipRackAdapter]:
    """Wrap a PLR tip rack factory so it returns ITipRack (via PLRTipRackAdapter).

    Callers (orca-core templates) pass (name, with_tips). Hamilton-style PLR
    factories take that pair; Opentrons Flex factories take only a name and
    always build full, so with_tips is honoured by emptying the rack after.
    """
    accepts_with_tips = "with_tips" in inspect.signature(plr_factory).parameters

    def wrapper(name: str, with_tips: bool = True) -> PLRTipRackAdapter:
        if accepts_with_tips:
            plr_rack = plr_factory(name, with_tips)
        else:
            plr_rack = plr_factory(name)
            if not with_tips:
                plr_rack.empty()
        return PLRTipRackAdapter(plr_rack)
    return wrapper


def plr_trough_factory(plr_factory: Callable[..., Trough]) -> Callable[..., PLRTroughAdapter]:
    """Wrap a PLR trough factory so it returns ITrough (via PLRTroughAdapter)."""
    def wrapper(*args: object, **kwargs: object) -> PLRTroughAdapter:
        plr_trough = plr_factory(*args, **kwargs)
        return PLRTroughAdapter(plr_trough)
    return wrapper
