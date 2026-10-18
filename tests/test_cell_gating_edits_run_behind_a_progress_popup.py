"""Every change made on the Cell Gating panel runs behind a progress popup.

The request: *all changes made in the cell gating tab go through a popup
progress bar that does not hang the GUI*. Each test here builds the shipped tab
(``build_cell_gating_tab``) on a real context with a real dataset, presses one
control the way a user does, and asserts three things at the boundary:

* a :class:`widgets.progress_popup.ProgressPopup` was **shown** for it, with the
  title that names the change;
* the slow part of the change ran **off the GUI thread** — the re-projection,
  the gating pass, the threshold scan, the auto-threshold sampling — measured by
  recording the thread the real function was entered on;
* the change still **took effect** once the job had finished.

The runner's own promises (the event loop keeps running while a job does, jobs
never overlap, cancel applies nothing) are ``tests/test_progress_jobs.py``.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")

from tests._panel_jobs import wait_for_panel_jobs  # noqa: E402

_MAIN = threading.get_ident()


@pytest.fixture
def panel(qtbot, tmp_path, monkeypatch):
    """The real tab on a loaded dataset, with every popup shown recorded."""
    from PySide6.QtWidgets import QWidget

    from review.features.home.cell_gating.controller import cell_gating_load_cell_areas
    from review.features.home.cell_gating.tab_view import build_cell_gating_tab
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.mini_dataset import build_mini_dataset
    from widgets import progress_popup

    dataset = build_mini_dataset(tmp_path / "ds", n_fovs=3)
    ctx, _window = minimal_shell(qtbot)
    load_dataset(ctx, dataset)
    parent = QWidget()
    qtbot.addWidget(parent)
    build_cell_gating_tab(ctx, parent)
    cell_gating_load_cell_areas(ctx)

    # The gating pass's finish redraws every plot tab through the full shell's
    # window, which a minimal shell does not have; there are no plot tabs here
    # to redraw, and the redraw is not what these tests measure.
    from review.features.home.cell_gating import controller
    monkeypatch.setattr(controller, "redraw_app", lambda _ctx: None)

    shown: list[str] = []
    real_show = progress_popup.ProgressPopup.show

    def _show(self):
        shown.append(self.windowTitle())
        return real_show(self)

    monkeypatch.setattr(progress_popup.ProgressPopup, "show", _show)
    # ``parent`` is held by this frame until the test ends: the widget registry
    # keeps only weak handles, and the tab dies with its parent.
    yield ctx, shown
    from review.core import progress_jobs
    wait_for_panel_jobs(qtbot, ctx)
    progress_jobs.stop_jobs(ctx)


def _record_thread(monkeypatch, module, name, threads, *, result=None, call_through=True):
    """Wrap ``module.name`` so each call records the thread it ran on."""
    real = getattr(module, name)

    def _wrapped(*args, **kwargs):
        threads.append(threading.get_ident())
        return real(*args, **kwargs) if call_through else result

    monkeypatch.setattr(module, name, _wrapped)


def _off_gui(threads) -> bool:
    return bool(threads) and all(t != _MAIN for t in threads)


# ── QC panel ─────────────────────────────────────────────────────────────

def test_ticking_a_qc_switch(qtbot, panel, monkeypatch):
    from review.features.qc import controller as qc

    ctx, shown = panel
    threads: list = []
    _record_thread(monkeypatch, qc, "reproject_verdicts", threads)
    box = ctx.widgets.get("_qc_gate_widgets")["enable"]
    box.setChecked(not box.isChecked())
    assert shown == ["Applying QC"]
    wait_for_panel_jobs(qtbot, ctx)
    assert _off_gui(threads), "the QC re-projection ran on the GUI thread"
    assert ctx.state.qc.params.enabled is box.isChecked()


def test_typing_a_qc_bound(qtbot, panel, monkeypatch):
    from review.features.qc import controller as qc

    ctx, shown = panel
    threads: list = []
    _record_thread(monkeypatch, qc, "reproject_verdicts", threads)
    w = ctx.widgets.get("_qc_gate_widgets")
    if not w["enable"].isChecked():
        w["enable"].setChecked(True)
        wait_for_panel_jobs(qtbot, ctx)
        shown.clear()
        threads.clear()
    w["min_area"].setText("1e9")          # above every cell: all flagged
    w["min_area"].editingFinished.emit()
    assert shown == ["Applying QC"]
    wait_for_panel_jobs(qtbot, ctx)
    assert _off_gui(threads)
    for df in ctx.state.dataset.cache.values():
        assert (df["QC_Pass"] == 0).all(), "the bound never reached the frames"
    assert w["status"].text().startswith("flagged"), "the count was not repainted"


# ── fields of view ───────────────────────────────────────────────────────

def test_changing_the_fields_of_view(qtbot, panel, monkeypatch):
    from PySide6.QtWidgets import QCheckBox

    from review.features.qc import controller as qc
    from widgets import menu_exec

    ctx, shown = panel
    threads: list = []
    _record_thread(monkeypatch, qc, "reproject_verdicts", threads)

    def _untick_2(menu, _pos):
        for box in menu.findChildren(QCheckBox):
            if box.text() == "FOV 2":
                box.setChecked(False)

    monkeypatch.setattr(menu_exec, "exec_menu", _untick_2)
    ctx.widgets.get("_cell_gating_fov_btn").click()
    assert shown == ["Applying the fields of view"]
    wait_for_panel_jobs(qtbot, ctx)
    assert _off_gui(threads)
    assert ctx.widgets.get("_cell_gating_fov_btn").text() == "FOVs 1, 3"


# ── cell inclusion: intensity floors and scatter gates ───────────────────

def _gating_threads(monkeypatch):
    from review.features.home.cell_gating import controller

    threads: list = []
    _record_thread(monkeypatch, controller.GatingWorker, "_run_inner", threads)
    return threads


def test_editing_an_intensity_floor(qtbot, panel, monkeypatch):
    ctx, shown = panel
    threads = _gating_threads(monkeypatch)
    edit = ctx.runtime.cell_gating.fluor_gate_edits["gfp"]
    edit.setText("7.5")
    edit.editingFinished.emit()
    assert shown == ["Applying cell gating"]
    wait_for_panel_jobs(qtbot, ctx)
    assert _off_gui(threads), "the gating pass ran on the GUI thread"
    assert ctx.state.cell_gating.applied_gating["fluor_gates"]["gfp"] == 7.5


def test_leaving_a_box_unchanged_applies_nothing(qtbot, panel):
    """``editingFinished`` fires on every focus change; a popup for a box the
    user only passed through would be noise."""
    ctx, shown = panel
    for edits in (ctx.runtime.cell_gating.fluor_gate_edits,
                  ctx.runtime.cell_gating.thresh_frac_edits):
        for edit in edits.values():
            edit.editingFinished.emit()
    assert shown == []


def _draw_two_gates(ctx):
    from review.features.home.cell_gating.controller import _cell_gating_refresh_gate_filter

    ctx.state.cell_gating.scatter_gates = [
        {"name": name, "x0": 0.0, "x1": 1e9, "y0": 0.0, "y1": 1e9,
         "x_col": "gfp_mean_intensity", "y_col": "mcherry_mean_intensity"}
        for name in ("G1", "G2")]
    _cell_gating_refresh_gate_filter(ctx)


def test_switching_inclusion_to_the_scatter_gates(qtbot, panel, monkeypatch):
    ctx, shown = panel
    _draw_two_gates(ctx)
    threads = _gating_threads(monkeypatch)
    ctx.runtime.cell_gating.use_scatter_gates_cb.setChecked(True)
    assert shown == ["Applying cell gating"]
    wait_for_panel_jobs(qtbot, ctx)
    assert _off_gui(threads)
    assert ctx.state.cell_gating.applied_gating["use_scatter"] is True


def test_switching_fraction_on_to_a_scatter_gate(qtbot, panel, monkeypatch):
    ctx, shown = panel
    _draw_two_gates(ctx)
    threads = _gating_threads(monkeypatch)
    ctx.runtime.cell_gating.use_scatter_thresh_cb.setChecked(True)
    assert shown == ["Applying cell gating"]
    wait_for_panel_jobs(qtbot, ctx)
    assert _off_gui(threads)
    assert ctx.state.cell_gating.applied_gating["use_thresh"] is True


def test_picking_gates_applies_once_when_the_menu_closes(qtbot, panel, monkeypatch):
    """Two ticks in one opening are one pass and one popup, run after the menu
    has closed — a popup opening on the first tick would close the menu."""
    from PySide6.QtWidgets import QCheckBox

    from review.features.home.cell_gating.tab_view import _open_gate_filter_menu
    from widgets import menu_exec

    ctx, shown = panel
    _draw_two_gates(ctx)
    ctx.runtime.cell_gating.use_scatter_gates_cb.setChecked(True)
    wait_for_panel_jobs(qtbot, ctx)
    shown.clear()
    threads = _gating_threads(monkeypatch)
    during_menu: list = []

    def _untick_both_then_retick_g2(menu, _pos):
        boxes = {b.text(): b for b in menu.findChildren(QCheckBox)}
        boxes["G1"].setChecked(False)
        boxes["G2"].setChecked(False)
        boxes["G2"].setChecked(True)
        during_menu.extend(shown)

    monkeypatch.setattr(menu_exec, "exec_menu", _untick_both_then_retick_g2)
    _open_gate_filter_menu(ctx, "gating")
    assert during_menu == [], "a tick applied while the menu was still open"
    assert shown == ["Applying cell gating"]
    wait_for_panel_jobs(qtbot, ctx)
    assert _off_gui(threads) and len(threads) == 1
    assert ctx.state.cell_gating.gating_choices == {"G2"}
    assert [g["name"] for g in ctx.state.cell_gating.applied_gating["gating_gates"]] == ["G2"]


# ── fraction-on ──────────────────────────────────────────────────────────

def test_editing_an_on_cutoff(qtbot, panel):
    ctx, shown = panel
    edit = ctx.runtime.cell_gating.thresh_frac_edits["gfp"]
    edit.setText("123.5")
    edit.editingFinished.emit()
    assert shown == ["Applying the ON cutoff"]
    wait_for_panel_jobs(qtbot, ctx)
    assert ctx.state.cell_gating.thresh_frac_on["gfp"] == 123.5


def test_auto_threshold(qtbot, panel, monkeypatch):
    from review.data import auto_threshold

    ctx, shown = panel
    threads: list = []
    _record_thread(monkeypatch, auto_threshold, "compute_auto_thresholds", threads,
                   result={"gfp": 321.0}, call_through=False)
    ctx.runtime.cell_gating.auto_btn.click()
    assert shown == ["Auto-threshold"]
    wait_for_panel_jobs(qtbot, ctx)
    assert _off_gui(threads), "auto-threshold sampled on the GUI thread"
    assert ctx.state.cell_gating.thresh_frac_on["gfp"] == 321.0
    assert ctx.runtime.cell_gating.auto_btn.isEnabled()


def test_the_optimizer(qtbot, panel, monkeypatch):
    from review.features.home.cell_gating import controller

    ctx, shown = panel
    threads: list = []
    _record_thread(monkeypatch, controller, "_optimize_thresh_frac_on_between_wells", threads)
    rt = ctx.runtime.cell_gating
    rt.opt_well_b_combo.setCurrentIndex(1)
    assert rt.opt_well_a_combo.currentText() != rt.opt_well_b_combo.currentText()
    before = dict(ctx.state.cell_gating.thresh_frac_on)
    rt.opt_btn.click()
    assert shown == ["Optimize ThreshFracOn"]
    wait_for_panel_jobs(qtbot, ctx)
    assert _off_gui(threads), "the threshold scan ran on the GUI thread"
    key = str(rt.opt_channel_combo.currentData())
    text = rt.opt_result_label.text()
    assert text.startswith(("Optimized", "Optimize ThreshFracOn (")), text
    if text.startswith("Optimized"):
        assert ctx.state.cell_gating.thresh_frac_on[key] != before.get(key)


def test_a_pass_that_ends_before_start_returns_still_closes_its_popup(qtbot, panel, monkeypatch):
    """The popup is closed by the pass's ``QThread.finished``. Connected after
    ``start()``, a pass over a handful of held wells could end before the
    connection existed, and the application-modal popup then stayed up for ever.
    Made deterministic here by a ``start`` that returns only once the thread has
    ended, which is the worst case of that race."""
    from PySide6.QtCore import QThread

    from review.features.home.cell_gating import controller

    def _start_and_finish(self):
        QThread.start(self)
        self.wait(5000)

    monkeypatch.setattr(controller.GatingWorker, "start", _start_and_finish)
    ctx, shown = panel
    edit = ctx.runtime.cell_gating.fluor_gate_edits["gfp"]
    edit.setText("3.5")
    edit.editingFinished.emit()
    assert shown == ["Applying cell gating"]
    wait_for_panel_jobs(qtbot, ctx, timeout=3000)
    assert ctx.state.cell_gating.applied_gating["fluor_gates"]["gfp"] == 3.5
