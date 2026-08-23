"""Regression guards for plot consumers and export parity (PR 3).

The rule this file exists to hold: **what a figure claims and what an export
beside it contains must be the same statement about the same cells.** Every
case below broke that in one of four ways — a readout denominated by the wrong
population, an export drawn from a different set of cells than the plot, a
label describing a normalisation that was not applied, or a shortfall reported
as a success.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── 1. a gate percentage counts against the cloud the gate was drawn over ────

def _gate_stats(scatter_data, gate, tps):
    from well_viewer.features.scatter.gating import compute_gate_stats
    return compute_gate_stats(scatter_data, gate, tps)


def test_gate_percentage_is_not_denominated_by_the_filtered_cloud():
    """With "Apply gates" on, the drawn cloud *is* the gate's contents, so a
    percentage taken against it reads 100.0% for every gate — arithmetically
    true and useless. The pre-gate arrays collect stashes are what it counts
    against."""
    gate = {"x0": 0.0, "x1": 10.0, "y0": 0.0, "y1": 10.0}
    data = {
        "A01": {
            # What is drawn: only the two cells inside the gate.
            "x": [1.0, 2.0], "y": [1.0, 2.0], "tp": [0.0, 0.0],
            # What was there before the gate filtered it: four cells.
            "x_ungated": [1.0, 2.0, 50.0, 60.0],
            "y_ungated": [1.0, 2.0, 50.0, 60.0],
            "tp_ungated": [0.0, 0.0, 0.0, 0.0],
            "color": "#123456",
        },
    }
    rows = _gate_stats(data, gate, [0.0])
    assert rows, "expected a percentage row"
    assert "50.0%" in rows[0][0]


def test_an_ungated_plot_still_counts_against_its_own_cloud():
    """The control: with no gate filter there are no ``*_ungated`` arrays and
    the drawn cloud is the population."""
    gate = {"x0": 0.0, "x1": 10.0, "y0": 0.0, "y1": 10.0}
    data = {"A01": {"x": [1.0, 2.0, 50.0, 60.0], "y": [1.0, 2.0, 50.0, 60.0],
                    "tp": [0.0] * 4, "color": "#123456"}}
    rows = _gate_stats(data, gate, [0.0])
    assert "50.0%" in rows[0][0]


# ── 2. an export beside a plot is drawn from the plot's cells ────────────────

def test_the_tab_csv_export_applies_the_tab_s_gate_filter():
    """The Scatter tab's own Export CSV button exported the whole cloud while
    the plot showed only the gated cells. Asserted at the call site, because
    the value it produces is only wrong when a gate is active and a fixture
    that builds one is a different test's job (CLAUDE.md §7.4 #1)."""
    src = (REPO_ROOT / "well_viewer" / "features" / "export" / "service.py").read_text()
    tree = ast.parse(src)
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", "") == "_scatter_collect_data"
    ]
    assert calls, "expected the per-cell scatter exporter to collect data"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "gates" in kwargs, (
            f"line {call.lineno}: the tab's CSV export collects without the "
            "gate filter the tab's plot is drawing with"
        )


# ── 3. a label describes the normalisation that was applied ──────────────────

def _fraction_title_sites():
    """(function name, whether the fraction title carries the FC suffix)."""
    src = (REPO_ROOT / "well_viewer" / "features" / "barplot" / "renderer.py").read_text()
    tree = ast.parse(src)
    out = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            target = (getattr(node.func, "attr", "")
                      or getattr(node.func, "id", ""))
            if target not in ("set_title", "apply_ax_style"):
                continue
            text = ast.unparse(node)
            if "fraction_title" not in text:
                continue
            out[fn.name] = "_fc_suffix" in text
    return out


def test_only_the_panels_that_normalise_the_fraction_say_so():
    """The per-cell display modes plot ``n_above / n_total`` off the raw rows —
    only the *values* on the mean panel are scaled — so giving their fraction
    panel the mean panel's fold-change suffix labelled a raw fraction as a fold
    change. The grouped paths do normalise it and must keep the suffix; a sweep
    that removed it everywhere would be just as wrong in the other direction.
    """
    sites = _fraction_title_sites()
    for per_cell in ("draw_violin", "draw_beeswarm"):
        assert per_cell in sites, f"{per_cell} no longer titles a fraction panel"
        assert sites[per_cell] is False, (
            f"{per_cell} labels a raw fraction as fold-change normalised")
    for grouped in ("draw_grouped_bar_mode", "redraw_bars"):
        assert grouped in sites, f"{grouped} no longer titles a fraction panel"
        assert sites[grouped] is True, (
            f"{grouped} normalises the fraction but no longer says so")


def test_the_line_fraction_panel_is_not_clamped_under_fold_change():
    """A raw fraction is bounded, so clamping is a good default; a fold change
    is a ratio or a difference, and clamping silently clips the effect the
    normalisation exists to show. The Bar tab already skipped the clamp."""
    src = (REPO_ROOT / "well_viewer" / "features" / "lineplot" / "controller.py").read_text()
    tree = ast.parse(src)
    clamps = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "set_ylim"
        and getattr(getattr(n.func, "value", None), "id", "") == "ax_frac"
    ]
    assert clamps, "expected the fraction panel to set a y-limit"
    guarded = []
    for parent in ast.walk(tree):
        if isinstance(parent, ast.If):
            for n in ast.walk(parent):
                if n in clamps:
                    guarded.append(n)
    assert len(guarded) == len(clamps), (
        "the Line tab clamps its fraction panel unconditionally, so a "
        "fold-change ratio above 1.05 leaves the panel without a trace")


# ── 4. a shortfall is not a success ──────────────────────────────────────────

@pytest.mark.parametrize(
    "path,marker",
    [
        ("well_viewer/features/batch_export/bar_panel.py", "rows_csv"),
        ("well_viewer/features/batch_export/base_panel.py", "rows_out"),
    ],
)
def test_an_empty_batch_group_reports_rather_than_saving_a_blank_figure(path, marker):
    """Both batch runners guarded the CSV write on having rows and then saved
    the figure regardless, returning success — so a group that produced nothing
    left a fully labelled empty figure in the output directory, and the run
    reported the same count as one that produced data."""
    src = (REPO_ROOT / path).read_text()
    assert f"if not {marker}:" in src, (
        f"{path} no longer refuses to export a group with no rows")


# ── 5. a redraw ends with the export-style pass ──────────────────────────────

def test_every_plot_renderer_applies_the_export_style():
    """ARCHITECTURE §5.8: the Properties sidebar's settings are re-applied by
    this pass and by nothing else, so a renderer that skips it loses them on
    its next recompute. Multivariate was the one that did."""
    roots = [
        REPO_ROOT / "well_viewer" / "features" / "multivariate" / "renderer.py",
        REPO_ROOT / "well_viewer" / "features" / "spatial" / "renderer.py",
    ]
    for path in roots:
        src = path.read_text()
        assert "apply_export_style_to_current" in src, (
            f"{path.name} redraws without re-applying the export style")


def test_the_properties_reset_repaints_the_cached_render_tabs():
    """Spatial and Multivariate render from their own cached result and are not
    in ``redraw_app``'s fan-out. Reset-to-defaults had a branch for one of
    them."""
    src = (REPO_ROOT / "well_viewer" / "features" / "export" / "sidebar_view.py").read_text()
    assert '"Spatial", "Multivariate"' in src, (
        "Reset-to-defaults repaints only one of the two cached-render tabs")


# ── 6. a count of zero is a measurement ──────────────────────────────────────

def test_a_heatmap_cell_with_no_point_is_undefined_not_zero():
    """``METRIC_COUNT`` returned 0.0 when no aggregate point matched the
    timepoint, so a well the pipeline never measured coloured as a real,
    empty one. Its two sibling metrics already returned NaN."""
    src = (REPO_ROOT / "well_viewer" / "features" / "heatmap" / "controller.py").read_text()
    tree = ast.parse(src)
    zero_returns = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Return) and isinstance(n.value, ast.IfExp)
        and "matched[0][5]" in ast.unparse(n.value)
    ]
    assert zero_returns, "the count metric no longer reads n_total"
    assert all("nan" in ast.unparse(n).lower() for n in zero_returns), (
        "an unmatched count cell is reported as a measured zero")


def test_a_measured_zero_events_count_draws_a_bar():
    src = (REPO_ROOT / "well_viewer" / "features" / "barplot" / "controller.py").read_text()
    assert "item.has_mean and item.n_above > 0" not in src, (
        "the Events panel still treats a measured zero as an uncomputed value")
    assert "_n_above_known" in src


# ── 7. a report follows the order the user curated ───────────────────────────

def test_the_saved_plots_report_follows_gallery_order():
    """The gallery shows newest first and the ▲/▼ buttons curate that order;
    the PDF walked the records as stored, i.e. exactly backwards."""
    src = (REPO_ROOT / "well_viewer" / "features" / "saved_plots" / "controller.py").read_text()
    assert "list(reversed(store.records))" in src, (
        "the report is built in storage order, the reverse of the gallery")
