"""The resolved move-parameter record and the sparse patch layers contribute."""

import json
from typing import get_args

import pytest
from pydantic import ValidationError

from cheshire_drivers.move_parameters import (
    MoveParameterField,
    MoveParameterPatch,
    MoveParameters,
)


def _params(**overrides: str | float | None) -> MoveParameters:
    base: dict[str, str | float | None] = dict(
        access_type="vertical",
        clearance=20.0,
        z_above=0.0,
        grasp_offset=20.0,
        resource_width=75.0,
        resource_height=14.35,
        travel_margin=10.0,
        jaw_opening=14.0,
        plate_present_margin=2.0,
        z_offset=0.0,
        speed=None,
    )
    base.update(overrides)
    return MoveParameters.model_validate(base)


class TestMoveParameters:
    def test_every_field_is_required(self) -> None:
        with pytest.raises(ValidationError):
            MoveParameters.model_validate({"access_type": "vertical"})

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _params(grip_height=4.0)

    def test_access_type_is_constrained_to_the_two_the_arm_understands(self) -> None:
        with pytest.raises(ValidationError):
            _params(access_type="diagonal")

    def test_round_trips_through_json(self) -> None:
        params = _params(speed=25.0)
        assert MoveParameters(**json.loads(params.model_dump_json())) == params

    def test_speed_none_is_a_value_meaning_leave_the_arm_alone(self) -> None:
        assert "speed" in _params().model_dump()
        assert _params().speed is None


class TestMoveParameterPatch:
    def test_every_field_is_optional(self) -> None:
        assert MoveParameterPatch().model_dump(exclude_none=True) == {}

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MoveParameterPatch.model_validate({"grip_height": 4.0})

    def test_an_empty_patch_changes_nothing(self) -> None:
        base = _params()
        assert MoveParameterPatch().apply_to(base) == base

    def test_a_patch_sets_only_the_fields_it_names(self) -> None:
        merged = MoveParameterPatch(resource_height=43.5).apply_to(_params())

        assert merged.resource_height == 43.5
        assert merged.resource_width == 75.0
        assert merged.clearance == 20.0
        assert merged.jaw_opening == 14.0

    def test_a_later_patch_wins_for_the_field_it_names_and_leaves_the_rest(self) -> None:
        first = MoveParameterPatch(resource_height=43.5, jaw_opening=14.0)
        second = MoveParameterPatch(jaw_opening=18.0)

        merged = second.apply_to(first.apply_to(_params()))

        assert merged.jaw_opening == 18.0
        assert merged.resource_height == 43.5

    def test_none_means_inherit_not_clear(self) -> None:
        """A patch cannot blank a field; that is what makes layers narrow."""
        merged = MoveParameterPatch(jaw_opening=None).apply_to(_params(jaw_opening=14.0))

        assert merged.jaw_opening == 14.0

    def test_a_patch_can_set_speed_that_the_defaults_left_alone(self) -> None:
        assert MoveParameterPatch(speed=20.0).apply_to(_params()).speed == 20.0

    def test_applying_a_patch_does_not_mutate_the_record_it_was_applied_to(self) -> None:
        base = _params()
        MoveParameterPatch(resource_width=85.0).apply_to(base)

        assert base.resource_width == 75.0

    def test_the_two_models_carry_the_same_fields(self) -> None:
        """A field on one and not the other is a layer that cannot be overridden."""
        assert set(MoveParameterPatch.model_fields) == set(MoveParameters.model_fields)


class TestMoveParameterPatchLayering:
    """Merging one sparse layer into another, which is how a stored patch grows."""

    def test_a_patch_over_another_stays_sparse(self) -> None:
        """Neither layer's silence may turn into a number, or a stored patch would
        start pinning fields its author never chose."""
        merged = MoveParameterPatch(jaw_opening=18.0).over(
            MoveParameterPatch(travel_margin=25.0),
        )

        assert merged.model_dump(exclude_none=True) == {
            "travel_margin": 25.0,
            "jaw_opening": 18.0,
        }

    def test_the_later_patch_wins_the_field_both_name(self) -> None:
        merged = MoveParameterPatch(travel_margin=25.0).over(
            MoveParameterPatch(travel_margin=10.0, jaw_opening=18.0),
        )

        assert merged.travel_margin == 25.0
        assert merged.jaw_opening == 18.0

    def test_over_leaves_the_earlier_patch_untouched(self) -> None:
        base = MoveParameterPatch(travel_margin=10.0)

        MoveParameterPatch(travel_margin=25.0).over(base)

        assert base.travel_margin == 10.0

    def test_without_puts_a_field_back_to_inheriting(self) -> None:
        trimmed = MoveParameterPatch(travel_margin=25.0, jaw_opening=18.0).without(
            ["travel_margin"],
        )

        assert trimmed.travel_margin is None
        assert trimmed.jaw_opening == 18.0

    def test_without_a_field_the_patch_never_set_changes_nothing(self) -> None:
        patch = MoveParameterPatch(jaw_opening=18.0)

        assert patch.without(["speed"]) == patch

    def test_clearing_speed_leaves_the_arm_on_its_own_setting(self) -> None:
        """The case `without` exists for: `speed` is the one field whose resolved
        value can be None, and a patch has no way to ask for that."""
        slowed = MoveParameterPatch(speed=20.0)

        resolved = slowed.without(["speed"]).apply_to(_params(speed=None))

        assert resolved.speed is None

    def test_clearing_before_merging_would_take_the_field_straight_back(self) -> None:
        """Pins the order an edit composes in: clear last, or the stored patch
        resupplies the field the operator just asked to be rid of."""
        stored = MoveParameterPatch(travel_margin=25.0)
        edit = MoveParameterPatch(jaw_opening=18.0)

        assert edit.over(stored).without(["travel_margin"]).travel_margin is None
        assert edit.without(["travel_margin"]).over(stored).travel_margin == 25.0

    def test_the_field_names_are_the_patch_field_names(self) -> None:
        """A caller naming a field to clear is checked against this list, so a name
        missing from it is a field nobody can put back to inheriting."""
        assert set(get_args(MoveParameterField)) == set(MoveParameterPatch.model_fields)
