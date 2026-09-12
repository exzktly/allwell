"""Every progress dashboard's legend, pinned to what it actually renders.

The five :class:`~widgets.plate_progress.PlateProgress` subclasses that show a
legend each described one — a bold heading, then one coloured dot beside one
label per state — by hand, in five copies of the same eighteen lines. The
rendering moved into the base and each subclass now supplies only its
``_legend_items()``.

These tests read the **rendered widgets**: the heading's text, and for each row
the dot's background colour off its style sheet and the label beside it. That
is the boundary a user sees (CLAUDE.md §7.4 #1), and it is the only assertion
that can tell a legend which draws from one which merely computes. They were
written against the five hand-written copies and passed there unchanged, which
is what makes them an equivalence proof for the extraction rather than a
description of whatever the new code happens to do.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from ui import theme  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _dot_color(label: QLabel) -> str | None:
    """The dot's colour, read back off the style sheet ``status_dot`` set."""
    m = re.search(r"background:\s*([^;]+);", label.styleSheet() or "")
    return m.group(1).strip() if m else None


def _legend(dash) -> tuple[str, list[tuple[str, str]]]:
    """The heading and the (colour, label) rows a dashboard's legend draws."""
    lay = dash._legend_lay
    head, rows = "", []
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        if w is None:
            continue
        if isinstance(w, QLabel):
            head = w.text()
            continue
        dots = [_dot_color(k) for k in w.findChildren(QLabel)]
        texts = [k.text() for k in w.findChildren(QLabel) if k.text()]
        rows.append((next((d for d in dots if d), ""), texts[0] if texts else ""))
    return head, rows


C = theme.Colors


def test_concatenate_legend(qapp):
    from datasets.concatenate.dashboard import ConcatDashboard

    assert _legend(ConcatDashboard()) == ("Wells", [
        (C.text_muted, "pending"),
        (C.warn, "merging"),
        (C.success, "merged"),
        (C.accent, "merged — no CSV on one side"),
    ])


def test_import_loose_legend(qapp):
    from datasets.import_loose.dashboard import ImportLooseDashboard

    assert _legend(ImportLooseDashboard()) == ("Legend", [
        (C.text_muted, "pending"),
        (C.warn, "zipping"),
        (C.success, "zipped"),
    ])


def test_merge_plates_legend(qapp):
    from datasets.merge_plates.dashboard import MergePlatesDashboard

    assert _legend(MergePlatesDashboard()) == ("Wells", [
        (C.text_muted, "pending"),
        (C.warn, "merging"),
        (C.success, "merged — dataset 1"),
        (C.accent2, "merged — dataset 2 (marked “2”)"),
    ])


def test_validate_legend(qapp):
    from datasets.validate.dashboard import ValidateDashboard

    assert _legend(ValidateDashboard()) == ("Legend", [
        (C.success, "complete"),
        (C.warn, "partial"),
        (C.danger, "no measurements"),
        (C.text_muted, "no output"),
    ])


def test_run_dashboard_legend_is_local_by_default(qapp):
    from widgets.run_dashboard import RunDashboard

    assert _legend(RunDashboard()) == ("Wells", [
        (C.text_muted, "pending"),
        (C.warn, "processing"),
        (C.accent, "processed"),
        (C.success, "complete"),
        (C.danger, "failed"),
    ])


def test_run_dashboard_legend_names_the_transfer_stages_when_remote(qapp):
    """The one legend whose rows are decided at run time rather than written
    out: a remote run gains an "uploaded" row and renames two others, so an
    extraction that hoisted the list to a class constant would quietly draw a
    local legend over a remote run."""
    from widgets.run_dashboard import RunDashboard

    dash = RunDashboard()
    dash.reset(remote=True)
    assert _legend(dash) == ("Wells", [
        (C.text_muted, "pending"),
        (C.accent2, "uploaded"),
        (C.warn, "processing"),
        (C.accent, "on server"),
        (C.success, "downloaded"),
        (C.danger, "failed"),
    ])


def test_a_rebuild_does_not_stack_a_second_copy(qapp):
    """``_build_legend`` is called again on every ``reset``. It clears the
    layout first; a version that appended would double the rows on the second
    run of a session, which is exactly the sort of thing no one notices until
    a plate has been run twice."""
    from widgets.run_dashboard import RunDashboard

    dash = RunDashboard()
    before = _legend(dash)
    dash.reset()
    assert _legend(dash) == before


def test_the_subset_dashboard_asks_for_no_legend(qapp):
    """It draws its states in the rail instead, so it opts out at construction
    and never implements the hook. The base must therefore not require one of
    every subclass — and must still fail loudly for a subclass that shows a
    legend without supplying its items."""
    from datasets.subset.dashboard import SubsetDashboard

    dash = SubsetDashboard()
    assert getattr(dash, "_legend_lay", None) is None
