"""The liquid handler with protocol composites implement both interfaces."""

from cheshire_drivers.interfaces import (
    BaseDriver,
    ILiquidHandlerWithProtocolDriver,
    IProtocolRunnerDriver,
)
from cheshire_drivers.liquid_handler_models import _StrictModel
from cheshire_drivers.sims import SimLiquidHandlerWithProtocolDriver


class TestLiquidHandlerWithProtocolComposite:
    """Composite LH drivers nominally implement the union interface.

    orca-core types its liquid-handler devices on `ILiquidHandlerWithProtocolDriver`
    (well-level verbs AND run_protocol) and assigns these concrete classes to that
    type on the check-run resolve path, so the nominal subclass relationship must
    hold or the typing breaks silently.
    """

    def test_sim_composite_implements_union_interface(self) -> None:
        assert issubclass(
            SimLiquidHandlerWithProtocolDriver, ILiquidHandlerWithProtocolDriver
        )

    def test_chatterbox_composite_implements_union_interface(self) -> None:
        from cheshire_drivers.plr.liquid_handler import (
            ChatterboxLiquidHandlerWithProtocolDriver,
        )

        assert issubclass(
            ChatterboxLiquidHandlerWithProtocolDriver, ILiquidHandlerWithProtocolDriver
        )
