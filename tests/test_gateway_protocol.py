"""Tests for the shared device-gateway wire protocol."""

import json
import re

import pytest
from pydantic import ValidationError

from cheshire_drivers.gateway_protocol import (
    PROTOCOL_VERSION,
    CancelMessage,
    CommandMessage,
    ConnectMessage,
    DeviceConnectInfo,
    HeartbeatMessage,
    LabwareDefinitionDTO,
    MessageEnvelope,
    ResponseMessage,
    DeviceLinkInfo,
    DeviceStatusInfo,
    StatusMessage,
)

_OPEN = {"is_connected": True, "is_initialized": True}
_SHUT = {"is_connected": False, "is_initialized": False}


def _reported(links: dict[str, dict[str, bool]], status: str = "ready") -> DeviceStatusInfo:
    msg = StatusMessage.model_validate(
        {"devices": {"pf400_1": {"status": status, "links": links}}, "timestamp": 1.0}
    )
    return msg.devices["pf400_1"]


def test_protocol_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", PROTOCOL_VERSION)


def test_connect_message_round_trips_through_envelope() -> None:
    msg = ConnectMessage(
        site="boston",
        lab="molbio",
        devices=[
            DeviceConnectInfo(name="shaker_1", type="shaker", interfaces=frozenset({"IShaker"})),
        ],
    )
    env = MessageEnvelope.wrap_connect(msg)
    assert env.type == "connect"
    unwrapped = env.unwrap()
    assert isinstance(unwrapped, ConnectMessage)
    assert unwrapped.devices[0].name == "shaker_1"


def test_command_message_carries_labware() -> None:
    cmd = CommandMessage(
        command_id="cmd_1",
        device_name="lh_1",
        command="pick_up_tips",
        params={"channels": [1, 2]},
        effective_mode="LIVE",
        labware=LabwareDefinitionDTO(
            labware_type="HTF_L",
            display_name="Hamilton Filter Tips",
            category="tip_rack",
            vendor="Hamilton",
            plr_class_name="HTF_L",
            geometry={"num_rows": 8, "num_cols": 12},
            source="plr_seed",
        ),
    )
    env = MessageEnvelope.wrap_command(cmd)
    parsed = env.unwrap()
    assert isinstance(parsed, CommandMessage)
    assert parsed.labware is not None
    assert parsed.labware.plr_class_name == "HTF_L"


def test_command_message_back_compat_labware_unset() -> None:
    cmd = CommandMessage(
        command_id="cmd_2",
        device_name="shaker_1",
        command="shake",
        params={"speed": 500.0},
        effective_mode="LIVE",
    )
    assert cmd.labware is None


def test_cancel_message_round_trips_and_is_in_discriminator() -> None:
    cancel = CancelMessage(command_id="cmd_1", reason="operator aborted")
    env = MessageEnvelope.wrap_cancel(cancel)
    assert env.type == "cancel"
    unwrapped = env.unwrap()
    assert isinstance(unwrapped, CancelMessage)
    assert unwrapped.command_id == "cmd_1"
    assert unwrapped.reason == "operator aborted"


def test_cancel_message_reason_optional() -> None:
    cancel = CancelMessage(command_id="cmd_1")
    assert cancel.reason is None


def test_response_status_heartbeat_round_trip() -> None:
    resp = MessageEnvelope.wrap_response(
        ResponseMessage(command_id="cmd_1", success=True, result={"ok": 1})
    )
    assert isinstance(resp.unwrap(), ResponseMessage)

    status = MessageEnvelope.wrap_status(
        StatusMessage(
            devices={
                "shaker_1": DeviceStatusInfo(
                    status="ready",
                    links={
                        "LIVE": DeviceLinkInfo(
                            is_connected=True, is_initialized=True,
                        )
                    },
                )
            },
            timestamp=1699459200.0,
        )
    )
    assert isinstance(status.unwrap(), StatusMessage)

    hb = MessageEnvelope.wrap_heartbeat(HeartbeatMessage(timestamp=1.0, uptime=2.0))
    assert isinstance(hb.unwrap(), HeartbeatMessage)


def test_effective_mode_still_carries_the_mode_no_driver_answers_for() -> None:
    """PURE_SIM is a valid dispatch mode and an invalid link key. Deriving one
    vocabulary from the other must not collapse that distinction."""
    cmd = CommandMessage(
        command_id="cmd_1", device_name="d", command="shake", effective_mode="PURE_SIM",
    )
    assert cmd.effective_mode == "PURE_SIM"


def test_effective_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        CommandMessage(
            command_id="cmd_1",
            device_name="d",
            command="shake",
            effective_mode="BOGUS",  # type: ignore[arg-type]
        )


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        ResponseMessage(command_id="cmd_1", success=True, unexpected="x")  # type: ignore[call-arg]


def test_envelope_unwrap_rejects_unknown_type() -> None:
    env = MessageEnvelope.model_construct(type="bogus", payload={})
    with pytest.raises(ValueError):
        env.unwrap()


def test_wrapped_connect_payload_is_json_serializable() -> None:
    msg = ConnectMessage(
        site="boston",
        lab="molbio",
        devices=[
            DeviceConnectInfo(
                name="shaker_1",
                type="shaker",
                interfaces=frozenset({"IShaker"}),
                capabilities=frozenset({"set_temperature"}),
            ),
        ],
    )
    env = MessageEnvelope.wrap_connect(msg)

    # The full envelope must be directly json.dumps-able with no custom encoder.
    json.dumps(env.model_dump(mode="json"))
    serialized = json.dumps(env.payload)

    # frozenset fields must round-trip as JSON lists, not leak Python sets.
    reloaded = json.loads(serialized)
    device = reloaded["devices"][0]
    assert sorted(device["interfaces"]) == ["IShaker"]
    assert sorted(device["capabilities"]) == ["set_temperature"]


def test_connect_message_rejects_unknown_protocol_version() -> None:
    bad = {"protocol_version": "9.9.9", "site": "boston", "lab": "molbio", "devices": []}
    with pytest.raises(ValidationError):
        ConnectMessage.model_validate(bad)


def test_status_carries_each_device_link_state() -> None:
    """The agent holds the driver, so its report is what the server can trust."""
    msg = StatusMessage.model_validate(
        {
            "devices": {
                "pf400_1": {
                    "status": "ready",
                    "links": {"LIVE": {"is_connected": True, "is_initialized": False}},
                },
            },
            "timestamp": 1.0,
        }
    )
    link = msg.devices["pf400_1"].links["LIVE"]
    assert link.is_connected is True
    assert link.is_initialized is False


def test_status_rejects_a_device_reported_without_its_link_state() -> None:
    """A bare status string leaves the link unanswered; that must not parse."""
    with pytest.raises(ValidationError):
        StatusMessage.model_validate({"devices": {"pf400_1": "ready"}, "timestamp": 1.0})


def test_status_keeps_each_run_modes_link_separate() -> None:
    """A device holds one driver per mode and the server stamps the mode, so a
    single pair of flags would describe a driver that is not the one running."""
    msg = StatusMessage.model_validate(
        {
            "devices": {
                "flex_1": {
                    "status": "ready",
                    "links": {
                        "LIVE": {"is_connected": False, "is_initialized": False},
                        "DEVICE_SIM": {"is_connected": True, "is_initialized": True},
                    },
                },
            },
            "timestamp": 1.0,
        }
    )
    links = msg.devices["flex_1"].links
    assert links["DEVICE_SIM"].is_connected is True
    assert links["LIVE"].is_connected is False


def test_status_reports_no_link_for_a_mode_the_agent_holds_no_driver_for() -> None:
    """An Opentrons LH whose live robot the agent never builds must not be
    described by its simulator: a LIVE command to it fails."""
    msg = StatusMessage.model_validate(
        {
            "devices": {
                "flex_1": {
                    "status": "ready",
                    "links": {"DEVICE_SIM": {"is_connected": True, "is_initialized": True}},
                },
            },
            "timestamp": 1.0,
        }
    )
    assert "LIVE" not in msg.devices["flex_1"].links


def test_status_rejects_a_status_word_the_dispatch_lock_cannot_produce() -> None:
    """status reports the lock, so 'offline' alongside an open link must not parse."""
    with pytest.raises(ValidationError):
        StatusMessage.model_validate(
            {
                "devices": {
                    "pf400_1": {
                        "status": "offline",
                        "links": {"LIVE": {"is_connected": True, "is_initialized": True}},
                    },
                },
                "timestamp": 1.0,
            }
        )


def test_status_rejects_a_link_keyed_by_a_mode_that_never_reaches_the_agent() -> None:
    """PURE_SIM runs server-side, so no agent driver can answer for it."""
    with pytest.raises(ValidationError):
        StatusMessage.model_validate(
            {
                "devices": {
                    "pf400_1": {
                        "status": "ready",
                        "links": {"PURE_SIM": {"is_connected": True, "is_initialized": True}},
                    },
                },
                "timestamp": 1.0,
            }
        )


def test_the_observed_link_prefers_the_instrument_when_both_are_open() -> None:
    """A reader that is not dispatching gets the instrument's answer, not a
    simulator's, whenever the instrument has one."""
    observed = _reported({"LIVE": _OPEN, "DEVICE_SIM": _OPEN}).observed_link
    assert observed.mode == "LIVE"
    assert observed.is_connected is True


def test_the_observed_link_names_the_simulator_when_only_it_is_open() -> None:
    """A DEVICE_SIM bench answers every command, so it must not read as offline,
    and the mode has to travel with the flag or the operator reads a simulator
    as the instrument."""
    observed = _reported({"LIVE": _SHUT, "DEVICE_SIM": _OPEN}).observed_link
    assert observed.mode == "DEVICE_SIM"
    assert observed.is_connected is True


def test_the_observed_link_reports_the_instrument_closed_when_nothing_is_open() -> None:
    """Nothing is up, so the answer is the one that matters to an operator."""
    observed = _reported({"LIVE": _SHUT, "DEVICE_SIM": _SHUT}).observed_link
    assert observed.mode == "LIVE"
    assert observed.is_connected is False


def test_the_observed_link_answers_for_a_device_with_only_a_simulator() -> None:
    """An Opentrons LH whose live robot the agent never builds still has to
    answer a registry read rather than blow up on a missing LIVE key."""
    observed = _reported({"DEVICE_SIM": _OPEN}).observed_link
    assert observed.mode == "DEVICE_SIM"
    assert observed.is_connected is True


def test_a_report_cannot_claim_a_command_on_a_device_it_holds_no_driver_for() -> None:
    """An advertised device always has a driver, so an empty map is not a state
    the agent can be in, and busy on top of it is a flat contradiction."""
    with pytest.raises(ValidationError):
        StatusMessage.model_validate(
            {"devices": {"pf400_1": {"status": "busy", "links": {}}}, "timestamp": 1.0}
        )


def test_the_observed_link_answers_with_the_flags_of_the_mode_it_picked() -> None:
    """Nothing is open and only the simulator is brought up. Carrying LIVE's
    flags there reports a device as never initialized while the map says
    otherwise, and an operator who re-runs initialize sees nothing move."""
    observed = _reported(
        {"LIVE": _SHUT, "DEVICE_SIM": {"is_connected": False, "is_initialized": True}}
    ).observed_link
    assert observed.mode == "DEVICE_SIM"
    assert observed.is_connected is False
    assert observed.is_initialized is True


def test_a_parsed_report_cannot_be_reshaped_into_one_that_answers_nothing() -> None:
    """observed_link indexes the map, so emptying it after parsing would turn a
    registry read into an IndexError rather than a device that reads offline."""
    with pytest.raises(ValidationError):
        _reported({"LIVE": _OPEN}).links = {}


def test_the_observed_link_takes_an_open_link_over_a_merely_brought_up_one() -> None:
    """The two tiers ask different questions and can point at different modes.

    A driver can hold an open link before bring-up, and a link can drop while
    bring-up survives, so the rule has to say which wins. It is the link being
    asked about, so an open one outranks a brought-up one even when the
    brought-up one is the instrument.
    """
    observed = _reported(
        {
            "LIVE": {"is_connected": False, "is_initialized": True},
            "DEVICE_SIM": {"is_connected": True, "is_initialized": False},
        }
    ).observed_link
    assert observed.mode == "DEVICE_SIM"
    assert observed.is_connected is True
    assert observed.is_initialized is False
