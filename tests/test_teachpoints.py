import pytest

from cheshire_drivers.teachpoints import (
    AccessConfig,
    CartesianCoordinates,
    InMemoryTeachpointStore,
    JointCoordinates,
    NullTeachpointStore,
    Teachpoint,
)


def _cartesian() -> CartesianCoordinates:
    return CartesianCoordinates(x=1.0, y=2.0, z=3.0, yaw=0.0, pitch=0.0, roll=0.0)


def _access_cfg(name: str = "my_cfg", access_type: str = "vertical") -> AccessConfig:
    return AccessConfig(
        name=name,
        access_type=access_type,
        gripper_offset=10.0,
        vertical_clearance=15.0,
        horizontal_clearance=50.0,
    )


class TestTeachpointAccessParam:
    def test_access_config_propagates_all_fields(self) -> None:
        cfg = _access_cfg()
        tp = Teachpoint(
            position_id="tp1",
            coordinates=_cartesian(),
            orientation="left",
            access=cfg,
        )
        assert tp.access_type == "vertical"
        assert tp.gripper_offset == 10.0
        assert tp.vertical_clearance == 15.0
        assert tp.horizontal_clearance == 50.0

    def test_access_config_sets_config_name(self) -> None:
        cfg = _access_cfg(name="stack_access")
        tp = Teachpoint(
            position_id="tp1",
            coordinates=_cartesian(),
            orientation="left",
            access=cfg,
        )
        assert tp._access_config_name == "stack_access"

    def test_raises_when_both_access_and_access_type(self) -> None:
        cfg = _access_cfg()
        with pytest.raises(ValueError, match="pass either 'access' or individual access params"):
            Teachpoint(
                position_id="tp1",
                coordinates=_cartesian(),
                orientation="left",
                access=cfg,
                access_type="horizontal",
            )

    def test_individual_params_still_work(self) -> None:
        tp = Teachpoint(
            position_id="tp1",
            coordinates=_cartesian(),
            orientation="left",
            access_type="horizontal",
            gripper_offset=5.0,
            vertical_clearance=8.0,
            horizontal_clearance=200.0,
        )
        assert tp.access_type == "horizontal"
        assert tp.gripper_offset == 5.0
        assert tp.vertical_clearance == 8.0
        assert tp.horizontal_clearance == 200.0
        assert tp._access_config_name is None

    def test_waypoint_without_access(self) -> None:
        tp = Teachpoint(
            position_id="waypoint",
            coordinates=JointCoordinates(base=1.0, shoulder=2.0, elbow=3.0, wrist=4.0),
        )
        assert tp.access_type is None
        assert tp._access_config_name is None
        assert tp.gripper_offset == 20.0
        assert tp.vertical_clearance == 20.0
        assert tp.horizontal_clearance == 100.0


class TestInMemoryTeachpointStoreResolve:
    """`resolve(name)` is the wire-source-of-truth lookup that returns a
    fully-flattened Teachpoint (access fields inlined). For
    `InMemoryTeachpointStore` it is semantically equivalent to `get` because
    the `Teachpoint` constructor inlines the access fields at construction
    time, but the explicit `resolve` name documents the wire contract that
    a gateway uses to materialize the teachpoint payload before dispatch.
    """

    @pytest.mark.asyncio
    async def test_resolve_returns_flattened_teachpoint(self) -> None:
        cfg = _access_cfg()
        tp = Teachpoint(
            position_id="tp1",
            coordinates=_cartesian(),
            orientation="left",
            access=cfg,
        )
        store = InMemoryTeachpointStore([tp])

        resolved = await store.resolve("tp1")

        assert resolved is not None
        assert resolved.position_id == "tp1"
        assert resolved.access_type == "vertical"
        assert resolved.gripper_offset == 10.0
        assert resolved.vertical_clearance == 15.0
        assert resolved.horizontal_clearance == 50.0

    @pytest.mark.asyncio
    async def test_resolve_unknown_name_returns_none(self) -> None:
        store = InMemoryTeachpointStore([])
        assert await store.resolve("missing") is None


class TestNullTeachpointStore:
    """`NullTeachpointStore` is bound on orca-client when no local seed
    exists -- the gateway resolves teachpoint names server-side and pushes
    fully-flattened `Teachpoint` values through `pick_at_coords` /
    `place_at_coords` / `move_to_coords`, so the wire-side driver never
    needs to call `resolve` / `get`. The store fails loudly if anything
    does try a name lookup, surfacing wire-protocol drift instead of
    silently returning None.
    """

    @pytest.mark.asyncio
    async def test_get_raises_loudly(self) -> None:
        store = NullTeachpointStore()
        with pytest.raises(RuntimeError, match="NullTeachpointStore"):
            await store.get("any")

    @pytest.mark.asyncio
    async def test_resolve_raises_loudly(self) -> None:
        store = NullTeachpointStore()
        with pytest.raises(RuntimeError, match="NullTeachpointStore"):
            await store.resolve("any")

    def test_list_sync_returns_empty(self) -> None:
        store = NullTeachpointStore()
        assert store.list_sync() == []
