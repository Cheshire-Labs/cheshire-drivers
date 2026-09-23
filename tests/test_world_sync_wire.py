"""Wire-shape tests for the world-sync transporter ops.

Pins down:
  * `seed_position` / `ensure_seeded` / `unseed_position` Request models
    survive `wrap_transporter_payload` round trips with `LabwareIdentity`
    intact.
  * `SimTransporterDriver` honors the wire ops via in-memory placeholder
    PLR resources, with the expected idempotency / conflict semantics.
  * `pick_at_coords(expected_labware=X)` raises a clear "wrong-plate"
    error when the position holds a different identity (per plan
    open-question 1).
"""

import pytest

from cheshire_drivers.move_parameters import SEED_MOVE_PARAMETERS
from cheshire_drivers.labware_models import LabwareIdentity
from cheshire_drivers.sims import (
    SimTransporterDriver,
    SimTransporterValidationError,
    SleepSim,
)
from cheshire_drivers.teachpoints import (
    CartesianCoordinates,
    Teachpoint,
)
from cheshire_drivers.transporter_models import (
    EnsureSeededRequest,
    PickAtCoordsRequest,
    PlaceAtCoordsRequest,
    ResetWorldRequest,
    SeedPositionRequest,
    UnseedPositionRequest,
)
from cheshire_drivers.transporter_request_validation import (
    wrap_transporter_payload,
)


def _identity(labware_id: str = "abc") -> LabwareIdentity:
    return LabwareIdentity(
        labware_id=labware_id,
        barcode=None,
        labware_type="sample_plate",
    )


def _teachpoint(name: str) -> Teachpoint:
    return Teachpoint(
        position_id=name,
        coordinates=CartesianCoordinates(x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0, roll=0.0),
        orientation="left",
        access_type="vertical",
        vertical_clearance=20.0,
    )


def _fast_driver(name: str = "sim_arm") -> SimTransporterDriver:
    return SimTransporterDriver(name=name, sim_strategy=SleepSim(sim_time=0.0))


class TestWireRoundTrip:
    def test_seed_position_round_trip(self) -> None:
        wire = {
            "position_id": "pad_1",
            "labware": _identity().model_dump(),
        }
        wrapped = wrap_transporter_payload("seed_position", wire)
        request = wrapped["request"]
        assert isinstance(request, SeedPositionRequest)
        assert request.position_id == "pad_1"
        assert request.labware.labware_id == "abc"

    def test_ensure_seeded_round_trip(self) -> None:
        wire = {
            "position_id": "pad_1",
            "labware": _identity().model_dump(),
        }
        wrapped = wrap_transporter_payload("ensure_seeded", wire)
        request = wrapped["request"]
        assert isinstance(request, EnsureSeededRequest)

    def test_unseed_position_round_trip(self) -> None:
        wire = {
            "position_id": "pad_1",
            "labware": _identity().model_dump(),
        }
        wrapped = wrap_transporter_payload("unseed_position", wire)
        request = wrapped["request"]
        assert isinstance(request, UnseedPositionRequest)

    def test_unknown_field_rejected(self) -> None:
        wire = {
            "position_id": "pad_1",
            "labware": _identity().model_dump(),
            "rogue": "x",
        }
        with pytest.raises(ValueError):
            wrap_transporter_payload("seed_position", wire)


class TestSimDriverWireSeedSemantics:
    @pytest.mark.asyncio
    async def test_seed_then_pick_succeeds(self) -> None:
        driver = _fast_driver()
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_1", labware=_identity("plate_x"))
        )
        await driver.pick_at_coords(
            PickAtCoordsRequest(
                teachpoint=_teachpoint("pad_1"),
                labware_type="sample_plate",
                expected_labware=_identity("plate_x"),
                handling=SEED_MOVE_PARAMETERS,
            )
        )

    @pytest.mark.asyncio
    async def test_seed_twice_raises(self) -> None:
        driver = _fast_driver()
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_1", labware=_identity("plate_x"))
        )
        with pytest.raises(SimTransporterValidationError, match="already occupied"):
            await driver.seed_position(
                SeedPositionRequest(position_id="pad_1", labware=_identity("plate_y"))
            )

    @pytest.mark.asyncio
    async def test_ensure_seeded_idempotent(self) -> None:
        driver = _fast_driver()
        identity = _identity("plate_x")
        await driver.ensure_seeded(
            EnsureSeededRequest(position_id="pad_1", labware=identity)
        )
        await driver.ensure_seeded(
            EnsureSeededRequest(position_id="pad_1", labware=identity)
        )

    @pytest.mark.asyncio
    async def test_ensure_seeded_reconciles_stale_occupant(self) -> None:
        """The engine ledger is the single source of truth; the sim graph obeys.

        A different occupant at the position means the sim graph holds a stale
        seed (e.g. a prior execution that failed or aborted without unseeding).
        `ensure_seeded` reconciles to the engine: evict the orphan and seed the
        authoritative labware, instead of vetoing the engine's instruction and
        wedging every future submission.
        """
        driver = _fast_driver()
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_1", labware=_identity("stale_plate"))
        )
        await driver.ensure_seeded(
            EnsureSeededRequest(position_id="pad_1", labware=_identity("fresh_plate"))
        )
        assert driver._resource_at("pad_1") is driver._resources_by_id["fresh_plate"]
        assert "stale_plate" not in driver._resources_by_id
        await driver.pick_at_coords(
            PickAtCoordsRequest(
                teachpoint=_teachpoint("pad_1"),
                labware_type="sample_plate",
                expected_labware=_identity("fresh_plate"),
                handling=SEED_MOVE_PARAMETERS,
            )
        )

    @pytest.mark.asyncio
    async def test_ensure_seeded_takes_the_labware_out_of_its_own_gripper(self) -> None:
        """An operator can lift a plate out of the jaws after a failed place and
        record where they put it. The ledger then says the plate is at a
        position while this driver still has it in the gripper, and the
        projection obeys the ledger rather than its own stale grip: the plate
        moves to the position, and the next pick is not refused as
        already-holding.
        """
        driver = _fast_driver()
        identity = _identity("plate_x")
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_1", labware=identity)
        )
        await driver.pick_at_coords(
            PickAtCoordsRequest(
                teachpoint=_teachpoint("pad_1"),
                labware_type="sample_plate",
                expected_labware=identity,
                handling=SEED_MOVE_PARAMETERS,
            )
        )
        assert driver._gripper_payload() is not None

        await driver.ensure_seeded(
            EnsureSeededRequest(position_id="pad_2", labware=identity)
        )

        assert driver._gripper_payload() is None
        assert driver._resource_at("pad_2") is driver._resources_by_id["plate_x"]
        await driver.pick_at_coords(
            PickAtCoordsRequest(
                teachpoint=_teachpoint("pad_2"),
                labware_type="sample_plate",
                expected_labware=identity,
                handling=SEED_MOVE_PARAMETERS,
            )
        )

    @pytest.mark.asyncio
    async def test_unseed_then_pick_fails(self) -> None:
        driver = _fast_driver()
        identity = _identity("plate_x")
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_1", labware=identity)
        )
        await driver.unseed_position(
            UnseedPositionRequest(position_id="pad_1", labware=identity)
        )
        with pytest.raises(SimTransporterValidationError, match="empty"):
            await driver.pick_at_coords(
                PickAtCoordsRequest(teachpoint=_teachpoint("pad_1"), labware_type="p", handling=SEED_MOVE_PARAMETERS)
            )

    @pytest.mark.asyncio
    async def test_unseed_unknown_position_is_noop(self) -> None:
        driver = _fast_driver()
        await driver.unseed_position(
            UnseedPositionRequest(position_id="pad_1", labware=_identity("never_seeded"))
        )


class TestExpectedLabwareCrossCheck:
    @pytest.mark.asyncio
    async def test_pick_with_matching_identity_succeeds(self) -> None:
        driver = _fast_driver()
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_1", labware=_identity("plate_x"))
        )
        await driver.pick_at_coords(
            PickAtCoordsRequest(
                teachpoint=_teachpoint("pad_1"),
                labware_type="sample_plate",
                expected_labware=_identity("plate_x"),
                handling=SEED_MOVE_PARAMETERS,
            )
        )

    @pytest.mark.asyncio
    async def test_pick_with_wrong_identity_raises(self) -> None:
        driver = _fast_driver()
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_1", labware=_identity("plate_x"))
        )
        with pytest.raises(SimTransporterValidationError, match="expected labware id"):
            await driver.pick_at_coords(
                PickAtCoordsRequest(
                    teachpoint=_teachpoint("pad_1"),
                    labware_type="sample_plate",
                    expected_labware=_identity("plate_y"),
                    handling=SEED_MOVE_PARAMETERS,
                )
            )

    @pytest.mark.asyncio
    async def test_pick_without_expected_labware_skips_check(self) -> None:
        """Older callers that do not yet thread `expected_labware` through
        keep working: identity check is skipped when the field is None."""
        driver = _fast_driver()
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_1", labware=_identity("plate_x"))
        )
        await driver.pick_at_coords(
            PickAtCoordsRequest(
                teachpoint=_teachpoint("pad_1"),
                labware_type="sample_plate",
                expected_labware=None,
                handling=SEED_MOVE_PARAMETERS,
            )
        )

    @pytest.mark.asyncio
    async def test_place_with_wrong_identity_raises(self) -> None:
        """Mirror of the pick-side cross-check: a misauthored caller passing
        the wrong identity to place surfaces at the place site, not at the
        next pick (which would look like world drift)."""
        driver = _fast_driver()
        seeded = _identity("plate_x")
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_1", labware=seeded)
        )
        await driver.pick_at_coords(
            PickAtCoordsRequest(
                teachpoint=_teachpoint("pad_1"),
                labware_type="sample_plate",
                expected_labware=seeded,
                handling=SEED_MOVE_PARAMETERS,
            )
        )
        # Gripper now holds plate_x. Asserting plate_y on place must raise.
        with pytest.raises(SimTransporterValidationError, match="expected labware id"):
            await driver.place_at_coords(
                PlaceAtCoordsRequest(
                    teachpoint=_teachpoint("pad_2"),
                    labware_type="sample_plate",
                    expected_labware=_identity("plate_y"),
                    handling=SEED_MOVE_PARAMETERS,
                )
            )

    @pytest.mark.asyncio
    async def test_place_without_expected_labware_skips_check(self) -> None:
        """`None` keeps the prior behaviour for callers that haven't yet
        threaded identity through."""
        driver = _fast_driver()
        identity = _identity("plate_x")
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_1", labware=identity)
        )
        await driver.pick_at_coords(
            PickAtCoordsRequest(
                teachpoint=_teachpoint("pad_1"),
                labware_type="sample_plate",
                expected_labware=identity,
                handling=SEED_MOVE_PARAMETERS,
            )
        )
        await driver.place_at_coords(
            PlaceAtCoordsRequest(
                teachpoint=_teachpoint("pad_2"),
                labware_type="sample_plate",
                expected_labware=None,
                handling=SEED_MOVE_PARAMETERS,
            )
        )

    @pytest.mark.asyncio
    async def test_seed_then_place_via_wire(self) -> None:
        """Pick from one position with identity, place at another, re-seeded
        graph stays consistent for subsequent operations."""
        driver = _fast_driver()
        identity = _identity("plate_x")
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_1", labware=identity)
        )
        await driver.pick_at_coords(
            PickAtCoordsRequest(
                teachpoint=_teachpoint("pad_1"),
                labware_type="sample_plate",
                expected_labware=identity,
                handling=SEED_MOVE_PARAMETERS,
            )
        )
        await driver.place_at_coords(
            PlaceAtCoordsRequest(
                teachpoint=_teachpoint("pad_2"),
                labware_type="sample_plate",
                expected_labware=identity,
                handling=SEED_MOVE_PARAMETERS,
            )
        )
        # Subsequent pick from pad_2 should now find the plate.
        await driver.pick_at_coords(
            PickAtCoordsRequest(
                teachpoint=_teachpoint("pad_2"),
                labware_type="sample_plate",
                expected_labware=identity,
                handling=SEED_MOVE_PARAMETERS,
            )
        )


class TestResetWorld:
    """`reset_world` is the authoritative blanket wipe the engine's
    `clear_all_labware` invokes: it clears every seeded position, the
    gripper, and the identity cache, reaching orphaned seeds that
    per-labware `unseed_position` cannot (the engine no longer knows
    their identities)."""

    def test_reset_world_round_trip(self) -> None:
        wrapped = wrap_transporter_payload("reset_world", {})
        assert isinstance(wrapped["request"], ResetWorldRequest)

    def test_reset_world_rejects_unknown_field(self) -> None:
        with pytest.raises(ValueError):
            wrap_transporter_payload("reset_world", {"rogue": "x"})

    @pytest.mark.asyncio
    async def test_reset_world_wipes_every_seed_and_the_gripper(self) -> None:
        driver = _fast_driver()
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_1", labware=_identity("plate_x"))
        )
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_2", labware=_identity("plate_y"))
        )
        # Put a third plate into the gripper (seed then pick).
        await driver.seed_position(
            SeedPositionRequest(position_id="pad_3", labware=_identity("plate_z"))
        )
        await driver.pick_at_coords(
            PickAtCoordsRequest(
                teachpoint=_teachpoint("pad_3"),
                labware_type="sample_plate",
                expected_labware=_identity("plate_z"),
                handling=SEED_MOVE_PARAMETERS,
            )
        )
        assert driver._gripper_payload() is not None

        await driver.reset_world(ResetWorldRequest())

        assert driver._resource_at("pad_1") is None
        assert driver._resource_at("pad_2") is None
        assert driver._gripper_payload() is None
        assert driver._resources_by_id == {}

    @pytest.mark.asyncio
    async def test_reset_world_unwedges_a_stale_orphan(self) -> None:
        """The operator-visible behavior: an orphaned seed blocks reseeding a
        position; after reset_world the position is free and a fresh seed +
        pick succeeds."""
        driver = _fast_driver()
        await driver.seed_position(
            SeedPositionRequest(position_id="mlstar_1", labware=_identity("stale"))
        )
        await driver.reset_world(ResetWorldRequest())
        # Position is clean: a fresh seed succeeds (no "already occupied").
        await driver.seed_position(
            SeedPositionRequest(position_id="mlstar_1", labware=_identity("fresh"))
        )
        await driver.pick_at_coords(
            PickAtCoordsRequest(
                teachpoint=_teachpoint("mlstar_1"),
                labware_type="sample_plate",
                expected_labware=_identity("fresh"),
                handling=SEED_MOVE_PARAMETERS,
            )
        )

    @pytest.mark.asyncio
    async def test_reset_world_idempotent_on_empty_graph(self) -> None:
        driver = _fast_driver()
        await driver.reset_world(ResetWorldRequest())
        await driver.reset_world(ResetWorldRequest())
