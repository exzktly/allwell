"""Plate-selection domain state.

The wells the user has picked on the plate map, the saved-selections ("v2")
model, and the bookkeeping that keeps a multi-well drag coherent — the
last-touched well, the previous-selection snapshot used to diff additions
against removals, and the per-tick commit-coalescing flag.

Composes ``well_viewer/selections_model.py``: that module owns the v2 schema,
validation / repair, v1→v2 migration, and colour assignment — ``SelectionState``
reuses its functions rather than reimplementing them.

Qt-free: imports only stdlib and ``selections_model``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from well_viewer.domain.selections_model import validate_repair


@dataclass
class SelectionState:
    """Plate selection + saved-selections model with the drag-coherence
    invariants.

    ``selected_wells`` is the live per-well plate selection. ``prev_sel`` is the
    snapshot taken before the current interaction so :meth:`commit_plate_change`
    can diff additions against removals; ``last_sel`` is the most-recently
    touched well that diff resolves. ``selections`` is the saved-selections v2
    list (schema in ``selections_model``); ``active_solo_wells`` are plotting-tab
    one-off wells drawn alongside the visible groups.
    """

    selected_wells: set[str] = field(default_factory=set)
    last_sel: str | None = None
    prev_sel: set[str] = field(default_factory=set)
    active_solo_wells: set[str] = field(default_factory=set)

    selections: list[dict] = field(default_factory=list)
    current_selection_id: str | None = None
    v2_writes_disabled: bool = False

    plate_commit_pending: bool = False

    # ── saved selections ("rep mode") ────────────────────────────────────────

    @property
    def rep_mode(self) -> bool:
        """True when saved selections exist — plots draw groups, not raw wells."""
        return bool(self.selections)

    def current_selection(self) -> dict | None:
        """The saved selection matching ``current_selection_id`` — None when the
        id is unset or stale."""
        if not self.current_selection_id:
            return None
        return next(
            (s for s in self.selections
             if s.get("id") == self.current_selection_id),
            None,
        )

    def validate(self) -> None:
        """Repair the saved-selections list in place (re-mint ids, resolve
        name / colour collisions, default missing keys) via ``selections_model``,
        then drop a ``current_selection_id`` that no longer resolves."""
        self.selections = validate_repair(self.selections)
        ids = {s["id"] for s in self.selections}
        if self.current_selection_id not in ids:
            self.current_selection_id = (
                self.selections[0]["id"] if self.selections else None
            )

    # ── plate selection ──────────────────────────────────────────────────────

    def select_wells(self, tokens) -> None:
        """Replace the plate selection with *tokens* and re-snapshot for the
        next diff."""
        self.selected_wells = set(tokens)
        if self.selected_wells:
            self.last_sel = next(iter(self.selected_wells))
        self.prev_sel = set(self.selected_wells)

    def clear_selection(self) -> None:
        """Drop the entire plate selection."""
        self.selected_wells = set()
        self.last_sel = None
        self.prev_sel = set()

    def commit_plate_change(self) -> None:
        """Reconcile ``last_sel`` / ``prev_sel`` after ``selected_wells`` was
        mutated in place.

        Diffs the new selection against the ``prev_sel`` snapshot to pick the
        last-touched well — an addition wins over a removal — then re-snapshots
        so the next interaction diffs cleanly.
        """
        current = set(self.selected_wells)
        added = current - self.prev_sel
        removed = self.prev_sel - current
        if added:
            self.last_sel = next(iter(added))
        elif removed:
            deselected = next(iter(removed))
            self.last_sel = deselected if current else None
        self.prev_sel = current
