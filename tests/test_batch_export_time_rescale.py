"""Batch-export output under the time-rescale factor.

A batch run writes several files at once, so the failure that matters here is
*internal disagreement*: one figure naming a timepoint 4 while its neighbour from
the same run names it 1. Both halves of the contract are pinned —

* every batch **figure** follows the factor and says that it did;
* every batch **CSV column and filename** keeps the hours the pipeline read out
  of the filenames, so a downstream script sees the same numbers whatever the
  Properties sidebar is set to.

The panels are Qt widgets that need a built app, so the behavioural tests call
the shipped methods against a stub ``self`` and the structural ones read the
source. That is deliberate: the gap these were written for — a figure builder
that scales its labels and forgets the marker — is a *structural* omission, and
a source guard is what catches the next one.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests._repo_scan import parse_source

BATCH = Path(__file__).resolve().parent.parent / "review" / "features" / "batch_export"

#: Anything that puts the rescale marker on a figure.
MARKERS = ("mark_time_rescale", "figure_timepoint_label", "axis_label",
           "_tp_figure_label", "_time_axis_label")

#: The scaling helpers. A figure builder that calls one of these has moved its
#: numbers and owes the reader a marker.
SCALERS = ("format_scaled", "_rescale_tp", "apply_time_rescale",
           "_tp_figure_label", "figure_timepoint_label")


def _fn(path: Path, name: str) -> ast.FunctionDef:
    tree = parse_source((BATCH / path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path}::{name} not found — did it get renamed?")


def _scaled_timepoint_columns(source: str) -> list[str]:
    """Lines in *source* that write ``timepoint_h`` from a rescaled value.

    The check both the real guard and its self-test run, so the self-test
    exercises the mechanism rather than restating the substrings.
    """
    return [ln.strip() for ln in source.splitlines()
            if "timepoint_h" in ln and any(s in ln for s in SCALERS)]


def _names_in(node: ast.AST) -> set[str]:
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


# ── the scatter title, which is the only place its timepoint appears ────────

def _panel(factor):
    """A stub ``self`` carrying just the ctx the method reads."""
    return SimpleNamespace(_ctx=SimpleNamespace(
        state=SimpleNamespace(plot=SimpleNamespace(time_scale=factor))))


def _tp_label(factor, tp_h):
    from review.features.batch_export.scatter_panel import ScatterBatchExportPanel
    return ScatterBatchExportPanel._tp_figure_label(_panel(factor), tp_h)


def test_scatter_title_timepoint_is_unchanged_when_the_factor_is_off():
    assert _tp_label(1, 1.0) == "t = 1 h"


def test_scatter_title_timepoint_follows_the_factor():
    """A scatter figure has no time axis — x and y are both metrics — so before
    this a batch run wrote a bar figure titled ``t = 4 h`` beside a scatter
    figure titled ``t = 1.0h`` for the same data."""
    assert _tp_label(4, 1.0).startswith("t = 4 h")


def test_scatter_title_says_it_was_rescaled():
    assert "× 4" in _tp_label(4, 1.0)


def test_scatter_title_composes_into_the_shipped_title_text():
    label = _tp_label(4, 2.0)
    assert f"Ctrl — Scatter Cells ({label})" == (
        "Ctrl — Scatter Cells (t = 8 h, filename value × 4)")


# ── every batch figure builder marks itself ────────────────────────────────

@pytest.mark.parametrize("path,name", [
    ("bar_panel.py", "_render_bar_group_figure"),
    # The violin / beeswarm modes never reach the builder above: it returns into
    # this one and stops. Its absence from this list is how it came to print raw
    # selection keys as tick labels under a rescaled suptitle, with the marker
    # underneath both — a list of figure builders is only a guard if it names
    # every builder, and an early return makes one builder into two.
    ("bar_panel.py", "_render_per_cell_panels"),
    ("scatter_panel.py", "_run_scatter_cells_job"),
    ("scatter_panel.py", "_run_scatter_agg_job"),
])
def test_a_batch_figure_builder_that_scales_also_marks(path, name):
    """The gap this closes, found after the feature shipped.

    ``_render_bar_group_figure`` builds its own ``Figure`` rather than going
    through the on-tab renderer, so it inherited the rescaled tick labels (they
    come from the collector) but not the marker the renderer adds — a saved batch
    PNG read ``t = 4``, ``t = 8`` with nothing on it saying those were not the hours
    the run recorded.
    """
    names = _names_in(_fn(Path(path), name))
    assert names & set(MARKERS), (
        f"{path}::{name} builds a figure whose timepoints follow the rescale "
        f"factor but calls none of {MARKERS} — a saved figure would read as real "
        "hours"
    )


def test_the_line_batch_figure_marks_its_axis():
    """The batch line figure duplicates the on-tab Line tab's axis label, so it
    needed the same treatment; ``test_time_rescale_marker`` guards the label text
    itself, this pins the formatter alongside it."""
    names = _names_in(_fn(Path("base_panel.py"), "_render_group_figure"))
    assert "apply_time_rescale" in names
    assert names & set(MARKERS)


# ── and every exported number does not ─────────────────────────────────────

@pytest.mark.parametrize("path,name", [
    ("scatter_panel.py", "_run_scatter_cells_job"),
    ("scatter_panel.py", "_run_scatter_agg_job"),
    ("scatter_panel.py", "_run_scatter_cells_multi_tp_job"),
    ("scatter_panel.py", "_run_scatter_agg_multi_tp_job"),
])
def test_scatter_csv_rows_are_written_from_unscaled_hours(path, name):
    """``timepoint_h`` holds what the pipeline measured.

    The rescale is a label pass; a CSV that moved with it would be
    indistinguishable from one recorded at those hours, and every downstream
    script reading the column would silently change answer with a sidebar
    setting.
    """
    src = ast.get_source_segment((BATCH / path).read_text(), _fn(Path(path), name)) or ""
    assert "timepoint_h" in src, f"{path}::{name} no longer writes a timepoint column"
    offenders = _scaled_timepoint_columns(src)
    assert not offenders, (
        f"{path}::{name} writes the exported timepoint column from a rescaled "
        f"value — it must stay in the hours read from the filenames:\n  "
        + "\n  ".join(offenders)
    )


def test_export_filenames_are_built_from_unscaled_hours():
    """Filenames are what downstream scripts glob on, and what the user matches
    against the timepoint list they picked from — which is also unscaled."""
    src = (BATCH / "scatter_panel.py").read_text()
    stem_lines = [ln for ln in src.splitlines()
                  if "safe_tp" in ln and "=" in ln]
    assert stem_lines, "the scatter export stem no longer builds a safe_tp"
    for line in stem_lines:
        assert not any(s in line for s in SCALERS), line.strip()


def test_the_export_column_guard_can_actually_see_a_bypass():
    """The guard is a source scan, so run it against the thing it forbids."""
    assert _scaled_timepoint_columns(
        'row = {"timepoint_h": format_scaled(tp_h, factor)}')


def test_the_export_column_guard_passes_the_shipped_form():
    assert _scaled_timepoint_columns('row = {"timepoint_h": f"{tp_h:.4f}"}') == []
