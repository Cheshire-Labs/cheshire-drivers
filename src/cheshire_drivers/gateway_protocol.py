"""WebSocket wire protocol between an on-prem device agent and its control plane.

This is the single source of truth for the device-gateway message format. Both
sides of the websocket import it from here: the on-prem agent that owns the real
drivers, and the server (control plane) that dispatches commands to them. Keeping
one definition in this shared package removes the per-repo copy that previously had
to be kept byte-compatible by hand.
"""

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from typing import Literal, Optional

from cheshire_drivers.driver_errors import InstrumentOutcome
from cheshire_drivers.driver_introspection import MethodInfo


# Protocol version for compatibility checking. Pinned as a Literal so a peer that
# announces any other version fails ConnectMessage validation at the handshake.
ProtocolVersion = Literal["1.3.0"]
PROTOCOL_VERSION: ProtocolVersion = "1.3.0"

# The run modes that reach the agent. PURE_SIM is not one of them: it runs
# entirely in-process server-side, so the agent holds no driver for it.
DriverMode = Literal["LIVE", "DEVICE_SIM"]
EffectiveMode = Literal[DriverMode, "PURE_SIM"]

# LIVE first: an observer needs the instrument's answer ahead of a simulator's.
_OBSERVER_PRIORITY: tuple[DriverMode, ...] = ("LIVE", "DEVICE_SIM")


__all__ = [
    "PROTOCOL_VERSION",
    "InstrumentOutcome",
    "ProtocolVersion",
    "MethodInfo",
    "DeviceConnectInfo",
    "LabwareDefinitionDTO",
    "ConnectMessage",
    "CommandMessage",
    "CancelMessage",
    "ResponseMessage",
    "DriverMode",
    "EffectiveMode",
    "DeviceLinkInfo",
    "ObservedLink",
    "DeviceStatusInfo",
    "StatusMessage",
    "HeartbeatMessage",
    "MessageEnvelope",
]


class LabwareDefinitionDTO(BaseModel):
    """Wire shape for a single labware-catalog row.

    Carries one catalog row across the wire so the agent can build a PLR Resource
    from server-sourced geometry instead of re-resolving against its own local
    catalog. The agent picks one of two resolution paths off this DTO:

    * **PLR-registered labware**: ``plr_class_name`` set -> instantiate the named
      PLR factory directly (``pylabrobot.resources.<plr_class_name>(name=...)``).
    * **Custom labware**: ``plr_class_name`` None -> hand ``geometry`` to
      ``cheshire_drivers.plr.labware_converter.PLRLabwareConverter`` which
      reconstructs a PLR Resource from the protocol-typed geometry blob. The
      geometry dict is the ``model_dump`` of a
      ``cheshire_drivers.labware_seed.LabwareSeedEntry`` subclass and so already
      satisfies the ``_HasPlateGeometry`` / ``_HasWellGeometry`` protocols the
      converter consumes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    labware_type: str = Field(..., description="Catalog primary key, e.g. 'Cor_96_wellplate_360ul_Fb'")
    display_name: str = Field(..., description="Operator-visible label")
    category: Literal["plate", "tip_rack", "trough", "tube", "carrier"] = Field(
        ...,
        description="Top-level catalog category; selects the custom-labware reconstruction branch",
    )
    vendor: Optional[str] = Field(default=None, description="Vendor, when known")
    plr_class_name: Optional[str] = Field(
        default=None,
        description=(
            "When set, the agent instantiates this name from pylabrobot.resources "
            "directly (PLR-registered labware). When None, the agent reconstructs "
            "the Resource from `geometry` via PLRLabwareConverter (custom labware)."
        ),
    )
    geometry: dict[str, JsonValue] = Field(
        ...,
        description=(
            "Full geometry blob shaped to satisfy _HasPlateGeometry / "
            "_HasWellGeometry. Sourced from the catalog row's `geometry` column, "
            "which is the model_dump of a cheshire_drivers.labware_seed entry."
        ),
    )
    source: Literal["plr_seed", "operator_custom"] = Field(
        ...,
        description="Origin of the row: bundled PLR-derived seed vs operator-authored",
    )


class DeviceConnectInfo(BaseModel):
    """Per-device handshake metadata.

    One entry per device the agent controls. ``name`` is the operator-visible
    identifier used as the binding key against the topology declaration: an agent
    device with ``name="shaker_1"`` binds to a topology ``Shaker(name="shaker_1")``.
    Also carries the kind label, the abstract interfaces this driver implements (the
    safety contract checked at connect time), the auto-derived vendor-specific extras
    the concrete driver supports beyond its declared interfaces, the LH-specific
    provides_state flag, and the full method-info dict for the operator-facing
    introspection endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Operator-visible device name (binding key against topology)")
    type: str = Field(..., description="Device kind label (e.g. 'shaker', 'liquid_handler')")
    interfaces: frozenset[str] = Field(
        ...,
        description="Abstract driver interfaces this driver implements (e.g. 'IShaker')",
    )
    capabilities: frozenset[str] = Field(
        default_factory=frozenset,
        description="Auto-derived vendor extras: public methods + properties on the concrete class minus members on declared interfaces",
    )
    provides_state: bool = Field(
        default=False,
        description="LH-specific: True if driver returns reliable LabwareStateResponse",
    )
    methods: dict[str, MethodInfo] = Field(
        default_factory=dict,
        description="Per-callable-member metadata (signature, docstring, return type) for operator/AI introspection",
    )


class ConnectMessage(BaseModel):
    """Sent by the agent when connecting to the control plane.

    The agent provides a list of devices it can control, along with organizational
    hierarchy metadata. Authentication is handled via the X-API-Key HTTP header
    during the WebSocket handshake.
    """
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "protocol_version": "1.3.0",
                "site": "boston",
                "lab": "molbio",
                "workcell": None,
                "devices": [
                    {
                        "name": "shaker_1",
                        "type": "shaker",
                        "interfaces": ["IShaker"],
                        "capabilities": [],
                        "provides_state": False,
                        "methods": {},
                    },
                ],
            }
        },
    )

    protocol_version: ProtocolVersion = PROTOCOL_VERSION
    site: str = Field(..., description="Geographic site location (e.g., 'boston', 'cambridge')")
    lab: str = Field(..., description="Lab or workcell name within site (e.g., 'molbio', 'cellculture')")
    workcell: Optional[str] = Field(default=None, description="Optional workcell identifier for standalone device setups")
    devices: list[DeviceConnectInfo] = Field(
        ...,
        description="List of available devices with full handshake metadata",
    )


class CommandMessage(BaseModel):
    """Command from the control plane to the agent.

    The server sends commands to control specific devices. The command_id is used
    to match responses. The ``effective_mode`` is the per-dispatch run-mode resolved
    by the server; the agent uses it to pick between its configured live backend
    (LIVE) and a cheshire-drivers Sim* class (DEVICE_SIM). PURE_SIM should never
    reach the wire; the agent treats PURE_SIM as a hard error.
    """
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "command_id": "cmd_1234567890",
                "device_name": "shaker_1",
                "command": "shake",
                "params": {
                    "speed": 500.0,
                    "duration": 30.0
                },
                "effective_mode": "LIVE",
            }
        },
    )

    command_id: str = Field(..., description="Unique identifier for this command")
    device_name: str = Field(..., description="Target device name")
    command: str = Field(..., description="Command name (e.g., 'shake', 'centrifuge')")
    params: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Command-specific parameters"
    )
    effective_mode: EffectiveMode = Field(
        ...,
        description=(
            "Per-dispatch run-mode resolved by the server. The agent routes LIVE "
            "to the configured backend and DEVICE_SIM to the matching Sim* class. "
            "PURE_SIM is rejected: that mode runs entirely in-process server-side "
            "and should never reach the wire."
        ),
    )
    labware: Optional[LabwareDefinitionDTO] = Field(
        default=None,
        description=(
            "Catalog row for the labware this command operates on. Populated by the "
            "server from its labware catalog when the caller passes a labware type. "
            "When None, the agent falls back to its legacy resolution path so older "
            "servers that pre-date the labware envelope keep working."
        ),
    )


class CancelMessage(BaseModel):
    """Cancel an in-flight command (control plane to agent).

    The server sends this when it is no longer awaiting the command identified by
    ``command_id`` (e.g. an operator aborted it after it overran its expected
    duration). The agent cancels the running task and discards the eventual orphan
    response. Idempotent: a cancel for an unknown or already-finished command is a
    no-op.
    """
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "command_id": "cmd_1234567890",
                "reason": "operator aborted after recoverable timeout",
            }
        },
    )

    command_id: str = Field(..., description="ID of the command to cancel")
    reason: Optional[str] = Field(
        default=None, description="Optional human-readable cancellation reason",
    )


class ResponseMessage(BaseModel):
    """Response from the agent to the control plane.

    Sent after executing a command. Includes the result or error information.
    """
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "command_id": "cmd_1234567890",
                    "success": True,
                    "result": None,
                    "error": None,
                    "error_type": None
                },
                {
                    "command_id": "cmd_9876543210",
                    "success": False,
                    "result": None,
                    "error": "Device shaker_1 not found",
                    "error_type": "DeviceNotFoundError"
                }
            ]
        },
    )

    command_id: str = Field(..., description="ID of the command this responds to")
    success: bool = Field(..., description="Whether the command succeeded")
    result: Optional[JsonValue] = Field(default=None, description="Result data if successful")
    error: Optional[str] = Field(default=None, description="Human-readable error message")
    error_type: Optional[str] = Field(default=None, description="Error type for programmatic handling")
    instrument_outcome: Optional[InstrumentOutcome] = Field(
        default=None,
        description=(
            "What the failed command left the instrument in, as the driver "
            "reports it: refused (checked its own state and did not act, and "
            "the same request works later), rejected (nothing acted on it and "
            "nothing ever will), failed (ran and stopped part-way), unknown "
            "(dispatched, no answer). None on a success, and on a failure the "
            "driver did not classify, which the control plane reads as failed."
        ),
    )


class DeviceLinkInfo(BaseModel):
    """What one driver on the agent reports about its own link.

    A driver the agent holds but cannot read (a property that raises) reports
    its link closed, never by going missing from the map: the operator's remedy
    is that driver, and the agent log carries the exception.
    """

    model_config = ConfigDict(extra="forbid")

    is_connected: bool = Field(
        ..., description="Whether this driver's link to its instrument is open",
    )
    is_initialized: bool = Field(
        ..., description="Whether this driver has been brought up (setup complete)",
    )


class ObservedLink(BaseModel):
    """The one link to show a reader that is not dispatching a command.

    Carries the mode as well as the flags because dropping it is how a
    simulator gets shown as an instrument.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: DriverMode = Field(
        ..., description="Which of the device's drivers this link belongs to",
    )
    is_connected: bool = Field(
        ..., description="Whether that driver's link is open",
    )
    is_initialized: bool = Field(
        ..., description="Whether that driver has been brought up",
    )


def _link_rank(entry: tuple[DriverMode, DeviceLinkInfo]) -> int:
    """How much a link deserves to be the one an observer is shown."""
    _, link = entry
    if link.is_connected:
        return 0
    return 1 if link.is_initialized else 2


class DeviceStatusInfo(BaseModel):
    """What the agent observes on one device.

    The agent holds the driver objects, so it is the only party that can answer
    whether a link is open: the control plane's own copy of those flags moves
    only when a command happens to pass through its proxy, which the typed
    device routes bypass. It cannot answer for the device as a whole, though,
    because a device has one driver per run mode and the server stamps the mode
    on each command. So the link is reported per mode.

    **How to read this map.** There are two kinds of reader and they must not
    use the same rule:

    * A reader that already holds a wire mode, because it is about to dispatch
      a command in that mode or is reporting on that world specifically, looks
      that mode up.
    * A reader that holds no wire mode, which is every registry read, must NOT
      resolve one. Resolving an ambient run mode on a read path yields PURE_SIM
      whenever nothing seeded it, and PURE_SIM is not a key here, so the lookup
      misses and a healthy instrument reports as disconnected. Such a reader
      takes :attr:`observed_link`, or carries the whole map through to its own
      caller. It must never invent a collapse of its own: there are several
      consumers and each naive rule is wrong somewhere.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ready", "busy"] = Field(
        ...,
        description=(
            "Whether a command currently holds the agent's dispatch lock for "
            "this device. Both of a device's drivers share that one lock, so "
            "this says nothing about any link; read `links` for that."
        ),
    )
    links: dict[DriverMode, DeviceLinkInfo] = Field(
        ...,
        min_length=1,
        description=(
            "Link state per driver, keyed by the CommandMessage.effective_mode "
            "that dispatches to it. A mode missing from this map means one "
            "thing only: the agent holds no driver for it, so a command in "
            "that mode fails outright and the remedy is the topology or the "
            "on-prem config. A driver that is present but unreadable reports "
            "its link closed instead. An advertised device always has at least "
            "one driver, so an empty map is not a state the agent can be in."
        ),
    )

    @property
    def observed_link(self) -> ObservedLink:
        """The single link to show a reader that is not dispatching.

        Takes the first of: a link that is open, one that is at least brought
        up, any. LIVE breaks a tie, and the chosen mode answers with its own
        flags: a blend of two modes reports a device as not brought up while
        the other one is.

        Show the mode alongside. A tie means both worlds are open and nothing
        here can know which one the next command takes, so the label is the
        whole mitigation, in both directions: without it an operator reads an
        open DEVICE_SIM link as the instrument, or an open LIVE link as the
        simulator every one of their commands is actually reaching.
        """
        present: list[tuple[DriverMode, DeviceLinkInfo]] = [
            (mode, self.links[mode])
            for mode in _OBSERVER_PRIORITY
            if mode in self.links
        ]
        mode, link = min(present, key=_link_rank)
        return ObservedLink(
            mode=mode,
            is_connected=link.is_connected,
            is_initialized=link.is_initialized,
        )


class StatusMessage(BaseModel):
    """Status update from the agent to the control plane.

    Sent with every heartbeat and again after each command, since connect /
    disconnect / initialize move what it reports.
    """
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "devices": {
                    "shaker_1": {
                        "status": "ready",
                        "links": {
                            "LIVE": {"is_connected": True, "is_initialized": True},
                            "DEVICE_SIM": {"is_connected": False, "is_initialized": False},
                        },
                    },
                    "centrifuge_1": {
                        "status": "busy",
                        "links": {
                            "LIVE": {"is_connected": True, "is_initialized": True},
                        },
                    },
                },
                "timestamp": 1699459200.0
            }
        },
    )

    devices: dict[str, DeviceStatusInfo] = Field(
        ...,
        description="Map of device name to the agent's view of that device",
    )
    timestamp: float = Field(..., description="Unix timestamp")


class HeartbeatMessage(BaseModel):
    """Heartbeat from the agent to the control plane.

    Sent periodically to indicate the agent is still connected.
    """
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "timestamp": 1699459200.0,
                "uptime": 3600.5
            }
        },
    )

    timestamp: float = Field(..., description="Unix timestamp")
    uptime: float = Field(..., description="Agent uptime in seconds")


class MessageEnvelope(BaseModel):
    """Wrapper for all WebSocket messages.

    All messages sent over the WebSocket are wrapped in this envelope which
    specifies the message type.
    """
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "type": "command",
                "payload": {
                    "command_id": "cmd_1234567890",
                    "device_name": "shaker_1",
                    "command": "shake",
                    "params": {"speed": 500.0, "duration": 30.0}
                }
            }
        },
    )

    type: Literal["connect", "command", "cancel", "response", "status", "heartbeat"] = Field(
        ...,
        description="Message type discriminator"
    )
    payload: dict[str, JsonValue] = Field(..., description="Message payload")

    @classmethod
    def wrap_connect(cls, message: ConnectMessage) -> "MessageEnvelope":
        """Wrap a ConnectMessage in an envelope."""
        return cls(type="connect", payload=message.model_dump(mode="json"))

    @classmethod
    def wrap_command(cls, message: CommandMessage) -> "MessageEnvelope":
        """Wrap a CommandMessage in an envelope."""
        return cls(type="command", payload=message.model_dump(mode="json"))

    @classmethod
    def wrap_cancel(cls, message: CancelMessage) -> "MessageEnvelope":
        """Wrap a CancelMessage in an envelope."""
        return cls(type="cancel", payload=message.model_dump(mode="json"))

    @classmethod
    def wrap_response(cls, message: ResponseMessage) -> "MessageEnvelope":
        """Wrap a ResponseMessage in an envelope."""
        return cls(type="response", payload=message.model_dump(mode="json"))

    @classmethod
    def wrap_status(cls, message: StatusMessage) -> "MessageEnvelope":
        """Wrap a StatusMessage in an envelope."""
        return cls(type="status", payload=message.model_dump(mode="json"))

    @classmethod
    def wrap_heartbeat(cls, message: HeartbeatMessage) -> "MessageEnvelope":
        """Wrap a HeartbeatMessage in an envelope."""
        return cls(type="heartbeat", payload=message.model_dump(mode="json"))

    def unwrap(self) -> ConnectMessage | CommandMessage | CancelMessage | ResponseMessage | StatusMessage | HeartbeatMessage:
        """Unwrap the envelope to get the inner message."""
        if self.type == "connect":
            return ConnectMessage.model_validate(self.payload)
        elif self.type == "command":
            return CommandMessage.model_validate(self.payload)
        elif self.type == "cancel":
            return CancelMessage.model_validate(self.payload)
        elif self.type == "response":
            return ResponseMessage.model_validate(self.payload)
        elif self.type == "status":
            return StatusMessage.model_validate(self.payload)
        elif self.type == "heartbeat":
            return HeartbeatMessage.model_validate(self.payload)
        else:
            raise ValueError(f"Unknown message type: {self.type}")
