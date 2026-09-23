"""Pre-armed fault injection for cheshire-drivers Sim drivers.

Unit tests and other harnesses attach a list of ``FaultSpec`` to a sim
driver at construction; the driver's ``FaultRegistry`` matches
each domain-method call against the specs and either raises or hangs
before the underlying simulation runs. After all configured ``on_calls``
have fired the registry is dormant: the device returns to clean
operation, so a single fault on call #1 does not brick the device.

Faults targeting unknown method names are rejected at boot rather than
silently no-op; same for ``error_type`` strings that do not resolve to
a real exception class (Python builtins or cheshire-drivers exceptions).
"""

import asyncio
import builtins
import logging
from collections import defaultdict
from typing import Annotated, ClassVar, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("cheshire_drivers.faults")


_CHESHIRE_DRIVERS_EXCEPTIONS: frozenset[str] = frozenset({
    "SimTransporterValidationError",
})


def resolve_exception_class(error_type: str) -> type[BaseException]:
    """Resolve a fault's ``error_type`` string to a real exception class.

    Looks up the name in cheshire-drivers' exception catalog first, then
    Python builtins. Rejects anything else so a typo does not silently
    fall through to ``RuntimeError`` or yield an injected fault that the
    engine can never recognize.
    """
    if error_type in _CHESHIRE_DRIVERS_EXCEPTIONS:
        from cheshire_drivers.sims import SimTransporterValidationError
        return SimTransporterValidationError
    candidate = getattr(builtins, error_type, None)
    if (
        candidate is None
        or not isinstance(candidate, type)
        or not issubclass(candidate, BaseException)
    ):
        raise ValueError(
            f"FaultSpec error_type {error_type!r} does not resolve to a "
            f"known exception class. Use a Python builtin exception name "
            f"(RuntimeError, ValueError, TimeoutError, ...) or one of: "
            f"{sorted(_CHESHIRE_DRIVERS_EXCEPTIONS)}."
        )
    return candidate


class RaiseFault(BaseModel):
    """Inject a raised exception on selected calls of ``method``.

    ``error_type`` must resolve to a real exception class via
    ``resolve_exception_class``; validated at registry construction so a
    typo aborts boot rather than silently turning into ``RuntimeError``.
    Default ``on_calls=[1]`` fires on the first call only and stays
    dormant after; pass an explicit list (e.g. ``[1, 2, 5]``) to fault
    multiple calls.

    ``extra="forbid"`` so a kwarg typo (``metohd="shake"``) raises at
    construction rather than silently constructing with ``method=""``
    and surfacing later as a confusing "not a faultable method" error.
    """
    model_config = ConfigDict(extra="forbid")

    kind: Literal["raise"] = "raise"
    method: str
    error_type: str = "RuntimeError"
    message: str = "injected fault"
    on_calls: list[int] = Field(default_factory=lambda: [1])


class HangFault(BaseModel):
    """Inject extra delay on selected calls of ``method``.

    The fault sleeps ``extra_seconds`` BEFORE the underlying method
    runs. The method then proceeds normally; combine with a ``RaiseFault``
    on the same ``on_calls`` to hang then fail.
    """
    model_config = ConfigDict(extra="forbid")

    kind: Literal["hang"] = "hang"
    method: str
    extra_seconds: float = 60.0
    on_calls: list[int] = Field(default_factory=lambda: [1])


class PartialFault(BaseModel):
    """Inject a per-channel partial-failure on selected calls of ``method``.

    Unlike ``RaiseFault``, this fault does NOT raise. The faulted call returns
    a ``LabwareStateResponse(success=False, per_channel_errors=[...])`` where
    ``failed_channels`` enumerate the 0-indexed physical channel ids that
    "errored." Channels not in the list are treated as successful by the
    consuming interpreter (which emits a
    ``confirmed_transferred`` outcome for them; the failed channels emit
    ``definitely_not_transferred``).

    Use only on faultable methods that return ``LabwareStateResponse`` and
    accept multi-channel arguments (``aspirate`` / ``dispense`` today;
    tip ops in a follow-up). The sim driver's domain method is responsible
    for consuming the fault via ``FaultRegistry.partial_for(method)`` after
    ``before(method)`` has incremented the counter.
    """
    model_config = ConfigDict(extra="forbid")

    kind: Literal["partial"] = "partial"
    method: str
    failed_channels: list[int]
    error_code: str = "FW_TEST_ERROR"
    error_message: str = "injected per-channel fault"
    on_calls: list[int] = Field(default_factory=lambda: [1])


FaultSpec = Annotated[
    Union[RaiseFault, HangFault, PartialFault],
    Field(discriminator="kind"),
]


class FaultRegistry:
    """Per-driver fault registry.

    Holds immutable ``FaultSpec`` definitions plus a mutable per-method
    call counter. ``before(method)`` is called by every faultable method
    on entry; it increments the counter and applies any matching faults.

    Reuse-safe: a registry instance is owned by one sim driver. Two
    drivers in the same process never share counters.
    """

    def __init__(self, specs: list[FaultSpec] | None = None) -> None:
        self._raise: dict[str, list[RaiseFault]] = defaultdict(list)
        self._hang: dict[str, list[HangFault]] = defaultdict(list)
        self._partial: dict[str, list[PartialFault]] = defaultdict(list)
        self._counts: dict[str, int] = defaultdict(int)
        for spec in specs or []:
            self._add(spec)

    def _add(self, spec: FaultSpec) -> None:
        if isinstance(spec, RaiseFault):
            resolve_exception_class(spec.error_type)
            self._raise[spec.method].append(spec)
        elif isinstance(spec, HangFault):
            self._hang[spec.method].append(spec)
        else:
            self._partial[spec.method].append(spec)

    def has_any(self) -> bool:
        return bool(self._raise) or bool(self._hang) or bool(self._partial)

    def call_count(self, method: str) -> int:
        return self._counts.get(method, 0)

    async def before(self, method: str) -> None:
        """Increment ``method``'s call counter and apply matching faults.

        Hangs run BEFORE raises so a same-call (hang + raise) combination
        delays then fails. Partial faults are NOT applied here -- the sim
        driver's domain method consumes them via ``partial_for`` after
        ``before`` returns, since they require request-shape knowledge to
        compose the response.
        """
        self._counts[method] += 1
        n = self._counts[method]
        for hf in self._hang.get(method, ()):
            if n in hf.on_calls:
                logger.warning(
                    "fault.hang method=%s call=%d extra_seconds=%.3f",
                    method, n, hf.extra_seconds,
                )
                await asyncio.sleep(hf.extra_seconds)
        for rf in self._raise.get(method, ()):
            if n in rf.on_calls:
                exc_cls = resolve_exception_class(rf.error_type)
                logger.warning(
                    "fault.raise method=%s call=%d error=%s",
                    method, n, exc_cls.__name__,
                )
                raise exc_cls(rf.message)

    def partial_for(self, method: str) -> "PartialFault | None":
        """Return the matching ``PartialFault`` for ``method`` on the current call, if any.

        Reads the per-method counter that ``before`` already incremented; does
        NOT increment on its own. Returns the first matching partial fault
        when multiple are armed for the same call (callers should avoid this;
        the registry does not validate uniqueness across kinds).
        """
        n = self._counts.get(method, 0)
        for pf in self._partial.get(method, ()):
            if n in pf.on_calls:
                logger.warning(
                    "fault.partial method=%s call=%d failed_channels=%s",
                    method, n, pf.failed_channels,
                )
                return pf
        return None


