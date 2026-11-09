"""Each nanowell's kept cells counted per size class, and the metrics built on those counts.

The rule is Cell Gating's (``QCParams.size_classes``, :mod:`review.domain.size_classes`): two labelled area ranges.
Each per-cell row of a nanowell run carries ``area_px`` and the address of the nanowell it was counted in, so the
classes are counted from the rows Cell Gating keeps — read in the same pass that takes the gated-out cells off each
count (``controller.gate_tally`` → ``cell_gate_compute.tally``), with the same inclusion — and need no re-run.

**The metrics** (:data:`COLUMNS`), one value per nanowell and timepoint:

=====================  ========================================  ===============================================
column                 value                                     undefined when
=====================  ========================================  ===============================================
``size_1``             cells in the first class                  no count; no classes set; cells not read
``size_2``             cells in the second class                 (the same)
``size_unclassified``  ``n`` less the two classes: cells in      (the same)
                       neither range, or with no area
``size_ratio``         ``size_1 / size_2``                       the same, or the second class is empty
``size_1_share``       ``size_1 / (size_1 + size_2)``            the same, or neither class holds a cell
=====================  ========================================  ===============================================

A nanowell with a count and no cells has counts of 0 — zero is a measurement — and no ratio or share. A well whose
cells could not be read or classified (no rows, no area column, no address columns), or some of whose classified
cells joined no nanowell of the table, keeps the run's count but has no classes, since nothing here may guess them. Should a
nanowell's classes ever add up to more than its count (a cell table that does not match the count the run wrote),
every size metric there is undefined with that reason rather than a negative unclassified count.

Each well is summarised by the **mean** over its nanowells (``summary_compute.MEAN_METRICS``): the user's choice,
and the right reduction for small whole-number counts, whose median jumps between integers.

Qt-free.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from review.domain.size_classes import PLACEHOLDERS, SizeClassRule, classify
from review.domain.size_classes import area_column as _area_column

SITE_KEY = ["well", "timepoint", "nw_q", "nw_r"]
#: The per-class count columns, in class order.
COUNT_COLUMNS = ("size_1", "size_2")
UNCLASSIFIED_COLUMN = "size_unclassified"
RATIO_COLUMN = "size_ratio"
SHARE_COLUMN = "size_1_share"
#: Every size metric, in the order the page lists them.
COLUMNS = (*COUNT_COLUMNS, UNCLASSIFIED_COLUMN, RATIO_COLUMN, SHARE_COLUMN)
#: The whole-number ones (packed into bands on a swarm).
INTEGER_COLUMNS = frozenset({*COUNT_COLUMNS, UNCLASSIFIED_COLUMN})
#: The page's metric menu entries: key -> label, the class names as placeholders (``SizeClassRule.name``).
LABELS = {
    "size_1": f"{PLACEHOLDERS[0]} count",
    "size_2": f"{PLACEHOLDERS[1]} count",
    UNCLASSIFIED_COLUMN: "Cells in no size class",
    RATIO_COLUMN: f"{PLACEHOLDERS[0]} : {PLACEHOLDERS[1]} ratio",
    SHARE_COLUMN: f"{PLACEHOLDERS[0]} share of classified cells",
}
#: The per-row reason column :func:`apply` adds: why every size metric is undefined at a row with a count.
REASON_COLUMN = "_size_reason"

NOT_SET = "size classes not set"
NOT_READ = "cells not read for size classes"
MISMATCH = "size classes exceed the count"
NOT_JOINED = "cells did not join their nanowells"
SECOND_EMPTY = "second size class empty"
NONE_CLASSIFIED = "no cells in either size class"
#: Every reason this module can give, for ``distribution_compute.reason_label``.
REASONS = (NOT_SET, NOT_READ, NOT_JOINED, MISMATCH, SECOND_EMPTY, NONE_CLASSIFIED)


def _empty_counts() -> pd.DataFrame:
    frame = pd.DataFrame({"well": pd.Series(dtype=object), "timepoint": pd.Series(dtype=object),
                          "nw_q": pd.Series(dtype=float), "nw_r": pd.Series(dtype=float)})
    for column in COUNT_COLUMNS:
        frame[column] = pd.Series(dtype=int)
    return frame


def area_column(cells: pd.DataFrame) -> str | None:
    """The area column the debris gate reads (``qc_params.AREA_COLS``), so one number means one thing on the panel —
    ``size_classes.area_column``, the one rule the Segmentation outlines and the crops are coloured by too."""
    return _area_column(cells.columns)


def well_counts(well: str, cells: pd.DataFrame, included, rule: SizeClassRule | None) -> pd.DataFrame | None:
    """One row per ``(well, timepoint, nw_q, nw_r)`` holding at least one kept, classified cell of *well*:
    ``size_1`` and ``size_2``. *cells* are the per-cell rows (a CSV may hold other wells' rows too) and *included*
    the analysis's inclusion of each row, as ``cell_gate_compute.well_left_out`` takes them. Only cells with a
    nanowell address and a timepoint are counted, as only those are in a nanowell's count.

    ``None`` when the well's cells **cannot** be classified — no rows, no area column, or no address columns — so
    :func:`apply` says so rather than counting them as cells in neither class. An empty frame is an answer: the
    well's cells were read and none fell in a class."""
    if rule is None or not rule.active():
        return _empty_counts()
    if (cells is None or not len(cells) or not {"timepoint", "nw_q", "nw_r"} <= set(cells.columns)
            or area_column(cells) is None):
        return None
    column = area_column(cells)
    rows = np.asarray(included, dtype=bool).copy()
    if "well" in cells.columns:
        rows &= cells["well"].astype(str).to_numpy() == str(well)
    q = pd.to_numeric(cells["nw_q"], errors="coerce").to_numpy(dtype=float)
    r = pd.to_numeric(cells["nw_r"], errors="coerce").to_numpy(dtype=float)
    rows &= np.isfinite(q) & np.isfinite(r) & cells["timepoint"].notna().to_numpy()
    if not rows.any():
        return _empty_counts()
    kind = classify(cells[column].to_numpy()[rows], rule)
    frame = pd.DataFrame({"well": str(well), "timepoint": cells["timepoint"].to_numpy()[rows].astype(str),
                          "nw_q": q[rows], "nw_r": r[rows]})
    for k, name in enumerate(COUNT_COLUMNS):
        frame[name] = (kind == k).astype(int)
    frame = frame[(kind >= 0)]
    if not len(frame):
        return _empty_counts()
    return frame.groupby(SITE_KEY, sort=False)[list(COUNT_COLUMNS)].sum().reset_index()


def apply(frame: pd.DataFrame, gate) -> None:
    """Add :data:`COLUMNS` and :data:`REASON_COLUMN` to *frame* (``series_compute.site_metrics``'s table, after Cell
    Gating has taken its cells off ``n``), in place. *gate* is the ``cell_gate_compute.GateTally`` the counts were
    read in, or ``None`` (no tally: the classes are not set as far as this table knows)."""
    n = pd.to_numeric(frame["n"], errors="coerce") if "n" in frame.columns else pd.Series(np.nan, index=frame.index)
    rule = getattr(gate, "sizes", None)
    reason = pd.Series("", index=frame.index, dtype=object)
    wells = frame["well"].astype(str) if "well" in frame.columns else pd.Series("", index=frame.index)
    for column in COLUMNS:
        frame[column] = np.nan
    if rule is None or not rule.active():
        reason[n.notna()] = NOT_SET
        frame[REASON_COLUMN] = reason
        return
    counts = getattr(gate, "classes", None)
    counts = _empty_counts() if counts is None else counts
    keyed = pd.DataFrame({"well": frame["well"].astype(str), "timepoint": frame["timepoint"].astype(str),
                          "nw_q": pd.to_numeric(frame["nw_q"], errors="coerce").astype(float),
                          "nw_r": pd.to_numeric(frame["nw_r"], errors="coerce").astype(float)})
    merged = keyed.merge(counts, on=SITE_KEY, how="left")
    # A well is unclassified as a whole when its cells could not be read or classified (no area or address column,
    # no rows), unless it has no cell to classify — and when some of its classified cells joined no nanowell of the
    # table (a timepoint or an address spelled differently on the two sides), whose nanowells would otherwise read
    # as holding none of them.
    classed = {str(w) for w in getattr(gate, "classed", ())}
    unread = ~wells.isin(classed) & n.gt(0).groupby(wells).transform("any")
    joined = pd.Series(merged[list(COUNT_COLUMNS)].fillna(0).sum(axis=1).to_numpy(), index=frame.index)
    counted = counts.assign(_n=counts[list(COUNT_COLUMNS)].sum(axis=1)).groupby(counts["well"].astype(str))["_n"].sum()
    landed = joined.groupby(wells).sum()
    lost = {w for w, total in counted.items() if landed.get(w, 0) < total}
    unjoined = wells.isin(lost)
    usable = n.notna() & ~unread & ~unjoined
    a = pd.Series(merged[COUNT_COLUMNS[0]].fillna(0).to_numpy(dtype=float), index=frame.index)
    b = pd.Series(merged[COUNT_COLUMNS[1]].fillna(0).to_numpy(dtype=float), index=frame.index)
    rest = n - a - b
    mismatch = usable & rest.lt(0)
    usable &= ~mismatch
    frame[COUNT_COLUMNS[0]] = a.where(usable)
    frame[COUNT_COLUMNS[1]] = b.where(usable)
    frame[UNCLASSIFIED_COLUMN] = rest.where(usable)
    frame[RATIO_COLUMN] = (a / b.where(b > 0)).where(usable & b.gt(0))
    both = a + b
    frame[SHARE_COLUMN] = (a / both.where(both > 0)).where(usable & both.gt(0))
    reason[n.notna() & unread] = NOT_READ
    reason[n.notna() & unjoined & ~unread] = NOT_JOINED
    reason[mismatch] = MISMATCH
    frame[REASON_COLUMN] = reason


def exclusion_reason(metrics: pd.DataFrame, column: str, state_reason: pd.Series) -> pd.Series:
    """Why size metric *column* is undefined at each row (``""`` where defined). *state_reason* is
    ``series_compute``'s reason for the rows whose state, or count, has no value — it comes first."""
    defined = pd.to_numeric(metrics[column], errors="coerce").notna()
    out = pd.Series("", index=metrics.index, dtype=object)
    pending = ~defined
    first = state_reason.fillna("").astype(str)
    out[pending & first.ne("")] = first[pending & first.ne("")]
    pending &= first.eq("")
    row_reason = (metrics[REASON_COLUMN] if REASON_COLUMN in metrics.columns
                  else pd.Series(NOT_SET, index=metrics.index)).fillna("").astype(str)
    out[pending & row_reason.ne("")] = row_reason[pending & row_reason.ne("")]
    pending &= row_reason.eq("")
    if column == RATIO_COLUMN:
        out[pending] = SECOND_EMPTY
    elif column == SHARE_COLUMN:
        out[pending] = NONE_CLASSIFIED
    else:
        out[pending] = NOT_SET                          # a count with nothing else to say: no rule reached it
    return out


def note(metrics: pd.DataFrame, rule: SizeClassRule | None, column: str) -> str:
    """The figure's sentence about the classes when *column* is a size metric: the ranges, and how many of the
    counted cells fell in neither — so a ratio is never read as covering every cell. ``""`` for any other metric."""
    if column not in COLUMNS:
        return ""
    if rule is None or not rule.active():
        return ("Size classes are not set: set two labelled area ranges on Home → Cell Gating to count each "
                "nanowell's cells by size.")
    said = f"Size classes (Cell Gating, cells it keeps): {rule.text()}."
    if all(c in metrics.columns for c in (*COUNT_COLUMNS, UNCLASSIFIED_COLUMN)):
        a = float(pd.to_numeric(metrics[COUNT_COLUMNS[0]], errors="coerce").sum())
        b = float(pd.to_numeric(metrics[COUNT_COLUMNS[1]], errors="coerce").sum())
        rest = float(pd.to_numeric(metrics[UNCLASSIFIED_COLUMN], errors="coerce").sum())
        total = a + b + rest
        if total > 0:
            said += (f" Of {total:,.0f} cell-timepoints counted here, {rest:,.0f} ({rest / total:.0%}) fall in "
                     "neither class and are left out of the ratio and the share.")
    return said
