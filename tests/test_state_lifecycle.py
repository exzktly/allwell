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
import tempfile
import types
from unittest import mock

import pytest
from tests._repo_scan import parse_source

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── 1. per-dataset state does not outlive its dataset ────────────────────────

def test_plot_state_reset_clears_everything_that_names_a_well_or_timepoint():
    """A fold-change control names a well or a replicate set; a baseline names a
    timepoint; a line order names wells. Carried into the next plate they
    resolve to nothing, and the loudest case is silent: plate B came up
    normalised against plate A's group, so every bar was NaN under an axis
    still labelled as a fold change."""
    from review.core.plot_state import PlotState

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
    from review.core.plot_state import PlotState

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
    from review.core.export_prefs_state import ExportPrefsState

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
    from review.core.image_view_state import ImageViewState

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
    src = (REPO_ROOT / "review" / "core" / "load_controller.py").read_text()
    for slice_name in ("plot", "export_prefs", "image_view"):
        assert f"{slice_name}.reset_for_new_dataset()" in src, (
            f"the {slice_name} slice is not reset when a dataset is opened")


# ── 2. a queued save is not a lost save ──────────────────────────────────────

def test_the_registry_can_flush_every_pending_save():
    from review.persistence.registry import PersistenceRegistry

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
    from review.persistence.registry import PersistenceRegistry

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
    "path", ["allwell.py", "review/core/load_controller.py"])
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
    from review.persistence import _doc

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
    from review.persistence import _doc

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
    from review import runtime_app

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
    """A profiled-out feature has a runtime slice holding no worker.

    Driven through the real ``AppRuntime`` rather than a hand-built namespace:
    ``AppRuntime`` constructs the known slices unconditionally and
    ``_FeatureRuntime`` answers an unknown attribute with ``None``, so the
    shape a profiled-out build actually produces is "slice present, worker
    ``None``" — not "no slice". A namespace with the attribute missing tests a
    state the app cannot reach, and it passed before the helper existed.
    """
    from review.core.app_runtime import AppRuntime
    from core.worker_bridge import stop_runtime_workers

    runtime = AppRuntime(store={})
    assert runtime.cell_gating is not None, (
        "a known feature's runtime slice is always constructed; if this ever "
        "becomes None the helper's slice_ guard is the load-bearing one")
    assert runtime.cell_gating.worker is None
    stop_runtime_workers(runtime, "cell_gating", ("worker", "auto_worker"))
    # And a slice name this build knows nothing about.
    stop_runtime_workers(runtime, "not_a_feature_in_this_build", ("worker",))


def test_teardown_clears_the_thread_reference_it_stopped():
    """A stopped QThread left hanging off the runtime slice is a dead C++
    object for whatever looks at it next."""
    from core.worker_bridge import stop_runtime_workers

    class _Worker:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

        def isRunning(self):
            return False

    slice_ = types.SimpleNamespace(worker=_Worker())
    runtime = types.SimpleNamespace(cell_gating=slice_)
    stop_runtime_workers(runtime, "cell_gating", ("worker",))
    assert slice_.worker is None


def test_a_failing_cancel_does_not_skip_the_wait():
    """``cancel()`` and ``wait()`` answer different questions. Sharing one
    ``try`` turned "cancel raised" into "the thread was never joined either",
    which is the case where a live worker emits into a widget tree being
    deleted."""
    from core.worker_bridge import stop_runtime_workers

    class _Worker:
        def __init__(self):
            self.waited = False

        def cancel(self):
            raise RuntimeError("cancel is broken")

        def isRunning(self):
            return True

        def wait(self, _ms):
            self.waited = True
            return True

    worker = _Worker()
    slice_ = types.SimpleNamespace(worker=worker)
    stop_runtime_workers(types.SimpleNamespace(cell_gating=slice_),
                         "cell_gating", ("worker",))
    assert worker.waited, "a raising cancel() swallowed the wait as well"


def test_a_cancelled_auto_threshold_scan_actually_stops():
    """The cancel has to be something the compute cannot swallow.

    The first version raised an exception from inside the progress callback —
    and ``compute_auto_thresholds._emit`` wraps that callback in ``except
    Exception``, so the raise was caught, the scan ran to completion, and every
    later progress line logged a suppressed traceback. The guard for it
    asserted only that a ``cancel`` method existed and that the restart site
    contained the string ``prev.cancel()``, which is why it shipped.
    """
    from review.data import auto_threshold as at

    # A directory that exists and names wells, so the scan gets past its own
    # "data directory not found" guard and reaches the per-well loop. Pointed
    # at a nonexistent path the call returns {} without consulting
    # ``should_cancel`` even once, which is a pass that proves only that the
    # parameter is accepted.
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = pathlib.Path(tmp)
        (out_dir / "A01.csv").write_text("well,cell_id\nA01,1\n")
        (out_dir / "A02.csv").write_text("well,cell_id\nA02,1\n")

        asked: list[int] = []

        def _cancel_now() -> bool:
            asked.append(1)
            return True

        seen: list[str] = []
        result = at.compute_auto_thresholds(
            out_dir,
            fluor_channels=["gfp"],
            progress=seen.append,
            well_labels=["A01", "A02"],
            should_cancel=_cancel_now,
        )
    assert asked, (
        "should_cancel was never consulted — the scan returned before reaching "
        "the well loop, so this asserts nothing about cancelling")
    assert result == {}
    assert any("cancel" in line.lower() for line in seen), (
        f"a cancelled pass must say so; got {seen!r}")


def test_a_scan_nobody_cancelled_runs_to_the_end():
    """The control for the test above: the same fixture with a predicate that
    says no must *not* stop early, or the one above would pass against code
    that simply refuses to run."""
    from review.data import auto_threshold as at

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = pathlib.Path(tmp)
        (out_dir / "A01.csv").write_text("well,cell_id\nA01,1\n")
        seen: list[str] = []
        at.compute_auto_thresholds(
            out_dir,
            fluor_channels=["gfp"],
            progress=seen.append,
            well_labels=["A01"],
            should_cancel=lambda: False,
        )
    assert not any("cancel" in line.lower() for line in seen), (
        f"nothing asked this pass to stop; got {seen!r}")


def test_every_worker_restart_cancels_first_and_waits_briefly():
    """Cancelling is what makes a restart safe — a cancelled worker disowns
    whatever it computed — so the wait is a courtesy, not the mechanism.

    Checked at *every* site that waits on a previous worker, not the first one
    found. Two versions of this test measured nothing before this one: the
    original asserted that the string ``prev.wait(2000)`` was absent, and that
    string has never appeared in this file; the replacement stopped at the
    first matching function and so never reached
    ``cell_gating_start_gating_worker``, whose ``prev.wait()`` takes no timeout
    at all and therefore blocks the GUI thread until the previous pass finishes
    reading a well.
    """
    src = (REPO_ROOT / "review" / "features" / "home" / "cell_gating"
           / "controller.py").read_text()
    tree = ast.parse(src)
    checked = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        waits = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and ast.unparse(n).startswith("prev.wait(")]
        if not waits:
            continue
        checked += 1
        assert "prev.cancel()" in ast.unparse(fn), (
            f"{fn.name} waits on the previous worker without cancelling it "
            "first; the wait alone does not stop anything")
        for call in waits:
            # An argless ``prev.wait()`` is the worst form of this, not an
            # absent one — Qt's default is to wait forever. Reading
            # ``call.args[0]`` without checking made an earlier version of this
            # guard raise IndexError against exactly the code it exists to
            # catch, which reads as a failure in a terminal and measures nothing.
            assert call.args, (
                f"{fn.name}, line {call.lineno}: {ast.unparse(call)} waits "
                "without a timeout, so the window is frozen for as long as the "
                "previous pass takes to reach its next checkpoint")
            ms = call.args[0]
            assert isinstance(ms, ast.Constant) and ms.value <= 500, (
                f"{fn.name}, line {call.lineno}: {ast.unparse(call)} blocks "
                "the GUI thread; the cancel is the mechanism, the wait is a "
                "courtesy")
    assert checked >= 2, (
        f"only {checked} restart site(s) found; both the gating worker and the "
        "auto-threshold worker restart this way, so a lower count means this "
        "guard has stopped seeing one of them")


def test_zip_handles_are_closed_under_the_reader_lock():
    """``with_handle`` yields the ZipFile while holding the entry's lock
    because ZipFile is not safe for concurrent reads; eviction, stale-mtime
    replacement and ``invalidate`` all closed it without taking that lock, and
    the readers are off-thread."""
    src = (REPO_ROOT / "review" / "data" / "zipfile_cache.py").read_text()
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
    src = (REPO_ROOT / "review" / "features" / "home" / "pipeline_info"
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
    src = (REPO_ROOT / "review" / "features" / "home" / "collect.py").read_text()
    assert 'info.get("ratios")' not in src
    assert "ratio_metrics" in src


# ── 6. the corrections a review of this branch required ──────────────────────

def test_a_drained_save_does_not_fire_again_when_its_timer_lands():
    """``schedule_save`` arms a ``QTimer.singleShot`` lambda that cannot be
    cancelled, so it still fires ~500 ms after ``flush_all`` has written and
    drained the domain. ``_flush`` discarded-then-saved unconditionally, so
    that second firing wrote again — and on a dataset swap it landed after
    ``begin_load``, putting the previous plate's ratios, layouts, line order or
    crop into the new plate's document. That is the exact bug ``flush_all``
    exists to prevent, so the pending set has to be the authority on whether a
    save is still owed."""
    from review.persistence.registry import PersistenceRegistry

    writes: list[str] = []
    reg = PersistenceRegistry(types.SimpleNamespace())
    reg.register("ratios", save=lambda _c: writes.append("ratios"))
    reg._pending.add("ratios")

    reg.flush_all()
    assert writes == ["ratios"]

    # The stray timer landing afterwards.
    reg._flush("ratios")
    assert writes == ["ratios"], "a drained domain was written a second time"


def test_a_log_scale_and_mark_styling_survive_a_dataset_swap():
    """Only the *limits* carry the old plate's units. Clearing the whole of
    ``axis_prefs`` also reset the user's log axes and mark styling on every
    dataset switch — the "how I like to look at it" half of the rule the reset
    is built on, and the half ARCHITECTURE says follows the user."""
    from review.core.export_prefs_state import ExportPrefsState

    st = ExportPrefsState()
    st.axis_prefs = {
        "bar": {"y_lim_max": 12.0, "y_log": True, "marker_size": 8.0},
        "line": {"y_lim_min": 1.0},
    }
    st.reset_for_new_dataset()
    assert st.axis_prefs == {"bar": {"y_log": True, "marker_size": 8.0}}


def test_the_time_scale_multiplier_does_not_follow_the_user_to_the_next_plate():
    """"Interval per timepoint" is a statement about the plate whose filename
    cadence does not match the hours it was imaged at, and the factor is
    printed in the axis label — so carrying it over relabels every time axis on
    the next plate in a way that reads as the data."""
    from review.core.plot_state import PlotState

    st = PlotState()
    st.time_scale = 4.0
    st.reset_for_new_dataset()
    assert st.time_scale == 1.0


def test_a_document_from_a_newer_build_is_left_alone(tmp_path):
    """A legible file written by a schema this build does not know is not
    corrupt. Renaming it aside would make a user who alternates builds lose the
    other one's state on every launch, under a filename claiming it was
    unreadable; refusing to write is enough to keep both safe."""
    from review.persistence import _doc

    out = tmp_path / "out"
    out.mkdir()
    state = _doc_state(out)
    state.dataset.persistence_doc_foreign = False
    p = _doc.path_for(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema_version": 9999, "ratios": ["theirs"]}))

    _doc.read(state)
    assert state.dataset.persistence_doc_foreign is True
    assert state.dataset.persistence_doc_degraded is False

    _doc.write(state, {"ratios": []})

    assert json.loads(p.read_text())["ratios"] == ["theirs"]
    assert not list(p.parent.glob(f"{p.stem}.unreadable-*{p.suffix}"))


def test_an_unpreservable_corrupt_file_is_not_overwritten(tmp_path, monkeypatch):
    """If the copy cannot be made — a read-only directory, a permission problem
    — writing anyway destroys the very file the rename exists to preserve."""
    from review.persistence import _doc

    out = tmp_path / "out"
    out.mkdir()
    state = _doc_state(out)
    p = _doc.path_for(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not json")
    _doc.read(state)
    assert state.dataset.persistence_doc_degraded is True

    monkeypatch.setattr(_doc, "_set_aside_unreadable", lambda _p: False)
    _doc.write(state, {"ratios": []})
    assert p.read_text() == "{ not json"


def test_an_in_progress_note_is_committed_before_the_queue_is_drained():
    """The notes editors commit on focus-out, and closing a window does not
    move focus — so the edit is not in the queue at all. Draining a queue the
    edit never entered writes everything except the thing the user was
    typing."""
    src = (REPO_ROOT / "allwell.py").read_text()
    tree = ast.parse(src)
    # The close-time saves live in ``_save_review_before_close`` so the QC
    # half of this order can be driven by a test
    # (``test_qc_bounds_apply_once.py::test_a_bound_typed_as_the_window_closes_is_saved``);
    # ``closeEvent`` must still call it.
    close = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "closeEvent")
    assert "_save_review_before_close" in ast.unparse(close)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_save_review_before_close")
    body = ast.unparse(fn)
    assert "commit_focused_notes" in body
    assert body.index("commit_focused_notes") < body.index("flush_all")
    assert body.index("commit_pending_qc_bounds") < body.index("flush_all")


# ── 8. the second review round ───────────────────────────────────────────────

def test_a_committed_note_is_queued_not_merely_folded_in():
    """Folding the text into the slice is only half the commit.

    Everything else the user edits reaches disk through the persistence queue,
    and text that never focused out was never queued — so the close path folded
    the edit in and then drained a queue it was not in. The test above asserts
    the *order* of two calls in ``closeEvent``, which it does correctly and
    which passed with the note still being lost; this one drives the commit and
    looks at the queue.
    """
    from review.core import ui_helpers

    # A stub rather than a real QPlainTextEdit: this asserts what the commit
    # does with the text, which needs no Qt and no QApplication.
    edit = types.SimpleNamespace(
        toPlainText=lambda: "cells looked odd today", hasFocus=lambda: True)
    pending: set[str] = set()
    ctx = types.SimpleNamespace(
        state=types.SimpleNamespace(
            selection=types.SimpleNamespace(notes_text="")),
        persistence=types.SimpleNamespace(schedule_save=pending.add),
        widgets=types.SimpleNamespace(get_or_none=lambda _k: None),
    )
    with mock.patch.object(ui_helpers, "notes_widgets",
                           lambda _ctx: [("home_notes", edit)]):
        ui_helpers.commit_focused_notes(ctx)
    assert ctx.state.selection.notes_text == "cells looked odd today"
    assert "notes" in pending, (
        "the note was folded into the slice but never queued, so a flush "
        "writes every domain except this one")


def test_committing_an_unchanged_note_queues_nothing():
    """The control: the save paths commit on every write, and queuing there
    would schedule a write of what was just written."""
    from review.core import ui_helpers

    edit = types.SimpleNamespace(
        toPlainText=lambda: "same text", hasFocus=lambda: True)
    pending: set[str] = set()
    ctx = types.SimpleNamespace(
        state=types.SimpleNamespace(
            selection=types.SimpleNamespace(notes_text="same text")),
        persistence=types.SimpleNamespace(schedule_save=pending.add),
        widgets=types.SimpleNamespace(get_or_none=lambda _k: None),
    )
    with mock.patch.object(ui_helpers, "notes_widgets",
                           lambda _ctx: [("home_notes", edit)]):
        ui_helpers.commit_focused_notes(ctx)
    assert pending == set()


def test_a_dataset_switch_drops_the_axis_limits_a_panel_kept_in_its_bucket():
    """A limit lives in two places at once and the drawn one is the bucket.

    ``apply_export_style_prefs`` reads ``axis_buckets[str(index)]`` first and
    only falls back to the flat key, and the Properties sidebar writes every
    limit into both on every edit. Clearing the flat keys alone left the drawn
    limit exactly where it was — a plate whose values run 0–1000 opening under
    the previous plate's 0–12.
    """
    from review.core.export_prefs_state import ExportPrefsState

    state = ExportPrefsState()
    state.axis_prefs = {
        "bar": {
            "y_lim_min": 0.0, "y_lim_max": 12.0, "axis_target": "1",
            "y_log": True, "marker_size": 8,
            "axis_buckets": {"1": {"y_lim_min": 0.0, "y_lim_max": 12.0,
                                   "tick_label_size": 10}},
        }
    }
    state.reset_for_new_dataset()
    bar = state.axis_prefs["bar"]
    assert "y_lim_max" not in bar
    assert "y_lim_max" not in bar["axis_buckets"]["1"], (
        "the per-panel copy of the limit survived the switch, and that is the "
        "one the renderer reads")
    # The other half of the rule: how a user likes a figure to read follows them.
    assert bar["y_log"] is True and bar["marker_size"] == 8
    assert bar["axis_buckets"]["1"]["tick_label_size"] == 10


def test_every_caller_that_forgets_a_limit_goes_through_one_helper():
    """Two call sites answer "forget the limits the user typed" and one of them
    carried its own copy of the key list, clearing the flat keys and the bucket
    by hand. A third will be written.

    The first version of this guard looked only for ``pop("y_lim_max")`` with a
    literal argument, and the site it exists to catch spelled it
    ``for _k in _BAR_LIMIT_KEYS: bar.pop(_k, None)`` — so it passed on the
    unfixed code and measured nothing. It now resolves module-level tuple and
    list constants, which is how a key list is actually written.
    """
    import review.core.plot_style as ps

    limit_keys = set(ps.AXIS_LIMIT_PREF_KEYS)
    roots = [REPO_ROOT / "review", REPO_ROOT / "widgets"]
    offenders: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if path.name == "plot_style.py":
                continue
            tree = parse_source(path)
            # Module-level name -> the literal strings it holds, so a pop whose
            # argument is a loop variable over such a name is seen too.
            rosters: dict[str, set[str]] = {}
            for node in tree.body:
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                    continue
                target = node.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                if isinstance(node.value, (ast.Tuple, ast.List, ast.Set)):
                    values = {e.value for e in node.value.elts
                              if isinstance(e, ast.Constant)
                              and isinstance(e.value, str)}
                    if values:
                        rosters[target.id] = values
            # Loop variable -> the roster it iterates.
            from_roster: dict[str, set[str]] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.For) and isinstance(node.target, ast.Name) \
                        and isinstance(node.iter, ast.Name) \
                        and node.iter.id in rosters:
                    from_roster[node.target.id] = rosters[node.iter.id]
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "attr", "") != "pop" or not node.args:
                    continue
                arg = node.args[0]
                touched: set[str] = set()
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    touched = {arg.value}
                elif isinstance(arg, ast.Name):
                    touched = from_roster.get(arg.id, set())
                if touched & limit_keys:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: "
                        f"{ast.unparse(node)}")
    assert not offenders, (
        f"{len(offenders)} site(s) clear an axis limit by hand instead of "
        "calling plot_style.drop_axis_limits, which also clears the per-panel "
        "bucket the renderer actually reads:\n  " + "\n  ".join(offenders))


def test_a_plate_with_no_saved_ratios_clears_the_previous_plate_s():
    """No loader in ``persistence/`` clears on a missing section — every one of
    them early-returns. So a hydrator that returns instead of clearing leaves
    the previous plate's work live under the new plate's name: its ratios stay
    as virtual channels in every plot, and Home's ratio row counts them.

    Driven through the loader, which is the point where "this dataset's block"
    is decided, rather than through a roster in ``load_directory`` that the
    next domain will not be added to.
    """
    from review.persistence import _doc, ratios

    applied: list[list] = []
    app = types.SimpleNamespace(
        _app_state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(data_dir=pathlib.Path("/plate-b"))),
        _set_ratio_metrics=applied.append,
    )
    ctx = types.SimpleNamespace(window=app)
    with mock.patch.object(_doc, "get_section", lambda _c, _k: None):
        ratios.load_from_data_dir(ctx)
    assert applied == [[]], (
        "a plate with no saved ratios left the previous plate's ratios live; "
        f"the loader applied {applied!r}")


def test_a_plate_with_no_saved_heatmap_layouts_clears_the_previous_plate_s():
    """Same rule, the other domain that was still inheriting: a layout names
    wells and a metric, and the next plate need have neither."""
    from review.persistence import heatmap_layouts as hl

    heatmap_slice = types.SimpleNamespace(persisted_settings={"metric": "gfp"})
    runtime_heatmap = types.SimpleNamespace(layouts=["a layout from plate A"])
    app = types.SimpleNamespace(
        _app_state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(data_dir=pathlib.Path("/plate-b")),
            feature_slice=lambda _name: heatmap_slice),
    )
    ctx = types.SimpleNamespace(
        window=app, runtime=types.SimpleNamespace(heatmap=runtime_heatmap))
    with mock.patch.object(hl._doc, "get_section", lambda _c, _k: None), \
            mock.patch.object(hl, "apply_persisted_settings", lambda _c: None):
        hl.load_from_data_dir(ctx)
    assert runtime_heatmap.layouts == [], (
        "plate A's heat-map layouts survived the switch to plate B")


def test_the_variable_band_does_not_survive_a_dataset_swap():
    """A variable id names an identity only within the set that minted it.

    Plate A's *Group by: Dose* left set is not a control offering something
    that no longer exists — every plate has a first variable, so the id
    resolves, and every plot silently regroups by whatever plate B's is, under
    a band still reading the old name. The wrong implementation this has to
    exclude is the one that clears *Group by* alone: the two decorations are on
    a separate roster and were the half that was missed.
    """
    from review.core.plot_state import PlotState

    st = PlotState()
    st.group_by_variable = "variable-1111"
    st.colour_by_variable = "variable-2222"
    st.split_by_variable = "variable-3333"
    st.sub_split_by_variable = "variable-4444"

    st.reset_for_new_dataset()

    assert st.group_by_variable == ""
    assert st.colour_by_variable == ""
    assert st.split_by_variable == ""
    assert st.sub_split_by_variable == ""
