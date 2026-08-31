"""Analyze's form must actually fit inside RAIL_W (well_viewer/core/app_shell_view.py).

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
its un-realized ``QSize(0, 0)`` before a layout pass — fits inside RAIL_W.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    import theme
    app.setStyleSheet(theme.qss())
    return app


def _expand_every_collapsible_section(tab) -> None:
    carets = [l for l in tab.findChildren(QLabel)
              if l.objectName() == "SectionTitle" and l.text() == "▸"]
    assert carets, "no collapsed sections found — did the section markup change?"
    for caret in carets:
        QTest.mouseClick(caret.parentWidget(), Qt.LeftButton)


def test_analyze_form_fits_rail_w_with_every_section_expanded(qapp):
    from analyze.tab import AnalyzeTab
    from well_viewer.core.app_shell_view import RAIL_W

    # Locks the specific target (438 shrunk 25%), not just "whatever RAIL_W
    # currently is" — a bare `width <= RAIL_W` check would pass trivially
    # against the pre-fix 438px width too, since that value was originally
    # sized to fit the pre-fix (wider) rows.
    assert RAIL_W == 329

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
    assert width <= RAIL_W, (
        f"Analyze form's minimum width ({width}) exceeds the shared sidebar "
        f"width RAIL_W ({RAIL_W}) once every collapsible section is expanded "
        "— some row doesn't fit."
    )


def test_segmentation_and_auto_threshold_method_combos_fit_and_elide(qapp):
    """The two widest fixed-width combos in the form must be narrower than
    RAIL_W's row budget, and still carry their full text on the tooltip since
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


def test_run_controls_fit_the_rail_with_the_queue_button_shown(qapp):
    """The run row has to fit RAIL_W *with* "+ Queue" on it.

    Regression: the button is hidden until the Datasets pane injects a queue
    sink, so every existing check measured the row without it and passed. With
    it shown the four buttons want 398px against a fixed 329px panel, and a
    QHBoxLayout cannot wrap — it just compresses them until the labels are
    unreadable. The row is a FlowLayout for that reason, and FlowLayout watches
    its children for show/hide so the row re-wraps when the button appears.
    """
    from analyze.tab import AnalyzeTab
    from well_viewer.core.app_shell_view import RAIL_W

    tab = AnalyzeTab()
    tab.resize(1640, 980)
    tab.show()
    try:
        tab.set_queue_sink(lambda spec: None)
        qapp.processEvents()
        assert tab._queue_btn.isVisibleTo(tab), "the sink should reveal the button"

        row = tab._run_btn.parentWidget()
        assert row.minimumSizeHint().width() <= RAIL_W, (
            f"run row needs {row.minimumSizeHint().width()}px, rail is {RAIL_W}px"
        )
        # And it fits by wrapping, not by squashing: at the rail's width the
        # row is taller than a single button.
        assert row.heightForWidth(RAIL_W) > tab._run_btn.sizeHint().height()
    finally:
        tab.close()
