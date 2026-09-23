"""Tests for per-channel partial-failure attribution on liquid handler ops.

Covers three layers:
1. ``LabwareStateResponse.per_channel_errors`` Pydantic round-trip.
2. Channel-to-well mapping helpers (default and sparse ``use_channels``).
3. Sim-driver ``PartialFault`` injection -- the sim's aspirate/dispense
   returns a partial-failure response when a fault is armed.

The PLR-wrapper catch-and-translate path (Hamilton ``ChannelizedError`` ->
``LabwareStateResponse``) is exercised through the LH device wrapper by the
engine's own tests; it is not separately unit-tested here
because PLR's STAR backend cannot be probed without the real instrument.
"""

import pytest

from cheshire_drivers import SimLiquidHandlerDriver, SleepSim
from cheshire_drivers.faults import PartialFault, RaiseFault
from cheshire_drivers.liquid_handler_models import (
    AspirateRequest,
    AspirateTarget,
    ChannelError,
    DispenseRequest,
    DispenseTarget,
    LabwareStateResponse,
    build_aspirate_partial_failure,
    build_dispense_partial_failure,
    channel_to_aspirate_well,
    channel_to_dispense_well,
)


def _fast_sim() -> SleepSim:
    return SleepSim(sim_time=0.0)


def _make_aspirate(
    positions: list[str], volumes: list[float], use_channels: list[int] | None = None,
) -> AspirateRequest:
    return AspirateRequest(
        aspirations=[AspirateTarget(labware="plate_1", positions=positions, volumes=volumes)],
        use_channels=use_channels,
    )


class TestLabwareStateResponseModel:

    def test_default_per_channel_errors_is_empty(self) -> None:
        response = LabwareStateResponse(success=True)
        assert response.per_channel_errors == []

    def test_round_trip_with_per_channel_errors(self) -> None:
        original = LabwareStateResponse(
            success=False,
            per_channel_errors=[
                ChannelError(
                    channel_id=2,
                    well_position="A3",
                    labware="plate_1",
                    attempted_volume=100.0,
                    error_code="HamiltonError123",
                    error_message="aspirate failure on channel 2",
                ),
            ],
        )
        wire = original.model_dump()
        roundtripped = LabwareStateResponse.model_validate(wire)
        assert roundtripped == original

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(Exception):
            LabwareStateResponse.model_validate({
                "success": False,
                "per_channel_errors": [],
                "unknown_field": True,
            })


class TestChannelToAspirateWell:

    def test_default_use_channels_maps_one_to_one(self) -> None:
        request = _make_aspirate(["A1", "A2", "A3"], [10.0, 20.0, 30.0])
        assert channel_to_aspirate_well(request, 0) == ("plate_1", "A1", 10.0)
        assert channel_to_aspirate_well(request, 2) == ("plate_1", "A3", 30.0)

    def test_sparse_use_channels(self) -> None:
        """Channel 4 in use_channels=[0,2,4,6] is the 3rd well in the flat list."""
        request = _make_aspirate(
            ["A1", "A2", "A3", "A4"], [10.0, 20.0, 30.0, 40.0],
            use_channels=[0, 2, 4, 6],
        )
        assert channel_to_aspirate_well(request, 0) == ("plate_1", "A1", 10.0)
        assert channel_to_aspirate_well(request, 2) == ("plate_1", "A2", 20.0)
        assert channel_to_aspirate_well(request, 4) == ("plate_1", "A3", 30.0)
        assert channel_to_aspirate_well(request, 6) == ("plate_1", "A4", 40.0)

    def test_channel_not_in_use_channels_returns_none(self) -> None:
        request = _make_aspirate(["A1", "A2"], [10.0, 20.0], use_channels=[0, 2])
        assert channel_to_aspirate_well(request, 1) is None
        assert channel_to_aspirate_well(request, 7) is None

    def test_multi_plate_aspiration(self) -> None:
        request = AspirateRequest(
            aspirations=[
                AspirateTarget(labware="plate_a", positions=["A1", "A2"], volumes=[10.0, 20.0]),
                AspirateTarget(labware="plate_b", positions=["B1"], volumes=[30.0]),
            ],
        )
        assert channel_to_aspirate_well(request, 0) == ("plate_a", "A1", 10.0)
        assert channel_to_aspirate_well(request, 1) == ("plate_a", "A2", 20.0)
        assert channel_to_aspirate_well(request, 2) == ("plate_b", "B1", 30.0)


class TestChannelToDispenseWell:

    def test_default_use_channels(self) -> None:
        request = DispenseRequest(
            dispenses=[DispenseTarget(labware="plate_1", positions=["A1", "A2"], volumes=[10.0, 20.0])],
        )
        assert channel_to_dispense_well(request, 0) == ("plate_1", "A1", 10.0)
        assert channel_to_dispense_well(request, 1) == ("plate_1", "A2", 20.0)

    def test_sparse_use_channels(self) -> None:
        request = DispenseRequest(
            dispenses=[DispenseTarget(labware="plate_1", positions=["A1", "A2"], volumes=[10.0, 20.0])],
            use_channels=[3, 5],
        )
        assert channel_to_dispense_well(request, 3) == ("plate_1", "A1", 10.0)
        assert channel_to_dispense_well(request, 5) == ("plate_1", "A2", 20.0)
        assert channel_to_dispense_well(request, 4) is None


class TestBuildAspiratePartialFailure:

    def test_one_failed_channel(self) -> None:
        request = _make_aspirate(["A1", "A2", "A3"], [10.0, 20.0, 30.0])
        response = build_aspirate_partial_failure(
            request,
            {1: ("HamiltonE100", "channel jammed")},
        )
        assert response.success is False
        assert len(response.per_channel_errors) == 1
        err = response.per_channel_errors[0]
        assert err.channel_id == 1
        assert err.well_position == "A2"
        assert err.labware == "plate_1"
        assert err.attempted_volume == 20.0
        assert err.error_code == "HamiltonE100"
        assert err.error_message == "channel jammed"

    def test_unused_channel_skipped(self) -> None:
        request = _make_aspirate(["A1", "A2"], [10.0, 20.0], use_channels=[0, 2])
        response = build_aspirate_partial_failure(
            request,
            {1: ("E", "m"), 0: ("E", "m")},
        )
        assert len(response.per_channel_errors) == 1
        assert response.per_channel_errors[0].channel_id == 0


class TestSimLiquidHandlerPartialFault:

    @pytest.mark.asyncio
    async def test_no_fault_returns_success(self) -> None:
        driver = SimLiquidHandlerDriver("lh", sim_strategy=_fast_sim())
        request = _make_aspirate(["A1", "A2"], [100.0, 100.0])
        response = await driver.aspirate(request)
        assert response.success is True
        assert response.per_channel_errors == []

    @pytest.mark.asyncio
    async def test_partial_fault_on_aspirate_emits_per_channel_response(self) -> None:
        driver = SimLiquidHandlerDriver(
            "lh",
            sim_strategy=_fast_sim(),
            faults=[PartialFault(
                method="aspirate",
                failed_channels=[2],
                error_code="FW_E100",
                error_message="injected for test",
            )],
        )
        request = AspirateRequest(
            aspirations=[
                AspirateTarget(
                    labware="plate_1",
                    positions=["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"],
                    volumes=[100.0] * 8,
                ),
            ],
        )
        response = await driver.aspirate(request)

        assert response.success is False
        assert len(response.per_channel_errors) == 1
        err = response.per_channel_errors[0]
        assert err.channel_id == 2
        assert err.well_position == "A3"
        assert err.attempted_volume == 100.0
        assert err.error_code == "FW_E100"
        assert err.error_message == "injected for test"

    @pytest.mark.asyncio
    async def test_partial_fault_only_fires_on_specified_calls(self) -> None:
        driver = SimLiquidHandlerDriver(
            "lh",
            sim_strategy=_fast_sim(),
            faults=[PartialFault(
                method="aspirate",
                failed_channels=[0],
                on_calls=[2],
            )],
        )
        request = _make_aspirate(["A1"], [100.0])

        ok = await driver.aspirate(request)
        assert ok.success is True

        partial = await driver.aspirate(request)
        assert partial.success is False
        assert len(partial.per_channel_errors) == 1

        ok_again = await driver.aspirate(request)
        assert ok_again.success is True

    @pytest.mark.asyncio
    async def test_partial_fault_on_dispense_uses_sparse_use_channels(self) -> None:
        driver = SimLiquidHandlerDriver(
            "lh",
            sim_strategy=_fast_sim(),
            faults=[PartialFault(
                method="dispense",
                failed_channels=[5],
                error_code="FW_E200",
                error_message="dispense fault",
            )],
        )
        request = DispenseRequest(
            dispenses=[DispenseTarget(labware="plate_1", positions=["A1", "A2"], volumes=[50.0, 50.0])],
            use_channels=[3, 5],
        )
        response = await driver.dispense(request)

        assert response.success is False
        assert len(response.per_channel_errors) == 1
        err = response.per_channel_errors[0]
        assert err.channel_id == 5
        assert err.well_position == "A2"

    @pytest.mark.asyncio
    async def test_partial_fault_targeting_unfaultable_method_rejected_at_boot(self) -> None:
        with pytest.raises(ValueError, match="not a faultable method"):
            SimLiquidHandlerDriver(
                "lh",
                sim_strategy=_fast_sim(),
                faults=[PartialFault(method="not_a_method", failed_channels=[0])],
            )

    @pytest.mark.asyncio
    async def test_partial_and_raise_faults_compose(self) -> None:
        """A RaiseFault on call 1 and a PartialFault on call 2 fire independently."""
        driver = SimLiquidHandlerDriver(
            "lh",
            sim_strategy=_fast_sim(),
            faults=[
                RaiseFault(method="aspirate", error_type="RuntimeError", on_calls=[1]),
                PartialFault(method="aspirate", failed_channels=[1], on_calls=[2]),
            ],
        )
        request = _make_aspirate(["A1", "A2"], [100.0, 100.0])

        with pytest.raises(RuntimeError):
            await driver.aspirate(request)

        partial = await driver.aspirate(request)
        assert partial.success is False
        assert len(partial.per_channel_errors) == 1
        assert partial.per_channel_errors[0].channel_id == 1

    @pytest.mark.asyncio
    async def test_non_channelized_exception_propagates(self) -> None:
        """Opaque exceptions (RaiseFault) still propagate unchanged.

        Pins the wrapper invariant: only ChannelizedError is translated to a
        partial-failure response. Other exception types -- whether from PLR
        directly or from a fault injection -- propagate so the caller can
        ERROR the action without losing the underlying reason.
        """
        driver = SimLiquidHandlerDriver(
            "lh",
            sim_strategy=_fast_sim(),
            faults=[RaiseFault(
                method="aspirate", error_type="RuntimeError",
                message="opaque", on_calls=[1],
            )],
        )
        request = _make_aspirate(["A1"], [100.0])
        with pytest.raises(RuntimeError, match="opaque"):
            await driver.aspirate(request)
