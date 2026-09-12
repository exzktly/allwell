"""Every hour the Response Curves figure prints is the corrected one.

Properties → **Time** states ``filename hours × interval + offset``. The Heat
Map, the Distribution tab and the batch-export scatter already name the
corrected hour in their titles, because none of them has a time axis to hang
the marker on. Response Curves is the same shape and was the last holdout: its
x is dose, its timepoints are the *series*, and it printed the filename's raw
number in four places at once.

That made it the worst of the group rather than merely the last. A tab whose
title, legend, no-fit caption and omission caption all name hours cannot be
half-corrected: a title reading ``t = [24, 48] h`` over a legend reading ``t=6``
is not a smaller version of the original defect, it is a new one *inside a
single figure*. So the four move together, and
:func:`test_no_response_site_formats_an_hour_outside_the_helpers` is the guard
that keeps a fifth from arriving uncorrected.

The marker (``filename value × 4``) appears **once**, on the title. It is a
property of the plate rather than of each curve, and repeated down a legend it
would read as several different corrections.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace


from review.domain.time_rescale import TimeRescale, figure_timepoints_label
from tests.test_response_figure_title import _one_dose_plate
from tests.test_response_tab import tab  # noqa: F401 — the fixture is the point

RESPONSE = Path(__file__).resolve().parent.parent / "review" / "features" / "response"

OFF = TimeRescale(factor=1.0, offset=0.0)
#: Six images four hours apart, numbered 1-6 by the microscope.
X4 = TimeRescale(factor=4.0, offset=0.0)


# ── the helper the titles go through ────────────────────────────────────────

def test_a_single_timepoint_reads_singular():
    assert figure_timepoints_label([6.0], OFF) == "t = 6 h"


def test_several_timepoints_read_as_a_list():
    assert figure_timepoints_label([6.0, 12.0], OFF) == "t = [6, 12] h"


def test_the_interval_corrects_every_number_in_the_list():
    assert figure_timepoints_label([6.0, 12.0], X4).startswith("t = [24, 48] h")


def test_the_offset_composes_after_the_interval():
    """``filename × interval + offset``, in that order — 6 × 4 − 2 = 22."""
    rescale = TimeRescale(factor=4.0, offset=-2.0)
    assert figure_timepoints_label([6.0], rescale).startswith("t = 22 h")


def test_the_marker_is_stated_once_however_many_timepoints():
    """Three numbers, one correction. A marker per value would read as three."""
    label = figure_timepoints_label([6.0, 12.0, 18.0], X4)
    assert label == "t = [24, 48, 72] h, filename value × 4"
    assert label.count("filename value") == 1


def test_an_uncorrected_label_carries_no_marker():
    assert figure_timepoints_label([6.0, 12.0], OFF) == "t = [6, 12] h"


def test_a_timepoint_the_rescale_cannot_format_is_still_counted():
    """A tidy string that misstates the figure is worse than an ugly honest one.

    Dropping the value made the count lie: two curves gave ``t = 24 h``, naming
    one, in the singular form this helper reserves for a genuine single, while
    the legend beside it read ``t=nan`` and ``t=24``. The fallback matches
    ``renderer._scaled_tp``, which has always kept the raw rendering.
    """
    assert figure_timepoints_label([float("nan"), 6.0], X4).startswith("t = [nan, 24] h")


def test_a_numpy_array_of_timepoints_is_accepted():
    """``values or ()`` asks an array whether it is truthy, which raises. This is
    a ``domain/`` helper and a caller will hand it an array eventually."""
    import numpy as np

    assert figure_timepoints_label(np.array([6.0, 12.0]), X4).startswith("t = [24, 48] h")


def test_no_timepoints_names_nothing():
    """Rather than ``t = [] h``, which reads as a figure whose numbers went
    missing instead of one with no timepoint to name."""
    assert figure_timepoints_label([], X4) == ""


def test_the_off_state_reads_exactly_as_the_bar_and_line_titles_do():
    """One spelling of the timepoint clause across every tab.

    Asserted against ``metric_labels.timepoint_title`` — the builder the Bar and
    Line titles use — rather than against a literal, because a literal is what
    let this go wrong: the first version of this test asserted the *new*
    spelling this branch introduced, so the wrong implementation that passed it
    was the one that shipped. Comparing the two builders cannot drift: whichever
    of them changes, this fails.

    The conversion also has to be invisible until a correction is set — every
    site swapped in this branch wrote ``f"{t:g}"`` — so the values go wide.
    """
    from review.data.metric_labels import timepoint_title

    for value in (0.0, 1.0, 1.5, 6.0, 24.0, 100.0, 0.25, 1e-7, 1e20):
        assert (figure_timepoints_label([value], OFF)
                == timepoint_title([value], f"{value:g}"))
    for values in ([24.0, 48.0], [1.0, 2.0, 3.0]):
        assert (figure_timepoints_label(values, OFF)
                == timepoint_title(values, ", ".join(f"{v:g}" for v in values)))


def test_the_singular_helper_reads_the_same_way():
    """``figure_timepoint_label`` sits beside the plural and serves the Heat Map,
    the Distribution tab and the batch-export scatter. Two near-identically
    named helpers disagreeing about their one output format is CLAUDE.md §7.6
    #4's hazard by construction, so they are held together here."""
    from review.data.metric_labels import timepoint_title
    from review.domain.time_rescale import figure_timepoint_label

    for value in (0.0, 6.0, 24.0, 1.5):
        assert (figure_timepoint_label(value, OFF)
                == timepoint_title([value], f"{value:g}"))


# ── the four surfaces of one figure ─────────────────────────────────────────

def _curves_data(timepoints, *, series_label=""):
    """The little of ``ResponseData`` the label functions read."""
    curves = [
        SimpleNamespace(timepoint=t, key=(t, ""), series_label=series_label,
                        points=[SimpleNamespace(x=1.0, y=1.0)])
        for t in timepoints
    ]
    return SimpleNamespace(curves=curves, series_name=("Line" if series_label else ""),
                           variable_name="Dose")


def test_the_legend_entry_names_the_corrected_hour():
    from review.features.response import renderer as R

    data = _curves_data([6.0, 12.0])
    labels = [R._curve_label(data, c, rescale=X4) for c in data.curves]
    assert labels == ["t=24", "t=48"], labels


def test_the_legend_entry_carries_no_marker_of_its_own():
    """The correction is named on the title. A legend of four curves would
    otherwise carry four copies of the same sentence."""
    from review.features.response import renderer as R

    data = _curves_data([6.0, 12.0])
    for c in data.curves:
        assert "filename value" not in R._curve_label(data, c, rescale=X4)


def test_the_no_fit_caption_names_the_hour_the_legend_shows():
    """This sentence identifies a curve *by* its legend entry, so a raw hour
    here points at an entry the figure does not carry."""
    from review.features.response import renderer as R

    data = _curves_data([6.0])
    fits = {(6.0, ""): SimpleNamespace(ok=False, reason="too few points")}
    caption = R._fit_caption(fits, data, rescale=X4)
    assert "No fit at t=24: too few points" == caption, caption


def test_the_omission_caption_names_the_corrected_hour():
    from review.features.response.collect import ResponseData, omission_caption

    data = ResponseData(variable_name="Dose", control_gaps=[("A01", 6.0)])
    assert "A01 t=24" in omission_caption(data, rescale=X4)


def test_the_omission_caption_is_unchanged_with_no_correction():
    from review.features.response.collect import ResponseData, omission_caption

    data = ResponseData(variable_name="Dose", control_gaps=[("A01", 6.0)])
    assert omission_caption(data, rescale=OFF) == omission_caption(data)
    assert "A01 t=6" in omission_caption(data)


def test_the_title_and_the_legend_agree_on_one_figure():
    """The defect a half-sweep would produce, asserted directly.

    A title naming 24 and 48 over a legend naming 6 and 12 is worse than the
    original: the two disagree *within one figure*, where a reader cannot even
    appeal to another tab to work out which is meant.
    """
    from review.features.response import renderer as R

    data = _curves_data([6.0, 12.0])
    title = figure_timepoints_label([6.0, 12.0], X4)
    legend = [R._curve_label(data, c, rescale=X4) for c in data.curves]
    for entry in legend:
        number = entry.split("=")[-1]
        assert number in title, (title, legend)


# ── the callers, not just the helpers ───────────────────────────────────────
#
# Everything above tests functions. A mutation that forced the correction off at
# the *call* — ``_tp_label(drawn, OFF)`` in the tab's own title builder — left
# every one of them green, which is CLAUDE.md §7.8's second face exactly: the
# assertion was weaker than the claim because it never reached the caller. These
# two drive the real title builders.

def test_the_tabs_own_title_carries_the_correction(tab):  # noqa: F811
    """Through ``_figure_title``, the function the tab actually draws with."""
    from review.features.response import controller as rctl

    _one_dose_plate(tab)
    tab.ctx.state.plot.time_scale = 4.0
    tab.ctx.state.plot.time_offset = 0.0
    title = rctl._figure_title(tab.ctx, rctl.response_data(tab.ctx))
    assert "t = 96 h" in title, title
    assert "filename value × 4" in title, title


def test_the_tabs_own_title_is_untouched_with_no_correction(tab):  # noqa: F811
    """The other half — a builder that always corrected would pass the first."""
    from review.features.response import controller as rctl

    _one_dose_plate(tab)
    title = rctl._figure_title(tab.ctx, rctl.response_data(tab.ctx))
    assert "t = 24 h" in title, title
    assert "filename value" not in title, title


def test_the_batch_export_title_reads_the_same_correction():
    """The exported figure and the tab must not name different hours.

    Driven through ``_figure_title`` on a panel whose ``_ctx`` is the only thing
    that method reads for the correction, so this fails for a panel that forgot
    to read it — the mutation that went unnoticed on the tab side.
    """
    from review.features.batch_export.response_panel import ResponseBatchExportPanel

    panel = ResponseBatchExportPanel.__new__(ResponseBatchExportPanel)
    panel._ctx = SimpleNamespace(
        state=SimpleNamespace(plot=SimpleNamespace(time_scale=4.0, time_offset=0.0)))
    title = ResponseBatchExportPanel._figure_title(
        panel, SimpleNamespace(name="Drug A"), SimpleNamespace(),
        channel="gfp", metric_key="mean_intensity",
        metric_label="Mean Intensity", draws_fraction=False, use_sem=True,
        variable_name="Dose", timepoints=[6.0, 12.0])
    assert "t = [24, 48] h" in title, title
    assert title.count("filename value") == 1, title


# ── the drawn figure, which is the only thing the user reads ────────────────
#
# Everything above this line calls a labelling function and hands it the
# correction. That cannot see whether ``draw_response`` *threads* the correction
# to those functions — and it does not: an adversarial review deleted the one
# line in the renderer that reads it and the whole fast lane stayed green
# (8904 passed), on a tree printing raw hours in every legend entry, in the
# ``No fit at …`` note and in the omission caption, under a corrected title.
#
# That is the within-figure disagreement this file exists to prevent, and the
# test named "the title and the legend agree on one figure" could not see it,
# because it built both halves itself and handed each the same rescale. A test
# that supplies the value under test is not measuring who supplies it.
#
# So these drive ``redraw_response`` and read the artists off the axes — the
# boundary (CLAUDE.md §7.4 #1).

def _drawn(tab, *, scale=1.0, offset=0.0, timepoints=(6.0, 12.0)):  # noqa: F811
    """Draw the real figure under a correction and hand back its axes.

    Every timepoint is ticked on, because a curve per timepoint is the shape
    this whole file is about and the picker opens with one.
    """
    from review.features.response import controller as rctl

    _one_dose_plate(tab, timepoints=timepoints)
    slice_ = tab.ctx.state.feature_slice("response")
    slice_.tp_selections = {f"{t:g}": True for t in timepoints}
    tab.ctx.state.plot.time_scale = scale
    tab.ctx.state.plot.time_offset = offset
    rctl.redraw_response(tab.ctx)
    return tab.ax


def _legend_texts(ax) -> list[str]:
    return list(ax.get_legend_handles_labels()[1])


def test_the_drawn_legend_carries_the_corrected_hours(tab):  # noqa: F811
    """The assertion the review's mutation had to fail and did not.

    Deleting the renderer's ``_rescale`` read makes this read ``['t = 6', 't = 12']``.
    """
    ax = _drawn(tab, scale=4.0)
    assert _legend_texts(ax) == ["t=24", "t=48"], _legend_texts(ax)


def test_the_drawn_legend_is_untouched_with_no_correction(tab):  # noqa: F811
    """The other half: a renderer that always corrected would pass the first."""
    ax = _drawn(tab)
    assert _legend_texts(ax) == ["t=6", "t=12"], _legend_texts(ax)


def test_the_drawn_title_and_the_drawn_legend_name_the_same_hours(tab):  # noqa: F811
    """Both halves read off **one** drawn figure, neither supplied by the test.

    This is what the earlier same-named test claimed to do. It built the title
    and the legend separately and gave each the correction by hand, so the one
    thing it could never catch was a renderer that failed to apply it.
    """
    from review.features.response import controller as rctl

    ax = _drawn(tab, scale=4.0)
    # The ``tab`` fixture stubs ``apply_ax_style``, which is what puts a title on
    # the axes, so the title comes from the builder the renderer is handed. The
    # legend still comes off the drawn artists, which is the half that was
    # unmeasured.
    title = rctl._figure_title(tab.ctx, rctl.response_data(tab.ctx))
    for entry in _legend_texts(ax):
        number = entry.rsplit("=", 1)[-1]
        assert number in title, (title, _legend_texts(ax))


def test_the_drawn_caption_carries_the_corrected_hour(tab):  # noqa: F811
    """The omission caption is drawn on the axes and travels with a saved figure.

    Reached through the renderer rather than by calling ``omission_caption``
    directly, for the same reason as the legend above.
    """
    from review.features.response import collect as rc
    from review.features.response import controller as rctl

    _one_dose_plate(tab, timepoints=(6.0,))
    tab.ctx.state.plot.time_scale = 4.0
    real = rc.omission_caption

    def _with_a_gap(data, **kwargs):
        data.control_gaps = [("A01", 6.0)]
        return real(data, **kwargs)

    import review.features.response.renderer as R
    saved = R.omission_caption
    R.omission_caption = _with_a_gap
    try:
        rctl.redraw_response(tab.ctx)
    finally:
        R.omission_caption = saved
    drawn = " ".join(t.get_text() for t in tab.ax.texts)
    assert "A01 t=24" in drawn, drawn


# ── the fold-change suffix, which lands in the same title string ────────────

def test_the_fold_change_baseline_hour_agrees_with_the_timepoint_clause(tab):  # noqa: F811
    """One title, one reading of the same moment.

    The normalisation suffix is built by ``domain/fold_change`` and appended to
    a title that names its own timepoints corrected, so a raw baseline hour put
    the same moment on one title twice — ``fold change vs t=6h`` beside
    ``t = [24, 48] h`` for a plate imaged every four hours. Worse than the state
    it replaced: before this branch the whole title was raw and at least
    internally consistent.

    Asserted on the whole title rather than on the suffix, because the defect is
    the disagreement between two clauses and only the assembled string holds
    both.
    """
    from review.features.response import controller as rctl

    _one_dose_plate(tab, timepoints=(6.0, 12.0))
    slice_ = tab.ctx.state.feature_slice("response")
    slice_.tp_selections = {"6": True, "12": True}
    plot = tab.ctx.state.plot
    plot.time_scale, plot.time_offset = 4.0, 0.0
    plot.fc_vs_t0_on, plot.fc_baseline_tp, plot.fc_op_t0 = True, "6", "ratio"

    title = rctl._figure_title(tab.ctx, rctl.response_data(tab.ctx))
    assert "vs t=24h" in title, title
    assert "t = [24, 48] h" in title, title
    assert "t=6h" not in title, title


def test_the_fold_change_baseline_hour_is_raw_with_no_correction(tab):  # noqa: F811
    """The other half — a suffix that always corrected would pass the first."""
    from review.features.response import controller as rctl

    _one_dose_plate(tab, timepoints=(6.0, 12.0))
    slice_ = tab.ctx.state.feature_slice("response")
    slice_.tp_selections = {"6": True, "12": True}
    plot = tab.ctx.state.plot
    plot.fc_vs_t0_on, plot.fc_baseline_tp, plot.fc_op_t0 = True, "6", "ratio"

    title = rctl._figure_title(tab.ctx, rctl.response_data(tab.ctx))
    assert "vs t=6h" in title, title
    assert "t = [6, 12] h" in title, title


def test_the_fold_change_suffix_carries_no_marker_of_its_own(tab):  # noqa: F811
    """The correction is stated once per figure, in the timepoint clause.

    Bar and Line append this same suffix and state it on their axis label
    instead; a copy inside the suffix would read as a second correction on
    every one of them.
    """
    from review.features.response import controller as rctl

    _one_dose_plate(tab, timepoints=(6.0,))
    slice_ = tab.ctx.state.feature_slice("response")
    slice_.tp_selections = {"6": True}
    plot = tab.ctx.state.plot
    plot.time_scale = 4.0
    plot.fc_vs_t0_on, plot.fc_baseline_tp, plot.fc_op_t0 = True, "6", "ratio"

    title = rctl._figure_title(tab.ctx, rctl.response_data(tab.ctx))
    assert title.count("filename value") == 1, title


# ── the guard: no fifth site arrives uncorrected ────────────────────────────

#: The files a Response figure's text can come from. Not the feature package
#: alone: the batch-export panel builds one of the two titles this sweep
#: converted, and ``domain/fold_change.py`` builds the normalisation suffix that
#: is appended to both. A guard whose surface is narrower than the sweep it
#: guards reports a clean tree by not looking (CLAUDE.md §7.5 #3) — this one did
#: exactly that, and the site in ``fold_change`` below is what it was not seeing.
_SCANNED = (
    RESPONSE / "controller.py",
    RESPONSE / "renderer.py",
    RESPONSE / "collect.py",
    RESPONSE.parent / "batch_export" / "response_panel.py",
    RESPONSE.parent.parent / "domain" / "fold_change.py",
)

#: Empty, and that is the point: every hour a Response figure names now goes
#: through a correcting helper. The roster stays because the guard needs
#: somewhere to record a site that legitimately keeps a filename hour — a menu
#: entry or a CSV column — and because an exemption must be a row with a written
#: reason rather than an absence.
#:
#: The one row this held came off by measurement, not by memory: it recorded
#: `fold_change.fold_change_suffix_for` as a known defect, that defect was
#: fixed, and the liveness test below said the entry no longer described a real
#: site on the very next run.
_KEEPS_FILENAME_HOURS: dict[tuple[str, str], str] = {}

#: An hour interpolated after a ``t=``. Deliberately wider than the one shape
#: the sweep converted (``f"t={x:g}"``): a reviewer found this guard walking
#: past a formatter that is not a ``:g`` conversion, a bare ``{x}``, a
#: ``%``-format, and a string concatenation, which is not an f-string node at
#: all and so is matched textually instead.
#:
#: The lookbehind is load-bearing: without it ``style: dict = {…}`` matches, on
#: the ``t`` of ``dict``, and the roster fills up with exemptions for lines that
#: never named an hour.
_RAW_HOUR = re.compile(
    r"""(?<![A-Za-z0-9_])t\s*=\s*(\{[^}]*\}|%[-#0-9.]*[dfgeGE])""")
_CONCAT_HOUR = re.compile(r"""["'](?<![A-Za-z0-9_])t\s*=\s*["']\s*\+""")

#: The formatters that apply the correction. An hour routed through one of these
#: is the *fixed* shape, so it is not a finding and needs no exemption — which
#: is the difference between a guard that names the rule and one that merely
#: lists lines.
_APPROVED = ("_scaled_tp(", "_tp(", "format_scaled(", "figure_timepoint")


def _is_approved(text: str) -> bool:
    """True when the hour is routed through a formatter that corrects it."""
    return any(helper in text for helper in _APPROVED)


def _enclosing_function(tree) -> dict[int, str]:
    """Line number → the name of the function whose body holds it."""
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            # Innermost wins: a nested helper owns its own lines.
            owner[line] = node.name
    return owner


def test_no_response_site_formats_an_hour_outside_the_helpers():
    """A further surface added with a raw hour fails here.

    The sites this file's fix converted were found by grepping *after* the first
    two had already been chosen, which is exactly how a half-sweep happens — so
    the protection is mechanical rather than a promise to remember. It missed a
    fifth site all the same, by scanning one package when the sweep spanned
    three; the surface above is that lesson.
    """
    offenders = []
    for path in _SCANNED:
        text = path.read_text()
        tree = ast.parse(text, filename=str(path))
        owner = _enclosing_function(tree)
        lines = text.splitlines()
        hits: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                rendered = ast.unparse(node)
                if _RAW_HOUR.search(rendered) and not _is_approved(rendered):
                    hits.append((node.lineno, rendered))
        for lineno, line in enumerate(lines, start=1):
            if ((_CONCAT_HOUR.search(line) or _RAW_HOUR.search(line))
                    and not _is_approved(line)):
                if not any(h[0] == lineno for h in hits):
                    hits.append((lineno, line.strip()))
        for lineno, rendered in hits:
            if (path.name, owner.get(lineno, "")) in _KEEPS_FILENAME_HOURS:
                continue
            offenders.append(
                f"{path.name}:{lineno} (in {owner.get(lineno, '<module>')}): {rendered}")
    assert not offenders, (
        "an hour formatted raw on a Response figure — route it through "
        "renderer._scaled_tp or time_rescale.figure_timepoints_label, or add its "
        "function to _KEEPS_FILENAME_HOURS with the reason:\n  "
        + "\n  ".join(offenders)
    )


def test_the_exemption_roster_still_describes_real_functions():
    """An entry that stopped matching anything is worse than none: it reads as a
    considered exemption while guarding nothing.

    The roster this replaced had three entries and exempted **zero** sites — the
    guard's pattern required a ``t=`` prefix that none of the three lines had —
    while its liveness test checked a weaker predicate (``":g}" in line``) and so
    confirmed all three.  This one asserts what the guard asserts: the named
    function exists, and it holds a line the guard would otherwise flag.
    """
    stale = []
    for (filename, func), reason in _KEEPS_FILENAME_HOURS.items():
        path = next((p for p in _SCANNED if p.name == filename), None)
        if path is None:
            stale.append(f"{filename}: not in the scanned set")
            continue
        text = path.read_text()
        tree = ast.parse(text, filename=str(path))
        owner = _enclosing_function(tree)
        if func not in owner.values():
            stale.append(f"{filename}::{func} ({reason[:40]}…): no such function")
            continue
        lines = text.splitlines()
        flagged = [
            lineno for lineno, line in enumerate(lines, start=1)
            if owner.get(lineno) == func
            and (_RAW_HOUR.search(line) or _CONCAT_HOUR.search(line))
            and not _is_approved(line)
        ]
        if not flagged:
            stale.append(
                f"{filename}::{func} ({reason[:40]}…): formats no raw hour any more")
    assert not stale, (
        "exemptions that no longer describe a real site:\n  " + "\n  ".join(stale))
