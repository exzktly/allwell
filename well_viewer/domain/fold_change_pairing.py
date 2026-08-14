"""Which control each plotted member is normalized against, and what came of it.

A single global control either resolves or it does not, and an empty plot says
so on its own. Per-sample pairing has no such luxury: **partial failure is the
normal case**. Twenty-two members pair and two do not, and the two that did not
must not be indistinguishable from the twenty-two that did — not in the figure,
not in the CSV, and not in the table the user edits.

So this module resolves the whole mapping in one pass and hands back a record
per member carrying *why*. ``ControlOutcome`` is the vocabulary: five reasons a
member can end up where it does, four of which are decidable here and one of
which is not (see :class:`ControlOutcome`). The renderer's annotations, the
status line, the CSV's ``fold_change_outcome`` column and the editor's Status
column all read the same record, rather than three consumers re-deriving the
same decision from a value that came out empty.

Qt-free, and free of ``features/`` imports: ``build_per_member_controls`` is
handed the member keys and their wells rather than reaching for a collector to
compute them. The collector has already worked out its draw order by the time it
needs a control, and the editor gets the same answer from
``domain/plot_members.py`` — which is the point of that module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from well_viewer.domain.control_ref import ControlRef
from well_viewer.domain.fold_change import resolve_control_wells


class ControlOutcome(str, Enum):
    """Why a member's control is what it is.

    Four of these are decidable from the mapping and what is loaded, so they
    are settled here, once, before any aggregation runs. The fifth is not:
    ``CONTROL_NO_SAMPLE`` is a function of *(member, control, timepoint)* and
    only the collector knows whether the control had a measurement at the
    timepoint being drawn, so it is stamped onto the collected item instead.

    They exist as a closed set because without them every failure produces the
    same empty cell. ``unmapped``, ``control_unresolved`` and
    ``control_no_sample`` are three different things to tell a user — nobody
    picked one, the one picked is gone, the one picked has no data here — and a
    blank ``fold_change_mean`` says none of them.
    """

    #: Paired with a control that resolved to wells other than its own.
    OK = "ok"
    #: The member *is* its control. Draws as 1.0 (or 0.0 for subtract) with a
    #: propagated error bar — not a short-circuit, because x/x is already
    #: exactly 1.0 in IEEE for the same float and short-circuiting would hide a
    #: numerator/denominator mismatch rather than show it.
    SELF = "self"
    #: Nobody assigned this member a control.
    UNMAPPED = "unmapped"
    #: A control was assigned and resolves to no loaded wells — a deleted
    #: group, a well that is not in this dataset, a group whose wells all
    #: unloaded. The assignment is kept; only the plot drops the member.
    CONTROL_UNRESOLVED = "control_unresolved"
    #: The control resolved, but has no measurement at the timepoint drawn.
    #: Stamped by the collector, never by :func:`build_per_member_controls`.
    CONTROL_NO_SAMPLE = "control_no_sample"


#: Outcomes that leave a member with a usable denominator.
RESOLVED_OUTCOMES = frozenset({ControlOutcome.OK, ControlOutcome.SELF})


@dataclass(frozen=True)
class MemberControl:
    """One member's pairing, resolved."""

    member: str
    control: ControlRef | None
    wells: tuple[str, ...]
    outcome: ControlOutcome
    #: The replicate set this pairing was inherited from, or ``""`` when the
    #: member was mapped in its own right. Carried so the editor can show an
    #: inherited row as inherited rather than as an edit the user made.
    inherited_from: str = ""

    @property
    def resolved(self) -> bool:
        return self.outcome in RESOLVED_OUTCOMES


@dataclass(frozen=True)
class PerMemberControls:
    """The whole mapping for one plot, in draw order."""

    order: tuple[str, ...] = ()
    by_member: dict = field(default_factory=dict)
    render: str = "self"
    #: ``(member key, control)`` pairs whose member is not currently plotted.
    #: Kept rather than dropped — switching pooling off, or hiding a group,
    #: must not be a way to silently lose a mapping the user built.
    unused: tuple = ()

    def get(self, member: str) -> MemberControl:
        """The record for *member*; an unmapped one for a member never seen."""
        found = self.by_member.get(member)
        if found is not None:
            return found
        return MemberControl(member, None, (), ControlOutcome.UNMAPPED)

    def counts(self) -> dict:
        """``outcome -> how many members`` — what the status line coalesces."""
        out: dict = {}
        for key in self.order:
            outcome = self.get(key).outcome
            out[outcome] = out.get(outcome, 0) + 1
        return out

    def resolved_count(self) -> int:
        return sum(1 for k in self.order if self.get(k).resolved)

    def distinct_controls(self) -> tuple:
        """The distinct controls actually in use, in first-drawn order.

        Distinct by **resolved wells**, not by name: ``A01`` the well and
        ``A01`` the replicate set are two controls, and two references that
        resolve to the same wells are one. This count is what the axis label
        branches on — one control reads exactly as today's single-control
        title, two name both, three or more read ``vs own control``.
        """
        seen: set = set()
        out: list = []
        for key in self.order:
            rec = self.get(key)
            if not rec.resolved or rec.control is None:
                continue
            if rec.wells in seen:
                continue
            seen.add(rec.wells)
            out.append(rec.control)
        return tuple(out)

    def is_pure_control(self, member: str) -> bool:
        """True when *member* is somebody's control and normalizes to itself.

        Both halves are the rule. A member nobody points at is an ordinary
        sample however it is normalized, and a member that points at something
        *other* than itself is a sample too, even while other members use it —
        it carries its own normalization and hiding it would delete a real
        measurement from the figure. Only the intersection is the flat 1.0 bar
        the ``Plotted controls`` toggle governs.
        """
        rec = self.get(member)
        if rec.outcome is not ControlOutcome.SELF:
            return False
        return any(
            other != member and self.get(other).wells == rec.wells
            for other in self.order
        )

    def hidden_members(self) -> tuple:
        """Members the render mode removes from the plot entirely."""
        if self.render != "hidden":
            return ()
        return tuple(k for k in self.order if self.is_pure_control(k))


def build_per_member_controls(ctx, member_keys, control_map, *,
                              member_wells=None, member_rsets=None,
                              render: str = "self") -> PerMemberControls:
    """Resolve *control_map* against what is loaded, for *member_keys* in order.

    *member_wells* maps each key to the wells it is drawn from — see
    ``domain/plot_members.member_wells`` for why the caller supplies it rather
    than a resolver guessing. Without it a member cannot be compared to its
    control by wells, so self-normalization would have to be detected by
    matching names, which is wrong in exactly the case ``ControlRef`` exists
    for.

    *member_rsets* maps a well token to the replicate set that owns it. When
    pooling is switched off, the key space flips from set names to well tokens
    and a mapping written against the sets would be silently orphaned by a
    toggle that has nothing to do with fold change — so a well inherits its
    set's control. **One hop, explicit wins**: a well with its own entry keeps
    it, and an inherited control is never itself looked up for further
    inheritance.
    """
    keys = [str(k) for k in (member_keys or [])]
    mapping = dict(control_map or {})
    wells_for = dict(member_wells or {})
    owner_of = dict(member_rsets or {})

    records: dict = {}
    for key in keys:
        ref, inherited_from = _control_for(key, mapping, owner_of)
        own_wells = tuple(sorted(wells_for.get(key) or ()))
        if ref is None:
            records[key] = MemberControl(key, None, (), ControlOutcome.UNMAPPED)
            continue
        ctrl_wells = tuple(sorted(resolve_control_wells(ctx, ref)))
        if not ctrl_wells:
            records[key] = MemberControl(
                key, ref, (), ControlOutcome.CONTROL_UNRESOLVED, inherited_from)
            continue
        outcome = (ControlOutcome.SELF if ctrl_wells == own_wells and own_wells
                   else ControlOutcome.OK)
        records[key] = MemberControl(
            key, ref, ctrl_wells, outcome, inherited_from)

    plotted = set(keys)
    unused = tuple(
        (str(k), ControlRef.coerce(v))
        for k, v in mapping.items()
        if str(k) not in plotted and ControlRef.coerce(v) is not None
    )
    return PerMemberControls(
        order=tuple(keys), by_member=records,
        render=render if render in ("self", "hidden") else "self",
        unused=unused,
    )


def _control_for(member: str, mapping: dict, owner_of: dict):
    """``(control ref, inherited-from)`` for *member*, explicit entry winning."""
    own = ControlRef.coerce(mapping.get(member))
    if own is not None:
        return own, ""
    owner = owner_of.get(member)
    if owner:
        inherited = ControlRef.coerce(mapping.get(owner))
        if inherited is not None:
            return inherited, str(owner)
    return None, ""


# ── bulk entry ───────────────────────────────────────────────────────────────

#: Where the control sits inside each chunk.
CONTROL_FIRST = "first"
CONTROL_LAST = "last"


def auto_pair(member_keys, member_controls, *, size: int,
              position: str = CONTROL_FIRST) -> dict:
    """Walk *member_keys* in chunks of *size*, pairing each chunk to one of its own.

    This is Home → Groups' Quick Replicates one level up, deliberately: a plate
    laid out in repeating blocks of "control then treatments" is the layout
    that makes per-sample controls worth having, and the vocabulary is one the
    user already has.

    *member_controls* maps a member key to the control reference that addresses
    **that member** — a set name resolves to a set reference, a well token to a
    well reference — because the chunk's control is one of the plotted members
    and a plain name could not say which. A trailing chunk shorter than *size*
    is still paired, against whichever of its own members *position* names; it
    is a real block of samples, and skipping it would leave a tail of unmapped
    members that looks like a bug rather than a choice.

    A plate-geometry rule (row, column, quadrant) is deliberately **not**
    offered: it is undefined the moment the members are replicate sets, which
    is a domain predicate applied outside its domain. Name-pattern matching is
    not offered either — every near-miss looks like a working feature.
    """
    keys = [str(k) for k in (member_keys or [])]
    if size < 1 or not keys:
        return {}
    refs = dict(member_controls or {})
    out: dict = {}
    for start in range(0, len(keys), size):
        chunk = keys[start:start + size]
        anchor = chunk[-1] if position == CONTROL_LAST else chunk[0]
        ref = ControlRef.coerce(refs.get(anchor))
        if ref is None:
            continue
        for key in chunk:
            out[key] = ref
    return out


# ── what the popover and the status line say ─────────────────────────────────

def summary_text(controls: PerMemberControls) -> str:
    """The one-line state of the mapping, for the combo and the summary label.

    It reports **resolved out of plotted**, not entries out of entries: a
    mapping with twenty-four entries of which two name deleted groups is not
    complete, and a count taken from the mapping would call it complete.
    """
    total = len(controls.order)
    if not total:
        return "Per-sample control (nothing plotted)"
    resolved = controls.resolved_count()
    return f"Per-sample control ({resolved}/{total})"


def shortfall_text(controls: PerMemberControls) -> str:
    """A phrase naming what fell short, or ``""`` when nothing did.

    Coalesced by reason rather than listed per member, so a plate where every
    member is unmapped reads as one sentence instead of twenty-four.
    """
    counts = controls.counts()
    parts = []
    if counts.get(ControlOutcome.UNMAPPED):
        parts.append(f"{counts[ControlOutcome.UNMAPPED]} with no control")
    if counts.get(ControlOutcome.CONTROL_UNRESOLVED):
        parts.append(
            f"{counts[ControlOutcome.CONTROL_UNRESOLVED]} whose control is missing")
    if counts.get(ControlOutcome.CONTROL_NO_SAMPLE):
        parts.append(
            f"{counts[ControlOutcome.CONTROL_NO_SAMPLE]} whose control has no "
            "data at this timepoint")
    return ", ".join(parts)
