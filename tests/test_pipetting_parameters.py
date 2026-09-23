"""The resolved pipetting record and the sparse patch layers contribute."""

import json

import pytest
from pydantic import ValidationError

from cheshire_drivers.liquid_handler_models import (
    SEED_PIPETTING_PARAMETERS,
    MixParamsModel,
    PipettingParameters,
    PipettingPatch,
)


class TestPipettingParameters:
    def test_the_seed_states_nothing_a_driver_did_not_already_do(self) -> None:
        """Seeding a deployment from this must change nothing an unconfigured one
        did, so height and flow rate leave the machine's own defaults alone."""
        assert SEED_PIPETTING_PARAMETERS.height is None
        assert SEED_PIPETTING_PARAMETERS.flow_rate is None
        assert SEED_PIPETTING_PARAMETERS.blow_out is False
        assert SEED_PIPETTING_PARAMETERS.mix is None

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PipettingParameters(submerge_depth=2.0)  # type: ignore[call-arg]

    def test_round_trips_through_json(self) -> None:
        params = PipettingParameters(
            height=4.0,
            flow_rate=25.0,
            blow_out=True,
            blow_out_volume=10.0,
            mix=MixParamsModel(volume=50.0, repetitions=3, flow_rate=100.0),
        )
        assert PipettingParameters(**json.loads(params.model_dump_json())) == params

    def test_height_none_is_a_value_meaning_use_the_machines_own_clearance(self) -> None:
        assert "height" in PipettingParameters().model_dump()
        assert PipettingParameters().height is None


class TestPipettingPatch:
    def test_every_field_is_optional(self) -> None:
        assert PipettingPatch().model_dump(exclude_none=True) == {}

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PipettingPatch(submerge_depth=2.0)  # type: ignore[call-arg]

    def test_an_empty_patch_changes_nothing(self) -> None:
        base = PipettingParameters(height=3.0)
        assert PipettingPatch().apply_to(base) == base

    def test_a_patch_sets_only_the_fields_it_names(self) -> None:
        base = PipettingParameters(height=3.0, flow_rate=10.0)

        merged = PipettingPatch(flow_rate=25.0).apply_to(base)

        assert merged.flow_rate == 25.0
        assert merged.height == 3.0

    def test_the_step_wins_over_the_liquid_for_the_field_it_names(self) -> None:
        """The layering the whole model exists for: a liquid says how it behaves,
        a step says what this one call needs, and the step is the narrower of the
        two. Neither is missing a knob the other has."""
        glycerol = PipettingPatch(flow_rate=20.0, height=2.0)
        top_up = PipettingPatch(flow_rate=5.0)

        merged = top_up.apply_to(glycerol.apply_to(PipettingParameters()))

        assert merged.flow_rate == 5.0
        assert merged.height == 2.0

    def test_none_means_inherit_not_clear(self) -> None:
        """A patch cannot blank a field; that is what makes layers narrow."""
        merged = PipettingPatch(height=None).apply_to(PipettingParameters(height=6.0))

        assert merged.height == 6.0

    def test_a_patch_can_turn_a_blow_out_on(self) -> None:
        merged = PipettingPatch(blow_out=True).apply_to(PipettingParameters())

        assert merged.blow_out is True

    def test_a_patch_can_turn_a_blow_out_back_off(self) -> None:
        """False is a value a patch states, unlike None which says nothing. So a
        narrower layer can switch a blow-out off that a wider one turned on."""
        merged = PipettingPatch(blow_out=False).apply_to(
            PipettingParameters(blow_out=True)
        )

        assert merged.blow_out is False

    def test_applying_a_patch_does_not_mutate_the_record_it_was_applied_to(self) -> None:
        base = PipettingParameters(flow_rate=10.0)

        PipettingPatch(flow_rate=99.0).apply_to(base)

        assert base.flow_rate == 10.0

    def test_the_two_models_carry_the_same_fields(self) -> None:
        """A field on one and not the other is a layer that cannot be overridden."""
        assert set(PipettingPatch.model_fields) == set(PipettingParameters.model_fields)
