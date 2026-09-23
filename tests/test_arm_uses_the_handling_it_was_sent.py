"""A driver acts on the numbers it is given and resolves no labware itself.

`PickAtCoordsRequest.handling` is documented as already resolved: the sender
layers defaults, the labware, the site and the carry, and the arm receives plain
numbers. The wrapper broke that by re-deriving the load's height from a
name-keyed catalog it shipped. The catalog was keyed by PyLabRobot's factory
name while the wire carried the labware's other name, so a 99 mm tip rack
resolved to nothing and kept a 14.35 mm microplate's height, silently.

That a pick uses exactly the height it was sent is pinned in
`test_arm_joint_translation.py`. What is pinned here is that the driver no
longer has a catalog to reach for, and that a rack can state its own height so
the sender has a real number to send.
"""

import inspect

import cheshire_drivers.move_parameters as move_parameters
from cheshire_drivers.labware_interfaces import ITipRack
from cheshire_drivers.plr.labware import PLRTipRackAdapter
from pylabrobot.resources import flex_96_tiprack_1000ul


def test_move_parameters_reaches_for_no_labware_catalog() -> None:
    """The layer that used to live here is the engine's: it holds the labware and
    the site at once, which is what makes the height knowable rather than guessed
    from a name that may not match."""
    source = inspect.getsource(move_parameters)

    assert "labware_seed" not in source
    assert "load_labware_seed" not in source


def test_a_tip_rack_states_its_own_height() -> None:
    """The height the wrapper used to hunt for by name is on the labware. The
    adapter has to actually forward it: a protocol member left unimplemented
    inherits the `...` stub and answers None, which is the silent miss again."""
    rack: ITipRack = PLRTipRackAdapter(flex_96_tiprack_1000ul("rack"))

    assert rack.size_z == 99.0
