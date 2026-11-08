"""What the Saved Cells page lists: every saved cell set and every gate, as rows.

Qt-free. Two kinds of saved population, read from the two stores that own them
and kept apart on purpose: a **saved set** is a fixed list of cell identities
(``review/core/cell_sets.py``) and goes stale when a well is segmented again; a
**gate** is a rule over two measurements (``ctx.state.cell_gating``,
``review/domain/gates.py``) and re-evaluates on whatever cells are loaded. The
page shows both in one place without pretending they are one thing, so each row
says what it is, what it currently does to the analysis, and how to look at its
cells.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SetRow:
    """One saved cell set."""

    name: str
    cells: int
    usable: int
    wells: tuple
    source: str
    note: str
    created: str
    stale: str
    highlighted: bool
    excluded: bool

    @property
    def size_text(self) -> str:
        n_wells = len(self.wells)
        text = f"{self.cells:,} cells in {n_wells} well{'s' if n_wells != 1 else ''}"
        if self.usable != self.cells:
            text += f" ({self.usable:,} usable)"
        return text

    @property
    def status_text(self) -> str:
        """What this set does to the analysis now, in words."""
        parts = []
        if self.excluded:
            parts.append("Excluded from analysis")
        if self.highlighted:
            parts.append("Highlighted on Scatter")
        return " · ".join(parts) or "Saved only — changes no number"


@dataclass(frozen=True)
class GateRow:
    """One gate drawn on the per-cell Scatter."""

    name: str
    x: str
    y: str
    wells: tuple
    includes: bool
    thresholds: bool

    @property
    def axes_text(self) -> str:
        return f"{self.x} × {self.y}"

    @property
    def status_text(self) -> str:
        parts = []
        if self.includes:
            parts.append("Defines the Included cells")
        if self.thresholds:
            parts.append("Defines the ON cells")
        return " · ".join(parts) or "Not applied — a view only"


def set_rows(ctx) -> list[SetRow]:
    """Every saved set, in the order it was saved."""
    from review.core import cell_sets as cs

    state = getattr(ctx.state, "cell_sets", None)
    exclude = getattr(state, "exclude", None)
    highlight = getattr(state, "highlight", None)
    rows = []
    for s in cs.sets(ctx):
        rows.append(SetRow(
            name=s.name, cells=len(s.cells), usable=len(cs.usable_cells(ctx, s)),
            wells=tuple(sorted({str(c[0]) for c in s.cells})),
            source=s.source, note=s.note, created=s.created,
            stale=cs.stale_summary(ctx, s),
            highlighted=s.name == highlight, excluded=s.name == exclude))
    return rows


def _chosen(choices, name: str) -> bool:
    """Whether a layer's gate selection takes *name* (``None`` means every gate)."""
    return choices is None or name in set(choices)


def gate_rows(ctx) -> list[GateRow]:
    """Every drawn gate, in the order it was drawn, with the layers that apply it.

    A gate defines the Included cells only while Cell Gating's inclusion layer
    uses scatter gates *and* that layer's selection takes it; the same for the
    ON population and the thresholding layer. Named here so the page cannot
    claim a gate is in force when its layer is set to something else.
    """
    from review.data.metric_labels import column_label

    cg = ctx.state.cell_gating
    rows = []
    for g in cg.scatter_gates or []:
        name = str(g.get("name") or "").strip()
        if not name:
            continue
        rows.append(GateRow(
            name=name, x=column_label(g.get("x_col") or "") or "?",
            y=column_label(g.get("y_col") or "") or "?",
            wells=tuple(g.get("wells") or ()),
            includes=bool(cg.use_scatter_gates) and _chosen(cg.gating_choices, name),
            thresholds=bool(cg.use_scatter_thresh) and _chosen(cg.thresh_choices, name)))
    return rows


def intensity_gates(ctx) -> list[tuple[str, float]]:
    """The per-channel intensity floors in force (``fluor_gates``; 0 means off), by channel.

    Shown beside the drawn gates because they are the other way a cell leaves
    the Included population, and the one a user is likeliest to have forgotten.
    Not while scatter gates define inclusion: they replace these floors then.
    """
    cg = ctx.state.cell_gating
    if cg.use_scatter_gates:
        return []
    return sorted((str(ch).upper(), float(v)) for ch, v in (cg.fluor_gates or {}).items()
                  if v and float(v) > 0)
