"""Labware interfaces for orca-core.

Protocol-based interfaces that decouple orca from any specific labware library.
PLR adapters in cheshire-drivers implement these behind the scenes.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class IContainer(Protocol):
    """A single pool of liquid: the unit every liquid-handling op acts on.

    Mirrors PLR's ``Container`` (``Well`` and ``Trough`` are both Containers).
    ``resource_name``/``position`` are how a driver re-finds the container on the
    deck: a well lives inside a plate (``resource_name`` = plate, ``position`` =
    "A1"); a trough IS the deck resource (``resource_name`` = trough, ``position``
    = None).
    """

    @property
    def resource_name(self) -> str:
        """Deck-resolvable resource: a well's parent plate, or the trough itself."""
        ...

    @property
    def position(self) -> str | None:
        """Item id within the resource ("A1"), or None when the resource IS the container."""
        ...

    @property
    def size_x(self) -> float:
        ...

    @property
    def size_y(self) -> float:
        ...

    @property
    def size_z(self) -> float:
        ...

    @property
    def max_volume(self) -> float:
        """Maximum volume capacity in uL."""
        ...

    @property
    def volume(self) -> float:
        """Current tracked volume in uL."""
        ...

    def set_volume(self, volume: float) -> None:
        """Set the current volume in uL."""
        ...


@runtime_checkable
class IWell(IContainer, Protocol):
    """A container that lives in a plate grid. Adds grid addressing on top of
    ``IContainer``; ``position`` equals ``identifier`` and ``resource_name``
    equals ``parent_name`` for a well."""

    @property
    def parent_name(self) -> str:
        """Name of the parent plate."""
        ...

    @property
    def identifier(self) -> str:
        """Well identifier in Excel notation, e.g. 'A1'."""
        ...

    @property
    def row(self) -> int:
        """0-indexed row number."""
        ...

    @property
    def col(self) -> int:
        """0-indexed column number."""
        ...

    @property
    def position_x(self) -> float:
        """X position relative to the parent plate origin (mm)."""
        ...

    @property
    def position_y(self) -> float:
        """Y position relative to the parent plate origin (mm)."""
        ...

    @property
    def position_z(self) -> float:
        """Z position relative to the parent plate origin (mm)."""
        ...

    @property
    def bottom_type(self) -> str:
        """Well bottom shape: 'flat', 'U', 'V', or 'unknown'."""
        ...


@runtime_checkable
class IPlate(Protocol):
    """A microplate with addressable wells."""

    @property
    def name(self) -> str:
        ...

    @property
    def barcode(self) -> str | None:
        ...

    @property
    def num_rows(self) -> int:
        ...

    @property
    def num_cols(self) -> int:
        ...

    @property
    def size_x(self) -> float:
        """Plate width in mm."""
        ...

    @property
    def size_y(self) -> float:
        """Plate depth in mm."""
        ...

    @property
    def size_z(self) -> float:
        """Plate height in mm."""
        ...

    @property
    def has_lid(self) -> bool:
        ...

    def well(self, identifier: str) -> IWell:
        """Get a single well by identifier (e.g. 'A1')."""
        ...

    def wells(self, identifiers: list[str]) -> list[IWell]:
        """Get multiple wells by identifier."""
        ...


@runtime_checkable
class ITipSpot(Protocol):
    """A single tip position in a tip rack."""

    @property
    def parent_name(self) -> str:
        """Name of the parent tip rack."""
        ...

    @property
    def identifier(self) -> str:
        ...

    @property
    def has_tip(self) -> bool:
        ...

    def set_tip(self, has_tip: bool) -> None:
        ...


@runtime_checkable
class ITipRack(Protocol):
    """A rack of pipette tips."""

    @property
    def name(self) -> str:
        ...

    @property
    def size_z(self) -> float:
        ...

    @property
    def num_tips(self) -> int:
        ...

    @property
    def has_tips(self) -> bool:
        """Whether any tips remain."""
        ...

    def tip_spot(self, identifier: str) -> ITipSpot:
        """Get a single tip spot by identifier."""
        ...

    def tip_spots(self) -> list[ITipSpot]:
        """Get all tip spots in the rack."""
        ...


@runtime_checkable
class ITrough(IContainer, Protocol):
    """A standalone single-pool reservoir. Adds only identity metadata on top of
    ``IContainer``; ``resource_name`` equals ``name`` and ``position`` is None."""

    @property
    def name(self) -> str:
        ...

