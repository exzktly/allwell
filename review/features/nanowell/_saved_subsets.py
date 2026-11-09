"""The Nanowells page's saved subsets as the Saved Cells page lists them (the ``nanowell.saved_subsets`` service).

A subset is saved on the Nanowells page (``controller.save_subset``) and kept in that page's state
(``NanowellState.saved_subsets``). The Saved Cells page lists every saved population of the dataset, so it lists
these too, with nothing to do to put them there: it asks this service each time it refreshes, and the page's save,
rename and delete refresh it (``controller._saved_cells_changed``). Saved Cells never imports this feature — a build without
Nanowells has no service, and the section is not shown.

Each row is a plain ``dict`` so the asker needs no type of this feature's:

``name``      the name it was saved under;
``text``      its conditions as the page's figures write them (``Subset.label``);
``kept``      nanowells inside it, or ``None`` when the page has not read its wells yet (counted, never guessed);
``total``     nanowells it was counted over: those of the wells the page has read;
``in_use``    whether it is the page's Subset now — the one the page's views and the Scatter Plot's nanowell
              scatter draw.

Qt-free.
"""

from __future__ import annotations

from review.features.nanowell import subset_compute as ss
from review.features.nanowell.controller import apply_saved_subset


def saved_subset_rows(ctx) -> list[dict] | None:
    """One row per saved subset, in the order saved; ``None`` when no dataset is open."""
    st = ctx.state.feature_slice("nanowell")
    if st is None:
        return None
    gates = ctx.state.cell_gating.scatter_gates or []
    current = ss.bind_gates(st.subset, gates)
    metrics, data = getattr(st, "metrics", None), getattr(st, "data", None)
    rows = []
    for name, subset in st.saved_subsets.items():
        bound = ss.bind_gates(subset, gates)
        kept = total = None
        if metrics is not None and data is not None and len(metrics):
            _rows, done = ss.apply(metrics, bound, st.baseline, data=data)
            if done is not None:
                kept, total = done.kept, done.total
        rows.append({"name": name, "text": subset.label(), "kept": kept, "total": total,
                     "in_use": bound == current})
    return rows


def use_saved_subset(ctx, name: str) -> bool:
    """Make the subset saved as *name* the page's Subset and show the page; whether it was found and shown."""
    if not apply_saved_subset(ctx, name):
        return False
    notebook = getattr(ctx.window, "_notebook", None)
    return bool(notebook is not None and notebook.setCurrentByName("Nanowells"))

