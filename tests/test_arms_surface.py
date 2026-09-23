"""Arm/transporter symbols on the cheshire_drivers surface.

cheshire_drivers hard-requires `pylabrobot.legacy.arms`: the pinned PyLabRobot's
`pylabrobot.liquid_handling` package eagerly imports the Hamilton STAR backend,
which imports `pylabrobot.legacy.arms.standard`. `plr_wrappers` imports the
liquid handler, so importing it always pulls in arms.

The package `__init__` used to import `plr_wrappers`, which made `import
cheshire_drivers` pull in arms too. It serves names lazily now, so the
coupling is pinned where it lives rather than at the package.
"""

import subprocess
import sys
import textwrap

_ARM_SYMBOLS = (
    "PLRArmBackend",
    "PLRTransporterBackendWrapper",
    "convert_cartesian_to_plr_coord",
    "convert_joint_to_plr_dict",
    "transporter_driver",
)


def test_importing_the_plr_wrappers_pulls_in_pylabrobot_arms() -> None:
    """Importing `plr_wrappers` loads pylabrobot.legacy.arms (upstream
    liquid_handling -> STAR -> arms). If this flips, upstream decoupled arms.
    Runs in a subprocess so the assertion reflects a clean import, not the
    suite's cache."""
    script = textwrap.dedent(
        """
        import sys
        import cheshire_drivers.plr_wrappers
        assert "pylabrobot.legacy.arms" in sys.modules, "pylabrobot.legacy.arms not eagerly loaded"
        print("ARMS_EAGER")
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "ARMS_EAGER" in result.stdout


def test_arm_symbols_are_re_exported_on_the_surface() -> None:
    """The arm symbols stay on the top-level surface (in __all__ and as real
    module attributes) and resolve to the same objects as `cheshire_drivers.plr`."""
    import cheshire_drivers
    from cheshire_drivers import plr

    for name in _ARM_SYMBOLS:
        assert name in cheshire_drivers.__all__, f"{name} dropped from __all__"
        assert getattr(cheshire_drivers, name) is getattr(plr, name)


def test_transporter_symbols_resolve_from_plr_package() -> None:
    """With arms installed (this env), the transporter symbols import from the
    documented `cheshire_drivers.plr` home alongside the concrete driver."""
    from cheshire_drivers.plr import (
        PLRArmBackend,
        PLRTransporterBackendWrapper,
        PreciseFlexTransporterDriver,
        convert_cartesian_to_plr_coord,
        convert_joint_to_plr_dict,
        transporter_driver,
    )

    assert PLRArmBackend is not None
    assert PLRTransporterBackendWrapper is not None
    assert PreciseFlexTransporterDriver is not None
    assert convert_cartesian_to_plr_coord is not None
    assert convert_joint_to_plr_dict is not None
    assert transporter_driver is not None
