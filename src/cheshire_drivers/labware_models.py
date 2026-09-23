"""Pydantic models for labware identity at the wire boundary.

These models carry the labware identity that orca-core tracks in
``LabwareInstance`` across process boundaries. orca-client's transporter
sim graph and PLR deck need to know which physical plate is being moved so
that cross-process world state stays consistent with orca-core's in-process
labware ledger.

All models reject unknown fields (``extra='forbid'``).
"""

from cheshire_drivers.liquid_handler_models import _StrictModel


class LabwareIdentity(_StrictModel):
    """Identity of a single labware instance crossing the wire.

    Mirrors the subset of ``orca.resource_models.labware.LabwareInstance``
    that orca-client needs to keep its sim world graph and PLR deck in
    sync with orca-core's labware tracker. Not a full serialization of
    ``LabwareInstance`` -- the runtime ledger (ops history, capacity,
    template binding) stays cloud-side; only what orca-client needs for
    move + deck operations rides the wire.
    """

    labware_id: str
    """Stable per-instance identifier. Mirrors ``LabwareInstance.id``
    (UUID hex). Two fresh racks from a stacker get different ids."""

    barcode: str | None = None
    """Operator-readable barcode. Optional; not every plate has one."""

    labware_type: str
    """Template name (e.g. ``"sample_plate"``, ``"tips_384"``). Mirrors
    ``LabwareInstance.labware_type``."""
