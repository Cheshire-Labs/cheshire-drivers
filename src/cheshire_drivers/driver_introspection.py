"""Driver introspection helpers for capability advertisement and operator-facing introspection.

Used by orca-client at handshake to populate DeviceConnectInfo over the wire,
and by a gateway server to surface each connected device's operator-facing
method surface (the @external interface contract plus auto-derived vendor
extras; internal plumbing is hidden) via GET /api/devices/{id}/capabilities
and the device_get_capabilities MCP tool.

`derive_capabilities(cls)` answers "what methods does this concrete driver expose
beyond its declared interfaces?" (default-allow auto-derive; the user opted into
this so lab automation engineers get full method-level access for troubleshooting).

`describe_method(cls, name)` and `describe_driver(instance)` produce structured
metadata (signatures, return types, docstrings) so an operator or AI looking at
the introspection endpoint can both see what is callable and how to call it.
"""

import inspect
import json
from abc import ABC
from collections.abc import Sequence
from typing import Any, Dict, List, Literal, NamedTuple, Optional, Set, Tuple, TypeVar

from pydantic import BaseModel, ConfigDict

from cheshire_drivers.command_timings import CommandTiming, collect_command_timings


F = TypeVar("F")

_EXTERNAL_ATTR = "__cheshire_external__"


def external(method: F) -> F:
    """Mark an interface-declared method as operator-facing for introspection.

    `describe_driver` shows an interface-declared member only if some definition
    of its name in the driver's MRO carries this marker. Unmarked interface
    methods are internal plumbing (e.g. the LH deck-occupancy ops, the
    transporter world-sync ops): hidden from the operator/AI introspection
    projection, yet still part of the contract and still invokable -- the
    capability validator (`interface_member_names`) and orca's invoke allow-list
    use separate, unmarked filters. Concrete-only vendor extras are unaffected;
    they keep underscore auto-derivation via `derive_capabilities`.

    Sets an attribute on the underlying function, so it composes with
    `@abstractmethod` and `@command_timing` (order-independent) and travels with
    a rename. Apply it directly to the function (below `@property` for a
    property); `_is_external_interface_member` resolves the marker through the
    property's fget.
    """
    setattr(method, _EXTERNAL_ATTR, True)
    return method


def _external_marked(member: Any) -> bool:
    """True if a class-dict member carries the `@external` marker.

    Unwraps `property` (checks fget) and static/class methods (checks __func__)
    so the marker is found wherever it was legally applied.
    """
    target = member
    if isinstance(target, property):
        target = target.fget
    if isinstance(target, (staticmethod, classmethod)):
        target = target.__func__
    return bool(getattr(target, _EXTERNAL_ATTR, False))


def _is_external_interface_member(driver_cls: type, name: str) -> bool:
    """True if any MRO definition of `name` is `@external`-marked.

    A concrete override does not re-carry the interface's marker, so every class
    in the MRO that declares `name` is checked, not just the most-derived one.
    """
    for base in driver_cls.__mro__:
        if base is object:
            continue
        member = vars(base).get(name)
        if member is not None and _external_marked(member):
            return True
    return False


class MethodInfo(BaseModel):
    """Wire-shape metadata for a single driver method or property.

    Mirrored byte-identical in orca-client and in the gateway's protocol
    modules. One of these is sent per callable member at handshake time;
    the gateway relays the dict to operators via REST + MCP.

    `duration` carries the driver-declared `CommandTiming` for this method
    (None for properties and methods without a registered timing). The gateway's
    dispatcher uses `max_seconds` to pick the per-command timeout; operator
    dashboards surface `typical_seconds`.

    `requires_confirm` is the per-command danger flag, and it is True only for
    the raw console (`send_command` and friends). The dispatch gate refuses
    such a command unless the caller acknowledges it with an explicit confirm.
    Nothing else is flagged: a confirmation on every command is a confirmation
    on none, and every other vendor extra carries a signature and a docstring
    the operator surface can show instead.
    Interface-contract methods are never flagged; they have their own typed
    validation and are what the engine itself dispatches.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["method", "property"]
    params: Dict[str, Any]
    returns: Optional[str] = None
    docstring: Optional[str] = None
    duration: Optional[CommandTiming] = None
    requires_confirm: bool = False


def _is_public_callable_or_property(name: str, value: Any) -> bool:
    """True for any non-underscore callable (sync or async) or property descriptor.

    The underscore filter governs CONCRETE-ONLY members only. Vendor drivers
    expose synchronous troubleshooting helpers (e.g. `get_teachpoints`,
    diagnostic readers) that are not declared on any interface; those stay
    auto-derived (`derive_capabilities`) and visible. Interface-declared
    members follow a separate rule: `describe_driver` shows one only if it is
    `@external`-marked, so internal plumbing on an interface (LH deck-occupancy
    ops, transporter world-sync ops) is hidden from introspection even though
    it does not start with an underscore.
    """
    if name.startswith("_"):
        return False
    if inspect.iscoroutinefunction(value):
        return True
    if inspect.isfunction(value):
        return True
    if isinstance(value, (staticmethod, classmethod)):
        return True
    if isinstance(value, property):
        return True
    return False


def _is_abstract_interface(cls: type) -> bool:
    """True if cls is itself abstract (has unimplemented abstract methods).

    Distinguishes interface ABCs (e.g. IShakerDriver) from concrete classes
    that happen to inherit from ABC (e.g. PLRShakerBackendWrapper, where every
    abstract has been implemented and `__abstractmethods__` is empty).
    """
    return bool(getattr(cls, "__abstractmethods__", frozenset()))


def interface_member_names(interface_cls: type) -> frozenset[str]:
    """Public callable + @property contract declared directly on one interface class.

    Returns the names of every abstract method plus every public callable / @property
    descriptor declared on `interface_cls` or on any ABSTRACT base of it. Note that
    `cls.__abstractmethods__` itself surfaces inherited abstracts that the class
    has not implemented, so unimplemented abstract names from parent ABCs flow
    through automatically; concrete members do not, which is why the abstract
    bases are walked explicitly. An interface that hands its children a working
    default (`BaseDriver.connect`) is still declaring that they answer to it, so
    the child's contract has to include it or the capability gate refuses a
    command the driver genuinely implements. Concrete BASES that are not
    themselves abstract are skipped: their members are implementation, not
    contract.

    Underscore-prefixed members are filtered (private abstracts like `_sim` are
    implementation hooks, never wire-callable).

    The caller is responsible for passing a genuine interface class; this function
    does not assert `interface_cls` is abstract. Passing a concrete class returns
    its public-member surface, which is rarely what consumers want.

    This is the canonical source of truth for "what does this interface let you
    call?" Both the gateway's capability validator (given an interface class via
    NAME_TO_INTERFACE) and the per-driver MRO walk in `_interface_member_names`
    delegate to this function.
    """
    members: Set[str] = set()
    for abstract_name in getattr(interface_cls, "__abstractmethods__", frozenset()):
        if not abstract_name.startswith("_"):
            members.add(abstract_name)
    for cls in interface_cls.__mro__:
        if cls is not interface_cls and not _is_abstract_interface(cls):
            continue
        for name, value in vars(cls).items():
            if _is_public_callable_or_property(name, value):
                members.add(name)
    return frozenset(members)


def interface_command_names(interface_cls: type) -> frozenset[str]:
    """Invokable command names of an interface: ``interface_member_names`` minus
    ``@property`` descriptors.

    Properties (``name``, ``single_carriage``, ...) are engine-read metadata, not
    wire commands. The capability gate consults this set rather than
    ``interface_member_names`` so a property on the interface surface is never
    accepted as a command. ``interface_member_names`` stays property-inclusive
    for the vendor-extras subtraction in ``derive_capabilities`` (a property must
    be subtracted there, or it leaks back as a vendor extra)."""
    commands: Set[str] = set()
    for name in interface_member_names(interface_cls):
        if isinstance(inspect.getattr_static(interface_cls, name, None), property):
            continue
        commands.add(name)
    return frozenset(commands)


def _interface_member_names(driver_cls: type) -> Set[str]:
    """Names of public callables + @property descriptors declared on any
    abstract interface base of driver_cls (excluding driver_cls itself).

    Walks the MRO and only considers bases that are themselves abstract
    (un-implemented abstracts in `__abstractmethods__`). A concrete subclass of
    ABC that has implemented every abstract is NOT an interface base -- its
    public members count as implementation, not contract.

    Per-class contribution comes from `interface_member_names`, the canonical
    single-class capability function.
    """
    members: Set[str] = set()
    for base in driver_cls.__mro__:
        if base is driver_cls or base is object:
            continue
        if not _is_abstract_interface(base):
            continue
        members.update(interface_member_names(base))
    return members


def derive_capabilities(driver_cls: type) -> frozenset[str]:
    """Auto-derived vendor extras on a concrete driver class.

    Returns the names of public callables (sync or async) and @property
    descriptors reachable on `driver_cls` (directly or via concrete-class
    inheritance) that are NOT declared on any abstract interface in the MRO.
    Filters underscore-prefixed privates.

    Walks the full MRO. Interface (ABC) bases contribute the contract; concrete
    bases (mixins, sim helpers) contribute the implementation surface. Vendor
    extras = (everything reachable on concrete bases) − (interface contract).

    Examples:
      - PLRCentrifugeBackendWrapper(ICentrifugeDriver) directly defines stop +
        set_acceleration → both returned.
      - SimShakerDriver(BaseSimDriver, ShakerSimMixin, Sim, IShakerDriver)
        inherits shake from a mixin and connect/disconnect from Sim → connect
        and disconnect returned (shake is on IShakerDriver, subtracted).
    """
    interface_members = _interface_member_names(driver_cls)
    own: Set[str] = set()
    for base in driver_cls.__mro__:
        if base is object:
            continue
        if _is_abstract_interface(base):
            continue
        for name, value in vars(base).items():
            if _is_public_callable_or_property(name, value):
                own.add(name)
    own |= vendor_backend_members(driver_cls)
    return frozenset(own - interface_members)


class VendorSurface(NamedTuple):
    """One vendor object a driver forwards commands to.

    `path` is how to reach the object from a driver instance, dotted for one
    held by another (`_flex.gripper`). `type` is the class whose public methods
    get advertised: read from the class, never from a live attribute, because a
    driver advertises at the orca-client handshake and optional hardware like
    the Flex gripper is not discovered until connect. `prefix` namespaces the
    commands on the wire, so a gripper's `move_to` and a pipette mount's
    `move_to` stay two names; the empty prefix leaves them bare.
    """

    path: str
    type: type
    prefix: str = ""

    def wire_name(self, member: str) -> str:
        return f"{self.prefix}.{member}" if self.prefix else member


def vendor_surfaces(driver_cls: type) -> Tuple[VendorSurface, ...]:
    """The vendor objects this driver declares, if any.

    A wrapper holds its vendor object as an attribute rather than inheriting
    it, so nothing about that object is reachable from the driver class alone.
    Declaring it here is what puts its surface in front of an operator.
    """
    declared = getattr(driver_cls, "vendor_surfaces", ())
    if isinstance(declared, VendorSurface):
        # A VendorSurface is a NamedTuple, so one declared without its trailing
        # comma is a sequence of its own three fields.
        raise TypeError(
            f"{driver_cls.__name__}.vendor_surfaces is a single VendorSurface, "
            f"not a sequence of them. Add the trailing comma."
        )
    if isinstance(declared, (str, bytes)) or not isinstance(declared, Sequence):
        raise TypeError(
            f"{driver_cls.__name__}.vendor_surfaces must be a sequence of "
            f"VendorSurface, not {type(declared).__name__}."
        )
    surfaces = tuple(declared)
    wrong = [s for s in surfaces if not isinstance(s, VendorSurface)]
    if wrong:
        raise TypeError(
            f"{driver_cls.__name__}.vendor_surfaces holds "
            f"{type(wrong[0]).__name__}, not VendorSurface. Silently dropping "
            f"it would leave the commands on that surface unadvertised and "
            f"uncallable, with nothing said anywhere."
        )
    return surfaces


def resolve_vendor_object(driver: Any, surface: VendorSurface) -> Any:
    """Walk a surface's path on a live driver.

    Raises AttributeError naming the missing hop, because a Flex with no
    gripper fitted has to say that rather than report an unknown command.
    """
    obj = driver
    for hop in surface.path.split("."):
        obj = getattr(obj, hop, None)
        if obj is None:
            raise AttributeError(
                f"{type(driver).__name__} cannot reach {surface.path!r}: "
                f"{hop!r} is not attached. The hardware it needs is absent or "
                f"the device has not connected yet."
            )
    return obj


def _fitted_type(driver: Any, surface: VendorSurface) -> type:
    """The class actually on the end of the path, or the declared one.

    A mount is declared once per head that can sit on it, because which one is
    fitted is unknown until the robot answers. Their verbs are not the same
    shape -- a 1-channel `liquid_probe` takes a well, an 8-channel one takes a
    plate and a column -- and the catalog is what an operator reads before
    calling one. Describe what is there whenever it is there.
    """
    try:
        return type(resolve_vendor_object(driver, surface))
    except AttributeError:
        return surface.type


def needs_confirmation(name: str) -> bool:
    """True when this vendor command must not dispatch without approval.

    Only the raw console qualifies: `send_command`, and the aliases a backend
    gives it (`send_raw_command`, `send_hhs_command`). Everything else a vendor
    exposes arrives with a signature and a docstring, so the operator surface
    can say what it will do before it is sent. A raw line carries a string
    nothing downstream can read, so nothing can.
    """
    return "command" in name.lower()


def vendor_command_sources(driver_cls: type) -> Dict[str, Tuple[VendorSurface, str]]:
    """Wire name -> the surface it forwards to and the member it calls there.

    A bare name (no prefix) is skipped when the driver already defines it: an
    interface method always wins over the vendor method it wraps, so a typed
    verb is never shadowed by the raw one underneath it. A prefixed name cannot
    collide with a driver method, so nothing is dropped there.

    Only coroutine functions. Properties, classmethods and staticmethods are
    not dispatchable wire commands (a property read raises at the executor, and
    a classmethod like PLR's ``deserialize`` is library plumbing), and a plain
    sync method is refused by the executor as not-async, so advertising any of
    them would offer names that can never succeed. The Flex mounts alone carry
    ten such names (``left.get_mounted_tips``, ``head96.default_flow_rates``).
    """
    on_driver = {
        name
        for base in driver_cls.__mro__
        if base is not object
        for name in vars(base)
    }
    sources: Dict[str, Tuple[VendorSurface, str]] = {}
    for surface in vendor_surfaces(driver_cls):
        for base in surface.type.__mro__:
            if base is object:
                continue
            for name, value in vars(base).items():
                if name.startswith("_"):
                    continue
                if not surface.prefix and name in on_driver:
                    continue
                if not inspect.iscoroutinefunction(value):
                    continue
                sources.setdefault(surface.wire_name(name), (surface, name))
    return sources


def vendor_backend_members(driver_cls: type) -> Set[str]:
    """Every wire name this driver's declared vendor surfaces contribute."""
    return set(vendor_command_sources(driver_cls))


def _annotation_str(annotation: Any) -> Optional[str]:
    """Render a Python type annotation as a short string.

    Plain classes (int, str, AspirateRequest, LabwareStateResponse) render as
    their bare name. Typing constructs (Optional[List[int]], Dict[str, Any],
    Union[X, Y]) render via str() so generic args survive — `__name__` collapses
    them to "Optional"/"Dict"/"Union" which is useless for operators reading
    the introspection endpoint.
    """
    if annotation is inspect.Signature.empty:
        return None
    if annotation is None or annotation is type(None):
        return "None"
    if inspect.isclass(annotation) and getattr(annotation, "__module__", None) != "typing":
        return annotation.__name__
    return str(annotation)


def _safe_default(value: Any) -> Any:
    """Return a JSON-serializable representation of a default value."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def _params_from_signature(non_self_params: List[Tuple[str, inspect.Parameter]]) -> Dict[str, Any]:
    """Plain-kwargs params dict (one entry per kwarg with type, default, required)."""
    out: Dict[str, Any] = {}
    for name, param in non_self_params:
        entry: Dict[str, Any] = {
            "type": _annotation_str(param.annotation),
            "required": param.default is inspect.Parameter.empty,
        }
        if param.default is not inspect.Parameter.empty:
            entry["default"] = _safe_default(param.default)
        out[name] = entry
    return out


def describe_method(cls: type, name: str) -> MethodInfo:
    """Build a MethodInfo for one method or property on cls.

    Resolves descriptors via inspect.getattr_static so @property is reported as
    kind="property" rather than evaluated against an instance. Pydantic Request
    methods (single-arg taking a BaseModel subclass) get full JSON Schema for
    that param via `model_json_schema()`; everything else gets a signature-derived
    params dict.

    Raises AttributeError if cls has no member named `name`.
    """
    try:
        member = inspect.getattr_static(cls, name)
    except AttributeError as e:
        raise AttributeError(f"{cls.__name__} has no member {name!r}") from e

    docstring = inspect.getdoc(member)
    duration = collect_command_timings(cls).get(name)

    if isinstance(member, property):
        returns: Optional[str] = None
        if member.fget is not None:
            returns = _annotation_str(inspect.signature(member.fget).return_annotation)
        return MethodInfo(
            kind="property",
            params={},
            returns=returns,
            docstring=docstring,
            duration=duration,
        )

    if not callable(member):
        raise TypeError(f"{cls.__name__}.{name} is not callable nor a property")

    sig = inspect.signature(member)
    non_self: List[Tuple[str, inspect.Parameter]] = [
        (n, p) for n, p in sig.parameters.items() if n != "self"
    ]

    if len(non_self) == 1:
        pname, pparam = non_self[0]
        anno = pparam.annotation
        if inspect.isclass(anno) and issubclass(anno, BaseModel):
            params: Dict[str, Any] = {pname: anno.model_json_schema()}
        else:
            params = _params_from_signature(non_self)
    else:
        params = _params_from_signature(non_self)

    return MethodInfo(
        kind="method",
        params=params,
        returns=_annotation_str(sig.return_annotation),
        docstring=docstring,
        duration=duration,
    )


def describe_driver(driver_instance: Any) -> Dict[str, MethodInfo]:
    """Operator-facing method surface of a constructed driver instance.

    Returns a dict from member name to MethodInfo, covering the @external-marked
    interface methods plus auto-derived vendor extras. Unmarked interface
    plumbing is hidden from this projection (it stays invokable by the engine).
    Used by orca-client at handshake to populate DeviceConnectInfo.methods.
    """
    cls = type(driver_instance)
    out: Dict[str, MethodInfo] = {}

    for name in sorted(_interface_member_names(cls)):
        if not _is_external_interface_member(cls, name):
            continue
        try:
            out[name] = describe_method(cls, name)
        except (AttributeError, TypeError):
            continue

    forwarded = vendor_command_sources(cls)
    for name in sorted(derive_capabilities(cls)):
        if name in out:
            continue
        # A forwarded name lives on the vendor object, not on the driver, so its
        # signature and docstring have to be read there.
        surface = forwarded.get(name)
        if surface is not None:
            declared_surface, member = surface
            source = _fitted_type(driver_instance, declared_surface)
        else:
            source, member = cls, name
        try:
            info = describe_method(source, member)
        except (AttributeError, TypeError):
            # The gate reads this catalog, so a dropped entry would dispatch
            # unclassified; an undescribable extra gets an empty schema instead.
            info = MethodInfo(kind="method", params={})
        info.requires_confirm = needs_confirmation(name)
        out[name] = info

    return out
