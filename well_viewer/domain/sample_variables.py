"""Sample variables — the annotation axes a user declares across a plate.

A **variable** is anything that was varied across the plate: `Cell line` with
values `HeLa` / `U2OS`, `Dose` with values `0 … 10 µM`. It is not a group. A
plate of two cell lines at six doses has twelve conditions, and a user with
variables defined does not have to paint twelve group names to get twelve series.

**Values are objects, not typed strings.** Each variable carries an *ordered*
list of :class:`VariableValue`, each with its own id. That list is what makes
renaming a value rewrite every well holding it, and what stops ``1uM`` /
``1 µM`` / ``1`` accumulating as three distinct doses in a free-text field. The
order is the user's and is what a plot's x axis and a legend read — for a
`number` variable it is also checked against the numbers, because a dose series
listed out of order is a curve drawn backwards.

**Values live per well, and the group is a brush.** The alternative — one value
per :class:`Selection` — was rejected on three checked facts. Groups partition
the plate (``sel_strip_wells``: "A well belongs to ≤1 group"), so a per-well
value rolls up to a group unambiguously while the reverse cannot annotate an
ungrouped well at all. ``make_selection`` is keyword-only and rebuilds a fresh
dict, so a field smuggled onto a selection is dropped by any of its five call
sites. And ``bar_pool_replicates`` flips the plot key space from set names to
well tokens mid-plot, which is precisely the trap ``member_replicate_sets``
exists to record: a mapping written against the sets is orphaned by a toggle
that has nothing to do with it.

**Nothing here ever invents a value.** A well added to a group after the
variables were filled has none, and does not inherit one from its neighbours.
:func:`roll_up` returns :data:`MIXED` for a set whose wells disagree and
``None`` for one where any well is unassigned — two distinct answers, because
"these wells say different things" and "this well was never annotated" are
different facts and only one of them is the user's mistake. A caller that
flattens either into a value is fabricating an annotation on a real
measurement, which is §7's cardinal sin.

Qt-free and side-effect-free. Persistence is
``well_viewer/persistence/sample_variables.py``; the editing UI is
``features/home/variables/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

#: What :func:`roll_up` returns for a set of wells that do not agree. A sentinel
#: rather than ``None``, because a caller must be able to tell "they disagree"
#: from "nothing is assigned" — the first is shown as ``Mixed (2)`` and the
#: second as an unassigned count, and collapsing them loses the only signal that
#: a group was edited after its variables were filled.
MIXED = "—mixed—"

#: The two kinds a variable can be. ``number`` is what makes a variable usable
#: as a plot's x axis, and is the only kind that carries a unit.
TEXT = "text"
NUMBER = "number"
KINDS = (TEXT, NUMBER)


@dataclass(frozen=True)
class VariableValue:
    """One value of a variable — ``HeLa``, or ``1 µM``.

    *id* is stable across a rename, which is what lets the label change in one
    place instead of being rewritten into every well. It is never shown.

    *number* is the numeric reading for a ``number`` variable, parsed once at
    entry rather than re-parsed at every plot. ``None`` on a ``text`` variable,
    and also on a ``number`` variable's value that could not be read as one —
    which is kept rather than rejected, because a dose series with a "vehicle"
    row in it is a real plate, and the plot decides what to do with a value that
    has no place on a numeric axis. It must not be silently read as zero: zero
    is a dose.
    """

    id: str
    label: str
    number: float | None = None


@dataclass(frozen=True)
class Variable:
    """One annotation axis, with its values in the user's order."""

    id: str
    name: str
    kind: str = TEXT
    unit: str = ""
    values: tuple[VariableValue, ...] = ()

    def is_numeric(self) -> bool:
        return self.kind == NUMBER

    def value_by_id(self, value_id: str) -> VariableValue | None:
        for value in self.values:
            if value.id == value_id:
                return value
        return None

    def display_label(self, value_id: str) -> str:
        """*value_id*'s label with the unit, or ``""`` for an id this no longer holds.

        An empty string rather than the raw id: an id is internal and showing
        one tells the user nothing, while a value deleted from the definition
        while wells still pointed at it is a state :func:`prune_values` and
        :func:`from_block` both clean up. :func:`orphaned_wells` is what a guard
        asserts is empty; nothing in the app expects to display one.
        """
        value = self.value_by_id(value_id)
        if value is None:
            return ""
        return f"{value.label} {self.unit}".strip() if self.unit else value.label


@dataclass(frozen=True)
class VariableSet:
    """Every variable on a plate, and which value each well holds.

    ``assignments`` is ``{variable_id: {well_token: value_id}}``. A well absent
    from a variable's map is **unassigned for that variable** — which is a fact
    to report, never a value to fill in.
    """

    variables: tuple[Variable, ...] = ()
    assignments: dict[str, dict[str, str]] = field(default_factory=dict)

    def by_id(self, variable_id: str) -> Variable | None:
        for variable in self.variables:
            if variable.id == variable_id:
                return variable
        return None

    def by_name(self, name: str) -> Variable | None:
        folded = (name or "").strip().casefold()
        for variable in self.variables:
            if variable.name.strip().casefold() == folded:
                return variable
        return None

    def value_id_for(self, variable_id: str, well: str) -> str | None:
        return self.assignments.get(variable_id, {}).get(well)

    def assigned_wells(self, variable_id: str) -> set[str]:
        return set(self.assignments.get(variable_id, {}))


def unassigned_wells(vset: VariableSet, variable_id: str, wells) -> list[str]:
    """The wells of *wells* carrying no value for *variable_id*, sorted.

    The plate rings these and the tab counts them. Reported rather than filled,
    because the commonest way to get here is adding a well to a group after the
    fill ran — and the only honest thing to do about it is say so.
    """
    return sorted(set(wells) - vset.assigned_wells(variable_id))


def orphaned_wells(vset: VariableSet, variable_id: str) -> list[str]:
    """Wells pointing at a value the variable no longer defines, sorted.

    Only reachable if a value was removed without :func:`prune_values`, so this
    is what a guard asserts is empty rather than something the UI expects to
    show. It exists because the alternative — a well silently reading as
    unassigned — is indistinguishable from a well that was never filled.

    **No production caller, by design.** This is what a guard asserts is
    empty, not something the UI shows — so a dead-code sweep will find it
    unreferenced outside ``tests/`` and should leave it there.
    """
    variable = vset.by_id(variable_id)
    if variable is None:
        return sorted(vset.assignments.get(variable_id, {}))
    known = {value.id for value in variable.values}
    return sorted(well for well, value_id
                  in vset.assignments.get(variable_id, {}).items()
                  if value_id not in known)


def coverage(vset: VariableSet, variable_id: str, wells) -> tuple[int, int]:
    """``(annotated, total)`` for *variable_id* over *wells*.

    The answer to "am I finished?", which is the question this whole tab is
    asked and the one it used to answer worst. The rail listed each variable's
    **value count** — a number the user typed in themselves and can see in the
    list beside it — so telling whether a plate was fully annotated meant
    selecting each variable in turn and reading a sentence under the plate.
    A pair per variable puts every answer on screen at once.

    Counted over *wells* rather than over the whole assignment map: a plate
    imaged in half has forty-eight wells, and ``48/48`` is finished while
    ``48/96`` is not. An assignment for a well the dataset does not hold — left
    behind by a dataset swap — is not evidence of coverage and does not count.
    """
    wells = set(wells)
    return len(wells & vset.assigned_wells(variable_id)), len(wells)


def wells_with_value(vset: VariableSet, variable_id: str,
                     value_id: str | None, wells) -> list[str]:
    """The wells of *wells* holding *value_id*, sorted; ``None`` means unassigned.

    What "select every well holding this dose" is made of. The tab could only
    ever paint that fact, so a user who needed to *act* on it — four wells got
    the wrong dose, which of them are they — had to find the wells by eye on a
    grid of ninety-six.
    """
    wells = set(wells)
    if value_id is None:
        return unassigned_wells(vset, variable_id, wells)
    mapping = vset.assignments.get(variable_id, {})
    return sorted(w for w in wells if mapping.get(w) == value_id)


def selection_summary(vset: VariableSet, variable_id: str,
                      wells) -> list[tuple[str | None, int]]:
    """What *wells* currently hold for *variable_id*: ``(value id, count)`` pairs.

    In the **variable's own order**, with unassigned last under ``None``, so a
    reader gets the same sequence here as in the value list, the legend and a
    plot's axis. Sorting by count instead would reorder the sentence every time
    a well changed hands.

    This is what a selection readout says, and it exists because the tab said
    nothing at all: no signal on the plate was connected, so a user who dragged
    a box saw no confirmation that anything had been picked, and the "select
    wells first" instruction stayed on screen after they had.
    """
    variable = vset.by_id(variable_id)
    mapping = vset.assignments.get(variable_id, {})
    held = [mapping.get(w) for w in wells]
    counts: list[tuple[str | None, int]] = []
    for value in (variable.values if variable is not None else ()):
        n = sum(1 for v in held if v == value.id)
        if n:
            counts.append((value.id, n))
    unassigned = sum(1 for v in held if v is None)
    if unassigned:
        counts.append((None, unassigned))
    return counts


def refusal_for_value(variable: Variable | None, label: str, *,
                      exclude: str = "") -> str | None:
    """Why adding or renaming a value to *label* would do nothing, or ``None``.

    :func:`add_value` and :func:`rename_value` both refuse a duplicate label by
    returning the set unchanged, which is the right rule and was delivered in
    the worst possible way: the entry box cleared itself and nothing appeared,
    or an edited row snapped back to its old text, with no word either way. The
    tab's own standard — *"a chip that appears inert is indistinguishable from
    one that is broken"* — was not being applied to its own value list.

    The reason is derived here rather than returned from the mutators so that
    their signatures stay what every other caller already expects, and so the
    sentence is a fact about the model that a test can ask for without a window.
    """
    if variable is None:
        return None
    label = (label or "").strip()
    if not label:
        return "Type a value first."
    twin = next((v for v in variable.values
                 if v.label.strip().casefold() == label.casefold()
                 and v.id != exclude), None)
    if twin is not None:
        return (f"“{variable.name}” already has a value called “{twin.label}”. "
                f"Two rows with one label would split that condition into two "
                f"series holding half the wells each.")
    return None


def roll_up(vset: VariableSet, variable_id: str, wells) -> str | None:
    """The one value *wells* share for *variable_id*, :data:`MIXED`, or ``None``.

    Three outcomes and they are deliberately not two:

    * a value id — every well in *wells* holds it;
    * :data:`MIXED` — they hold more than one, which is what a group row shows
      as ``Mixed (n)``;
    * ``None`` — *wells* is empty, or **any** well in it is unassigned.

    The last is the strict half and it is the point. A group of eight wells
    where seven say `1 µM` and one says nothing is not a `1 µM` group: a plotted
    member built from it would put an unannotated measurement under a dose it
    was never given. So the set answers "no value", the tab shows the shortfall,
    and the plot excludes the member with a stated count.
    """
    wells = list(wells)
    if not wells:
        return None
    mapping = vset.assignments.get(variable_id, {})
    seen = set()
    for well in wells:
        value_id = mapping.get(well)
        if value_id is None:
            # Unassigned wins over disagreement, and the whole list is read
            # before answering. Short-circuiting on the second distinct value
            # made the answer depend on the order the wells arrived in: the
            # same group read ``MIXED`` or ``None`` according to whether its
            # unassigned well happened to sit before or after the second value,
            # and the tab then printed two different sentences for one plate.
            return None
        seen.add(value_id)
    if len(seen) > 1:
        return MIXED
    return next(iter(seen))


def assign(vset: VariableSet, variable_id: str, wells, value_id: str | None) -> VariableSet:
    """*vset* with *wells* set to *value_id*, or cleared when it is ``None``.

    Pure; returns a new set. Assigning a value the variable does not define is
    refused rather than stored — an id that resolves to nothing is exactly the
    orphan state :func:`orphaned_wells` exists to detect, and creating one here
    would mean the editor could manufacture it.
    """
    variable = vset.by_id(variable_id)
    if variable is None:
        return vset
    if value_id is not None and variable.value_by_id(value_id) is None:
        return vset
    mapping = dict(vset.assignments.get(variable_id, {}))
    for well in wells:
        if value_id is None:
            mapping.pop(well, None)
        else:
            mapping[well] = value_id
    assignments = dict(vset.assignments)
    assignments[variable_id] = mapping
    return replace(vset, assignments=assignments)


def prune_values(vset: VariableSet, variable_id: str,
                 values: tuple[VariableValue, ...]) -> VariableSet:
    """Replace *variable_id*'s values with *values*, dropping wells left dangling.

    Called when the user removes or reorders values. A well pointing at a value
    that no longer exists is not silently retained: it becomes **unassigned**,
    which the plate rings and the tab counts, so removing a dose tells the user
    how many wells it cost them. Retaining the pointer would leave a set that
    looks complete and resolves to nothing at plot time.
    """
    variable = vset.by_id(variable_id)
    if variable is None:
        return vset
    known = {value.id for value in values}
    mapping = {well: value_id
               for well, value_id in vset.assignments.get(variable_id, {}).items()
               if value_id in known}
    assignments = dict(vset.assignments)
    assignments[variable_id] = mapping
    variables = tuple(replace(v, values=values) if v.id == variable_id else v
                      for v in vset.variables)
    return replace(vset, variables=variables, assignments=assignments)


def remove_variable(vset: VariableSet, variable_id: str) -> VariableSet:
    """*vset* without *variable_id*, and without its assignments."""
    assignments = {k: v for k, v in vset.assignments.items() if k != variable_id}
    return replace(
        vset,
        variables=tuple(v for v in vset.variables if v.id != variable_id),
        assignments=assignments,
    )


def fill(vset: VariableSet, variable_id: str, lines, *, repeat: int = 1) -> VariableSet:
    """Cycle *variable_id*'s values over *lines*, *repeat* **lines** each.

    A *line* is a sequence of wells that all take the same value — one plate row
    for a fill along rows, one column for a fill along columns. Grouping the
    wells into lines is the **caller's** job (``plate_layout.wells_in_fill_lines``):
    this module knows nothing about plate geometry, and mixing the two would put
    a second definition of "along columns" here beside that one.

    **The unit is the line, not the well**, and that is the whole point. Cycling
    over a flat well sequence made ``repeat`` count wells, so "along Rows ·
    repeat each 4" gave four wells per value rather than four rows and striped
    the plate instead of blocking it. It also meant a single absent well shifted
    every well after it along the cycle, handing one of them the wrong value
    with nothing on the plate to show it — a value is a value, so no ring, no
    count, no mention.

    Lines beyond the last cycle keep going round, because a plate is rarely an
    exact multiple: eight columns over six doses at two each fills the first
    four doses twice and then wraps, which is visible on the plate and better
    than four unassigned columns with no explanation.
    """
    variable = vset.by_id(variable_id)
    if variable is None or not variable.values:
        return vset
    repeat = max(1, int(repeat))
    out = vset
    for index, line in enumerate(lines):
        value = variable.values[(index // repeat) % len(variable.values)]
        out = assign(out, variable_id, list(line), value.id)
    return out


# ── the on-disk block ───────────────────────────────────────────────────────
#
# ``pipeline_info.json`` gains a ``variables`` block. It is written by
# ``persistence/sample_variables.py`` and read here, and everything below is
# pure so the round trip is testable without a dataset.

#: Bumped when a block written by an older version cannot be read as-is. There
#: is one shape so far; the number exists so the next one can be migrated rather
#: than discarded, which is what happened to every block that shipped without it.
SCHEMA_VERSION = 1


def to_block(vset: VariableSet) -> dict:
    """Serialise *vset* to the ``variables`` block.

    The caller merges this under the ``variables`` key of
    ``pipeline_info.json``, preserving every other key verbatim — the pipeline
    writes that file too, and a block-level rewrite that dropped its keys would
    take the run's own record with it.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "variables": [
            {
                "id": v.id,
                "name": v.name,
                "kind": v.kind,
                "unit": v.unit,
                "values": [
                    {"id": val.id, "label": val.label, "number": val.number}
                    for val in v.values
                ],
            }
            for v in vset.variables
        ],
        "assignments": {
            variable_id: dict(mapping)
            for variable_id, mapping in vset.assignments.items()
        },
    }


def from_block(block) -> VariableSet:
    """Parse a ``variables`` block. Anything unreadable is an empty set.

    This file is hand-editable, is written by two programs, and survives across
    app versions, so it is treated the way every other untrusted input here is:
    a block that is not a dict, a variable without an id, a value list that is
    not a list, a kind that is not one of :data:`KINDS` — each means "there are
    no variables to show", and a single malformed entry is dropped while the
    rest are kept. Raising instead would make one stray character cost the user
    a dataset that opens.

    Two repairs rather than rejections, because both are recoverable and
    dropping the variable would silently discard the user's whole annotation:
    a value id duplicated within one variable keeps its first occurrence (ids
    address values, so a duplicate makes assignments ambiguous), and an
    assignment pointing at a value the definition does not list is dropped —
    the well reads as unassigned, which the plate rings and the tab counts,
    rather than resolving to nothing at plot time.
    """
    if not isinstance(block, dict):
        return VariableSet()
    raw_variables = block.get("variables")
    if not isinstance(raw_variables, list):
        return VariableSet()

    variables: list[Variable] = []
    seen_variable_ids: set[str] = set()
    for item in raw_variables:
        if not isinstance(item, dict):
            continue
        variable_id = item.get("id")
        name = item.get("name")
        if not isinstance(variable_id, str) or not variable_id:
            continue
        if variable_id in seen_variable_ids:
            # The same argument the value-id dedupe below is written for, one
            # level up: ids address variables, so two sharing one id share one
            # ``assignments`` map, ``by_id`` answers with the first and
            # ``remove_variable`` deletes both.
            continue
        seen_variable_ids.add(variable_id)
        if not isinstance(name, str) or not name.strip():
            continue
        kind = item.get("kind")
        if kind not in KINDS:
            kind = TEXT
        unit = item.get("unit")
        unit = unit.strip() if isinstance(unit, str) and kind == NUMBER else ""
        values: list[VariableValue] = []
        seen_ids: set[str] = set()
        for raw in (item.get("values") or []):
            if not isinstance(raw, dict):
                continue
            value_id = raw.get("id")
            label = raw.get("label")
            if not isinstance(value_id, str) or not value_id or value_id in seen_ids:
                continue
            if not isinstance(label, str):
                continue
            number = raw.get("number")
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                number = None
            elif number != number or number in (float("inf"), float("-inf")):
                # NaN and the infinities pass ``isinstance(x, float)`` and then
                # break every axis, fit and sort they reach. Treated as "this
                # label is not a number", which is what they are.
                number = None
            seen_ids.add(value_id)
            values.append(VariableValue(id=value_id, label=label,
                                        number=None if number is None else float(number)))
        variables.append(Variable(id=variable_id, name=name, kind=kind,
                                  unit=unit, values=tuple(values)))

    known = {v.id: {val.id for val in v.values} for v in variables}
    assignments: dict[str, dict[str, str]] = {}
    raw_assignments = block.get("assignments")
    if isinstance(raw_assignments, dict):
        for variable_id, mapping in raw_assignments.items():
            if variable_id not in known or not isinstance(mapping, dict):
                continue
            assignments[variable_id] = {
                well: value_id for well, value_id in mapping.items()
                if isinstance(well, str) and isinstance(value_id, str)
                and value_id in known[variable_id]
            }
    return VariableSet(variables=tuple(variables), assignments=assignments)


# ── editing ─────────────────────────────────────────────────────────────────
#
# The definition editor's semantics live here rather than in its widgets, so
# every rule below is driven by a fast-lane test rather than by clicking. The
# view's job is to call these and redraw.

def _new_id(prefix: str, taken) -> str:
    """A short id not in *taken*. Not a UUID: these are read in a JSON file a
    user may open, and a short word is legible where a 32-hex string is noise.

    A *value's* id is minted from the label the user typed, so those read
    ``d0`` / ``10``, and uniqueness is per variable — the only scope anything
    looks a value up in.

    A *variable's* is not minted here; see :func:`_new_variable_id`.
    """
    base = "".join(ch for ch in (prefix or "").lower() if ch.isalnum()) or "v"
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}{n}"
    return candidate


def _new_variable_id(taken) -> str:
    """A variable id that names no *slot*, unique beyond this variable set.

    The obvious mint — the first free ``variable`` / ``variable2`` — makes an id
    a position rather than an identity, and the band settings that name a
    variable are read against whatever set is loaded. Every plate's first
    variable then answers to ``variable``, so a *Group by* carried across a
    dataset swap does not fail to resolve: it resolves to a different variable,
    regrouping every plot under a band still reading the old name. The same
    reuse happens within one plate, where deleting a variable frees its slot for
    the next one minted.

    ``PlotState.reset_for_new_dataset`` clears those fields on a swap, which is
    the live half of that defect. This is the other half: an id that cannot be
    re-minted means a stale reference resolves to *nothing*, which every reader
    already handles, rather than to the wrong variable, which none of them can
    detect.

    Still legible in the JSON a user may open — ``variable-4f2a`` beside a
    ``"name": "Dose"`` — rather than a 32-hex string, which is what :func:`_new_id`
    exists to avoid. *taken* is honoured so a hand-edited file with a collision
    in it cannot be made worse.
    """
    from uuid import uuid4
    while True:
        candidate = f"variable-{uuid4().hex[:4]}"
        if candidate not in taken:
            return candidate


def parse_number(label: str) -> float | None:
    """*label* as a number, or ``None`` when it is not one.

    Tolerates the ways a dose actually gets typed — leading and trailing space,
    a unit stuck on the end (``1 µM``), a comma decimal (``0,5``) — because the
    alternative is a `number` variable whose values are all unreadable and a
    plot with no x axis, over a space nobody could see.

    Returns ``None`` rather than 0.0 for anything it cannot read. **Zero is a
    dose**, and a vehicle row that read as 0 would be plotted at the origin as
    though it were a measured concentration.
    """
    text = (label or "").strip()
    if not text:
        return None
    # Take the leading numeric run, so "1 µM" and "10%" read while "µM 1" does
    # not — a value whose number is not at the front is not a quantity with a
    # unit, it is something else.
    keep = []
    for ch in text.replace(",", "."):
        if ch.isdigit() or ch in "+-." or (ch in "eE" and keep):
            keep.append(ch)
        else:
            break
    try:
        return float("".join(keep))
    except ValueError:
        return None


def unique_name(vset: VariableSet, name: str, *, exclude: str = "") -> str:
    """*name*, suffixed if another variable already answers to it.

    Two variables called `Dose` make ``by_name`` ambiguous and a plot's
    "Group by: Dose" meaningless, and the user cannot tell the two chips apart.
    Renaming beats refusing: the user is mid-thought, and a dialog saying no is
    a worse answer than one that keeps their word and numbers it.
    """
    base = (name or "").strip() or "Variable"
    taken = {v.name.strip().casefold() for v in vset.variables if v.id != exclude}
    candidate, n = base, 1
    while candidate.casefold() in taken:
        n += 1
        candidate = f"{base} {n}"
    return candidate


def new_variable(vset: VariableSet, name: str = "Variable", *,
                 kind: str = TEXT, unit: str = "") -> tuple[VariableSet, str]:
    """Add an empty variable. Returns the new set and the new variable's id."""
    kind = kind if kind in KINDS else TEXT
    variable = Variable(
        id=_new_variable_id({v.id for v in vset.variables}),
        name=unique_name(vset, name),
        kind=kind,
        unit=unit.strip() if kind == NUMBER else "",
    )
    return replace(vset, variables=(*vset.variables, variable)), variable.id


def rename_variable(vset: VariableSet, variable_id: str, name: str) -> VariableSet:
    """Rename a variable, keeping its id — so nothing assigned is disturbed."""
    if vset.by_id(variable_id) is None:
        return vset
    resolved = unique_name(vset, name, exclude=variable_id)
    return replace(vset, variables=tuple(
        replace(v, name=resolved) if v.id == variable_id else v
        for v in vset.variables))


def set_kind(vset: VariableSet, variable_id: str, kind: str,
             unit: str = "") -> VariableSet:
    """Change a variable's kind, re-reading its values against the new one.

    Text → number re-parses every label, so a list typed before the kind was
    switched becomes usable as an axis without being retyped. Number → text
    drops the parsed numbers *and* the unit, because a unit on a text variable
    would render as ``HeLa µM``.

    No value is discarded either way. A label the number parse cannot read
    keeps ``number=None``, which is what a vehicle row in a dose series is —
    the plot decides what to do with it.
    """
    variable = vset.by_id(variable_id)
    if variable is None or kind not in KINDS:
        return vset
    numeric = kind == NUMBER
    values = tuple(
        replace(val, number=parse_number(val.label) if numeric else None)
        for val in variable.values)
    return replace(vset, variables=tuple(
        replace(v, kind=kind, unit=unit.strip() if numeric else "", values=values)
        if v.id == variable_id else v
        for v in vset.variables))


def set_unit(vset: VariableSet, variable_id: str, unit: str) -> VariableSet:
    """Set a `number` variable's unit. A no-op on a `text` one."""
    variable = vset.by_id(variable_id)
    if variable is None or not variable.is_numeric():
        return vset
    return replace(vset, variables=tuple(
        replace(v, unit=(unit or "").strip()) if v.id == variable_id else v
        for v in vset.variables))


def add_value(vset: VariableSet, variable_id: str, label: str) -> VariableSet:
    """Append a value. A label the variable already carries is not added twice.

    Duplicate labels are the failure this whole value list exists to prevent:
    two `1 µM` rows split one dose into two series that each hold half the
    wells, and neither is visibly wrong.
    """
    variable = vset.by_id(variable_id)
    if variable is None:
        return vset
    label = (label or "").strip()
    if not label:
        return vset
    if any(val.label.strip().casefold() == label.casefold()
           for val in variable.values):
        return vset
    value = VariableValue(
        id=_new_id(label, {val.id for val in variable.values}),
        label=label,
        number=parse_number(label) if variable.is_numeric() else None,
    )
    return replace(vset, variables=tuple(
        replace(v, values=(*v.values, value)) if v.id == variable_id else v
        for v in vset.variables))


def rename_value(vset: VariableSet, variable_id: str, value_id: str,
                 label: str) -> VariableSet:
    """Relabel a value in place.

    **The reason values carry ids.** The label changes in one place and every
    well holding it follows, because the wells point at the id. Correcting
    `1uM` to `1 µM` after filling a plate is a rename, not a re-fill.
    """
    variable = vset.by_id(variable_id)
    label = (label or "").strip()
    if variable is None or not label:
        return vset
    if any(val.label.strip().casefold() == label.casefold() and val.id != value_id
           for val in variable.values):
        return vset
    values = tuple(
        replace(val, label=label,
                number=parse_number(label) if variable.is_numeric() else None)
        if val.id == value_id else val
        for val in variable.values)
    return replace(vset, variables=tuple(
        replace(v, values=values) if v.id == variable_id else v
        for v in vset.variables))


def remove_value(vset: VariableSet, variable_id: str, value_id: str) -> VariableSet:
    """Drop a value, and unassign the wells that held it — see :func:`prune_values`."""
    variable = vset.by_id(variable_id)
    if variable is None:
        return vset
    return prune_values(vset, variable_id,
                        tuple(v for v in variable.values if v.id != value_id))


def move_value(vset: VariableSet, variable_id: str, value_id: str,
               delta: int) -> VariableSet:
    """Move a value *delta* places in the list, clamped at both ends.

    The order is the user's and it is read: it decides the fill's cycle, a
    legend's rows and a numeric axis's direction. Clamped rather than wrapped —
    a value that jumped from the top of the list to the bottom because someone
    pressed the button once too often is a reorder nobody asked for.
    """
    variable = vset.by_id(variable_id)
    if variable is None:
        return vset
    values = list(variable.values)
    index = next((i for i, v in enumerate(values) if v.id == value_id), None)
    if index is None:
        return vset
    target = max(0, min(len(values) - 1, index + int(delta)))
    if target == index:
        return vset
    values.insert(target, values.pop(index))
    return replace(vset, variables=tuple(
        replace(v, values=tuple(values)) if v.id == variable_id else v
        for v in vset.variables))
