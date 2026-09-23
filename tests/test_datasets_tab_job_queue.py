"""Smoke tests for DatasetsTab's wiring: the page builds with every tool
controller plus a fake Analyze registered, "+ Queue" on a tool's page lands a
job the Job Queue page can see, and the left rail carries the tools and
nothing else.

Offscreen, no real pipeline or worker execution — this only checks the
registration/wiring datasets/tab.py and datasets/job_queue/controller.py do,
not any tool's actual run behaviour (covered by each feature's own tests).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal

from datasets.tab import DatasetsTab


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _FakeAnalyzeController(QObject):
    """Duck-types datasets.job_queue.controller.QueueableController plus the
    collect_spec() hook AnalyzeRunMixin adds — standing in for AnalyzeTab so
    this test doesn't need a real one built."""

    jobFinished = Signal(bool, str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.started_specs: list[dict] = []
        self.queue_sink = None
        self.hydrated: list[dict] = []

    def collect_spec(self) -> dict:
        return {"opts": {"raw": "/data/plate7"}}

    def start_from_queue(self, spec: dict) -> None:
        self.started_specs.append(spec)

    def show_job(self, spec: dict) -> None:
        pass

    def hydrate_job(self, spec: dict) -> None:
        """The real AnalyzeTab has one; the queue prefers it over show_job."""
        self.hydrated.append(spec)

    def set_queue_sink(self, sink) -> None:
        self.queue_sink = sink

    def stop(self) -> None:
        pass

    def is_busy(self) -> bool:
        return False


def _make_tab(tmp_path, *, analyze=True):
    fake_analyze = _FakeAnalyzeController() if analyze else None
    tab = DatasetsTab(
        analyze_controller=fake_analyze,
        queue_state_dir=tmp_path,
    )
    return tab, fake_analyze


def test_builds_a_page_for_every_declared_feature(qapp, tmp_path):
    """One built page per ``_FEATURES`` entry, derived rather than counted.

    This asserted a hand-written 7, which is a roster of one number: it had to
    be edited by whoever added the eighth page, and the only thing telling them
    so was a failure that reads like their page is broken. Taken from the
    feature list, a page that fails to build is still caught and a page that is
    merely *new* is covered by having been declared.
    """
    from datasets.tab import _FEATURES

    tab, _ = _make_tab(tmp_path)
    try:
        assert tab._pages.count() == len(_FEATURES)
        built = [tab._pages.widget(i) for i in range(tab._pages.count())]
        assert all(page is not None for page in built)
    finally:
        tab.shutdown()


def test_job_queue_controller_has_all_kinds_registered(qapp, tmp_path):
    """Every queueable feature reaches the bridge, plus the injected Analyze.

    Derived from ``_FEATURES`` for the same reason as the count above: a tool
    whose page builds but whose kind never registers is a Run button that
    works and a Queue button that silently does nothing.
    """
    from datasets.tab import _FEATURES

    tab, fake_analyze = _make_tab(tmp_path)
    try:
        jq = tab._controllers["job_queue"]
        kinds = [feat.kind for feat in _FEATURES if feat.kind] + ["analyze"]
        assert "czi_import" in kinds      # the roster is not empty by accident
        for kind in kinds:
            assert kind in jq.bridge._runners
    finally:
        tab.shutdown()


def test_add_to_queue_from_a_tool_lands_a_job_in_the_bridge(qapp, tmp_path):
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("concatenate", {"dataset1": "/a", "dataset2": "/b", "destination": "/c"})
        jq = tab._controllers["job_queue"]
        jobs = jq.bridge.queue.jobs
        assert len(jobs) == 1
        assert jobs[0].kind == "concatenate"
        assert jobs[0].spec["destination"] == "/c"
    finally:
        tab.shutdown()


def test_analyze_s_own_queue_button_lands_a_job(qapp, tmp_path):
    """Analyze's page gained its own "+ Queue", wired through a sink the
    Datasets side injects — so neither module imports the other."""
    tab, fake_analyze = _make_tab(tmp_path)
    try:
        assert fake_analyze.queue_sink is not None
        fake_analyze.queue_sink(fake_analyze.collect_spec())
        jobs = tab._controllers["job_queue"].bridge.queue.jobs
        assert len(jobs) == 1
        assert jobs[0].kind == "analyze"
        assert jobs[0].spec["opts"]["raw"] == "/data/plate7"
    finally:
        tab.shutdown()


def test_a_queued_job_is_named_after_what_it_runs(qapp, tmp_path):
    """Every row used to read the bare tool name, so eight queued subsets were
    indistinguishable and picking the right one to remove was guesswork."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/d/plateA", "destination": "/d/plateA_sub"})
        job = tab._controllers["job_queue"].bridge.queue.jobs[0]
        assert job.label == "Subset — plateA → plateA_sub"
    finally:
        tab.shutdown()


def test_starting_queue_runs_the_queued_analyze_job(qapp, tmp_path):
    tab, fake_analyze = _make_tab(tmp_path)
    try:
        jq = tab._controllers["job_queue"]
        jq.add_job("analyze", {"opts": {"raw": "/data/plate7"}})
        jq.page.startToggled.emit()
        assert len(fake_analyze.started_specs) == 1
    finally:
        tab.shutdown()


def test_without_an_analyze_controller_the_analyze_entry_is_a_no_op(qapp, tmp_path):
    """A profile build can drop Analyze entirely; the menu entry must refuse
    rather than raise."""
    tab, _ = _make_tab(tmp_path, analyze=False)
    try:
        jq = tab._controllers["job_queue"]
        assert jq.begin_new_job("analyze") is False
        assert jq.bridge.queue.jobs == []
    finally:
        tab.shutdown()


# ── edit / new-job sessions ──────────────────────────────────────────────────

def test_editing_a_job_opens_its_page_with_a_banner(qapp, tmp_path):
    """The whole point of the banner: without it the tool's page looks exactly
    as it always does, and there is no way to tell you are editing a queued
    job rather than setting up a fresh run."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/d/a", "destination": "/d/b"})
        jq = tab._controllers["job_queue"]
        job_id = jq.bridge.queue.jobs[0].id

        assert jq.begin_edit(job_id) is True
        assert jq.session() == ("subset", job_id)
        assert tab._session_banner is not None
        assert "Editing queued job" in tab._session_banner.text()
        # And the rail actually went there — a banner on a page the user
        # cannot see is no better than no banner.
        assert tab._pages.currentName() == "Subset / Split"
    finally:
        tab.shutdown()


def test_saving_an_edit_writes_back_to_the_same_job(qapp, tmp_path):
    """Not a second job appended at the tail, which is what the old
    remove-and-re-add workflow produced."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/d/a", "destination": "/d/b"})
        jq = tab._controllers["job_queue"]
        job_id = jq.bridge.queue.jobs[0].id
        jq.begin_edit(job_id)

        subset_page = tab._controllers["subset"].page
        subset_page.set_destination_path("/d/edited")
        tab._save_session()

        jobs = jq.bridge.queue.jobs
        assert len(jobs) == 1
        assert jobs[0].id == job_id
        assert jobs[0].spec["destination"] == "/d/edited"
        # The name follows the spec it describes.
        assert jobs[0].label.endswith("→ edited")
        assert tab._session_banner is None
    finally:
        tab.shutdown()


def test_cancelling_an_edit_leaves_the_job_alone(qapp, tmp_path):
    """Cancel goes through the controller, not just the banner: tearing the
    strip down on its own would leave a session the controller still believed
    in and would happily save later."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/d/a", "destination": "/d/b"})
        jq = tab._controllers["job_queue"]
        job_id = jq.bridge.queue.jobs[0].id
        jq.begin_edit(job_id)
        tab._controllers["subset"].page.set_destination_path("/d/edited")

        tab._cancel_session()
        assert jq.session() is None
        assert jq.bridge.queue.jobs[0].spec["destination"] == "/d/b"
        assert tab._session_banner is None
    finally:
        tab.shutdown()


def test_a_new_job_session_adds_rather_than_updates(qapp, tmp_path):
    tab, _ = _make_tab(tmp_path)
    try:
        jq = tab._controllers["job_queue"]
        assert jq.begin_new_job("validate") is True
        assert jq.session() == ("validate", None)
        assert "new Validate job" in tab._session_banner.text()

        tab._controllers["validate"].page.set_dataset_path("/d/plate7")
        tab._save_session()

        jobs = jq.bridge.queue.jobs
        assert len(jobs) == 1
        assert jobs[0].kind == "validate"
        assert jobs[0].spec["dataset"] == "/d/plate7"
    finally:
        tab.shutdown()


def test_a_session_is_abandoned_when_its_job_starts_running(qapp, tmp_path):
    """Saving it then would write over a spec already handed to a runner."""
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    fake_analyze = _FakeAnalyzeController()
    # An Analyze session needs a form to put its banner on and a shell to
    # switch panes; without them it is refused rather than opened unseen.
    form_host = QWidget()
    fake_analyze._form_layout = QVBoxLayout(form_host)
    tab = DatasetsTab(analyze_controller=fake_analyze, queue_state_dir=tmp_path,
                      on_reveal_analyze=lambda: True)
    try:
        jq = tab._controllers["job_queue"]
        jq.add_job("analyze", {"opts": {"raw": "/data/plate7"}})
        job_id = jq.bridge.queue.jobs[0].id
        jq.begin_edit(job_id)
        assert jq.session() is not None

        jq.page.startToggled.emit()          # the job is now running
        assert jq.session() is None
        assert tab._session_banner is None
    finally:
        tab.shutdown()


def test_editing_is_refused_while_that_tool_has_its_own_run_going(qapp, tmp_path):
    """show_job writes fields the controller drives as its progress display,
    and set_running's locks only block user clicks, not that path."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/d/a", "destination": "/d/b"})
        jq = tab._controllers["job_queue"]
        job_id = jq.bridge.queue.jobs[0].id

        subset = tab._controllers["subset"]
        subset.is_busy = lambda: True
        assert jq.begin_edit(job_id) is False
        assert jq.session() is None
        assert tab._session_banner is None
    finally:
        tab.shutdown()


def test_shutdown_does_not_raise(qapp, tmp_path):
    tab, _ = _make_tab(tmp_path)
    tab.shutdown()  # must not raise even with nothing running


def _notices(jq):
    """Capture the controller's user-facing refusals.

    Patched at the _notify seam rather than at Toast: a real Toast carries a
    fade animation and a dismiss timer, neither of which a test wants running.
    """
    seen = []
    jq._notify = lambda text, kind="warn": seen.append((kind, text))
    return seen


def test_clicking_another_row_while_editing_is_refused(qapp, tmp_path):
    """Regression: it silently rewrote the job under edit.

    Preview writes the clicked job's fields straight into the tool's page —
    the same page the session has open — so the next Save collected *that*
    job's settings into the one being edited, turning it into a duplicate.
    Nothing about the queue changes, so the stale-session check never fired.
    """
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/A", "destination": "/A_sub"})
        tab._add_to_queue("subset", {"source": "/B", "destination": "/B_sub"})
        jq = tab._controllers["job_queue"]
        a, b = [j.id for j in jq.bridge.queue.jobs]
        jq.begin_edit(a)
        notices = _notices(jq)

        jq.page.showRequested.emit(b)
        assert notices, "the refusal has to be visible"
        tab._save_session()

        saved = jq.bridge.queue.get(a)
        assert saved.spec["source"] == "/A", "job A must not have become job B"
    finally:
        tab.shutdown()


def test_starting_a_second_session_says_the_first_was_dropped(qapp, tmp_path):
    """Silently overwriting it discarded whatever the user had typed on the
    other page, with the banner vanishing from a page they had left."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/A", "destination": "/A_sub"})
        tab._add_to_queue("subset", {"source": "/B", "destination": "/B_sub"})
        jq = tab._controllers["job_queue"]
        a, b = [j.id for j in jq.bridge.queue.jobs]
        jq.begin_edit(a)
        notices = _notices(jq)
        jq.begin_edit(b)
        assert any("without saving" in text for _kind, text in notices)
        assert jq.session() == ("subset", b)
    finally:
        tab.shutdown()


def test_editing_a_finished_job_says_it_will_not_rerun(qapp, tmp_path):
    """update_spec deliberately leaves state alone, so saving changes what the
    job says it will do without putting it back in the queue. Discovering that
    after pressing Save is too late."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/A", "destination": "/A_sub"})
        jq = tab._controllers["job_queue"]
        job_id = jq.bridge.queue.jobs[0].id
        jq.bridge.queue.mark_running(job_id)
        jq.bridge.queue.mark_finished(job_id, ok=True)

        jq.begin_edit(job_id)
        assert "Retry" in tab._session_banner.text()
    finally:
        tab.shutdown()


def test_clearing_completed_warns_about_the_jobs_it_unlinks(qapp, tmp_path):
    """A survivor chained to a cleared job loses its link — it has to, or it
    could never run — and then falls back to whatever placeholder its own spec
    still holds. Silence made a tidy-up look free."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/A", "destination": "/A_sub"})
        tab._add_to_queue("validate", {"dataset": "/PLACEHOLDER"})
        jq = tab._controllers["job_queue"]
        a, v = [j.id for j in jq.bridge.queue.jobs]
        jq.bridge.set_chain(v, a)
        jq.bridge.queue.mark_running(a)
        jq.bridge.queue.mark_finished(a, ok=True, produced={"dataset_root": "/A_sub"})

        notices = _notices(jq)
        jq.page.clearCompletedRequested.emit()
        assert any("waiting on a job you cleared" in text for _kind, text in notices)
        assert jq.bridge.queue.get(v).chain_from is None
    finally:
        tab.shutdown()


def test_the_session_banner_uses_a_real_glyph(qapp, tmp_path):
    """make_pixmap substitutes alert-triangle for an unknown name, so the edit
    banner shipped with a warning triangle on it."""
    from widgets.icons import has_glyph

    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {"source": "/A", "destination": "/A_sub"})
        jq = tab._controllers["job_queue"]
        jq.begin_edit(jq.bridge.queue.jobs[0].id)
        assert tab._session_banner is not None
        import inspect

        from widgets.context_banner import ContextBanner

        default = inspect.signature(ContextBanner.__init__).parameters["icon"].default
        assert has_glyph(default), f"banner glyph {default!r} falls back to a warning triangle"
    finally:
        tab.shutdown()


def test_editing_a_job_whose_page_cannot_show_it_says_so(qapp, tmp_path):
    """Regression: a Subset job whose source has moved lost its filters.

    Subset builds its timepoint and FOV checkboxes by reading the source
    dataset. If that dataset is missing, no boxes exist, `all_timepoints_
    selected()` answers "yes" over an empty list, and collecting the page back
    turns a one-timepoint subset into every timepoint — a widening, invisible
    in the diff of a job the user thought they were leaving alone.

    The session collects the page it just populated and reports anything that
    was in the spec and is now gone, so this is not a per-tool patch.
    """
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("subset", {
            "source": str(tmp_path / "gone"), "destination": "/x",
            "wells": ["A1"], "timepoint_labels": ["T1"], "fovs": ["1"],
        })
        jq = tab._controllers["job_queue"]
        notices = _notices(jq)
        jq.begin_edit(jq.bridge.queue.jobs[0].id)

        assert notices, "a silent widening is the thing this guards"
        text = notices[0][1]
        assert "timepoint_labels" in text and "fovs" in text
    finally:
        tab.shutdown()


def test_a_job_its_page_can_show_reports_no_loss(qapp, tmp_path):
    """The guard must not cry wolf on the ordinary case, or it teaches the
    user to ignore it."""
    tab, _ = _make_tab(tmp_path)
    try:
        tab._add_to_queue("validate", {"dataset": "/d/plate7", "apply_repair": False})
        jq = tab._controllers["job_queue"]
        notices = _notices(jq)
        jq.begin_edit(jq.bridge.queue.jobs[0].id)
        assert notices == []
    finally:
        tab.shutdown()


def test_current_help_page_names_the_selected_tool(qapp, tmp_path):
    """What the help drawer asks the pane so it can open on that tool's section
    of the manual. The rail key is the label is the help-topic key — there is no
    second name map, which is the point."""
    from ui import help_topics

    tab, _ = _make_tab(tmp_path)
    try:
        # Whatever the rail opens on, it must be a page the map knows.
        opening = tab.current_help_page()
        assert opening in help_topics.DATASETS_PAGE_HELP, opening

        # Drive the rail alone. Setting the page stack here as well would make
        # the assertion read back what the test itself wrote; the rail's own
        # currentChanged is what moves the stack in the app, so letting it do
        # that is the thing worth checking.
        for name in ("Merge Plates", "Job Queue", "Validate & Repair"):
            tab._nav.setCurrentKey(name)
            assert tab.current_help_page() == name
            assert tab._pages.currentName() == name, "the rail did not move the page"
            assert name in help_topics.DATASETS_PAGE_HELP
    finally:
        tab.shutdown()


def test_current_help_page_is_safe_before_the_rail_exists(qapp, tmp_path):
    """Partial construction must not take the drawer down with it."""
    import types

    from datasets.tab import DatasetsTab as _DT

    stub = types.SimpleNamespace()
    assert _DT.current_help_page(stub) == ""


# ── the left rail ────────────────────────────────────────────────────────────

def test_the_rail_lists_the_tools_and_carries_no_caption_above_them(qapp,
                                                                    tmp_path):
    """The left column is the tool list, with nothing restating the pane.

    It carried a "DATASETS" caption above the rail, under a comment claiming
    it matched Review's sidebar. Review has no such thing — its rail head is
    the *dataset control*, and its own comment records why: "the rail's items
    are self-evidently sections". The same is true of these, the pane is
    already named by the title bar's mode switcher, and Analyze has no caption
    either, so the label restated the one thing on screen that was never in
    doubt while taking a row from the list it sat above.

    Asserted as the exact set of label texts rather than as "no label says
    DATASETS", because the second passes for a caption reading anything else.
    """
    from PySide6.QtWidgets import QLabel

    from datasets.tab import _FEATURES

    tab, _fake = _make_tab(tmp_path)
    try:
        texts = [w.text().strip()
                 for w in tab._left_sidebar.findChildren(QLabel)
                 if w.text().strip()]
        assert texts == [feat.name for feat in _FEATURES]
    finally:
        tab.shutdown()


def test_no_pane_rail_carries_a_caption_naming_its_own_pane(qapp, tmp_path):
    """The rule, rather than the one instance of it.

    A caption naming the pane is redundant in every pane for the same reason,
    so this is stated against all of them instead of against Datasets alone —
    the shape CLAUDE.md asks for when a roster would otherwise be written by
    hand and go stale on the next pane.
    """
    from PySide6.QtWidgets import QLabel

    tab, _fake = _make_tab(tmp_path)
    try:
        shouting = [w.text().strip()
                    for w in tab._left_sidebar.findChildren(QLabel)
                    if w.text().strip().upper() in {"DATASETS", "ANALYZE",
                                                    "REVIEW"}]
        assert not shouting, f"the rail restates its own pane: {shouting}"
    finally:
        tab.shutdown()
