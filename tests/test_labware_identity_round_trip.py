"""Wire-shape round-trip tests for `LabwareIdentity` on transporter requests.

These tests pin down the wire contract: a Pick/Place request constructed with
a non-None ``expected_labware`` survives the gateway `_coords_payload` ->
orca-client `wrap_transporter_payload` round trip with identity intact.
Without this guarantee orca-client would silently drop or mangle the field.
"""

import pytest

from cheshire_drivers.move_parameters import SEED_MOVE_PARAMETERS
from cheshire_drivers.labware_models import LabwareIdentity
from cheshire_drivers.teachpoints import (
    CartesianCoordinates,
    Teachpoint,
)
from cheshire_drivers.transporter_models import (
    PickAtCoordsRequest,
    PlaceAtCoordsRequest,
)
from cheshire_drivers.transporter_request_validation import (
    wrap_transporter_payload,
)


def _teachpoint(name: str) -> Teachpoint:
    return Teachpoint(
        position_id=name,
        coordinates=CartesianCoordinates(x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0, roll=0.0),
        orientation="left",
        access_type="vertical",
        vertical_clearance=20.0,
    )


def _identity() -> LabwareIdentity:
    return LabwareIdentity(
        labware_id="abc123def456",
        barcode="BC-001",
        labware_type="sample_plate",
    )


def _wire_dict_pick(
    teachpoint: Teachpoint,
    labware_type: str = "sample_plate",
    expected_labware: LabwareIdentity | None = None,
) -> dict[str, object]:
    """Mirror the gateway `RemoteTransporterDriver._coords_payload` shape."""
    payload: dict[str, object] = {
        "teachpoint": teachpoint.to_dict(),
        "labware_type": labware_type,
        "gateway_path": [],
        "handling": SEED_MOVE_PARAMETERS.model_dump(),
    }
    if expected_labware is not None:
        payload["expected_labware"] = expected_labware.model_dump()
    else:
        payload["expected_labware"] = None
    return payload


class TestLabwareIdentityModel:
    def test_full_fields(self) -> None:
        identity = _identity()
        assert identity.labware_id == "abc123def456"
        assert identity.barcode == "BC-001"
        assert identity.labware_type == "sample_plate"

    def test_barcode_optional(self) -> None:
        identity = LabwareIdentity(labware_id="abc", labware_type="tip_rack")
        assert identity.barcode is None

    def test_labware_id_required(self) -> None:
        with pytest.raises(ValueError):
            LabwareIdentity(labware_type="sample_plate")  # type: ignore[call-arg]

    def test_labware_type_required(self) -> None:
        with pytest.raises(ValueError):
            LabwareIdentity(labware_id="abc")  # type: ignore[call-arg]

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValueError):
            LabwareIdentity.model_validate(
                {"labware_id": "abc", "labware_type": "p", "extra": "x"}
            )

    def test_round_trip_via_model_dump(self) -> None:
        identity = _identity()
        wire = identity.model_dump()
        rebuilt = LabwareIdentity.model_validate(wire)
        assert rebuilt == identity


class TestPickAtCoordsRequestExpectedLabware:
    def test_default_is_none(self) -> None:
        request = PickAtCoordsRequest(teachpoint=_teachpoint("pad_1"), handling=SEED_MOVE_PARAMETERS)
        assert request.expected_labware is None

    def test_carries_identity(self) -> None:
        request = PickAtCoordsRequest(
            teachpoint=_teachpoint("pad_1"),
            expected_labware=_identity(),
            handling=SEED_MOVE_PARAMETERS,
        )
        assert request.expected_labware is not None
        assert request.expected_labware.labware_id == "abc123def456"

    def test_wire_round_trip_with_identity(self) -> None:
        wire = _wire_dict_pick(_teachpoint("pad_1"), expected_labware=_identity())
        wrapped = wrap_transporter_payload("pick_at_coords", wire)
        request = wrapped["request"]
        assert isinstance(request, PickAtCoordsRequest)
        assert request.expected_labware == _identity()
        assert request.teachpoint.position_id == "pad_1"

    def test_wire_round_trip_without_identity(self) -> None:
        wire = _wire_dict_pick(_teachpoint("pad_1"), expected_labware=None)
        wrapped = wrap_transporter_payload("pick_at_coords", wire)
        request = wrapped["request"]
        assert isinstance(request, PickAtCoordsRequest)
        assert request.expected_labware is None

    def test_wire_round_trip_omitted_field_back_compat(self) -> None:
        """A payload that names no expected labware still validates."""
        wire = {
            "teachpoint": _teachpoint("pad_1").to_dict(),
            "labware_type": "sample_plate",
            "gateway_path": [],
            "handling": SEED_MOVE_PARAMETERS.model_dump(),
        }
        wrapped = wrap_transporter_payload("pick_at_coords", wire)
        request = wrapped["request"]
        assert isinstance(request, PickAtCoordsRequest)
        assert request.expected_labware is None

    def test_unknown_field_still_rejected(self) -> None:
        wire = {
            "teachpoint": _teachpoint("pad_1").to_dict(),
            "labware_type": "sample_plate",
            "gateway_path": [],
            "expected_labware": None,
            "rogue": "x",
        }
        with pytest.raises(ValueError):
            wrap_transporter_payload("pick_at_coords", wire)


class TestPlaceAtCoordsRequestExpectedLabware:
    def test_default_is_none(self) -> None:
        request = PlaceAtCoordsRequest(teachpoint=_teachpoint("pad_2"), handling=SEED_MOVE_PARAMETERS)
        assert request.expected_labware is None

    def test_wire_round_trip_with_identity(self) -> None:
        wire = _wire_dict_pick(_teachpoint("pad_2"), expected_labware=_identity())
        wrapped = wrap_transporter_payload("place_at_coords", wire)
        request = wrapped["request"]
        assert isinstance(request, PlaceAtCoordsRequest)
        assert request.expected_labware == _identity()

    def test_wire_round_trip_omitted_field_back_compat(self) -> None:
        wire = {
            "teachpoint": _teachpoint("pad_2").to_dict(),
            "labware_type": "sample_plate",
            "gateway_path": [],
            "handling": SEED_MOVE_PARAMETERS.model_dump(),
        }
        wrapped = wrap_transporter_payload("place_at_coords", wire)
        request = wrapped["request"]
        assert isinstance(request, PlaceAtCoordsRequest)
        assert request.expected_labware is None
