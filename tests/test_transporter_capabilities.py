"""The transporter's wire-only teachpoint model refuses unknown fields."""

from cheshire_drivers.liquid_handler_models import _StrictModel
from cheshire_drivers.transporter_models import TeachpointModel


def test_teachpoint_model_is_strict_mode() -> None:
    assert issubclass(TeachpointModel, _StrictModel)
    assert TeachpointModel.model_config.get("extra") == "forbid"
