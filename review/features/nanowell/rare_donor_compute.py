"""Which donor each group of the Rare responders test belongs to, and which control each donor's groups are tested
against — so that every donor is its own experiment, with its own control and its own Benjamini–Hochberg family.

**Why per donor.** Cells from different donors differ in everything the test measures: how often a nanowell grows,
how much wells of one donor differ from each other, and whether a molecule wakes any T cell at all. A treatment of
donor 2 tested against donor 1's control measures the donors, not the molecule; and one correction over every
donor's groups lets a donor with many molecules dilute the evidence of a donor with few. So each treatment is tested
against the control group **of its own donor**, and q corrects each p across the groups tested **for that donor**.

**Where the donor comes from.** A sample variable (Home → Sample Map → Variables), chosen on the page's band — by
default the one named *Donor* (:func:`default_donor`). Each group takes the one value its wells share
(``sample_variables.roll_up``); nothing here ever invents one.

**Which group is each donor's control.** The user chooses one control under *Control*; that group's donor uses it.
For every other donor the control is the group that matches it on the variables that set it apart within its own
donor (:func:`setting_apart`) — the variables on which the chosen control differs from a group of its own donor it
is tested against: *Molecule = control* rather than *Molecule = A*, or *Dose = 0*. A variable every group of a
donor shares (the donor's age, its plate) is not one, so a control is never sought at another donor's age.

**A group that cannot be placed is refused by name, never guessed** — the figure lists it with the reason:

* its wells come from more than one donor, or one of them has no donor value;
* its donor has no group matching the control, or more than one;
* no variable but the donor sets the chosen control apart, so no other donor's control can be told.

Qt-free and ctx-free: the caller hands over the variables and the groups.
"""

from __future__ import annotations

from dataclasses import dataclass

from review.domain.sample_variables import MIXED, VariableSet, roll_up, unassigned_wells

#: What the band's *Donor* combo stores for "do not split by donor": one control, one correction over every group.
OFF = "none"
#: What it stores for "the variable named Donor, when there is one" (:func:`default_donor`) — the default.
AUTO = ""
#: Where the variables are set, said in every refusal that asks the user to fill one in.
WHERE = "Home → Sample Map → Variables"


@dataclass(frozen=True)
class Family:
    """One donor's comparisons: its label (``Donor = 1``), its control group, and the groups tested against it."""

    label: str
    control: str
    treatments: tuple


@dataclass(frozen=True)
class DonorPlan:
    """What :func:`plan` decided. ``families`` empty means "not split by donor": every group against one control."""

    variable: str = ""                   # the donor variable's name, "" when not split
    families: tuple = ()                 # Family, in the order of the donor variable's values
    refused: tuple = ()                  # one sentence per group (or donor) not tested, saying why
    problem: str = ""                    # why nothing can be tested at all (the control itself cannot be placed)

    @property
    def split(self) -> bool:
        return bool(self.variable)

    def tested(self) -> list:
        """Every group a family tests, its control first, in family order."""
        return [name for f in self.families for name in (f.control, *f.treatments)]


def donor_variables(vset: VariableSet | None) -> list:
    """The plate's variables, in order: every one of them can be the donor."""
    return list(getattr(vset, "variables", ()) or ())


def default_donor(vset: VariableSet | None) -> str:
    """The id of the variable named *Donor* (any case), else of the one variable whose name holds the word
    *donor*; ``""`` when there is none, or several: a guess between two is no guess."""
    if vset is None:
        return ""
    exact = vset.by_name("Donor")
    if exact is not None:
        return exact.id
    hits = [v for v in vset.variables if "donor" in v.name.casefold()]
    return hits[0].id if len(hits) == 1 else ""


def resolve(vset: VariableSet | None, choice: str) -> str:
    """The donor variable's id for the band's *choice* (:data:`OFF`, :data:`AUTO` or an id), ``""`` for none. An
    id this plate no longer has falls back to :data:`AUTO` rather than to a variable picked by position."""
    if choice == OFF or vset is None:
        return ""
    if choice and vset.by_id(choice) is not None:
        return choice
    return default_donor(vset)


def _label(vset: VariableSet, variable_id: str, value_id: str) -> str:
    variable = vset.by_id(variable_id)
    return f"{variable.name} {variable.display_label(value_id)}"


def _why_unplaced(vset: VariableSet, variable_id: str, wells) -> str:
    """Why a group with no single donor value has none: its wells' donors, or the wells that have no value."""
    name = vset.by_id(variable_id).name
    missing = unassigned_wells(vset, variable_id, wells)
    if missing:
        return f"{', '.join(missing)} {'has' if len(missing) == 1 else 'have'} no {name} value ({WHERE})"
    values = dict.fromkeys(vset.value_id_for(variable_id, w) for w in wells)
    labels = [vset.by_id(variable_id).display_label(v) for v in values]
    return f"its wells come from more than one {name} ({', '.join(labels)}): define a group per {name}"


def setting_apart(vset: VariableSet, donor_id: str, groups: dict, donor_of: dict, control: str) -> list:
    """The ids of the variables other than *donor_id* on which *control* holds one value and a group of its own donor
    holds another — what makes it the control rather than one more group (:func:`plan`), in the plate's order."""
    out = []
    mine = [g for g, d in donor_of.items() if d == donor_of[control] and g != control]
    for variable in vset.variables:
        if variable.id == donor_id:
            continue
        own = roll_up(vset, variable.id, groups[control])
        if own is None or own == MIXED:
            continue
        others = {roll_up(vset, variable.id, groups[g]) for g in mine} - {None, MIXED}
        if others - {own}:
            out.append(variable.id)
    return out


def plan(vset: VariableSet | None, groups: dict, control: str, donor_id: str) -> DonorPlan:
    """The donor families for *groups* (``name -> wells``, in draw order) tested against *control*, the donor read
    from the variable *donor_id* (:func:`resolve`); see the module docstring. ``DonorPlan()`` — not split — when
    *donor_id* names no variable of *vset*."""
    variable = vset.by_id(donor_id) if vset is not None and donor_id else None
    if variable is None or control not in groups:
        return DonorPlan()
    name = variable.name
    donor_of, refused = {}, []
    for group, wells in groups.items():
        value = roll_up(vset, donor_id, wells)
        if value is None or value == MIXED:
            if group == control:
                return DonorPlan(variable=name, problem=(
                    f"The control {control} cannot be placed with one {name}: {_why_unplaced(vset, donor_id, wells)}. "
                    f"Choose a control from one {name}, or set {name} to none on the band."))
            refused.append(f"{group}: not tested — {_why_unplaced(vset, donor_id, wells)}.")
            continue
        donor_of[group] = value
    apart = setting_apart(vset, donor_id, groups, donor_of, control)
    signature = {g: tuple(roll_up(vset, v, groups[g]) for v in apart) for g in donor_of}
    rule = " and ".join(f"{vset.by_id(v).name} = {vset.by_id(v).display_label(roll_up(vset, v, groups[control]))}"
                        for v in apart)
    order = [v.id for v in variable.values]
    donors = sorted(dict.fromkeys(donor_of.values()), key=lambda d: order.index(d) if d in order else len(order))
    families = []
    for donor in donors:
        members = [g for g, d in donor_of.items() if d == donor]
        label = _label(vset, donor_id, donor)
        if donor == donor_of[control]:
            own = control
        elif not apart:
            refused.append(f"{label} ({', '.join(members)}): not tested — no variable but {name} sets {control} apart "
                           f"from the groups it is tested against, so no group of {label} can be told to be its "
                           f"control. Give the groups a variable that does (Molecule = control, say: {WHERE}).")
            continue
        else:
            matches = [g for g in members if signature[g] == signature[control]]
            if len(matches) != 1:
                why = (f"no group has {rule}" if not matches else
                       f"{', '.join(matches)} all have {rule}: pool them into one group, or tell them apart with "
                       f"another variable ({WHERE})")
                refused.append(f"{label} ({', '.join(members)}): not tested — {why}, so it has no control.")
                continue
            own = matches[0]
        treatments = tuple(g for g in members if g != own)
        if treatments:
            families.append(Family(label, own, treatments))
    return DonorPlan(variable=name, families=tuple(families), refused=tuple(refused))
