"""Analyze's form must actually fit inside theme.Metrics.form_column_w (the design language's Analyze form column, ui/theme.py).

Regression: unifying Review/Datasets/Analyze's left-panel width let Analyze's
widest content (its per-channel rows, several label+field rows, and two wide
method combos) set the shared width for all three panes at 438px — narrower
than that, several rows genuinely overflowed:

- ``_row()``'s 150px label column left too little room beside a field once the
  panel narrowed, and the label itself doesn't word-wrap, so long labels like
  "Background subtraction" or "Positive control well" would clip.
- The segmentation-method and auto-threshold-method combos (230px / 220px
  fixed) are wider than a narrow sidebar has room for.
- The Compute Options page's "(0 → auto-select 4)" / "(process count
  override)" hints sat inline after their entry field and got clipped instead
  of wrapping.
- A collapsed-by-default section's content (Auto-threshold, Shape Metrics,
  Output Options, Compute Options, Scratch Disk) contributes nothing to the
  form's measured minimum width until expanded — a narrow-fit check that never
  expands them only proves the *visible* rows fit, not the whole form.

This drives every collapsible section open (real ``QTest.mouseClick``, not a
direct call, so it exercises the exact code path a user's click does) and
requires several fluorescent-channel rows (the worst case for the Channel
Tokens section), then asserts the form's real, laid-out minimum width — not
its un-realized ``QSize(0, 0)`` before a layout pass — fits inside theme.Metrics.form_column_w.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    from ui import theme
    before = app.styleSheet()
    app.setStyleSheet(theme.qss())
    yield app
    app.setStyleSheet(before)        # app-wide: left applied, it changed later tests' layout (phone view's 361 px)


def _expand_every_collapsible_section(tab) -> None:
    folded = [card for card in tab._section_cards.values() if not card.isExpanded()]
    assert folded, "no collapsed sections found — did the section markup change?"
    for card in folded:
        QTest.mouseClick(card.header(), Qt.LeftButton)     # the header a user clicks


def test_analyze_form_fits_its_column_with_every_section_expanded(qapp):
    from analyze.tab import AnalyzeTab
    from ui import theme
    # Locks the specific target (438 shrunk 25%), not just "whatever theme.Metrics.form_column_w
    # currently is" — a bare `width <= theme.Metrics.form_column_w` check would pass trivially
    # against the pre-fix 438px width too, since that value was originally
    # sized to fit the pre-fix (wider) rows.
    assert theme.Metrics.form_column_w == 430

    tab = AnalyzeTab()
    tab._fluor_add_row("RFP")
    tab._fluor_add_row("DAPI")
    tab.resize(1640, 4000)
    tab.show()
    for _ in range(10):
        qapp.processEvents()

    _expand_every_collapsible_section(tab)
    for _ in range(10):
        qapp.processEvents()

    form = tab._left_sidebar.widget()
    width = form.minimumSizeHint().width()
    assert width <= theme.Metrics.form_column_w, (
        f"Analyze form's minimum width ({width}) exceeds the shared sidebar "
        f"width theme.Metrics.form_column_w ({theme.Metrics.form_column_w}) once every collapsible section is expanded "
        "— some row doesn't fit."
    )


def test_segmentation_and_auto_threshold_method_combos_fit_and_elide(qapp):
    """The two widest fixed-width combos in the form must be narrower than
    theme.Metrics.form_column_w's row budget, and still carry their full text on the tooltip since
    Qt elides the closed box's label rather than showing it in full."""
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    seg_cb = tab._seg_method_cb
    assert seg_cb.width() <= 200

    long_idx = max(range(seg_cb.count()), key=lambda i: len(seg_cb.itemText(i)))
    seg_cb.setCurrentIndex(long_idx)
    assert seg_cb.toolTip() == seg_cb.itemText(long_idx)

    thr_cb = tab._auto_threshold_method_cb
    assert thr_cb.width() <= 200


def test_run_controls_fit_the_form_column_with_the_queue_button_shown(qapp):
    """The run row fits the foot card *with* "Add to queue" on it, the primary last.

    Regression: the button is hidden until the Datasets pane injects a queue sink, so a check that measured the
    row without it passed while the shown row overflowed. In the form column (``Metrics.form_column_w``) the two
    buttons shown at a time sit side by side, right-aligned, with "Run pipeline" at the right end.
    """
    from analyze.tab import AnalyzeTab
    from ui import theme
    tab = AnalyzeTab()
    tab.resize(1640, 980)
    tab.show()
    try:
        tab.set_queue_sink(lambda spec: None)
        qapp.processEvents()
        assert tab._queue_btn.isVisibleTo(tab), "the sink should reveal the button"

        row = tab._run_btn.parentWidget()
        assert row.minimumSizeHint().width() <= row.width() <= theme.Metrics.form_column_w, (
            f"run row needs {row.minimumSizeHint().width()}px and has {row.width()}px")
        queue, run = tab._queue_btn.geometry(), tab._run_btn.geometry()
        assert queue.top() == run.top() and queue.right() < run.left(), "not one line, primary last"
        assert run.right() >= row.width() - 1, "the row is not right-aligned"
    finally:
        tab.close()


def test_a_channel_row_and_the_run_time_detect_box_can_narrow_to_one_control(qapp):
    """Reported (phone layout): on the Analyze form many fields were cut off.
    Two rows could not get narrower than their whole content: a channel row
    (then field, ✕, smFISH, Phase in an unbreakable QHBoxLayout) and
    "Detect schema & channels at run time" (a QCheckBox reports its whole label
    as its minimum width). At phone layout's touch size those two pinned the
    form 14 px wider than the drawer on a 432 px screen, so every section was
    clipped at its right edge. Each must now narrow to its widest single
    control — the row by wrapping, the box by wrapping its label — whatever
    scale the form is drawn at."""
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    tab.resize(1640, 4000)
    tab.show()
    try:
        for _ in range(5):
            qapp.processEvents()
        # A channel row is now three controls — the name (which shrinks to
        # nothing), the Role dropdown and ✕ — so it no longer needs to wrap
        # to fit: it has to fit the form's column with its margins, at the
        # desk, which leaves the phone drawer (wider than rail_w) room too.
        from ui import theme
        edit, _smfish, _phase, remove = tab._fluor_rows[0]
        row = edit.parentWidget()
        assert row.minimumSizeHint().width() <= theme.Metrics.form_column_w - 24, (
            f"a channel row needs {row.minimumSizeHint().width()} px, more than "
            f"the form's {theme.Metrics.form_column_w - 24} px column")
        assert remove.parentWidget() is row

        detect = tab._auto_detect_cb
        one_line = detect.fontMetrics().horizontalAdvance(detect.text())
        assert detect.minimumSizeHint().width() < one_line // 2, (
            f"the run-time detect box needs {detect.minimumSizeHint().width()} px — "
            f"its whole one-line label ({one_line} px)")
    finally:
        tab.close()
