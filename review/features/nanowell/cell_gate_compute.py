"""The cells Home's Cell Gating leaves out, taken out of each nanowell's count and brightness.

The pipeline counts a nanowell's cells once, when it runs (``n_cells`` in ``nanowells/<well>.csv``), and the
Nanowells page used to draw that number whatever was set in Review. Every other figure in Review counts only the
cells the analysis includes — :func:`review.data.data_loading.df_included_mask`: manual curation, the QC size and
shape gate, the crop, the scatter gates, the field and focus layers — so a cell gated out as debris still counted
towards its nanowell here and nowhere else.

**What follows the gate.** Each per-cell row carries the address of the nanowell it was counted in (``nw_q``,
``nw_r``), so the cells left out of each nanowell at each timepoint can be counted. A nanowell's count is the
run's ``n_cells`` minus the cells left out of it, so with nothing gated out every count is exactly the run's own.
Everything computed from the count follows it — fold changes, the growth rate, the occupancy class, Starting
cells and the *Subset* conditions — and so does *brightness*, the mean per-cell intensity, which is taken over the
cells kept. A nanowell whose cells are all left out counts 0 and has no brightness.

**What does not.** Confluence and nanowell fluorescence (``<channel>_integrated``) are measured from the pixels
of the nanowell, not from its segmented cells, so no cell gate can remove anything from them; the figure's note
says so whenever cells are left out (:func:`note`).

**Which cells were left out** is kept too, by identity (:data:`CELL_KEY`), so the page's cell dots can draw a
left-out cell differently from a kept one: the dots and the count under a nanowell then describe the same cells,
with the difference between them visible rather than silent (:func:`left_out_keys`, :func:`cell_keys`).

**A well whose cells cannot be read keeps the run's counts**, and is named in the note, so a figure mixing gated
and ungated wells never reads as gated throughout.

Qt-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

SITE_KEY = ["well", "timepoint", "nw_q", "nw_r"]
#: The suffix of a per-cell intensity column (``<token>_mean_intensity``), the one brightness is the mean of.
MEAN_SUFFIX = "_mean_intensity"
KEPT_PREFIX = "kept_"
#: What identifies one cell across the per-cell CSV as the page reads it and as the row cache holds it: the well,
#: and the ``(fov, timepoint, nucleus_id)`` triple in the normal form ``domain.image_identity`` gives every lookup.
CELL_KEY = ("well", "fov", "timepoint", "nucleus_id")


@dataclass(frozen=True)
class GateTally:
    """The cells left out of each nanowell, over the wells whose per-cell rows were read.

    *sites* has one row per ``(well, timepoint, nw_q, nw_r)`` that lost at least one cell: ``excluded`` (cells
    left out) and, per intensity token in lower case, ``kept_<token>`` (the kept cells' mean, NaN with none
    kept). *cells* and *excluded* count every addressed cell of those wells; *unread* names the wells whose
    rows could not be read, whose counts are the run's own. *left_out* is the identity (:data:`CELL_KEY`) of every
    cell of those wells the analysis leaves out, addressed or not, which the cell dots draw hollow."""
    sites: pd.DataFrame
    cells: int = 0
    excluded: int = 0
    unread: tuple = field(default_factory=tuple)
    left_out: frozenset = field(default_factory=frozenset)


def _empty_sites() -> pd.DataFrame:
    return pd.DataFrame({"well": pd.Series(dtype=object), "timepoint": pd.Series(dtype=object),
                         "nw_q": pd.Series(dtype=float), "nw_r": pd.Series(dtype=float),
                         "excluded": pd.Series(dtype=int)})


def _keyed(frame: pd.DataFrame) -> pd.DataFrame:
    """*frame*'s site key in one spelling: text well and timepoint, float address."""
    return pd.DataFrame({"well": frame["well"].astype(str), "timepoint": frame["timepoint"].astype(str),
                         "nw_q": pd.to_numeric(frame["nw_q"], errors="coerce").astype(float),
                         "nw_r": pd.to_numeric(frame["nw_r"], errors="coerce").astype(float)},
                        index=frame.index)


def well_left_out(well: str, cells: pd.DataFrame, included) -> tuple[pd.DataFrame, int, int]:
    """``(sites, cells, excluded)`` for one well: its per-cell rows *cells* (a whole CSV may hold other wells'
    rows too) and *included*, the analysis's inclusion of each row (``df_included_mask``).

    Only cells with a nanowell address are counted: a cell outside every nanowell is in no nanowell's count."""
    if cells is None or not len(cells) or not {"timepoint", "nw_q", "nw_r"} <= set(cells.columns):
        return _empty_sites(), 0, 0
    kept = np.asarray(included, dtype=bool)
    rows = np.ones(len(cells), dtype=bool)
    if "well" in cells.columns:
        rows &= cells["well"].astype(str).to_numpy() == str(well)
    q = pd.to_numeric(cells["nw_q"], errors="coerce").to_numpy(dtype=float)
    r = pd.to_numeric(cells["nw_r"], errors="coerce").to_numpy(dtype=float)
    rows &= np.isfinite(q) & np.isfinite(r) & cells["timepoint"].notna().to_numpy()
    total, out = int(rows.sum()), int((rows & ~kept).sum())
    if not out:
        return _empty_sites(), total, 0
    intensity = [c for c in cells.columns if str(c).endswith(MEAN_SUFFIX)]
    sub = cells.loc[rows, intensity].apply(pd.to_numeric, errors="coerce")
    sub = sub.where(pd.DataFrame({c: kept[rows] for c in intensity}, index=sub.index))     # kept cells' values only
    key = _keyed(cells.loc[rows].assign(well=str(well)))
    sub = pd.concat([key, sub], axis=1)
    sub["excluded"] = (~kept[rows]).astype(int)
    grouped = sub.groupby(SITE_KEY, sort=False)
    sites = grouped["excluded"].sum().to_frame()
    for column in intensity:
        sites[KEPT_PREFIX + column[: -len(MEAN_SUFFIX)].lower()] = grouped[column].mean()
    sites = sites[sites["excluded"] > 0].reset_index()
    return sites, total, out


def cell_keys(well: str, cells: pd.DataFrame) -> list | None:
    """The identity (:data:`CELL_KEY`) of each row of *cells*, all taken as *well*'s; ``None`` when the rows carry
    no nucleus id, since without one two cells of a field at a timepoint share a key and cannot be told apart.

    Both sides go through this one function — the per-cell CSV the dots are read from and the row cache the
    inclusion is read from — so a cell is the same key on both whatever spelling each gives its field or its
    timepoint."""
    from domain.image_identity import NUCLEUS_ID_COLUMN_NAMES, cell_key_tuples_for_dataframe, resolve_identity_column

    if cells is None or resolve_identity_column(cells, *NUCLEUS_ID_COLUMN_NAMES) is None:
        return None
    if not len(cells):
        return []
    return [(str(well), *key) for key in cell_key_tuples_for_dataframe(cells)]


def left_out_keys(well: str, cells: pd.DataFrame, included) -> frozenset:
    """The identity (:data:`CELL_KEY`) of each of *well*'s cells the analysis leaves out: the rows of *cells* (a whole
    CSV may hold other wells' rows too) that *included* (``df_included_mask``) says no to. Every such cell, with a
    nanowell address or without, since the dots draw both."""
    if cells is None or not len(cells):
        return frozenset()
    out = ~np.asarray(included, dtype=bool)
    if "well" in cells.columns:
        out &= cells["well"].astype(str).to_numpy() == str(well)
    if not out.any():
        return frozenset()
    return frozenset(cell_keys(well, cells.loc[out]) or ())


def tally(per_well, unread=()) -> GateTally:
    """One :class:`GateTally` from *per_well*, an iterable of ``(well, cells, included)``."""
    found, cells, excluded, keys = [], 0, 0, set()
    for well, frame, included in per_well:
        sites, n, out = well_left_out(well, frame, included)
        cells, excluded = cells + n, excluded + out
        keys |= left_out_keys(well, frame, included)
        if len(sites):
            found.append(sites)
    sites = pd.concat(found, ignore_index=True) if found else _empty_sites()
    return GateTally(sites=sites, cells=cells, excluded=excluded, unread=tuple(sorted(unread)),
                     left_out=frozenset(keys))


def left_out(gate: GateTally | None) -> frozenset:
    """The cells *gate* leaves out (:attr:`GateTally.left_out`); empty with no gate."""
    return gate.left_out if gate is not None else frozenset()


def apply(frame: pd.DataFrame, gate: GateTally | None, channels) -> None:
    """Take the cells *gate* left out of *frame*'s ``n`` and ``brightness_<channel>`` columns, in place.

    *frame* is the site metrics table as :func:`series_compute.site_metrics` builds it, before anything is
    computed from those columns. Only a row with a count is changed: a nanowell that was occluded has no count
    for a gate to change."""
    if gate is None or not len(gate.sites) or not len(frame):
        return
    merged = _keyed(frame).merge(gate.sites, on=SITE_KEY, how="left")
    excluded = pd.Series(merged["excluded"].fillna(0).to_numpy(dtype=float), index=frame.index)
    hit = excluded.gt(0) & frame["n"].notna()
    if not hit.any():
        return
    frame.loc[hit, "n"] = (frame.loc[hit, "n"] - excluded[hit]).clip(lower=0)
    for ch in channels:
        kept = KEPT_PREFIX + str(ch).lower()
        column = f"brightness_{ch}"
        if column in frame.columns:
            values = (pd.Series(merged[kept].to_numpy(dtype=float), index=frame.index) if kept in merged.columns
                      else pd.Series(np.nan, index=frame.index))
            frame.loc[hit, column] = values[hit]


def note(gate: GateTally | None) -> str:
    """The figure's sentence about the gate: ``""`` when it left nothing out."""
    if gate is None or not gate.excluded:
        return ""
    said = (f"Cell Gating leaves out {gate.excluded:,} of {gate.cells:,} cells in these wells' nanowells: counts "
            "and brightness exclude them; confluence and nanowell fluorescence are measured from pixels and do not.")
    if gate.unread:
        said += f" The cells of {', '.join(gate.unread)} could not be read, so their counts are the run's own."
    return said
