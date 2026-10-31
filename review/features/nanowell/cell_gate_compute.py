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

**A whole field is left out as a whole field.** Three layers speak about fields rather than cells
(:data:`FIELD_LAYERS`): the run's field-quality flag, a field excluded by hand in QC triage, and the FOVs chosen on
Cell Gating. In nanowell mode a field is a tile, and each nanowell is owned by one tile at each timepoint (the
site row's ``owner``, the cell row's ``fov``). Subtracting such a field's cells from its nanowells' counts made
every loaded nanowell in it read as measured and emptied — a count of 0, a fold change of 0, a dot at zero — and
left its empty nanowells counted as empty, from a field the analysis had excluded. So those layers are left out of
the per-cell inclusion here, and the fields they exclude are read from the app's own state instead
(:func:`excluded_fields`, gathered by ``controller.gate_tally``), so a field with no cell in it is excluded too:
each nanowell such a tile owns at that timepoint takes the state :data:`collect.FIELD_EXCLUDED`
(:func:`exclude_fields`), with no count, confluence, brightness or signal, and every metric says why. A well
whose FOV labels are untrusted is not filtered, the refusal those layers make everywhere else. The crop, focus and
border layers need per-cell centroid, focus and border columns that a nanowell cell table does not have, so they
pass every nanowell cell, as they pass any cell they cannot place.

**A well whose cells cannot be read keeps the run's counts**, and is named in the note, so a figure mixing gated
and ungated wells never reads as gated throughout.

Qt-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

SITE_KEY = ["well", "timepoint", "nw_q", "nw_r"]
#: The inclusion layers that decide whole fields, applied here by field (:func:`exclude_fields`) and never per cell.
FIELD_LAYERS = ("Field_Pass", "Excluded_Field_Pass", "FOV_Pass")
#: The suffix of a per-cell intensity column (``<token>_mean_intensity``), the one brightness is the mean of.
MEAN_SUFFIX = "_mean_intensity"
KEPT_PREFIX = "kept_"


@dataclass(frozen=True)
class GateTally:
    """The cells left out of each nanowell, over the wells whose per-cell rows were read.

    *sites* has one row per ``(well, timepoint, nw_q, nw_r)`` that lost at least one cell: ``excluded`` (cells
    left out) and, per intensity token in lower case, ``kept_<token>`` (the kept cells' mean, NaN with none
    kept). *cells* and *excluded* count every addressed cell of those wells; *unread* names the wells whose
    rows could not be read, whose counts are the run's own. *fields* holds the ``(well, tile, timepoint)`` the
    field layers leave out (:func:`excluded_fields`), whose nanowells are excluded whole."""
    sites: pd.DataFrame
    cells: int = 0
    excluded: int = 0
    unread: tuple = field(default_factory=tuple)
    fields: frozenset = frozenset()


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


def excluded_fields(sites: pd.DataFrame, *, excluded=(), flagged=(), chosen_fovs=None, untrusted=()) -> frozenset:
    """The ``(well, tile, timepoint)`` of *sites* (the page's site table) that a field layer leaves out.

    *excluded* and *flagged* are ``(well, fov, timepoint)`` keys — QC triage's exclusions, and the run's flagged
    fields when their exclusion is on; *chosen_fovs* the FOV labels chosen on Cell Gating (``None``: every field);
    *untrusted* the wells whose FOV labels cannot be joined on, which are never filtered."""
    from review.data.fov_selection import fov_match_key

    if not len(sites) or "owner" not in sites.columns:
        return frozenset()
    named = {tuple(str(v) for v in key) for key in (*excluded, *flagged)}
    chosen = None if chosen_fovs is None else {fov_match_key(f) for f in chosen_fovs}
    skip = {str(w) for w in untrusted}
    keys = sites[["well", "owner", "timepoint"]].dropna().astype(str).drop_duplicates()
    out = set()
    for well, owner, tp in keys.itertuples(index=False):
        if well in skip:
            continue
        if (well, owner, tp) in named or (chosen is not None and fov_match_key(owner) not in chosen):
            out.add((well, owner, tp))
    return frozenset(out)


def exclude_fields(frame: pd.DataFrame, gate: GateTally | None) -> None:
    """Give every row of *frame* (``series_compute``'s addressed grid) whose tile *gate* leaves out the state
    :data:`collect.FIELD_EXCLUDED`, in place, before anything is read from its values."""
    if gate is None or not gate.fields or not len(frame) or "owner" not in frame.columns:
        return
    from review.features.nanowell.collect import FIELD_EXCLUDED

    keys = pd.MultiIndex.from_arrays([frame["well"].astype(str), frame["owner"].astype(str),
                                      frame["timepoint"].astype(str)])
    hit = pd.Series(keys.isin(list(gate.fields)), index=frame.index) & frame["owner"].notna()
    frame.loc[hit, "state"] = FIELD_EXCLUDED


def tally(per_well, unread=(), fields=frozenset()) -> GateTally:
    """One :class:`GateTally` from *per_well*, an iterable of ``(well, cells, included)``, where *included* leaves
    out :data:`FIELD_LAYERS`; *fields* is :func:`excluded_fields`'s answer."""
    found, cells, excluded = [], 0, 0
    for well, frame, included in per_well:
        sites, n, out = well_left_out(well, frame, included)
        cells, excluded = cells + n, excluded + out
        if len(sites):
            found.append(sites)
    sites = pd.concat(found, ignore_index=True) if found else _empty_sites()
    return GateTally(sites=sites, cells=cells, excluded=excluded, unread=tuple(sorted(unread)),
                     fields=frozenset(fields))


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
    if gate is None or not (gate.excluded or gate.fields):
        return ""
    said = ""
    if gate.excluded:
        said = (f"Cell Gating leaves out {gate.excluded:,} of {gate.cells:,} cells in these wells' nanowells: "
                "counts and brightness exclude them; confluence and nanowell fluorescence are measured from pixels "
                "and do not.")
    if gate.fields:
        n = len(gate.fields)
        said += (" " if said else "") + (
            f"{n:,} excluded field (a tile at one timepoint) leaves out every nanowell it holds, with no value at all."
            if n == 1 else
            f"{n:,} excluded fields (a tile at one timepoint each) leave out every nanowell they hold, with no value "
            "at all.")
    if gate.unread:
        said += f" The cells of {', '.join(gate.unread)} could not be read, so their counts are the run's own."
    return said
