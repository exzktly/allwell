"""The Nanowells page shows a progress bar while it reads the nanowell tables.

Asked for: "When nanowells load there's a page that says 'reading the nanowell tables'. This can just sit
unchanged for a long time when loading a lot. Give it a progress bar."

The load counts its wells as it goes — ``collect.load_nanowell_data(on_well=...)`` for the tables,
``controller.gate_tally(on_well=...)`` for the Cell Gating pass over their cells — and the worker hands each
count to the GUI thread (``core.worker_bridge``), where the page's placeholder (``widgets.empty_state``) draws it.
The page test asserts at that boundary: what the page's own placeholder was told to draw, through a real load.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.test_nanowell_page import WELLS, _loaded, _page, _write_nanowell_dataset  # noqa: E402


# ── the loader counts the wells it reads ─────────────────────────────────────
def test_the_loader_counts_each_well_it_reads_and_only_those(tmp_path):
    from review.features.nanowell.collect import load_nanowell_data

    out = _write_nanowell_dataset(tmp_path)
    counts: list[tuple[int, int]] = []
    parts: dict = {}
    load_nanowell_data(out, wells=["A01"], parts=parts, on_well=lambda d, t: counts.append((d, t)))
    assert counts == [(0, 1), (1, 1)]
    counts.clear()
    # A01 is already read: only the two wells read now are counted, so the bar measures the work left.
    load_nanowell_data(out, wells=list(WELLS), parts=parts, on_well=lambda d, t: counts.append((d, t)))
    assert counts == [(0, 2), (1, 2), (2, 2)]
    counts.clear()
    load_nanowell_data(out, wells=list(WELLS), parts=parts, on_well=lambda d, t: counts.append((d, t)))
    assert counts == []                                                # nothing read, nothing counted


# ── the placeholder draws a bar ──────────────────────────────────────────────
def test_the_placeholder_draws_the_count_it_is_given_and_hides_the_bar_without_one(qtbot):
    from widgets.empty_state import EmptyState

    es = EmptyState("Reading the nanowell tables…", hint="Reading the tables: 3 of 40 wells.")
    qtbot.addWidget(es)
    assert es.progress() is None and es._progress_row.isHidden()      # absent unless asked for
    es.setProgress((3, 40))
    assert es.progress() == (3, 40)
    assert (es._progress.minimum(), es._progress.maximum(), es._progress.value()) == (0, 40, 3)
    es.setProgress((0, 0))                                             # running, not counted yet: no known end
    assert (es._progress.minimum(), es._progress.maximum()) == (0, 0) and es.progress() == (0, 0)
    es.setProgress(None)
    assert es.progress() is None and es._progress_row.isHidden()


def test_a_hidden_bar_takes_no_room(qtbot):
    from widgets.empty_state import EmptyState

    plain = EmptyState("Title", hint="Hint")
    barred = EmptyState("Title", hint="Hint")
    for es in (plain, barred):
        qtbot.addWidget(es)
    barred.setProgress((1, 2))
    barred.setProgress(None)
    assert plain.sizeHint() == barred.sizeHint()
    barred.setProgress((1, 2))
    assert barred.sizeHint().height() > plain.sizeHint().height()


def test_the_shared_refresher_hands_a_reason_s_progress_to_the_placeholder_and_clears_it():
    from review.core.ui_helpers import EmptyReason, _apply_reason

    told = []

    class Placeholder:
        def setText(self, _t): pass
        def setHint(self, _h): pass
        def setBusy(self, _b): pass
        def setIconName(self, _i): pass
        def setProgress(self, p): told.append(p)

    class Stack:
        _empty_placeholder = Placeholder()
        def currentIndex(self): return 0

    _apply_reason(Stack(), EmptyReason("Reading", "2 of 5", busy=True, progress=(2, 5)))
    _apply_reason(Stack(), EmptyReason("No wells selected."))
    assert told == [(2, 5), None]


# ── through the page ─────────────────────────────────────────────────────────
def _nanowell_placeholder(ctx):
    from review.features.nanowell import controller as nwc

    stacks = [s for s in ctx.window._plot_empty_stacks if s._empty_reason is nwc.nanowell_empty_reason]
    assert len(stacks) == 1
    return stacks[0]._empty_placeholder


def test_the_page_s_placeholder_counts_the_wells_as_a_load_reads_them(qtbot, tmp_path, monkeypatch):
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    placeholder = _nanowell_placeholder(ctx)
    drawn: list[tuple[str, object]] = []
    real = placeholder.setProgress

    def spy(progress):
        drawn.append((placeholder._hint.text(), progress))
        real(progress)

    monkeypatch.setattr(placeholder, "setProgress", spy)
    ctx.state.selection.selected_wells = set(WELLS)
    st = _loaded(qtbot, ctx)
    assert st.data.wells == list(WELLS) and st.load_progress is None   # done: the count is cleared
    tables = [p for hint, p in drawn if hint.startswith("Reading the tables")]
    gating = [p for hint, p in drawn if hint.startswith("Counting the gated cells")]
    # Every well read moves the bar, in order; then the Cell Gating pass counts them again.
    assert [p for i, p in enumerate(tables) if i == 0 or p != tables[i - 1]] == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert gating and gating[-1] == (3, 3)
    assert ("Reading the tables: 2 of 3 wells.", (2, 3)) in drawn     # the words say what the bar shows


def test_the_reading_reason_carries_the_count_and_has_no_end_before_one(qtbot, tmp_path):
    from review.features.nanowell import controller as nwc

    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    st = ctx.state.feature_slice("nanowell")
    st.computing, st.load_progress = True, ("tables", 1, 4)
    reason = nwc._reading_reason(st)
    assert (reason.title, reason.hint, reason.progress, reason.busy) == (
        "Reading the nanowell tables…", "Reading the tables: 1 of 4 wells.", (1, 4), True)
    st.load_progress = None
    assert nwc._reading_reason(st).progress == (0, 0)                 # running, not counted yet
