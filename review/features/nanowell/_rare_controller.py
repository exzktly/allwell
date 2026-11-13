"""The Statistics view's **Rare responders** test: the page's groups and nanowells handed to the rare-responder
analysis (``rare_report_compute``), run on a worker, drawn, and its flagged nanowells shown in the crops pop-out.

**What it tests.** Each group the page draws (a Sample Definitions replicate set, or a well standing alone) other
than the control chosen under *vs* is a treatment, tested against the control group with the **wells** as the units:
the calibrated exceedance count, Higher Criticism and the upper-tail Anderson–Darling statistic, with the self-check
off (it re-runs every test dozens of times). The values are always the count fold change ``Nt / N0`` — the count at
the page's **Timepoint** over the count at its **Baseline** — whatever the Metric shows, because the tests stratify
nanowells by their starting count; the Subset, Cell Gating and the rim-focus rule have already been applied to the
metrics it reads, and a nanowell not measurable at the timepoint is excluded with its state as the reason
(``rare_data_compute.from_site_metrics``). The Compare unit, the moving average and the Fixed cohort do not apply
to it, and the figure says so when they are set.

**On a worker, once.** :func:`compose` builds the per-nanowell table on the GUI thread (vectorised, cheap) and keys
the result on a hash of that table and the parameters — everything the result is computed from, so a key can never
return a result computed for other numbers. A key not yet in ``NanowellState.rare_results`` is computed on a worker
through ``controller._fetch`` while the figure says so, and the page redraws when it arrives.

**Review by eye.** The nanowells the drawn result flagged — above the primary threshold, or at or below their well's
HC threshold — are kept on ``NanowellState.rare_flagged``, and the pop-out's **Show: Flagged** draws them as rows of
crops over time, :data:`FLAGGED_PAGE` to a page, through the same crop cutting the Distribution window's rows use.

Reaches ``controller`` the way ``_window_controller`` does, through :func:`_nwc`, because the controller imports this
module.
"""

from __future__ import annotations

import pandas as pd

from review.features.nanowell import controller as _controller
from review.features.nanowell import window_compute as wc
from review.features.nanowell._rare_render import render_rare_page
from review.features.nanowell.rare_data_compute import RareParams, from_site_metrics
from review.features.nanowell.rare_export_compute import report_lines, verdict_lines
from review.features.nanowell.rare_report_compute import RareResult, analyse
from review.features.nanowell.renderer import Footer, render_message, render_site_rows

#: Flagged nanowells the pop-out shows to a page, as the Distribution window's rows.
FLAGGED_PAGE = 24
#: Results kept at most: the one drawn and a few the user may step back to.
RESULTS_KEPT = 4
COMPUTING = "Running the rare-responder tests on a worker…"
NO_CONTROL = ("Rare responders test each group of wells against a control group: choose the control group under "
              "vs.")
NOT_APPLIED = "The moving average and the Fixed cohort do not apply to the rare-responder tests."
UNIT_IGNORED = ("The rare-responder tests always compare groups with wells as the units; Compare does not apply.")
VALUE_NOTE = "Values: count fold change Nt/N0 at the page's Timepoint over its Baseline, whatever the Metric."


def _nwc():
    return _controller


def conditions_of(groups: dict) -> dict:
    """``{well: group}`` for every well of *groups*, the first group naming a well keeping it."""
    out: dict = {}
    for name, wells in groups.items():
        for well in wells:
            out.setdefault(well, name)
    return out


def result_key(table: pd.DataFrame, params: RareParams) -> tuple:
    """What a result is computed from: every value of *table* (hashed) and every parameter."""
    digest = int(pd.util.hash_pandas_object(table, index=False).sum()) if len(table) else 0
    return digest, len(table), tuple(table.columns), params


def drawn_timepoint(metrics: pd.DataFrame, groups: dict, timepoint: str) -> str:
    """*timepoint*, or — when the page names none — the latest the drawn wells have, as the Statistics view's own
    samples are drawn (``controller.shown_timepoint``)."""
    if timepoint:
        return timepoint
    wells = [w for members in groups.values() for w in members]
    rows = metrics[metrics["well"].isin(wells)]
    if rows.empty:
        return ""
    order = "tp_order" if "tp_order" in rows else "time_h"
    return str(rows.sort_values(order, kind="stable")["timepoint"].iloc[-1])


def why_not(groups: dict, control: str, metrics: pd.DataFrame, timepoint: str) -> str:
    """The sentence the figure says instead of a result, or ``""`` when the tests can run."""
    if not control:
        return NO_CONTROL
    if control not in groups:
        return (f"{control} is not one of the groups drawn: choose a group under vs (the Rare responders test compares "
                "groups, not single wells' nanowells).")
    if not any(name != control for name in groups):
        return "Only the control group is drawn: select a treatment group too, or define replicate sets."
    at = metrics[metrics["timepoint"] == timepoint]
    if len(at) and (at["baseline_timepoint"] == timepoint).all():
        return f"{timepoint} is the Baseline, so every fold change is 1: choose a later Timepoint."
    return ""


def compose(fig, metrics, groups: dict, *, control: str, timepoint: str, note: str = "", store: dict | None,
            request, moving_on: bool = False, cohort: bool = False, unit_set: bool = False) -> RareResult | None:
    """Draw the Rare responders test into *fig*: its result when *store* holds it, else a sentence while *request*
    (``request(key, job)``) has it computed, or one saying why it cannot run. Returns the result drawn, or ``None``."""
    timepoint = drawn_timepoint(metrics, groups, timepoint)
    reason = why_not(groups, control, metrics, timepoint)
    if reason:
        render_message(fig, reason, gid="nw-rare-why")
        return None
    try:
        table = from_site_metrics(metrics, conditions_of(groups), timepoint)
    except ValueError as exc:
        render_message(fig, str(exc), gid="nw-rare-why")
        return None
    params = RareParams(control=control)
    key = result_key(table, params)
    held = (store or {}).get(key)
    if held is None:
        if request is not None:
            request(key, lambda: analyse(table, params))
        render_message(fig, COMPUTING, gid="nw-rare-computing")
        return None
    if isinstance(held, str):
        render_message(fig, held, gid="nw-rare-why")
        return None
    notes = [f"{VALUE_NOTE} Timepoint: {timepoint}.", *([NOT_APPLIED] if moving_on or cohort else []),
             *([UNIT_IGNORED] if unit_set else []), *([note] if note else [])]
    held.page_notes = notes                               # what Details lists under the report (:func:`footer_of`)
    render_rare_page(fig, held, verdict_lines(held), note=DETAILS_POINTER)
    return held


#: What the figure says under its verdicts: the full report is in the field under it, not in the figure.
DETAILS_POINTER = "Full report, warnings and notes: Details ▸ under the figure."


def footer_of(result: RareResult) -> Footer:
    """The page's **Details** field for a rare result: the counts, the warnings, then the full report and the page's
    notes — everything the figure leaves out so its panels keep their room."""
    log = result.log
    counts = (f"Rare responders: {len(result.units)} treatment(s) · {log['nanowells_kept']:,} nanowells kept, "
              f"{log['nanowells_excluded']:,} excluded")
    return Footer(counts=counts, alerts=list(result.warnings),
                  notes=[*report_lines(result), *result.page_notes])


def hook(ctx, st) -> dict:
    """The keywords ``controller.draw_view`` passes :func:`compose`: the page's store and its worker request."""
    def request(key, job) -> None:
        _nwc()._fetch(ctx, "rare_results", key, job,
                      failed=lambda message: f"The rare-responder tests could not be run: {message}")
    return {"store": st.rare_results, "request": request}


def flagged_rows(result: RareResult) -> list[dict]:
    """The flagged nanowells of *result*, one each, the highest fold change first: ``site``, ``fc``, ``why``."""
    if result is None or result.responders.empty:
        return []
    rows = []
    frame = result.responders.drop_duplicates(["well_key", "nanowell_id"]).sort_values("fc", ascending=False)
    for _i, row in frame.iterrows():
        q, r = (int(v) for v in str(row["nanowell_id"]).split(","))
        why = " + ".join(t for t, on in (("above threshold", row["above_threshold"]), ("HC", row["hc_candidate"]))
                         if on)
        rows.append({"site": (str(row["well_id"]), q, r), "fc": float(row["fc"]), "why": why,
                     "unit": row["unit"]})
    return rows


def after_draw(ctx, st, model) -> None:
    """After the Statistics view is drawn: keep the flagged nanowells of the rare result drawn (none otherwise), drop
    results beyond :data:`RESULTS_KEPT`, and refresh the pop-out when it shows them."""
    st.rare_flagged = flagged_rows(model) if isinstance(model, RareResult) else []
    st.rare_footer = footer_of(model) if isinstance(model, RareResult) else None
    while len(st.rare_results) > RESULTS_KEPT:
        st.rare_results.pop(next(iter(st.rare_results)))
    nwc = _nwc()
    nwc._call(ctx, "_nw_details_refresh")
    nwc._call(ctx, "_nw_popout_window_refresh")
    if st.popout_source == "responders":
        nwc.draw_popout(ctx)


def page_count(st) -> int:
    return max(1, -(-len(st.rare_flagged) // FLAGGED_PAGE))


def open_flagged_popout(ctx) -> None:
    """**Review flagged crops**: show the flagged nanowells in the pop-out, from the first page."""
    st = _nwc()._state(ctx)
    if st is None:
        return
    st.popout_source, st.window_page = "responders", 0
    nwc = _nwc()
    nwc._save_soon(ctx)
    nwc._call(ctx, "_nw_popout_refresh")
    nwc._call(ctx, "_nw_popout_window_refresh")
    nwc._call(ctx, "_nw_open_popout")
    nwc.draw_popout(ctx)


def _values(st, sites, column: str) -> dict:
    """``site -> {timepoint: value of column}`` from the page's metrics."""
    metrics = st.metrics
    if metrics is None or column not in metrics:
        return {}
    wells = {s[0] for s in sites}
    rows = metrics[metrics["well"].isin(wells)]
    out: dict = {tuple(s): {} for s in sites}
    for well, q, r, tp, value in zip(rows["well"], rows["nw_q"], rows["nw_r"], rows["timepoint"], rows[column]):
        site = (well, int(q), int(r))
        if site in out:
            out[site][str(tp)] = float(value) if pd.notna(value) else float("nan")
    return out


def draw_flagged_popout(ctx, st, fig) -> None:
    """The pop-out in **Flagged** mode: a page of the flagged nanowells as rows of crops over time, the highest
    count fold change first, each row's label its fold change at the Statistics view's timepoint."""
    wnc = _nwc().wnc
    st.popout_hits, st.popout_tiles = {}, []
    flagged = st.rare_flagged
    if not flagged:
        render_message(fig, "Run the Rare responders test on the Statistics view: the nanowells it flags are shown "
                            "here for review by eye.", gid="nw-popout-flagged-empty")
        return
    nwc = _nwc()
    if st.popout_tps_none:
        render_message(fig, nwc.NO_TIMEPOINTS_SAID, gid="nw-popout-no-timepoints")
        return
    st.window_page = min(max(st.window_page, 0), page_count(st) - 1)
    first = st.window_page * FLAGGED_PAGE
    shown = flagged[first:first + FLAGGED_PAGE]
    sites = [row["site"] for row in shown]
    timepoints = [str(tp) for tp in st.data.timepoints()]
    values = _values(st, sites, "count_fc")
    rows = [wc.WindowRow(row["site"], first + i + 1, row["fc"], values.get(row["site"], {}))
            for i, row in enumerate(shown)]
    columns = nwc.extremes_columns(timepoints, st.timepoint, st.popout_tps)
    wnc._want_crops(ctx, st, sites, sorted(columns, key=lambda tp: tp != st.timepoint))
    head = (f"{len(flagged):,} nanowell(s) flagged by the rare-responder tests (above the threshold, or at or below "
            "their well's HC threshold): check each for segmentation errors, doublets and debris.")
    st.popout_hits = render_site_rows(
        fig, wc.WindowRows(st.timepoint, "count_fc", timepoints),
        [(f"Flagged {first + 1}–{first + len(shown)} of {len(flagged):,}", rows)],
        wnc._page_crops(st, sites, columns), label="count fold change", empty=head, head=head,
        title="Rare responders · flagged nanowells", cells=st.crop_cells, numbers=st.crop_metrics,
        image_channel=nwc.page_image_channel(st), display_range=nwc.display_range(ctx), timepoints=columns,
        with_well=True)
