"""A rescaled time axis must never pass as one drawn on real hours.

The rescale is deliberately label-only, so while it is on the numbers on a time
axis disagree with the timepoint menus, the Statistics tab and every exported
CSV. The axis label is the only place on the figure that explains why — which
makes it load-bearing rather than decorative, and makes "someone adds a fourth
time axis and forgets the marker" a real failure mode. The source guard at the
bottom is what closes that.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from well_viewer.domain.time_rescale import axis_label  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent / "well_viewer"

#: The base text of every time axis in the app. A new one belongs in this list
#: *and* wrapped in ``axis_label`` — the guard below fails either way round.
TIME_AXIS_LABELS = ("Time (hours)", "Timepoint (h)")


# ── the marker itself ───────────────────────────────────────────────────────

@pytest.mark.parametrize("base", TIME_AXIS_LABELS)
def test_every_time_axis_label_gains_the_marker(base):
    marked = axis_label(base, 4)
    assert marked != base
    assert "4" in marked


@pytest.mark.parametrize("base", TIME_AXIS_LABELS)
def test_the_off_state_is_byte_identical(base):
    assert axis_label(base, 1) == base


# ── the Bar plot's marker, which is an x-label it otherwise has none of ────

def test_bar_panels_gain_a_marker_label_only_while_rescaled():
    from well_viewer.core.plot_style import mark_time_rescale

    fig, (ax_mean, ax_frac) = plt.subplots(2, 1)
    try:
        mark_time_rescale(ax_mean, ax_frac, factor=1)
        assert ax_frac.get_xlabel() == ""

        mark_time_rescale(ax_mean, ax_frac, factor=4)
        assert "4" in ax_frac.get_xlabel()
        # Only the bottom panel: three stacked copies of the same sentence is
        # noise, and the panels share one x-axis reading.
        assert ax_mean.get_xlabel() == ""
    finally:
        plt.close(fig)


def test_bar_marker_lands_on_the_n_panel_when_there_is_one():
    from well_viewer.core.plot_style import mark_time_rescale

    fig, (ax_mean, ax_frac, ax_n) = plt.subplots(3, 1)
    try:
        mark_time_rescale(ax_mean, ax_frac, ax_n, factor=4)
        assert "4" in ax_n.get_xlabel()
        assert ax_frac.get_xlabel() == ""
    finally:
        plt.close(fig)


def test_bar_marker_tolerates_a_missing_n_panel():
    from well_viewer.core.plot_style import mark_time_rescale

    fig, (ax_mean, ax_frac) = plt.subplots(2, 1)
    try:
        mark_time_rescale(ax_mean, ax_frac, None, factor=4)
        assert "4" in ax_frac.get_xlabel()
    finally:
        plt.close(fig)


# ── the guard: no time axis reaches a figure unmarked ──────────────────────

def _source_files():
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _code_lines_mentioning(path: Path, needle: str):
    """Lines containing *needle* as a string literal, comments excluded.

    Comments are dropped by parsing the file and keeping only the lines that
    hold a matching ``ast.Constant`` — a docstring or ``# … Time (hours) …``
    note is prose, not an axis.
    """
    text = path.read_text()
    if needle not in text:
        return []
    tree = ast.parse(text, filename=str(path))
    literal_lines = {
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and needle in node.value
    }
    lines = text.splitlines()
    return [(n, lines[n - 1]) for n in sorted(literal_lines)]


@pytest.mark.parametrize("base", TIME_AXIS_LABELS)
def test_no_time_axis_label_bypasses_the_marker(base):
    """Every place that names a time axis wraps it in ``axis_label``.

    Without this, adding a fourth time axis (or a second batch-export figure)
    silently produces a rescaled axis that reads like real hours — the exact
    substitution this feature is built to avoid. Two such sites already existed
    when the feature landed: the batch-export line figure duplicated the Line
    tab's label verbatim, and the Spatial summary set its own.
    """
    offenders = []
    for path in _source_files():
        if path.name == "time_rescale.py":
            continue                      # the module that defines the marker
        for lineno, line in _code_lines_mentioning(path, base):
            if "axis_label" in line:
                continue
            offenders.append(f"{path.relative_to(ROOT.parent)}:{lineno}: {line.strip()}")
    assert not offenders, (
        f"time axis label {base!r} used without domain.time_rescale.axis_label — "
        "a rescaled axis would read as real hours:\n  " + "\n  ".join(offenders)
    )


def test_the_guard_can_actually_see_a_bypass(tmp_path):
    """The guard is a source scan, so prove it fails on the thing it forbids."""
    bad = tmp_path / "fake_tab.py"
    bad.write_text('ax.set_xlabel("Time (hours)", fontsize=8)\n')
    found = _code_lines_mentioning(bad, "Time (hours)")
    assert found and "axis_label" not in found[0][1]


def test_the_guard_ignores_prose(tmp_path):
    ok = tmp_path / "fake_doc.py"
    ok.write_text('# the "Time (hours)" x-labels are set below\nx = 1\n')
    assert _code_lines_mentioning(ok, "Time (hours)") == []


def test_the_marker_text_names_the_factor_that_was_used():
    """A marker naming a different number than the axis printed would be worse
    than no marker at all."""
    for factor in (2, 4, 0.5, 60):
        marked = axis_label("Time (hours)", factor)
        assert re.search(rf"×\s*{factor:g}\b", marked), marked
