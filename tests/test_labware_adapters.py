"""Tests for PLR labware adapters, factory wrappers, and converter.

Covers:
- PLRPlateAdapter: live view over PLR Plate, well caching, property delegation
- PLRWellAdapter: live view over PLR Well, volume tracking bidirectional
- PLRTipRackAdapter/PLRTipSpotAdapter: tip state management
- PLRTroughAdapter: volume tracking
- Factory wrappers: plr_plate_factory, plr_tip_rack_factory, plr_trough_factory
- PLRLabwareConverter: adapter-unwrap branch and geometry-construction branch
"""

import pytest
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well, WellBottomType
from pylabrobot.resources.tip_rack import TipRack, TipSpot, Tip
from pylabrobot.resources.trough import Trough
from pylabrobot.resources.resource import Coordinate
from pylabrobot.resources.utils import create_ordered_items_2d

from cheshire_drivers.plr.labware import (
    PLRPlateAdapter,
    PLRWellAdapter,
    PLRTipRackAdapter,
    PLRTipSpotAdapter,
    PLRTroughAdapter,
    plr_plate_factory,
    plr_tip_rack_factory,
    plr_trough_factory,
)
from cheshire_drivers.plr.labware_converter import PLRLabwareConverter


# --- Helpers ---

def _make_96_well_plate(name: str = "test_plate") -> Plate:
    """Create a simple 96-well plate (8 rows x 12 cols) for testing."""
    ordered_items = create_ordered_items_2d(
        Well,
        num_items_x=12,
        num_items_y=8,
        dx=7.2, dy=5.3, dz=2.0,
        item_dx=9.0, item_dy=9.0,
        size_x=9.0, size_y=9.0, size_z=17.0,
        max_volume=385.0,
        bottom_type=WellBottomType.FLAT,
    )
    return Plate(
        name=name,
        size_x=127.76,
        size_y=85.48,
        size_z=14.5,
        ordered_items=ordered_items,
    )


def _make_tip_rack(name: str = "test_tips", with_tips: bool = True) -> TipRack:
    """Create a simple 96-position tip rack for testing."""
    def _make_tip(name: str = "tip") -> Tip:
        return Tip(has_filter=False, total_tip_length=60.0, maximal_volume=300.0, fitting_depth=10.0, name=name)

    ordered_items = create_ordered_items_2d(
        TipSpot,
        num_items_x=12,
        num_items_y=8,
        dx=7.2, dy=5.3, dz=2.0,
        item_dx=9.0, item_dy=9.0,
        size_x=9.0, size_y=9.0, size_z=50.0,
        make_tip=_make_tip,
    )
    return TipRack(
        name=name,
        size_x=127.76,
        size_y=85.48,
        size_z=97.5,
        ordered_items=ordered_items,
        with_tips=with_tips,
    )


def _make_trough(name: str = "test_trough") -> Trough:
    """Create a simple trough for testing."""
    return Trough(
        name=name,
        size_x=127.76,
        size_y=85.48,
        size_z=40.0,
        max_volume=190000.0,
    )


# --- PLRPlateAdapter ---

class TestPLRPlateAdapter:
    def test_name(self) -> None:
        plate = _make_96_well_plate("my_plate")
        adapter = PLRPlateAdapter(plate)
        assert adapter.name == "my_plate"

    def test_barcode_none(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        assert adapter.barcode is None

    def test_grid_dimensions(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        assert adapter.num_rows == 8
        assert adapter.num_cols == 12

    def test_plate_size(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        assert adapter.size_x == 127.76
        assert adapter.size_y == 85.48
        assert adapter.size_z == 14.5

    def test_has_lid_false(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        assert adapter.has_lid is False

    def test_well_access(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        well = adapter.well("A1")
        assert isinstance(well, PLRWellAdapter)
        assert well.identifier == "A1"

    def test_well_caching(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        well1 = adapter.well("A1")
        well2 = adapter.well("A1")
        assert well1 is well2

    def test_wells_batch(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        wells = adapter.wells(["A1", "A2", "B1"])
        assert len(wells) == 3
        assert wells[0].identifier == "A1"
        assert wells[1].identifier == "A2"
        assert wells[2].identifier == "B1"


# --- PLRWellAdapter ---

class TestPLRWellAdapter:
    def test_identifier(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        well = adapter.well("C5")
        assert well.identifier == "C5"

    def test_row_col(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        well = adapter.well("C5")
        assert well.row == 2  # 0-indexed
        assert well.col == 4  # 0-indexed

    def test_geometry(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        well = adapter.well("A1")
        assert well.size_x == 9.0
        assert well.size_y == 9.0
        assert well.size_z == 17.0

    def test_max_volume(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        well = adapter.well("A1")
        assert well.max_volume == 385.0

    def test_volume_initially_zero(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        well = adapter.well("A1")
        assert well.volume == 0.0

    def test_set_volume_propagates_to_plr(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        well = adapter.well("A1")
        well.set_volume(100.0)
        # Verify it propagated to the underlying PLR well
        plr_well = plate.get_well("A1")
        assert plr_well.tracker.volume == 100.0

    def test_plr_volume_change_visible_through_adapter(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        well = adapter.well("A1")
        # Modify volume through PLR directly
        plate.get_well("A1").set_volume(200.0)
        # Should be visible through the adapter (live view)
        assert well.volume == 200.0

    def test_bidirectional_volume_tracking(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        well = adapter.well("B3")
        plr_well = plate.get_well("B3")

        # Write through adapter, read through PLR
        well.set_volume(50.0)
        assert plr_well.tracker.volume == 50.0

        # Write through PLR, read through adapter
        plr_well.set_volume(75.0)
        assert well.volume == 75.0

    def test_bottom_type(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        well = adapter.well("A1")
        assert well.bottom_type == "flat"

    def test_position(self) -> None:
        plate = _make_96_well_plate()
        adapter = PLRPlateAdapter(plate)
        well = adapter.well("A1")
        # Position should be a number (exact value depends on PLR layout)
        assert isinstance(well.position_x, float)
        assert isinstance(well.position_y, float)
        assert isinstance(well.position_z, float)


# --- PLRTipRackAdapter ---

class TestPLRTipRackAdapter:
    def test_name(self) -> None:
        rack = _make_tip_rack("my_tips")
        adapter = PLRTipRackAdapter(rack)
        assert adapter.name == "my_tips"

    def test_num_tips(self) -> None:
        rack = _make_tip_rack()
        adapter = PLRTipRackAdapter(rack)
        assert adapter.num_tips == 96

    def test_has_tips_when_full(self) -> None:
        rack = _make_tip_rack(with_tips=True)
        adapter = PLRTipRackAdapter(rack)
        assert adapter.has_tips is True

    def test_has_tips_when_empty(self) -> None:
        rack = _make_tip_rack(with_tips=False)
        adapter = PLRTipRackAdapter(rack)
        assert adapter.has_tips is False

    def test_tip_spot_access(self) -> None:
        rack = _make_tip_rack(with_tips=True)
        adapter = PLRTipRackAdapter(rack)
        spot = adapter.tip_spot("A1")
        assert isinstance(spot, PLRTipSpotAdapter)
        assert spot.identifier == "A1"

    def test_tip_spot_caching(self) -> None:
        rack = _make_tip_rack()
        adapter = PLRTipRackAdapter(rack)
        spot1 = adapter.tip_spot("A1")
        spot2 = adapter.tip_spot("A1")
        assert spot1 is spot2


# --- PLRTipSpotAdapter ---

class TestPLRTipSpotAdapter:
    def test_has_tip_true(self) -> None:
        rack = _make_tip_rack(with_tips=True)
        adapter = PLRTipRackAdapter(rack)
        spot = adapter.tip_spot("A1")
        assert spot.has_tip is True

    def test_has_tip_false(self) -> None:
        rack = _make_tip_rack(with_tips=False)
        adapter = PLRTipRackAdapter(rack)
        spot = adapter.tip_spot("A1")
        assert spot.has_tip is False

    def test_set_tip_remove(self) -> None:
        rack = _make_tip_rack(with_tips=True)
        adapter = PLRTipRackAdapter(rack)
        spot = adapter.tip_spot("A1")
        assert spot.has_tip is True
        spot.set_tip(False)
        assert spot.has_tip is False
        # Verify PLR state
        plr_spot = rack.get_item("A1")
        assert plr_spot.has_tip() is False

    def test_set_tip_add(self) -> None:
        rack = _make_tip_rack(with_tips=False)
        adapter = PLRTipRackAdapter(rack)
        spot = adapter.tip_spot("A1")
        assert spot.has_tip is False
        spot.set_tip(True)
        assert spot.has_tip is True
        # Verify PLR state
        plr_spot = rack.get_item("A1")
        assert plr_spot.has_tip() is True

    def test_set_tip_idempotent(self) -> None:
        rack = _make_tip_rack(with_tips=True)
        adapter = PLRTipRackAdapter(rack)
        spot = adapter.tip_spot("A1")
        spot.set_tip(True)  # already has tip, should not error
        assert spot.has_tip is True


# --- PLRTroughAdapter ---

class TestPLRTroughAdapter:
    def test_name(self) -> None:
        trough = _make_trough("my_trough")
        adapter = PLRTroughAdapter(trough)
        assert adapter.name == "my_trough"

    def test_geometry(self) -> None:
        trough = _make_trough()
        adapter = PLRTroughAdapter(trough)
        assert adapter.size_x == 127.76
        assert adapter.size_y == 85.48
        assert adapter.size_z == 40.0

    def test_max_volume(self) -> None:
        trough = _make_trough()
        adapter = PLRTroughAdapter(trough)
        assert adapter.max_volume == 190000.0

    def test_volume_initially_zero(self) -> None:
        trough = _make_trough()
        adapter = PLRTroughAdapter(trough)
        assert adapter.volume == 0.0

    def test_set_volume(self) -> None:
        trough = _make_trough()
        adapter = PLRTroughAdapter(trough)
        adapter.set_volume(50000.0)
        assert adapter.volume == 50000.0
        assert trough.tracker.volume == 50000.0

    def test_bidirectional_volume(self) -> None:
        trough = _make_trough()
        adapter = PLRTroughAdapter(trough)
        trough.tracker.set_volume(10000.0)
        assert adapter.volume == 10000.0


# --- Factory wrappers ---

class TestFactoryWrappers:
    def test_plr_plate_factory(self) -> None:
        def falcon_96(name: str, with_lid: object = None) -> Plate:
            return _make_96_well_plate(name)

        factory = plr_plate_factory(falcon_96)
        result = factory("my_plate")
        assert isinstance(result, PLRPlateAdapter)
        assert result.name == "my_plate"
        assert result.num_rows == 8
        assert result.num_cols == 12

    def test_plr_tip_rack_factory(self) -> None:
        def tip_factory(name: str, with_tips: bool) -> TipRack:
            return _make_tip_rack(name, with_tips)

        factory = plr_tip_rack_factory(tip_factory)
        result = factory("my_tips", True)
        assert isinstance(result, PLRTipRackAdapter)
        assert result.name == "my_tips"
        assert result.has_tips is True

    def test_plr_trough_factory(self) -> None:
        def trough_factory(name: str) -> Trough:
            return _make_trough(name)

        factory = plr_trough_factory(trough_factory)
        result = factory("my_trough")
        assert isinstance(result, PLRTroughAdapter)
        assert result.name == "my_trough"


# --- PLRLabwareConverter ---

class TestPLRLabwareConverterAdapterUnwrap:
    """Tests for the adapter-unwrap branch: unwrapping PLR adapters."""

    def test_plate_unwrap_returns_original(self) -> None:
        plr_plate = _make_96_well_plate("original")
        adapter = PLRPlateAdapter(plr_plate)
        converter = PLRLabwareConverter()
        result = converter.to_plr_plate(adapter)
        assert result is plr_plate

    def test_well_unwrap_returns_original(self) -> None:
        plr_plate = _make_96_well_plate()
        plr_well = plr_plate.get_well("A1")
        adapter = PLRWellAdapter(plr_well)
        converter = PLRLabwareConverter()
        result = converter.to_plr_well(adapter)
        assert result is plr_well

    def test_trough_unwrap_returns_original(self) -> None:
        plr_trough = _make_trough()
        adapter = PLRTroughAdapter(plr_trough)
        converter = PLRLabwareConverter()
        result = converter.to_plr_trough(adapter)
        assert result is plr_trough

    def test_tip_rack_unwrap_returns_original(self) -> None:
        plr_rack = _make_tip_rack()
        adapter = PLRTipRackAdapter(plr_rack)
        converter = PLRLabwareConverter()
        result = converter.to_plr_tip_rack(adapter)
        assert result is plr_rack

    def test_plate_unwrap_preserves_volume_state(self) -> None:
        plr_plate = _make_96_well_plate()
        plr_plate.get_well("A1").set_volume(100.0)
        adapter = PLRPlateAdapter(plr_plate)
        converter = PLRLabwareConverter()
        result = converter.to_plr_plate(adapter)
        assert result.get_well("A1").tracker.volume == 100.0

    def test_factory_wrapped_plate_unwraps_correctly(self) -> None:
        def falcon_96(name: str, with_lid: object = None) -> Plate:
            return _make_96_well_plate(name)

        factory = plr_plate_factory(falcon_96)
        adapted = factory("my_plate")
        converter = PLRLabwareConverter()
        plr_plate = converter.to_plr_plate(adapted)
        assert isinstance(plr_plate, Plate)
        assert plr_plate.name == "my_plate"


class TestPLRLabwareConverterGeometryConstruction:
    """Tests for the geometry-construction branch: building PLR objects from raw geometry."""

    def test_construct_plate_from_custom_iplate(self) -> None:
        """Non-PLR IPlate should be constructable into a PLR Plate."""

        class CustomWell:
            def __init__(self, identifier: str, row: int, col: int) -> None:
                self.identifier = identifier
                self.row = row
                self.col = col
                self.position_x = col * 9.0
                self.position_y = row * 9.0
                self.position_z = 0.0
                self.size_x = 9.0
                self.size_y = 9.0
                self.size_z = 17.0
                self.max_volume = 385.0
                self.volume = 0.0
                self.bottom_type = "flat"

            def set_volume(self, v: float) -> None:
                self.volume = v

        class CustomPlate:
            def __init__(self) -> None:
                self.name = "custom_plate"
                self.barcode = None
                self.num_rows = 2
                self.num_cols = 3
                self.size_x = 50.0
                self.size_y = 30.0
                self.size_z = 14.0
                self.has_lid = False
                self._wells: dict[str, CustomWell] = {}
                for r in range(2):
                    for c in range(3):
                        wid = f"{chr(65 + r)}{c + 1}"
                        self._wells[wid] = CustomWell(wid, r, c)

            def well(self, identifier: str) -> CustomWell:
                return self._wells[identifier]

            def wells(self, identifiers: list[str]) -> list[CustomWell]:
                return [self._wells[i] for i in identifiers]

        custom = CustomPlate()
        converter = PLRLabwareConverter()
        plr_plate = converter.to_plr_plate(custom)

        assert isinstance(plr_plate, Plate)
        assert plr_plate.name == "custom_plate"
        assert plr_plate.get_size_x() == 50.0
        # Wells should exist
        well_a1 = plr_plate.get_well("A1")
        assert well_a1.get_size_x() == 9.0
        assert well_a1.max_volume == 385.0

    def test_construct_plate_preserves_volumes(self) -> None:
        """Volumes set on custom IWell should be transferred to constructed PLR Plate."""

        class SimpleWell:
            def __init__(self, identifier: str, row: int, col: int) -> None:
                self.identifier = identifier
                self.row = row
                self.col = col
                self.position_x = 0.0
                self.position_y = 0.0
                self.position_z = 0.0
                self.size_x = 9.0
                self.size_y = 9.0
                self.size_z = 17.0
                self.max_volume = 300.0
                self.volume = 0.0
                self.bottom_type = "flat"

            def set_volume(self, v: float) -> None:
                self.volume = v

        class SimplePlate:
            name = "vol_test"
            model = None
            barcode = None
            num_rows = 1
            num_cols = 2
            size_x = 30.0
            size_y = 15.0
            size_z = 14.0
            has_lid = False

            def __init__(self) -> None:
                self._wells = {
                    "A1": SimpleWell("A1", 0, 0),
                    "A2": SimpleWell("A2", 0, 1),
                }
                self._wells["A1"].volume = 150.0
                self._wells["A2"].volume = 75.0

            def well(self, identifier: str) -> SimpleWell:
                return self._wells[identifier]

            def wells(self, identifiers: list[str]) -> list[SimpleWell]:
                return [self._wells[i] for i in identifiers]

        custom = SimplePlate()
        converter = PLRLabwareConverter()
        plr_plate = converter.to_plr_plate(custom)

        assert plr_plate.get_well("A1").tracker.volume == 150.0
        assert plr_plate.get_well("A2").tracker.volume == 75.0

    def test_construct_well_standalone(self) -> None:
        """Standalone IWell -> PLR Well construction."""

        class StandaloneWell:
            identifier = "X1"
            row = 0
            col = 0
            position_x = 0.0
            position_y = 0.0
            position_z = 0.0
            size_x = 6.0
            size_y = 6.0
            size_z = 12.0
            max_volume = 200.0
            volume = 50.0
            bottom_type = "U"

            def set_volume(self, v: float) -> None:
                self.volume = v

        converter = PLRLabwareConverter()
        plr_well = converter.to_plr_well(StandaloneWell())

        assert isinstance(plr_well, Well)
        assert plr_well.get_size_x() == 6.0
        assert plr_well.max_volume == 200.0
        assert plr_well.tracker.volume == 50.0
        assert plr_well.bottom_type == WellBottomType.U


class TestPLRLabwareConverterEdgeCases:
    def test_non_plr_trough_raises_with_actionable_message(self) -> None:
        converter = PLRLabwareConverter()
        with pytest.raises(NotImplementedError, match="PLRTroughAdapter") as exc_info:
            converter.to_plr_trough(object())
        # Message names the type the user passed, the V1 requirement, and how to fix.
        assert "object" in str(exc_info.value)
        assert "wrap" in str(exc_info.value).lower()

    def test_non_plr_tip_rack_raises_with_actionable_message(self) -> None:
        converter = PLRLabwareConverter()
        with pytest.raises(NotImplementedError, match="PLRTipRackAdapter") as exc_info:
            converter.to_plr_tip_rack(object())
        assert "object" in str(exc_info.value)
        assert "wrap" in str(exc_info.value).lower()

    def test_roundtrip_plate_adapter_to_plr_and_back(self) -> None:
        """PLR plate -> adapter -> converter -> same PLR plate. Full roundtrip."""
        original = _make_96_well_plate("roundtrip")
        original.get_well("D6").set_volume(123.0)

        adapter = PLRPlateAdapter(original)
        converter = PLRLabwareConverter()
        recovered = converter.to_plr_plate(adapter)

        assert recovered is original
        assert recovered.get_well("D6").tracker.volume == 123.0
        assert recovered.name == "roundtrip"

    def test_volume_sync_after_roundtrip(self) -> None:
        """After roundtrip, modifying through adapter still affects PLR plate."""
        original = _make_96_well_plate()
        adapter = PLRPlateAdapter(original)
        converter = PLRLabwareConverter()

        # Set volume through adapter
        adapter.well("A1").set_volume(50.0)
        # Roundtrip
        plr_plate = converter.to_plr_plate(adapter)
        assert plr_plate.get_well("A1").tracker.volume == 50.0

        # Modify through PLR, check adapter
        plr_plate.get_well("A1").set_volume(75.0)
        assert adapter.well("A1").volume == 75.0
