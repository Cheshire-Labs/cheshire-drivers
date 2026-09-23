"""Merging one sparse layer of parameters over an already-resolved record.

Move parameters and pipetting parameters are the same shape twice: a total
record of what a command runs under, and sparse patches that narrow it. They
merge the same way, so they merge here.
"""

from typing import TypeVar

from pydantic import BaseModel

ResolvedT = TypeVar("ResolvedT", bound=BaseModel)


def merge_patch_over(patch: BaseModel, parameters: ResolvedT) -> ResolvedT:
    """One layer merged over the record under it, field by field.

    Re-validated rather than copied in: a bound added to these fields later
    should hold on the merged record too, not only on what each layer declared.

    A field the patch leaves None is inherited, because None is how a patch says
    it has no opinion. That is what makes a layer narrow, and it is why a patch
    can set a value but never put one back to None: a layer that wants the
    driver's own default says so by not being in the stack at all.

    A patch from the wrong family is caught by the record's own
    ``extra="forbid"`` when the merge re-validates, not by the type checker:
    "the patch's fields are a subset of the record's" is not a relation Python's
    types can state.
    """
    merged = {**parameters.model_dump(), **patch.model_dump(exclude_none=True)}
    return type(parameters).model_validate(merged)
