"""The request shape for the shared homing capability.

`HomeRequest` used to live in `transporter_models`, back when an arm was the
only thing that homed. Homing is a capability now (`IHomeableDriver`), carried
by anything whose axes lose their reference at power-off, so its model does not
belong to one device kind.

Empty on purpose: homing takes no arguments. It stays a model rather than a
bare call so every command crosses the wire in the same shape.
"""

from cheshire_drivers.liquid_handler_models import _StrictModel


class HomeRequest(_StrictModel):
    pass
