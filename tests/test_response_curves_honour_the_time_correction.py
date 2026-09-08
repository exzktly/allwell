"""Every hour the Response Curves figure prints is the corrected one.

Properties → **Time** states ``filename hours × interval + offset``. The Heat
Map, the Distribution tab and the batch-export scatter already name the
corrected hour in their titles, because none of them has a time axis to hang
the marker on. Response Curves is the same shape and was the last holdout: its
x is dose, its timepoints are the *series*, and it printed the filename's raw
number in four places at once.

That made it the worst of the group rather than merely the last. A tab whose
title, legend, no-fit caption and omission caption all name hours cannot be
half-corrected: a title reading ``t=[24, 48] h`` over a legend reading ``t=6``
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


from well_viewer.domain.time_rescale import TimeRescale, figure_timepoints_label

RESPONSE = Path(__file__).resolve().parent.parent / "well_viewer" / "features" / "response"

OFF = TimeRescale(factor=1.0, offset=0.0)
#: Six images four hours apart, numbered 1-6 by the microscope.
X4 = TimeRescale(factor=4.0, offset=0.0)


# ── the helper the titles go through ────────────────────────────────────────

def test_a_single_timepoint_reads_singular():
    assert figure_timepoints_label([6.0], OFF) == "t=6 h"


def test_several_timepoints_read_as_a_list():
    assert figure_timepoints_label([6.0, 12.0], OFF) == "t=[6, 12] h"


def test_the_interval_corrects_every_number_in_the_list():
    assert figure_timepoints_label([6.0, 12.0], X4).startswith("t=[24, 48] h")


def test_the_offset_composes_after_the_interval():
    """``filename × interval + offset``, in that order — 6 × 4 − 2 = 22."""
    rescale = TimeRescale(factor=4.0, offset=-2.0)
    assert figure_timepoints_label([6.0], rescale).startswith("t=22 h")


def test_the_marker_is_stated_once_however_many_timepoints():
    """Three numbers, one correction. A marker per value would read as three."""
    label = figure_timepoints_label([6.0, 12.0, 18.0], X4)
    assert label == "t=[24, 48, 72] h, filename value × 4"
    assert label.count("filename value") == 1


def test_an_uncorrected_label_carries_no_marker():
    assert figure_timepoints_label([6.0, 12.0], OFF) == "t=[6, 12] h"


def test_no_timepoints_names_nothing():
    """Rather than ``t=[] h``, which reads as a figure whose numbers went
    missing instead of one with no timepoint to name."""
    assert figure_timepoints_label([], X4) == ""


def test_the_off_state_matches_what_the_sites_used_to_print():
    """The conversion has to be invisible until a correction is set.

    Every site swapped here wrote ``f"{t:g}"``. If the helper formatted even
    one ordinary value differently, this change would silently reword every
    Response figure in the app for users who never touch the Time controls.
    """
    for value in (0.0, 1.0, 1.5, 6.0, 24.0, 100.0, 0.25):
        assert figure_timepoints_label([value], OFF) == f"t={value:g} h"


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
    from well_viewer.features.response import renderer as R

    data = _curves_data([6.0, 12.0])
    labels = [R._curve_label(data, c, rescale=X4) for c in data.curves]
    assert labels == ["t=24", "t=48"], labels


def test_the_legend_entry_carries_no_marker_of_its_own():
    """The correction is named on the title. A legend of four curves would
    otherwise carry four copies of the same sentence."""
    from well_viewer.features.response import renderer as R

    data = _curves_data([6.0, 12.0])
    for c in data.curves:
        assert "filename value" not in R._curve_label(data, c, rescale=X4)


def test_the_no_fit_caption_names_the_hour_the_legend_shows():
    """This sentence identifies a curve *by* its legend entry, so a raw hour
    here points at an entry the figure does not carry."""
    from well_viewer.features.response import renderer as R

    data = _curves_data([6.0])
    fits = {(6.0, ""): SimpleNamespace(ok=False, reason="too few points")}
    caption = R._fit_caption(fits, data, rescale=X4)
    assert "No fit at t=24: too few points" == caption, caption


def test_the_omission_caption_names_the_corrected_hour():
    from well_viewer.features.response.collect import ResponseData, omission_caption

    data = ResponseData(variable_name="Dose", control_gaps=[("A01", 6.0)])
    assert "A01 t=24" in omission_caption(data, rescale=X4)


def test_the_omission_caption_is_unchanged_with_no_correction():
    from well_viewer.features.response.collect import ResponseData, omission_caption

    data = ResponseData(variable_name="Dose", control_gaps=[("A01", 6.0)])
    assert omission_caption(data, rescale=OFF) == omission_caption(data)
    assert "A01 t=6" in omission_caption(data)


def test_the_title_and_the_legend_agree_on_one_figure():
    """The defect a half-sweep would produce, asserted directly.

    A title naming 24 and 48 over a legend naming 6 and 12 is worse than the
    original: the two disagree *within one figure*, where a reader cannot even
    appeal to another tab to work out which is meant.
    """
    from well_viewer.features.response import renderer as R

    data = _curves_data([6.0, 12.0])
    title = figure_timepoints_label([6.0, 12.0], X4)
    legend = [R._curve_label(data, c, rescale=X4) for c in data.curves]
    for entry in legend:
        number = entry.split("=")[-1]
        assert number in title, (title, legend)


# ── the guard: no fifth site arrives uncorrected ────────────────────────────

#: Sites that print an hour somewhere the *correction does not apply* — the
#: menus and the exported CSVs, which keep the hours the pipeline parsed out of
#: the filenames. That is the app-wide rule, not an exemption invented here:
#: nothing another part of All-Well reads is touched by the Time controls.
_KEEPS_FILENAME_HOURS = {
    "controller.py": {
        462: "the Timepoint picker's own entries — a menu, not a figure",
        700: "an exported CSV row",
        742: "an exported CSV row",
    },
    "collect.py": {},
    "renderer.py": {},
}

#: Raw hour formatting: an ``f"…{value:g}…"`` next to a ``t=``, or the bare
#: ``{x:g}`` used to build one.
_RAW_HOUR = re.compile(r"t=\{[^}]*:g\}")


def _python_files():
    for path in sorted(RESPONSE.rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path


def test_no_response_site_formats_an_hour_outside_the_helpers():
    """A fifth surface added with ``f"t={t:g}"`` fails here.

    The four sites this file fixes were found by grepping *after* the first two
    were already chosen, which is exactly how a half-sweep happens — so the
    protection is mechanical rather than a promise to remember.
    """
    offenders = []
    for path in _python_files():
        text = path.read_text()
        tree = ast.parse(text, filename=str(path))
        allowed = _KEEPS_FILENAME_HOURS.get(path.name, {})
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            rendered = ast.unparse(node)
            if not _RAW_HOUR.search(rendered):
                continue
            if node.lineno in allowed:
                continue
            offenders.append(f"{path.name}:{node.lineno}: {rendered}")
    assert not offenders, (
        "an hour formatted raw on a Response figure — route it through "
        "renderer._scaled_tp or time_rescale.figure_timepoints_label, or add "
        "it to _KEEPS_FILENAME_HOURS with the reason:\n  " + "\n  ".join(offenders)
    )


def test_the_exemption_roster_still_describes_real_sites():
    """An allowlist entry that stopped matching anything is worse than none:
    it reads as a considered exemption while guarding nothing."""
    stale = []
    for name, rows in _KEEPS_FILENAME_HOURS.items():
        if not rows:
            continue
        text = (RESPONSE / name).read_text()
        lines = text.splitlines()
        for lineno, reason in rows.items():
            line = lines[lineno - 1] if lineno <= len(lines) else ""
            if ":g}" not in line:
                stale.append(f"{name}:{lineno} ({reason}) -> {line.strip()!r}")
    assert not stale, "exemptions naming a line that no longer formats an hour:\n  " + "\n  ".join(stale)
