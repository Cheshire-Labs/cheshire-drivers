"""Walk-style guards for the liquid-handler motion wire-validation contract.

Mirrors `test_transporter_capabilities.py` for the pipette-motion and gripper interfaces. Guards
two drift classes:

1. The `lh_motion_request_validation` request registry against the interfaces' actual abstract
   method signatures, in both directions.
2. The silent failure mode `response_lookup` invites: a readback that is never registered falls
   through to `EmptyCommandResponse`, so its payload is discarded behind a 200 OK with nothing
   raising. Every readback is asserted to resolve to its own typed model.
"""

import inspect

import pytest

from cheshire_drivers.command_responses import EmptyCommandResponse
from cheshire_drivers.gripper_models import (
    GripperPosition,
    GripperRotationResponse,
    JawWidthResponse,
)
from cheshire_drivers.interfaces import (
    IForceGripperJawDriver,
    IGantryParkingDriver,
    IGripperMotionDriver,
    IGripperPositionDriver,
    IGripperRotationDriver,
    IPipetteMotionDriver,
    IWidthGripperJawDriver,
)
from cheshire_drivers.lh_motion_request_validation import (
    FORCE_GRIPPER_JAW_RESPONSE_MODELS,
    GANTRY_PARKING_RESPONSE_MODELS,
    GRIPPER_MOTION_RESPONSE_MODELS,
    GRIPPER_POSITION_RESPONSE_MODELS,
    GRIPPER_ROTATION_RESPONSE_MODELS,
    LH_MOTION_REQUEST_MODELS,
    PIPETTE_MOTION_RESPONSE_MODELS,
    WIDTH_GRIPPER_JAW_RESPONSE_MODELS,
)
from cheshire_drivers.pipette_motion_models import ChannelPosition
from cheshire_drivers.response_lookup import lookup_response_model

MOTION_INTERFACES = [
    IPipetteMotionDriver,
    IGantryParkingDriver,
    IGripperMotionDriver,
    IGripperPositionDriver,
    IForceGripperJawDriver,
    IWidthGripperJawDriver,
    IGripperRotationDriver,
]

INTERFACE_RESPONSE_MAPS = [
    ("IPipetteMotion", IPipetteMotionDriver, PIPETTE_MOTION_RESPONSE_MODELS),
    ("IGantryParking", IGantryParkingDriver, GANTRY_PARKING_RESPONSE_MODELS),
    ("IGripperMotion", IGripperMotionDriver, GRIPPER_MOTION_RESPONSE_MODELS),
    ("IGripperPosition", IGripperPositionDriver, GRIPPER_POSITION_RESPONSE_MODELS),
    ("IForceGripperJaw", IForceGripperJawDriver, FORCE_GRIPPER_JAW_RESPONSE_MODELS),
    ("IWidthGripperJaw", IWidthGripperJawDriver, WIDTH_GRIPPER_JAW_RESPONSE_MODELS),
    ("IGripperRotation", IGripperRotationDriver, GRIPPER_ROTATION_RESPONSE_MODELS),
]

ALL_MOTION_COMMANDS = {m for iface in MOTION_INTERFACES for m in iface.__abstractmethods__}


class TestRequestRegistryMatchesTheInterfaces:
    def test_every_registered_command_is_abstract_on_a_motion_interface(self) -> None:
        for command in LH_MOTION_REQUEST_MODELS:
            assert command in ALL_MOTION_COMMANDS, (
                f"LH_MOTION_REQUEST_MODELS lists {command!r} but no motion interface declares it"
            )

    def test_every_abstract_motion_command_is_registered(self) -> None:
        """The reverse direction: a new interface method with no Request model would reach the
        driver unvalidated, which is how a wire-shape regression ships silently."""
        for command in ALL_MOTION_COMMANDS:
            assert command in LH_MOTION_REQUEST_MODELS, (
                f"{command!r} is abstract on a motion interface but has no Request model"
            )

    def test_registered_kwarg_name_matches_the_method_signature(self) -> None:
        for command, (kwarg_name, _model) in LH_MOTION_REQUEST_MODELS.items():
            method = next(
                getattr(iface, command) for iface in MOTION_INTERFACES if hasattr(iface, command)
            )
            params = [p for p in inspect.signature(method).parameters if p != "self"]
            assert kwarg_name in params, (
                f"LH_MOTION_REQUEST_MODELS[{command!r}] declares kwarg {kwarg_name!r} "
                f"but the signature has {params}"
            )


class TestResponseLookupResolvesEveryCommand:
    @pytest.mark.parametrize(
        "command,interface,expected",
        [
            ("get_channel_position", "IPipetteMotion", ChannelPosition),
            ("get_gripper_position", "IGripperPosition", GripperPosition),
            ("get_jaw_width", "IWidthGripperJaw", JawWidthResponse),
            ("get_gripper_rotation", "IGripperRotation", GripperRotationResponse),
        ],
    )
    def test_each_readback_resolves_to_its_typed_model(
        self, command: str, interface: str, expected: type
    ) -> None:
        """An unregistered readback silently returns EmptyCommandResponse and drops the payload,
        so each one is pinned to the model that actually carries its data."""
        assert lookup_response_model(command, frozenset({interface})) is expected

    def test_every_command_resolves_through_its_own_interface(self) -> None:
        for interface_name, _iface, models in INTERFACE_RESPONSE_MAPS:
            for command, model in models.items():
                assert lookup_response_model(command, frozenset({interface_name})) is model

    def test_response_maps_cover_exactly_their_interface(self) -> None:
        """A response map that drifts from its interface leaves a command unresolvable."""
        for interface_name, iface, models in INTERFACE_RESPONSE_MAPS:
            assert set(models) == set(iface.__abstractmethods__), (
                f"{interface_name} response map {sorted(models)} does not match its abstract "
                f"methods {sorted(iface.__abstractmethods__)}"
            )

    def test_a_command_does_not_resolve_through_an_unrelated_interface(self) -> None:
        """The interfaces are split by what the hardware exposes, so a force-controlled jaw must
        not resolve the width readback it has no way to perform."""
        assert (
            lookup_response_model("get_jaw_width", frozenset({"IForceGripperJaw"}))
            is EmptyCommandResponse
        )
