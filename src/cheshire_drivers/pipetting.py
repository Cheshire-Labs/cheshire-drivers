"""Pipetting parameter types for liquid handling operations.

Frozen dataclasses that parameterize aspirate/dispense/mix operations.
These are PLR-agnostic -- the PLR wrapper translates them to PLR types.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MixParams:
    """Mixing parameters for aspirate/dispense operations.

    Maps to PLR's Mix class internally.
    """
    volume: float
    repetitions: int
    flow_rate: float


@dataclass(frozen=True)
class PipettingProfile:
    """A reusable bundle of pipetting parameters, built once and passed in.

    One shape serves both the liquid and the step, because a parameter gets set
    for two different reasons and only one of them is about the liquid. Glycerol
    is drawn slowly because it is viscous, wherever it goes. A last top-up into
    a nearly-full well is dispensed slowly so it does not splash, whatever is in
    it. Splitting the fields between two shapes would leave one of those with
    nowhere to live, so the layers differ by what they are keyed on instead, and
    a field left None says nothing and inherits from the layer under it.

    Direction lives in the instance rather than the shape. A liquid whose draw
    and delivery want different rates is two profiles built from one base::

        glycerol = PipettingProfile(name="glycerol", height=2.0,
                                    blow_out=True, blow_out_volume=10.0)
        draw = replace(glycerol, flow_rate=20.0)
        deliver = replace(glycerol, flow_rate=50.0)

    The blow-out sits on the base, not on ``deliver``: a head that expels air
    the tip took in needs the aspirate to have taken it, so a blow-out named at
    delivery alone fails the dispense with a tip already full of liquid.
    """

    name: str | None = None
    """What this profile is called, for the record of which layer set what.
    Never sent to a machine."""
    height: float | None = None
    """Tip end height above the inner floor of the well, in mm."""
    flow_rate: float | None = None
    """uL/s."""
    blow_out: bool | None = None
    """Push the plunger past its stop at the end of a dispense."""
    blow_out_volume: float | None = None
    """uL of air to expel, for a head that can expel a named volume."""
    blow_out_flow_rate: float | None = None
    """uL/s for the blow-out."""
    mix: MixParams | None = None
    """Cycles run at the same target, before an aspirate or after a dispense."""
