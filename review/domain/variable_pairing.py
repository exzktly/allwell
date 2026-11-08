"""Pair each plotted sample with its control by what the plate says it is.

The per-sample control editor can already pair members by **position** —
``fold_change_pairing.auto_pair`` walks the draw order in blocks of *N*. That is
right for a plate laid out in repeating blocks and wrong for every other plate:
reorder the groups, hide one, or switch pooling off, and the blocks slide across
condition boundaries without anything on screen saying so.

This module pairs by **annotation** instead. The user states the rule the way
they would say it out loud — *within each Cell line, the control is the sample
whose Compound is DMSO* — and each member is paired with the one member that
holds the reference value on the reference variable and agrees with it on every
"within each" variable. The sample variables (``domain/sample_variables``) are
already the plate's own description of what each well is, so the rule reads
from the same place the Response tab's x axis and the Colour-by legend do, and
cannot disagree with them.

**A member that cannot be paired is named, with the reason, never guessed.**
Four things can go wrong and each has a different fix, so each is its own
reason:

* the member is not annotated on a "within each" variable, or has no loaded
  wells — fill it in on Home → Variables;
* its wells disagree on one — the group mixes conditions;
* nothing matches — no sample holds the reference value for that condition;
* more than one sample matches — the rule does not single one out. The fix is
  a further "within each" variable (a replicate number, say) or pooling the
  candidates into one group. Picking the first candidate would silently
  normalise against one replicate of the control and discard the others.

A fifth reason, *the matching control cannot be pointed at*, covers a member
that a ``ControlRef`` cannot address — a cell of a Response *Group by* cross,
which is neither a user group nor a single well. The caller says which members
are addressable by leaving the others out of *member_controls*.

**Which variables "within each" means is the user's choice.** The default
(:func:`default_within`) is every other variable on which the control
candidates take more than one value. The reasoning: a variable on which every
control holds the *same* value is one the control does not vary along — the
vehicle wells all sit at Dose 0 — so matching on it would demand a vehicle at
every dose and pair nothing. A variable on which the controls *do* differ (one
vehicle per cell line) is exactly the one the user means. It is a default shown
in the editor as ticked boxes, not a hidden decision.

Qt-free, ctx-free and free of ``features/`` imports: the caller hands over the
variables, the members' wells and the references that address them, so every
rule here is driven by a fast-lane test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from review.domain.control_ref import ControlRef
from review.domain.sample_variables import MIXED, Variable, VariableSet, roll_up

#: Why a member was not paired. Stable machine spellings; the sentences that
#: go on screen are in :attr:`Unpaired.detail`.
NOT_ANNOTATED = "not_annotated"
WELLS_DISAGREE = "wells_disagree"
NO_MATCH = "no_match"
AMBIGUOUS = "ambiguous"
UNADDRESSABLE = "unaddressable"

REASONS = (NOT_ANNOTATED, WELLS_DISAGREE, NO_MATCH, AMBIGUOUS, UNADDRESSABLE)

#: How many unpaired members :func:`summary_text` names before counting the rest.
_NAMED_IN_SUMMARY = 4


@dataclass(frozen=True)
class Unpaired:
    """One member the rule could not pair, and why, in words."""

    member: str
    reason: str
    detail: str


@dataclass(frozen=True)
class VariablePairing:
    """What a pair-by-variable rule produced: the mapping and its shortfall."""

    #: ``member key -> ControlRef`` for every member the rule paired.
    mapping: dict = field(default_factory=dict)
    #: Every member it did not, in draw order.
    unpaired: tuple[Unpaired, ...] = ()
    #: How many members the rule was run over.
    total: int = 0


def annotating_variables(vset: VariableSet | None, member_wells) -> list[Variable]:
    """The variables assigned on at least one of the members' wells, in order.

    A variable that annotates none of the plotted wells says nothing about
    them, so it can be neither the reference nor a "within each" condition.
    """
    if vset is None:
        return []
    wells = {w for ws in (member_wells or {}).values() for w in (ws or ())}
    return [v for v in vset.variables if vset.assigned_wells(v.id) & wells]


def _value_of(vset: VariableSet, variable_id: str, wells) -> str | None:
    """The member's value id, ``MIXED``, or ``None`` when it has no wells or value."""
    return roll_up(vset, variable_id, wells) if wells else None


def control_candidates(vset: VariableSet, member_wells, variable_id: str,
                       value_id: str) -> list[str]:
    """The members holding *value_id* on *variable_id*, in draw order."""
    return [key for key, wells in (member_wells or {}).items()
            if _value_of(vset, variable_id, wells) == value_id]


def default_within(vset: VariableSet | None, member_wells, variable_id: str,
                   value_id: str) -> tuple[str, ...]:
    """The "within each" variables the editor ticks by default.

    Every other annotating variable on which the control candidates hold more
    than one value — see the module docstring for why one held constant across
    the controls is left out.
    """
    if vset is None or vset.by_id(variable_id) is None:
        return ()
    candidates = control_candidates(vset, member_wells, variable_id, value_id)
    out: list[str] = []
    for variable in annotating_variables(vset, member_wells):
        if variable.id == variable_id:
            continue
        values = {_value_of(vset, variable.id, member_wells[key])
                  for key in candidates}
        values.discard(None)
        values.discard(MIXED)
        if len(values) > 1:
            out.append(variable.id)
    return tuple(out)


def _condition_text(vset: VariableSet, variable_id: str, value_id: str,
                    within: tuple[str, ...], signature: tuple) -> str:
    """``Compound = DMSO and Cell line = HeLa`` — the rule, for one condition."""
    parts = []
    reference = vset.by_id(variable_id)
    if reference is not None:
        parts.append(f"{reference.name} = {reference.display_label(value_id)}")
    for vid, val in zip(within, signature):
        variable = vset.by_id(vid)
        if variable is not None:
            parts.append(f"{variable.name} = {variable.display_label(val)}")
    return " and ".join(parts)


def pair_by_variable(vset: VariableSet | None, member_wells, member_controls, *,
                     variable_id: str, value_id: str,
                     within=()) -> VariablePairing:
    """Pair every member of *member_wells* with its control under the rule.

    *member_wells* is ``member key -> wells``, in draw order
    (``plot_members.member_wells``). *member_controls* is ``member key ->
    ControlRef`` addressing **that member**; a member missing from it cannot be
    used as a control (see :data:`UNADDRESSABLE`). *within* is the "within
    each" variable ids; the reference variable is dropped from it if present.

    A control candidate pairs with itself, which the outcome vocabulary reports
    as ``SELF`` — it is drawn at 1.0, the reference every other member of its
    condition is read against.
    """
    wells_for = dict(member_wells or {})
    keys = list(wells_for)
    refs = {str(k): ControlRef.coerce(v) for k, v in (member_controls or {}).items()}
    reference = vset.by_id(variable_id) if vset is not None else None
    if reference is None or reference.value_by_id(value_id) is None:
        detail = "the reference variable or value no longer exists"
        return VariablePairing(
            unpaired=tuple(Unpaired(k, NO_MATCH, detail) for k in keys),
            total=len(keys))
    within = tuple(dict.fromkeys(
        str(v) for v in (within or ())
        if v != variable_id and vset.by_id(v) is not None))

    def signature(key: str):
        """``(values, None)`` or ``(None, Unpaired)`` for *key*'s condition."""
        wells = wells_for.get(key) or ()
        if not wells:
            return None, Unpaired(key, NOT_ANNOTATED, "has no loaded wells")
        values = []
        for vid in within:
            value = roll_up(vset, vid, wells)
            name = vset.by_id(vid).name
            if value is None:
                return None, Unpaired(key, NOT_ANNOTATED, f"{name} is not set")
            if value == MIXED:
                return None, Unpaired(
                    key, WELLS_DISAGREE, f"its wells disagree on {name}")
            values.append(value)
        return tuple(values), None

    by_condition: dict[tuple, list[str]] = {}
    for key in control_candidates(vset, wells_for, variable_id, value_id):
        sig, problem = signature(key)
        if problem is None:
            by_condition.setdefault(sig, []).append(key)

    mapping: dict = {}
    unpaired: list[Unpaired] = []
    for key in keys:
        sig, problem = signature(key)
        if problem is not None:
            unpaired.append(problem)
            continue
        matches = by_condition.get(sig, [])
        condition = _condition_text(vset, variable_id, value_id, within, sig)
        if not matches:
            unpaired.append(Unpaired(key, NO_MATCH, f"no sample with {condition}"))
            continue
        if len(matches) > 1:
            unpaired.append(Unpaired(
                key, AMBIGUOUS,
                f"{', '.join(matches)} all have {condition} — tick another "
                f"“within each” variable, or pool them into one group"))
            continue
        ref = refs.get(matches[0])
        if ref is None:
            unpaired.append(Unpaired(
                key, UNADDRESSABLE,
                f"its control {matches[0]} is neither a group nor a well, so "
                f"it cannot be chosen as a control"))
            continue
        mapping[key] = ref
    return VariablePairing(mapping=mapping, unpaired=tuple(unpaired),
                           total=len(keys))


def apply_pairing(current: dict, pairing: VariablePairing) -> dict:
    """The control map after *pairing* is applied on top of *current*.

    Paired members take their new control. **Unpaired members lose any control
    they had**: the rule was run over them and found none, and leaving an
    earlier assignment in place would show the row as "paired" directly under
    a sentence saying it was not. Members the rule was not run over keep
    theirs.
    """
    out = dict(current or {})
    out.update(pairing.mapping)
    for item in pairing.unpaired:
        out.pop(item.member, None)
    return out


def summary_text(pairing: VariablePairing) -> str:
    """``Paired 10 of 12. Not paired: X — …; Y — …`` for the editor."""
    head = f"Paired {len(pairing.mapping)} of {pairing.total}."
    if not pairing.unpaired:
        return head
    named = "; ".join(f"{u.member} — {u.detail}"
                      for u in pairing.unpaired[:_NAMED_IN_SUMMARY])
    rest = len(pairing.unpaired) - _NAMED_IN_SUMMARY
    more = f"; and {rest} more" if rest > 0 else ""
    return f"{head} Not paired: {named}{more}."
