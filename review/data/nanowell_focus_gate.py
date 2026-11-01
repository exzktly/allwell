"""Exclude the cells of a nanowell whose rim was out of focus, and say what that did.

Every nanowell run measures each fitted nanowell's rim sharpness at every timepoint (the ``focus`` column of
``nanowells/<well>.csv``). :mod:`review.domain.nanowell_rim_focus` judges each nanowell-timepoint against its own
well's sharp rims; this module turns that into ``NW_Focus_Pass`` — one 0/1 per cell, read from the cell's own
nanowell address and timepoint (``nw_q``, ``nw_r``, ``timepoint``, which the nanowell run writes on every cell) —
which the inclusion chokepoint (:func:`review.data.data_loading.df_included_mask`) ANDs with its siblings. So a
cell of an out-of-focus nanowell-timepoint is left out of every tab, exactly as its nanowell is a gap on the
Nanowells page: both read one verdict, judged on the same rows.

**Its own column, never ``Focus_Pass``.** That one is each nucleus's own outline against an absolute cut; this one
is the cell's nanowell rim against its well. A methods section, an export and the Image Browser's legend each name
the rule that left a cell out, and one column holding two rules could not.

**On by default for a nanowell run** (:func:`review.domain.qc_params.default_exclude_soft_nanowells`): the rule is
relative, so it needs no number from the user, and the Cell Gating panel, the inclusion funnel and the Nanowells
page each say what it left out.

**Three behaviours that are deliberate, each because the alternative is a silent substitution.**

* A well with **no nanowell table**, a table with no ``focus`` column, or a cell with no address or no timepoint
  passes: a cell nobody judged is not a cell judged and found wanting. :func:`nanowell_focus_status` says when the
  run recorded no rim focus at all.
* Switched off, the column is **removed** from every cached frame (``cache_projectors.drop_verdict_column``), so a
  frame is not filtered by a control the user has just unticked.
* The site table is read once per file version (path, modification time and size) and only the five columns the
  rule reads, and it is judged once per file version *and rule*: the projector runs on every well at every
  dataset load and on every plate-wide narrow read, and re-judging the whole table each time was much of what made
  a nanowell plate slow to open. A change of fraction re-judges without re-reading.

Qt-free: this is the data layer. It imports the domain's rule and parameters and nothing above them.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path

import pandas as pd

from review.data.cache_projectors import drop_verdict_column, project_verdict_column
from review.domain.nanowell_rim_focus import (
    CELL_KEY, NW_FOCUS_PASS_COLUMN, SITE_COLUMNS, FocusTally, RimFocusRule, cell_pass, flag_sites, tally,
)

log = logging.getLogger("review")

NANOWELL_DIR = "nanowells"


def nanowell_dir(data_dir) -> Path | None:
    """The ``nanowells/`` folder for a loaded data dir (the ``out/`` folder or its parent), or ``None``."""
    if not data_dir:
        return None
    data_dir = Path(data_dir)
    for candidate in (data_dir / NANOWELL_DIR, data_dir / "out" / NANOWELL_DIR):
        if candidate.is_dir():
            return candidate
    return None


def _params(ctx):
    """The loaded dataset's :class:`~review.domain.qc_params.QCParams`, read straight off typed state."""
    qc = getattr(ctx.state, "qc", None)
    return getattr(qc, "params", None)


def gate_is_on(ctx) -> bool:
    """Whether the user has asked for the cells of out-of-focus nanowells to be excluded."""
    params = _params(ctx)
    return bool(params is not None and params.exclude_soft_nanowells)


@functools.lru_cache(maxsize=512)
def _read_site_table(path: str, _mtime_ns: int, _size: int) -> pd.DataFrame | None:
    """The rule's columns of one site table, or ``None`` when it cannot be read. Keyed on the file's version, so
    a table rewritten by a later run is read again."""
    try:
        frame = pd.read_csv(path, usecols=lambda c: c in SITE_COLUMNS, dtype={"timepoint": str, "state": str})
    except (OSError, ValueError) as exc:
        log.warning("Nanowell focus: could not read %s (%s); its cells are kept unjudged", path, exc)
        return None
    if not {"timepoint", "nw_q", "nw_r", "state"} <= set(frame.columns):
        return None
    return frame


@functools.lru_cache(maxsize=512)
def _judged_site_table(path: str, mtime_ns: int, size: int, well: str, rule_key: tuple) -> pd.DataFrame | None:
    """:func:`flag_sites` of one site table version under the rule whose :meth:`RimFocusRule.key` is *rule_key*.

    Equal keys give equal verdicts (that is what the key is for), so the judged table is shared between callers:
    **read it, never mutate it**. Every caller here only filters or concatenates it, which copies."""
    table = _read_site_table(path, mtime_ns, size)
    if table is None:
        return None
    rule = RimFocusRule(True, rule_key[1]) if rule_key and rule_key[0] else RimFocusRule(False)
    return flag_sites(table, well, rule)


def flagged_sites(ctx, well: str, rule) -> pd.DataFrame | None:
    """*well*'s addressed nanowell-timepoints judged by *rule*, or ``None`` with no table. Cached per file version
    and rule (module docstring); the frame returned is shared, so a caller must not change it."""
    folder = nanowell_dir(getattr(ctx.state.dataset, "data_dir", None))
    if folder is None:
        return None
    path = folder / f"{well}.csv"
    try:
        stat = path.stat()
    except OSError:
        return None
    key = rule.key() if rule is not None else (False,)
    return _judged_site_table(str(path), stat.st_mtime_ns, stat.st_size, str(well), key)


def _wells_of(df, label: str) -> list[str]:
    if "well" in df.columns:
        return [str(w) for w in pd.unique(df["well"].dropna())]
    return [label]


def compute_nanowell_focus_pass(ctx, df, label: str, rule) -> pd.Series:
    """``1`` for each cell whose nanowell was in focus at its timepoint, ``0`` where the rule left it out.

    Every row passes with the rule off, on a frame without the nanowell address columns (any dataset not run in
    nanowell mode), and for a well with no site table."""
    passed = pd.Series(1, index=df.index, dtype=int)
    if rule is None or not rule.active() or df is None or not len(df) or any(c not in df for c in CELL_KEY):
        return passed
    tables = [t for t in (flagged_sites(ctx, well, rule) for well in _wells_of(df, label)) if t is not None]
    if not tables:
        return passed
    cells = df if "well" in df.columns else df.assign(well=label)
    return cell_pass(cells, pd.concat(tables, ignore_index=True))


def nanowell_focus_input_columns(ctx, label: str | None = None) -> tuple[str, ...]:
    """The columns :func:`apply_nanowell_focus_to_cache`'s verdict reads (``narrow_rows``)."""
    return (*CELL_KEY, "well") if gate_is_on(ctx) else ()


def apply_nanowell_focus_to_cache(ctx, label: str | None = None) -> list[str]:
    """Write ``NW_Focus_Pass`` onto cached well frame(s); drop it when switched off.

    Returns the labels whose verdict could not be computed, as its siblings do."""
    if not gate_is_on(ctx):
        drop_verdict_column(ctx, label, column=NW_FOCUS_PASS_COLUMN)
        return []
    rule = _params(ctx).nanowell_focus_rule()
    return project_verdict_column(
        ctx, label,
        column=NW_FOCUS_PASS_COLUMN,
        verdict=lambda df, well: compute_nanowell_focus_pass(ctx, df, well, rule),
        layer="Nanowell focus",
    )


def plate_tally(ctx, rule) -> FocusTally | None:
    """What *rule* does across every nanowell table of the dataset, or ``None`` when it has none.

    Counted from the site tables, not the cells, so it says what ticking the box *would* do before it is ticked."""
    folder = nanowell_dir(getattr(ctx.state.dataset, "data_dir", None))
    if folder is None:
        return None
    frames = [t for t in (flagged_sites(ctx, path.stem, rule) for path in sorted(folder.glob("*.csv")))
              if t is not None]
    if not frames:
        return None
    return tally(pd.concat(frames, ignore_index=True))


def _cells(n: int) -> str:
    return f"{n:,} cell" if n == 1 else f"{n:,} cells"


def excluded_cells(ctx) -> int:
    """How many cells ``NW_Focus_Pass`` is excluding across the plate right now (0 when it is off)."""
    from review.data.plate_scan import every_well

    if not gate_is_on(ctx):
        return 0

    def count(df) -> int:
        if df is None or not len(df) or NW_FOCUS_PASS_COLUMN not in df.columns:
            return 0
        return int(pd.to_numeric(df[NW_FOCUS_PASS_COLUMN], errors="coerce").eq(0).sum())

    rule = _params(ctx).nanowell_focus_rule()
    return sum(n or 0 for n in every_well(ctx, "nanowell_focus_cells", rule.key(), count,
                                          columns=(*CELL_KEY, "well")).values())


def nanowell_focus_status(ctx) -> tuple[str, bool]:
    """``(sentence, is_problem)`` for the Cell Gating panel's nanowell focus row; ``("", False)`` for a dataset
    with no nanowell tables, where the row is not shown."""
    params = _params(ctx)
    if params is None:
        return ("", False)
    on = bool(params.exclude_soft_nanowells)
    rule = params.nanowell_focus_rule()
    judged = plate_tally(ctx, rule if on else RimFocusRule(True, rule.fraction))
    if judged is None:
        return ("", False)
    if not judged.recorded:
        return ("This run recorded no nanowell rim focus, so no nanowell can be checked; a nanowell run records "
                "it for every fitted nanowell, so re-running the analysis does.", on)
    unchecked = []
    if judged.unjudged:
        unchecked.append(f"{judged.unjudged:,} with no rim focus (rim not fitted)")
    if judged.unreferenced:
        unchecked.append(f"{judged.unreferenced:,} in a well with too few rims at that timepoint")
    tail = f" Kept unchecked: {'; '.join(unchecked)}." if unchecked else ""
    if not judged.flagged:
        return (f"No nanowell was out of focus at any timepoint.{tail}", False)
    found = (f"{judged.flagged:,} nanowell-timepoint{'s' if judged.flagged != 1 else ''} "
             f"({judged.nanowells:,} nanowell{'s' if judged.nanowells != 1 else ''}) out of focus across the plate")
    if on:
        return (f"{found} — excluded, {_cells(excluded_cells(ctx))}.{tail}", False)
    return (f"{found} — currently kept.{tail}", False)
