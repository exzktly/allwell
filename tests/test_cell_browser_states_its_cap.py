"""The Cell Browser's count label names how many cells matched, not only how many it kept (tracker F3f).

The gallery keeps the first ``MAX_TOTAL`` matching cells in sort order and its
label used to read ``Showing 0 / 5000 cells`` whether 5,000 or 80,000 had
matched. Driven through ``apply_filters``, the function the Generate button runs,
with the collector returning more cells than the cap.
"""

from __future__ import annotations

from types import SimpleNamespace

from review.features.image_browser.cell_browser import controller
from review.features.image_browser.cell_browser.state import MAX_TOTAL, CellBrowserState


class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):  # noqa: N802 - Qt's spelling
        self.text = text


def _ctx():
    from review.core.app_runtime import AppRuntime

    runtime = AppRuntime(None, store={})
    runtime.cell_browser.state = CellBrowserState()
    runtime.cell_browser.count_label = _Label()
    return SimpleNamespace(runtime=runtime, notify=SimpleNamespace(set_status=lambda *_: None))


def _run(monkeypatch, n):
    ctx = _ctx()
    monkeypatch.setattr(controller, "collect_cells", lambda *a, **k: list(range(n)))
    for name in ("read_criteria", "read_timepoints", "read_fovs", "read_sort_keys"):
        monkeypatch.setattr(controller, name, lambda _ctx: [])
    monkeypatch.setattr(controller, "read_channels", lambda _ctx: [])
    controller.apply_filters(ctx)
    return ctx.runtime.cell_browser


def test_a_capped_gallery_names_how_many_matched(monkeypatch):
    cb = _run(monkeypatch, MAX_TOTAL + 1234)
    assert len(cb.state.cells) == MAX_TOTAL and cb.state.n_matched == MAX_TOTAL + 1234
    assert cb.count_label.text == (f"Showing 0 / {MAX_TOTAL} cells (the first {MAX_TOTAL:,} of "
                                   f"{MAX_TOTAL + 1234:,} matching, in sort order)")


def test_an_uncapped_gallery_reads_as_before(monkeypatch):
    cb = _run(monkeypatch, 40)
    assert cb.count_label.text == "Showing 0 / 40 cells"
