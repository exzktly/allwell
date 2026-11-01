"""Export-group membership chosen by sample variable rather than by well.

Batch Export's group editor started out with one way in: paint wells on its
plate map. A plate annotated in the Variables tab already says which wells hold
``HeLa`` or ``10 µM``, so picking those wells again by eye is both slow and the
easiest way to miss one. This module answers the two questions the editor asks
of a variable, without Qt:

* :func:`membership_for_value` — which members and solo wells "every well
  holding *this value*" adds to (or takes out of) a group;
* :func:`groups_per_value` — one group per value of a variable, in the
  variable's own order, for Quick Setup.

Both resolve to the same thing a plate drag builds — replicate sets and solo
wells — so a group made this way persists, exports and edits exactly like one
made by hand. The selection is resolved **when it is made**: a group does not
track the variable afterwards, the same as Quick Setup's rows and columns do
not track the plate.

**A replicate set joins only when every one of its loaded wells holds the
value.** A plate drag pulls in a well's whole set, which is right when the set
is one condition. A set whose wells disagree about this variable (``MIXED`` in
:func:`review.domain.sample_variables.roll_up`) is not one condition as far as
the variable is concerned, and pulling the whole set in would export wells that
do not hold the value under that value's name. So the wells that *do* hold it
join as solo wells, and the set is named in ``split_sets`` so the editor can
say so — degraded output stated in the artifact, never silently substituted.

**Unassigned wells join nothing.** A well never given a value for the variable
is not evidence of any value; :func:`groups_per_value` returns them in
``unassigned`` for the caller to report, rather than inventing a group for
them or dropping them without a word.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from review.domain.sample_variables import VariableSet, wells_with_value


@dataclass
class ValueMembership:
    """What "the wells holding one value" resolves to on this plate.

    *members* are replicate sets wholly inside the value, *solo_wells* the
    matching wells that are in no set or in a set that was split, and
    *split_sets* the names of the sets that were split. *wells* is every
    matching loaded well, in plate order.
    """

    wells: list[str] = field(default_factory=list)
    members: list = field(default_factory=list)
    solo_wells: list[str] = field(default_factory=list)
    split_sets: list[str] = field(default_factory=list)


@dataclass
class ValueGroup:
    """One Quick Setup group: a value's label and its membership."""

    name: str
    value_id: str
    membership: ValueMembership


def membership_for_value(vset: VariableSet, variable_id: str,
                         value_id: str | None, loaded_wells,
                         rep_sets) -> ValueMembership:
    """Resolve the wells of *loaded_wells* holding *value_id* into members.

    *loaded_wells* is the dataset's wells in plate order; *rep_sets* the loaded
    replicate sets (``plot_replicate_sets_loaded``). ``value_id=None`` selects
    the wells with no value for the variable — offered so a user can find the
    gaps, not used by :func:`groups_per_value`.
    """
    loaded = list(loaded_wells)
    loaded_set = set(loaded)
    matching = set(wells_with_value(vset, variable_id, value_id, loaded))
    out = ValueMembership(wells=[w for w in loaded if w in matching])
    claimed: set[str] = set()
    for rset in rep_sets:
        set_wells = [w for w in rset.wells if w in loaded_set]
        inside = [w for w in set_wells if w in matching]
        if not inside:
            continue
        if len(inside) == len(set_wells):
            out.members.append(rset)
            claimed.update(inside)
        else:
            out.split_sets.append(rset.name)
    out.solo_wells = [w for w in out.wells if w not in claimed]
    return out


def groups_per_value(vset: VariableSet, variable_id: str, loaded_wells,
                     rep_sets) -> tuple[list[ValueGroup], list[str]]:
    """``(groups, unassigned wells)``: one group per value holding a loaded well.

    In the variable's declared value order — the order its legend and axis
    use — and named by the value's label with its unit. A value no loaded well
    holds makes no group, because an empty group exports an empty figure.
    """
    variable = vset.by_id(variable_id)
    if variable is None:
        return [], []
    loaded = list(loaded_wells)
    groups: list[ValueGroup] = []
    for value in variable.values:
        membership = membership_for_value(vset, variable_id, value.id, loaded,
                                          rep_sets)
        if membership.wells:
            groups.append(ValueGroup(variable.display_label(value.id) or value.label,
                                     value.id, membership))
    unassigned = wells_with_value(vset, variable_id, None, loaded)
    unassigned_set = set(unassigned)
    return groups, [w for w in loaded if w in unassigned_set]


def add_membership(grp, membership: ValueMembership) -> int:
    """Add *membership* to *grp*; return how many wells the group gained.

    Sets are matched by **name**, as the plate drag matches them: rep-sets are
    rebuilt fresh on every read and have no ``__eq__``.
    """
    before = set(grp.wells)
    names = {m.name for m in grp.members}
    for rset in membership.members:
        if rset.name not in names:
            grp.members.append(rset)
            names.add(rset.name)
    in_group = set(grp.wells)
    for w in membership.solo_wells:
        if w not in in_group:
            grp.solo_wells.append(w)
            in_group.add(w)
    return len(set(grp.wells) - before)


def remove_membership(grp, membership: ValueMembership) -> tuple[int, list[str]]:
    """Take *membership*'s wells out of *grp*.

    Returns ``(wells removed, sets kept)``. A set in the group that holds some
    matching wells and some others is **kept** and named in the second list:
    removing it would take wells that do not hold the value out with it, and
    keeping it is the only answer that leaves the group as it was for those
    wells. "Wholly inside" is the same test :func:`membership_for_value` made,
    over the loaded wells only, so a set is never kept for a well the dataset
    does not have. The editor reports it, so the user can drag the set off by hand.
    """
    matching = set(membership.wells)
    whole = {m.name for m in membership.members}
    before = set(grp.wells)
    kept: list[str] = []
    remaining = []
    for rset in grp.members:
        if rset.name in whole:
            continue
        if matching.intersection(rset.wells):
            kept.append(rset.name)
        remaining.append(rset)
    grp.members[:] = remaining
    grp.solo_wells[:] = [w for w in grp.solo_wells if w not in matching]
    return len(before - set(grp.wells)), kept
