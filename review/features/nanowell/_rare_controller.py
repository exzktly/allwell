"""The Statistics view's **Rare responders** test: the page's groups and nanowells handed to the rare-responder
analysis (``rare_report_compute``), run on a worker, drawn, and its flagged nanowells shown in the crops pop-out.

**What it tests.** Each group the page draws (a Sample Definitions replicate set, or a well standing alone) other
than the control chosen under *vs* is a treatment, tested against the control group with the **wells** as the units:
the calibrated exceedance count, Higher Criticism and the upper-tail Anderson–Darling statistic, with the self-check
off (it re-runs every test dozens of times). The values are the page's **Metric** at its **Timepoint** — a
fold-change metric compares each nanowell with its own **Baseline** — with no grouping by starting cells (a
difference in seeding between the sides is said beside the verdict, ``rare_design_compute``), and *Responders higher
/ lower* chooses the tail; the Subset, Cell Gating and the rim-focus rule have already been applied to the metrics it
reads, and a nanowell not measurable at the timepoint is excluded with its state as the reason
(``rare_data_compute.from_site_metrics``).

**The moving average.** While it is on, each nanowell's value is its moving average at the Timepoint, taken exactly
as the other Statistics tests and the Distribution view take it — the baseline's own window first
(``moving_compute.rebase``), then the endpoint's (``moving_compute.apply``), with the same window, kind and baseline
window (:func:`windowed`). A nanowell the window leaves without a value is excluded with the window's reason
(``moving_compute.EARLY_REASON`` and its siblings), which the report's exclusion counts carry, never tested as a
number; a window that leaves no value at the Timepoint draws ``moving_compute.nothing_left_text`` instead of a result.
The control cutoff and the counts are then those of the averaged values, so a result with the average and one without
are different tests. The Compare unit and the Fixed cohort do not apply to it, and Details says so when they are
set.

**On a worker, once.** :func:`compose` builds the per-nanowell table on the GUI thread (vectorised, cheap) and keys
the result on a hash of that table and the parameters — everything the result is computed from, so a key can never
return a result computed for other numbers. A key not yet in ``NanowellState.rare_results`` is computed on a worker
through ``controller._fetch`` while the figure says so, and the page redraws when it arrives.

**Review by eye.** The nanowells the drawn result flagged — the treatment nanowells past the control cutoff — are
kept on ``NanowellState.rare_flagged``, and the pop-out's **Show: Flagged** draws them as rows of
crops over time, ``NanowellState.popout_rows`` to a page (the pop-out's **Grid** control), through the same crop cutting the Distribution window's rows use.

Reaches ``controller`` the way ``_window_controller`` does, through :func:`_nwc`, because the controller imports this
module.
"""

from __future__ import annotations

import pandas as pd

from review.features.nanowell import controller as _controller
from review.features.nanowell import moving_compute as mvc
from review.features.nanowell import window_compute as wc
from review.features.nanowell._rare_render import render_rare_page
from review.features.nanowell.rare_data_compute import RARE_QC_FLAGS, RareParams, from_site_metrics, qc_flag_counts
from review.features.nanowell.rare_export_compute import report_lines
from review.features.nanowell.rare_report_compute import RareResult, analyse
from review.features.nanowell.renderer import Footer, render_message, render_site_rows
from review.features.nanowell.summary_compute import timepoint_phrase

#: Results kept at most: the one drawn and a few the user may step back to.
RESULTS_KEPT = 4
COMPUTING = "Testing"                                   # the start of every computing message (computing_text)
NO_CONTROL = ("Rare responders test each group of wells against a control group: choose the control group under "
              "Control, above.")
NOT_APPLIED = "The Fixed cohort does not apply to the rare-responder tests."
UNIT_IGNORED = ("The rare-responder tests always compare groups with wells as the units; Compare does not apply.")
VALUE_NOTE = "Values: the page's Metric at its Timepoint, each nanowell on its own; no strata."
#: :data:`VALUE_NOTE` while the moving average is on: the values tested are averages, and the cutoff is theirs.
VALUE_NOTE_MOVING = ("Values: each nanowell's moving average of the page's Metric at its Timepoint, each nanowell on "
                     "its own; no strata. The control cutoff and the counts are those of the averaged values.")


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


def why_not(groups: dict, control: str, metrics: pd.DataFrame, timepoint: str, hidden: dict | None = None,
            column: str = "") -> str:
    """The sentence the figure says instead of a result, or ``""`` when the tests can run. *hidden* is the groups
    hidden on the plate (``controller.hidden_groups``): a control or treatment the plate hides is named, with how to
    show it again — a click on a group's well toggles the group, so choosing a group by clicking it can hide it."""
    hidden = hidden or {}
    if not control:
        return NO_CONTROL
    if control not in groups:
        if control in hidden:
            return (f"The control group {control} is hidden on the plate, so there is nothing to test against. "
                    + _nwc().hidden_sentence({control: hidden[control]}))
        return (f"{control} is not one of the groups drawn: choose a group under Control (the Rare responders test "
                "compares groups, not single wells' nanowells).")
    if not any(name != control for name in groups):
        if hidden:
            return "Only the control group is drawn. " + _nwc().hidden_sentence(hidden)
        return "Only the control group is drawn: select a treatment group too, or define replicate sets."
    at = metrics[metrics["timepoint"] == timepoint]
    # Only a change from the Baseline is constant at the Baseline; a level (a count, a brightness) still varies there.
    if (not column or "_fc" in column) and len(at) and (at["baseline_timepoint"] == timepoint).all():
        return f"{timepoint} is the Baseline, so every fold change is 1: choose a later Timepoint."
    return ""


def windowed(metrics: pd.DataFrame, wells, column: str, *, moving: int, baseline_moving: int = 0,
             moving_centred: bool = False) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """*metrics* of *wells* with *column* replaced by its moving average, as ``distribution_compute`` takes it for
    every other Statistics test: the baseline's own window (``moving_compute.rebase``, *baseline_moving* already as
    ``moving_compute.baseline_window_for`` allows it), then the endpoint's (``moving_compute.apply``, *moving*
    timepoints, centred with *moving_centred*). Returns ``(averaged, why, rebased)``: *why* is the window's reason for
    each value it left out (``""`` elsewhere), and *rebased* the frame the endpoint's window was taken over, which
    ``moving_compute.nothing_left_text`` reads."""
    frame = metrics[metrics["well"].isin(list(wells))]
    blank = pd.Series("", index=frame.index, dtype=object)
    rebased, reasons = mvc.rebase(frame, column, blank, baseline_moving, moving_centred)
    averaged, reasons = mvc.apply(rebased, column, reasons, moving, moving_centred)
    return averaged, reasons, rebased


def compose(fig, metrics, groups: dict, *, control: str, timepoint: str, note: str = "", store: dict | None,
            request, moving: int = 0, baseline_moving: int = 0, moving_centred: bool = False, cohort: bool = False,
            unit_set: bool = False, hidden: dict | None = None, guessed: str = "", column: str = "",
            value_label: str = "", direction: str = "higher", stamped: bool = False,
            exclude_qc=frozenset()) -> RareResult | None:
    """Draw the Rare responders test into *fig*: its result when *store* holds it, else a sentence while *request*
    (``request(key, job)``) has it computed, or one saying why it cannot run. Returns the result drawn, or ``None``.

    *moving* (0 or 1: off), *baseline_moving* and *moving_centred* are the moving average the page's other Statistics
    tests apply (:func:`windowed`); the caller passes 0 for a metric with one value per nanowell. *exclude_qc* (the
    band's *QC flags* ticks, keys of ``rare_data_compute.RARE_QC_FLAGS``) leaves out the nanowells those flags mark;
    every other flag's column is dropped from the table, so ``qc_reasons`` never sees it — by default all four, so the
    test keeps every nanowell the other views draw — and each choice is keyed apart. The figure says how many an
    excluded flag left out (:func:`qc_line`)."""
    timepoint = drawn_timepoint(metrics, groups, timepoint)
    reason = why_not(groups, control, metrics, timepoint, hidden, column)
    if reason:
        render_message(fig, reason, gid="nw-rare-why")
        return None
    averaging = bool(column) and mvc.is_on(moving) and column in metrics
    why = None
    if averaging:
        before = metrics[metrics["well"].isin(list(conditions_of(groups)))]
        metrics, why, rebased = windowed(metrics, conditions_of(groups), column, moving=moving,
                                         baseline_moving=baseline_moving, moving_centred=moving_centred)
        empty = (mvc.baseline_nothing_left_text(before, column, baseline_moving, timepoint=timepoint,
                                                centred=moving_centred)
                 or mvc.nothing_left_text(rebased, column, moving, timepoint=timepoint,
                                          baseline_window=baseline_moving, centred=moving_centred))
        if empty:
            render_message(fig, empty, gid="nw-moving-empty")
            return None
    try:
        table = from_site_metrics(metrics, conditions_of(groups), timepoint, value_column=column or None,
                                  value_reasons=why)
    except ValueError as exc:
        render_message(fig, str(exc), gid="nw-rare-why")
        return None
    excluded = [key for key in RARE_QC_FLAGS if key in set(exclude_qc)]
    table = table.drop(columns=[RARE_QC_FLAGS[key][0] for key in RARE_QC_FLAGS if key not in excluded],
                       errors="ignore")
    params = RareParams(control=control, direction=direction)
    key = (*result_key(table, params), value_label)      # the label is in the result's sentences
    held = (store or {}).get(key)
    if held is None:
        if request is not None:
            request(key, lambda: analyse(table, params, value_label=value_label))
        render_message(fig, computing_text(groups, control), gid="nw-rare-computing")
        return None
    if isinstance(held, str):
        render_message(fig, held, gid="nw-rare-why")
        return None
    values = VALUE_NOTE_MOVING if averaging else VALUE_NOTE
    notes = [f"{values} Timepoint: {timepoint_phrase(timepoint, timepoint_hours(metrics, timepoint))}.",
             *([mvc.describe(moving, column, baseline_moving, moving_centred)] if averaging else []),
             *([excluded_note(excluded)] if excluded else []),
             *([NOT_APPLIED] if cohort else []), *([UNIT_IGNORED] if unit_set else []), *([note] if note else [])]
    held.page_notes = notes                               # what Details lists under the report (:func:`footer_of`)
    lead = [*([guessed_note(control)] if guessed and guessed == control else []), qc_line(held)]
    render_rare_page(fig, held, note=DETAILS_POINTER, stamped=stamped, lead=[line for line in lead if line])
    return held


def qc_line(result: RareResult) -> str:
    """The figure's line, drawn before everything and never cut, naming how many nanowells each excluded flag of the
    run left out of the test — which every other view draws, so a test on fewer nanowells than the Distribution view
    shows says why on its face. ``""`` when no flag left any out."""
    counts = qc_flag_counts(result.prepared.exclusions)
    if not counts:
        return ""
    parts = ", ".join(f"{n:,} {RARE_QC_FLAGS[key][1]}" for key, n in counts.items())
    return (f"⚠ Left out by the run's flags before testing, as chosen under QC flags: {parts}. Other views draw "
            "them.")


def excluded_note(excluded) -> str:
    """Details' sentence when the page excludes some of the run's flags."""
    names = ", ".join(RARE_QC_FLAGS[key][1] for key in excluded)
    return f"Left out by the QC flags choice: nanowells {names}. Every other view draws them."


def timepoint_hours(metrics: pd.DataFrame, timepoint: str) -> float:
    """*timepoint*'s hours as the page's metrics carry them (``time_h``), NaN when they carry none."""
    if "time_h" not in metrics:
        return float("nan")
    hours = pd.to_numeric(metrics.loc[metrics["timepoint"] == timepoint, "time_h"], errors="coerce").dropna()
    return float(hours.iloc[0]) if len(hours) else float("nan")


#: Words that name a control group. A group whose name has one of them as a word is taken as the control when the
#: Rare responders test is chosen with none (:func:`guess_control`).
CONTROL_WORDS = frozenset({"control", "controls", "ctrl", "ctl", "dmso", "vehicle", "veh", "untreated", "mock",
                           "negative", "neg"})


def guess_control(names: list) -> str:
    """The one group among *names* whose name says it is a control (:data:`CONTROL_WORDS`, any case, as a word:
    ``DMSO 0.1%``, ``Ctrl-A``), or ``""`` when none or several do — a guess between two is no guess."""
    import re

    hits = [n for n in names if CONTROL_WORDS & set(re.split(r"[^a-z0-9]+", str(n).lower()))]
    return hits[0] if len(hits) == 1 else ""


def guessed_note(control: str) -> str:
    """The figure's sentence when the control was chosen by its name."""
    return f"⚠ {control} was taken as the control because of its name: choose another under Control if it is not."


def computing_text(groups: dict, control: str) -> str:
    """What the figure says while the tests run: what is being tested against what, not where it runs."""
    others = [name for name in groups if name != control]
    return f"{COMPUTING} {', '.join(others)} against {control} for rare responders…"


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
    return {"store": st.rare_results, "request": request, "guessed": st.control_guessed,
            "direction": st.rare_direction, "exclude_qc": frozenset(st.rare_qc_excluded)}


def flagged_rows(result: RareResult) -> list[dict]:
    """The flagged nanowells of *result*, one each, the most extreme first — the highest value, or the lowest when the
    test looked lower: ``site``, ``fc`` (the value as measured), ``why``."""
    if result is None or result.responders.empty:
        return []
    rows = []
    frame = result.responders.drop_duplicates(["well_key", "nanowell_id"]).sort_values(
        "fc", ascending=result.prepared.sign < 0)
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
    st.rare_value_label = model.prepared.value_label if isinstance(model, RareResult) else ""
    st.rare_footer = footer_of(model) if isinstance(model, RareResult) else None
    nwc_ = _nwc()
    nwc_._call(ctx, "_nw_refresh_controls")             # Review flagged crops follows what this result flagged
    while len(st.rare_results) > RESULTS_KEPT:
        st.rare_results.pop(next(iter(st.rare_results)))
    nwc = _nwc()
    nwc._call(ctx, "_nw_details_refresh")
    nwc._call(ctx, "_nw_popout_window_refresh")
    if st.popout_source == "responders":
        nwc.draw_popout(ctx)


#: The pop-out with nothing flagged: after a result with none, and before any result.
FLAGGED_NONE = "No nanowell was past the control cutoff, so the Rare responders test flagged nothing to review."
FLAGGED_NOT_RUN = ("Run the Rare responders test on the Statistics view: the nanowells that grow past the control "
                   "cutoff are shown here for review by eye.")


def page_count(st) -> int:
    return max(1, -(-len(st.rare_flagged) // st.popout_rows))


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
        render_message(fig, FLAGGED_NONE if st.rare_footer is not None else FLAGGED_NOT_RUN,
                       gid="nw-popout-flagged-empty")
        return
    nwc = _nwc()
    if st.popout_tps_none:
        render_message(fig, nwc.NO_TIMEPOINTS_SAID, gid="nw-popout-no-timepoints")
        return
    st.window_page = min(max(st.window_page, 0), page_count(st) - 1)
    first = st.window_page * st.popout_rows
    shown = flagged[first:first + st.popout_rows]
    sites = [row["site"] for row in shown]
    timepoints = [str(tp) for tp in st.data.timepoints()]
    column = nwc.drawn_metric_column(ctx) or "count_fc"        # the Metric the test ran on, not a fixed one
    values = _values(st, sites, column)
    rows = [wc.WindowRow(row["site"], first + i + 1, row["fc"], values.get(row["site"], {}))
            for i, row in enumerate(shown)]
    columns = nwc.extremes_columns(timepoints, st.timepoint, st.popout_tps)
    wnc._want_crops(ctx, st, sites, sorted(columns, key=lambda tp: tp != st.timepoint))
    head = (f"{len(flagged):,} nanowell(s) were past the control cutoff — the ones the verdict is counted from. Check "
            "each for segmentation errors, doublets and debris")       # the caption adds its own full stop
    st.popout_hits = render_site_rows(
        fig, wc.WindowRows(st.timepoint, column, timepoints),
        [(f"Flagged {first + 1}–{first + len(shown)} of {len(flagged):,}", rows)],
        wnc._page_crops(st, sites, columns), label=st.rare_value_label or "count fold change", empty=head, head=head,
        title="Rare responders · flagged nanowells", cells=st.crop_cells, numbers=st.crop_metrics,
        image_channel=nwc.page_image_channel(st), display_range=nwc.display_range(ctx), timepoints=columns,
        with_well=True, tp_names=nwc.timepoint_names(st))
