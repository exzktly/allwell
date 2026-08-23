"""Regression guards for per-dataset state, persistence and worker lifecycle (PR 4).

Three questions, each of which the app answered wrongly somewhere:

1. What survives opening another dataset? (Nothing that names the previous
   plate's wells, timepoints or intensities.)
2. What happens to an edit the user made half a second before quitting, and to
   a save still queued when the data directory changes underneath it?
3. What happens to a background thread when the pane it writes into goes away?
"""

from __future__ import annotations

import ast
import json
import pathlib
import types

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── 1. per-dataset state does not outlive its dataset ────────────────────────

def test_plot_state_reset_clears_everything_that_names_a_well_or_timepoint():
    """A fold-change control names a well or a replicate set; a baseline names a
    timepoint; a line order names wells. Carried into the next plate they
    resolve to nothing, and the loudest case is silent: plate B came up
    normalised against plate A's group, so every bar was NaN under an axis
    still labelled as a fold change."""
    from well_viewer.core.plot_state import PlotState

    st = PlotState()
    st.fc_vs_control_on = True
    st.fc_control = "A01"
    st.fc_control_mode = "per_member"
    st.fc_control_map = {"g": "A01"}
    st.fc_vs_t0_on = True
    st.fc_baseline_tp = "24"
    st.focus_timepoint = 24.0
    st.line_order_wells = ["B02", "A01"]
    st.line_order_rsets = ["set"]
    st.bar_group_mode = "timepoint"
    st.bar_order = ["A01"]

    st.reset_for_new_dataset()

    assert st.fc_vs_control_on is False
    assert st.fc_control is None
    assert st.fc_control_mode == "single"
    assert st.fc_control_map == {}
    assert st.fc_vs_t0_on is False
    assert st.fc_baseline_tp == ""
    assert st.focus_timepoint is None
    assert st.line_order_wells == [] and st.line_order_rsets == []
    assert st.bar_group_mode == "well"
    assert st.bar_order is None


def test_a_look_the_user_chose_is_not_reset():
    """The distinction the reset rests on: *which data* versus *how I like to
    look at it*. SEM-vs-SD is the second kind and must follow the user."""
    from well_viewer.core.plot_state import PlotState

    st = PlotState()
    st.use_sem = not st.use_sem
    chosen = st.use_sem
    st.reset_for_new_dataset()
    assert st.use_sem is chosen


def test_export_prefs_reset_drops_axis_views_and_keeps_the_style():
    """``axis_prefs`` are limits in the units of the plate that was open;
    re-applying plate A's ``y_lim_max`` to plate B happens on every redraw and
    looks exactly like a plot that has decided to clip itself. ``style_prefs``
    is a publication look and should follow the user."""
    from well_viewer.core.export_prefs_state import ExportPrefsState

    st = ExportPrefsState()
    st.style_prefs = {"font": "Arial"}
    st.axis_prefs = {"_bar_fig": {"y_lim_max": 12.0}}
    st.reset_for_new_dataset()
    assert st.axis_prefs == {}
    assert st.style_prefs == {"font": "Arial"}


def test_image_view_reset_clears_luts_and_tints():
    """A LUT is a pair of intensities, and intensities belong to the plate that
    produced them: a bright plate's window renders the next plate black, with
    the histogram controls showing the numbers, so it reads as the data."""
    from well_viewer.core.image_view_state import ImageViewState

    st = ImageViewState()
    st.set_lut("gfp", 100.0, 4000.0)
    st.tint_by_channel["gfp"] = (255, 0, 0)
    st.reset_for_new_dataset()
    assert st.lut_by_channel == {} and st.tint_by_channel == {}


def test_the_load_path_calls_every_per_dataset_reset():
    """Each slice owns the list of what "per-dataset" means for it, but the
    load path has to actually ask. ``view_state.restore`` cannot stand in for
    this: it early-returns when the new plate has no saved block, which is
    exactly the case where the old plate's settings would carry over."""
    src = (REPO_ROOT / "well_viewer" / "core" / "load_controller.py").read_text()
    for slice_name in ("plot", "export_prefs", "image_view"):
        assert f"{slice_name}.reset_for_new_dataset()" in src, (
            f"the {slice_name} slice is not reset when a dataset is opened")


# ── 2. a queued save is not a lost save ──────────────────────────────────────

def test_the_registry_can_flush_every_pending_save():
    from well_viewer.persistence.registry import PersistenceRegistry

    written: list[str] = []
    ctx = types.SimpleNamespace()
    reg = PersistenceRegistry(ctx)
    reg.register("alpha", save=lambda _c: written.append("alpha"))
    reg.register("beta", save=lambda _c: written.append("beta"))
    reg._pending.update({"alpha", "beta"})

    reg.flush_all()

    assert sorted(written) == ["alpha", "beta"]
    assert not reg._pending


def test_one_domain_failing_does_not_strand_the_others():
    """``flush_all`` runs on the way out; a disk error in one domain must not
    stop the rest being written."""
    from well_viewer.persistence.registry import PersistenceRegistry

    written: list[str] = []

    def _boom(_c):
        raise OSError("disk full")

    reg = PersistenceRegistry(types.SimpleNamespace())
    reg.register("alpha", save=_boom)
    reg.register("beta", save=lambda _c: written.append("beta"))
    reg._pending.update({"alpha", "beta"})

    reg.flush_all()
    assert written == ["beta"]


@pytest.mark.parametrize(
    "path", ["allwell.py", "well_viewer/core/load_controller.py"])
def test_both_moments_the_debounce_never_fires_flush_it(path):
    """Quitting, and swapping the data directory. On quit the edit was lost —
    only ``view_state`` had a force-save. On a swap it was worse than lost: a
    surviving timer fired against the *new* dataset's directory and wrote the
    previous dataset's state into it."""
    assert "flush_all()" in (REPO_ROOT / path).read_text(), (
        f"{path} leaves pending debounced saves unflushed")


# ── 3. an unreadable sidecar is not an empty one ─────────────────────────────

def _doc_state(tmp_path):
    dataset = types.SimpleNamespace(
        data_dir=tmp_path, persistence_doc=None,
        persistence_doc_degraded=False,
    )
    return types.SimpleNamespace(dataset=dataset)


def test_a_corrupt_persistence_file_is_kept_rather_than_overwritten(tmp_path):
    """The in-memory doc is empty *by failure*, not because the dataset has
    nothing saved. Writing it back erased every section the file still held —
    every ratio, layout, override, note and sample definition — because one
    byte failed to parse."""
    from well_viewer.persistence import _doc

    out = tmp_path / "out"
    out.mkdir()
    state = _doc_state(out)
    p = _doc.path_for(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ this is not json")

    doc = _doc.read(state)
    assert doc == {} or "schema_version" in doc
    assert state.dataset.persistence_doc_degraded is True

    _doc.write(state, {"ratios": []})

    kept = list(p.parent.glob(f"{p.stem}.unreadable-*{p.suffix}"))
    assert kept, "the unreadable document was overwritten instead of kept"
    assert kept[0].read_text() == "{ this is not json"
    assert json.loads(p.read_text())["ratios"] == []
    assert state.dataset.persistence_doc_degraded is False


def test_an_ordinary_write_keeps_no_backup(tmp_path):
    """The control: a readable document is replaced in place, as before."""
    from well_viewer.persistence import _doc

    out = tmp_path / "out"
    out.mkdir()
    state = _doc_state(out)
    _doc.write(state, {"ratios": [1]})
    state.dataset.persistence_doc = None
    _doc.write(state, {"ratios": [2]})
    p = _doc.path_for(state)
    assert not list(p.parent.glob(f"{p.stem}.unreadable-*{p.suffix}"))


# ── 4. a background thread does not outlive the pane it writes into ──────────

class _FakeWorker:
    """A stand-in QThread: records whether it was asked to stop."""

    def __init__(self, running: bool = True) -> None:
        self._running = running
        self.cancelled = False
        self.waited = False

    def cancel(self) -> None:
        self.cancelled = True

    def isRunning(self) -> bool:
        return self._running

    def wait(self, _ms: int | None = None) -> bool:
        self.waited = True
        self._running = False
        return True


def test_both_cell_gating_workers_are_stopped_at_teardown():
    """``teardown_review_pane`` covered smFISH and the Cell Browser loader and
    not these two, so quitting mid-pass left a QThread running against a pane
    being destroyed — and the inclusion pass writes ``Included`` flags onto the
    row cache as it goes.

    Asserted behaviourally rather than by reading the function's source: the
    first version of this guard pinned the inline shape of the loop and broke
    the moment the loop was extracted into a helper, which is a test of the
    implementation, not of the rule.
    """
    from well_viewer import runtime_app

    worker, auto = _FakeWorker(), _FakeWorker()
    runtime = types.SimpleNamespace(
        smfish=types.SimpleNamespace(cancel_event=None),
        cell_browser=types.SimpleNamespace(loader=None),
        cell_gating=types.SimpleNamespace(worker=worker, auto_worker=auto),
    )
    app = types.SimpleNamespace(_ctx=types.SimpleNamespace(runtime=runtime))

    runtime_app.teardown_review_pane(app)

    assert worker.cancelled and worker.waited
    assert auto.cancelled and auto.waited


def test_teardown_survives_a_build_without_the_feature():
    """A profiled-out Cell Gating has no runtime slice at all; teardown must
    not care."""
    from well_viewer import runtime_app

    runtime = types.SimpleNamespace(
        smfish=types.SimpleNamespace(cancel_event=None),
        cell_browser=types.SimpleNamespace(loader=None),
    )
    app = types.SimpleNamespace(_ctx=types.SimpleNamespace(runtime=runtime))
    runtime_app.teardown_review_pane(app)


def test_the_auto_threshold_worker_can_be_cancelled():
    """Restarting the pass waited 50 ms for the previous worker and then
    started another regardless, so two live threads could both reach ``done``
    and the later-*finishing* one decided the thresholds."""
    from well_viewer.features.home.cell_gating import controller as cg

    assert hasattr(cg._AutoThresholdWorker, "cancel")
    src = (REPO_ROOT / "well_viewer" / "features" / "home" / "cell_gating"
           / "controller.py").read_text()
    assert "prev.cancel()" in src, "the restart guard does not cancel the old pass"


def test_zip_handles_are_closed_under_the_reader_lock():
    """``with_handle`` yields the ZipFile while holding the entry's lock
    because ZipFile is not safe for concurrent reads; eviction, stale-mtime
    replacement and ``invalidate`` all closed it without taking that lock, and
    the readers are off-thread."""
    src = (REPO_ROOT / "well_viewer" / "data" / "zipfile_cache.py").read_text()
    tree = ast.parse(src)
    closes = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "close"
        and "zf" in ast.unparse(n)
    ]
    assert closes, "the cache no longer closes handles"
    fn_of = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef,)):
            for n in ast.walk(fn):
                fn_of[id(n)] = fn.name
    for call in closes:
        assert fn_of.get(id(call)) == "_close_entry", (
            f"line {call.lineno}: a handle is closed outside _close_entry, so a "
            "concurrent reader can have the file closed under it")


# ── 5. a readout reads the key that is actually written ──────────────────────

def test_the_run_receipt_reads_the_qc_keys_the_pipeline_writes():
    """``qc_options`` is written with un-prefixed names; the ``qc_`` prefix
    belongs to the CLI flags they come from. Three rows of the receipt showed
    "—" and the literal "None, None" for every run ever made."""
    src = (REPO_ROOT / "well_viewer" / "features" / "home" / "pipeline_info"
           / "tab_view.py").read_text()
    for key in ("qc_remove_debris", "qc_min_area", "qc_max_area", "qc_min_solidity"):
        assert f'qc.get("{key}")' not in src, (
            f"the receipt reads {key}, which the sidecar never writes")
    for key in ("remove_debris", "min_area", "max_area", "min_solidity"):
        assert f'qc.get("{key}")' in src


def test_the_ratio_count_reads_live_state_not_a_sidecar_key():
    """Home read ``pipeline_info["ratios"]``, a key nothing has ever written,
    so the row said "None yet" however many ratios were defined. It cannot read
    the persistence document either — this page's premise is that it opens no
    files — so it counts what the loader already put in memory."""
    src = (REPO_ROOT / "well_viewer" / "features" / "home" / "collect.py").read_text()
    assert 'info.get("ratios")' not in src
    assert "ratio_metrics" in src
