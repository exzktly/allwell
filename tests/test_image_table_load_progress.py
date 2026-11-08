"""The Image Table's Generate progress bar (``image_table/_load_progress.py``).

* The helper, on a fake clock: hidden until a Generate has run for ``SHOW_AFTER_S``, then naming the pass and the
  count, repainting at most every ``PUMP_EVERY_S``, and hidden again on ``finish``.
* The decode (``_prefetch.py``) tells it how many images there are and ticks once per completed read.
* Through the real tab (``image_table_app``, slow lane): a Generate shows the bar on the tab's own widgets, reaches
  "Drawing images… M of M" for an M-cell table, and leaves it hidden.
"""

from __future__ import annotations

import types

import pytest

from review.features.image_browser.image_table import _load_progress as LP


class _Widget:
    def __init__(self) -> None:
        self.visible = False
        self.value = None
        self.range = None
        self.text = ""

    def setVisible(self, on):
        self.visible = bool(on)

    def setValue(self, v):
        self.value = v

    def setRange(self, lo, hi):
        self.range = (lo, hi)

    def setText(self, t):
        self.text = t


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _progress():
    bar, label, clock, pumps = _Widget(), _Widget(), _Clock(), []
    p = LP.LoadProgress(bar, label, clock=clock, pump=lambda: pumps.append(clock.now))
    return p, bar, label, clock, pumps


def test_a_quick_generate_never_shows_the_bar():
    p, bar, label, clock, pumps = _progress()
    p.phase("Drawing images", 4)
    for _ in range(4):
        clock.now += 0.05
        p.tick()
    p.finish()
    assert not bar.visible and not label.visible and pumps == []


def test_a_slow_generate_shows_the_pass_and_the_count_then_hides():
    p, bar, label, clock, pumps = _progress()
    p.phase("Loading images", 10)
    clock.now += LP.SHOW_AFTER_S + 0.01
    p.tick()
    assert bar.visible and label.visible
    assert (bar.range, bar.value) == ((0, 10), 1)
    assert label.text == "Loading images… 1 of 10"
    clock.now += 1.0
    for _ in range(9):
        p.tick()
    assert label.text == "Loading images… 10 of 10", "the last tick always repaints"
    p.finish()
    assert not bar.visible and not label.visible


def test_repaints_are_throttled_while_the_bar_is_up():
    p, bar, label, clock, pumps = _progress()
    p.phase("Drawing images", 1000)
    clock.now += LP.SHOW_AFTER_S + 0.01
    for _ in range(100):
        clock.now += 0.001                                # 100 ticks inside 0.1 s
        p.tick()
    assert 1 < len(pumps) <= 4, pumps


def test_begin_finds_the_tab_s_widgets_and_hides_a_bar_left_up():
    bar, label = _Widget(), _Widget()
    bar.visible = label.visible = True                    # a previous Generate raised with its bar showing
    reg = {"_image_table_load_bar": bar, "_image_table_load_label": label}
    ctx = types.SimpleNamespace(widgets=types.SimpleNamespace(get_or_none=reg.get))
    LP.begin(ctx)
    assert not bar.visible and not label.visible


def test_the_decode_reports_one_tick_per_image_read(monkeypatch):
    from review.features.image_browser.image_table import _prefetch as PF

    reads = []
    monkeypatch.setattr(PF, "_find_ratio_for_channel", lambda ctx, chan: None)
    monkeypatch.setattr(PF, "_row_display_opts", lambda ctx, r, chan: (False,))
    monkeypatch.setattr(PF, "_is_nuc_seg", lambda chan: False)
    monkeypatch.setattr(PF, "_load_array",
                        lambda ctx, cache, well, chan, tp, fov, use_tophat=False: reads.append(well) or object())
    ctx = types.SimpleNamespace(state=types.SimpleNamespace(image_table=types.SimpleNamespace(prefetched_cells=set())))
    cells = [[{"well": w, "chan": "GFP", "tp": "0h", "fov": "1"} for w in ("A01", "A02", "A03")],
             [{"well": "A01", "chan": "GFP", "tp": "0h", "fov": "1"}] * 3]   # the second row repeats the first well
    seen = []
    spy = types.SimpleNamespace(phase=lambda text, total: seen.append((text, total)),
                                tick=lambda: seen.append("tick"))
    PF._prefetch_cell_arrays(ctx, cells, {}, spy)
    assert seen == [("Loading images", 3), "tick", "tick", "tick"]
    assert sorted(reads) == ["A01", "A02", "A03"]


@pytest.mark.qt
def test_a_generate_drives_the_tab_s_bar_and_leaves_it_hidden(image_table_app, monkeypatch):
    """The real Generate on the real tab, with the show delay at zero so the bar comes up at once."""
    from review.features.image_browser.image_table.controller import image_table_generate

    ctx = image_table_app._ctx
    monkeypatch.setattr(LP, "SHOW_AFTER_S", 0.0)
    label = ctx.widgets.get("_image_table_load_label")
    bar = ctx.widgets.get("_image_table_load_bar")
    texts = []
    real_set_text = label.setText
    monkeypatch.setattr(label, "setText", lambda t: (texts.append(t), real_set_text(t)))
    ctx.state.image_table.prefetched_cells = set()
    ctx.state.image_table.image_cache = {}
    image_table_generate(ctx)
    n = sum(len(row) for row in ctx.runtime.image_table.cells)
    assert n > 1
    assert texts and texts[-1] == f"Drawing images… {n} of {n}"
    assert bar.isHidden() and label.isHidden()
