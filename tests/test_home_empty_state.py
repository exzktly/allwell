"""Home before a dataset is open.

Home is the page Review launches on, so its no-dataset state is the first thing
the application shows. It used to build the whole page regardless — a masthead
reading "No dataset open" over an empty plate and a column of em-dashes, which
describes its own emptiness at length and says nothing about what to do.

It now shows the same placeholder every other Review page shows, through the
same registry, plus one button: the landing page is the one place where
pointing at a control in the rail and stopping is not good enough.

Fast lane — the placeholder widget and the action are testable without an
application. The stack's wiring into a real window is in ``test_home_page.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtTest import QTest

from widgets.empty_state import EmptyState


def _ctx(*, wells=None, browse=None):
    """A context with only what Home's action router and predicate read."""
    calls = []

    def _browse():
        calls.append("browse")

    window = SimpleNamespace(_browse=browse or _browse)
    ctx = SimpleNamespace(
        window=window,
        state=SimpleNamespace(
            dataset=SimpleNamespace(well_paths=wells or {}, data_dir=None,
                                    pipeline_info={}),
            selection=SimpleNamespace(selected_wells=set(), notes_text=""),
        ),
    )
    return ctx, calls


# ── the predicate ───────────────────────────────────────────────────────────

def test_a_folder_that_opened_but_yielded_no_wells_is_not_a_dataset():
    """``data_dir`` is set when a load *begins*; the wells arrive as it runs.

    Home's own empty state is flipped by the shared refresher, which tests the
    wells. A page testing something else would contradict the placeholder
    covering it — reporting on a dataset the placeholder says is not there.
    """
    from well_viewer.features.home import collect as hc

    ctx, _ = _ctx()
    ctx.state.dataset.data_dir = "/tmp/opened/but/empty"
    assert hc.has_dataset(ctx) is False
    assert hc.next_step(ctx)["action"] == "open"


def test_wells_are_what_makes_it_a_dataset():
    from well_viewer.features.home import collect as hc

    ctx, _ = _ctx(wells={"A01": None})
    assert hc.has_dataset(ctx) is True
    assert hc.next_step(ctx)["action"] != "open"


# ── the action ──────────────────────────────────────────────────────────────

def test_open_a_dataset_reaches_the_window_s_own_picker():
    """Regression: ``next_step`` has always offered ``action="open"``, and
    ``run_home_action`` had no branch for it — so the button did nothing."""
    from well_viewer.features.home.controller import run_home_action

    ctx, calls = _ctx()
    run_home_action(ctx, "open")
    assert calls == ["browse"], (
        "the Open action must reach the window's folder picker; "
        "run_home_action swallows exceptions, so a silent no-op looks like "
        "success unless the call itself is asserted")


def test_open_does_nothing_when_the_shell_has_no_picker():
    """A profile or a test harness may mount no such window."""
    from well_viewer.features.home.controller import run_home_action

    ctx, _ = _ctx()
    ctx.window = SimpleNamespace()
    run_home_action(ctx, "open")


# ── the placeholder's action button ─────────────────────────────────────────

def test_a_placeholder_has_no_button_until_one_is_asked_for(qtbot):
    """The twelve existing empty states describe their emptiness and name a
    control elsewhere. None of them grows a button because Home wanted one."""
    empty = EmptyState("Load a directory.", hint="The Open button is in the rail.")
    qtbot.addWidget(empty)
    assert empty._action.isVisible() is False
    assert empty._action.text() == ""


def test_the_action_fires_what_it_was_given(qtbot):
    fired = []
    empty = EmptyState("Load a directory.")
    qtbot.addWidget(empty)
    empty.show()
    empty.setAction("Open a dataset…", lambda: fired.append(1))

    assert empty._action.isVisible() is True
    assert empty._action.text() == "Open a dataset…"
    empty._action.click()
    assert fired == [1]


def test_setting_an_action_twice_does_not_fire_the_old_one(qtbot):
    first, second = [], []
    empty = EmptyState("Load a directory.")
    qtbot.addWidget(empty)
    empty.setAction("One", lambda: first.append(1))
    empty.setAction("Two", lambda: second.append(1))

    empty._action.click()
    assert first == [], "the replaced callback is still connected"
    assert second == [1]


def test_an_action_survives_a_reworded_placeholder(qtbot):
    """The shared refresher rewrites text, hint and icon on every pass. Home
    sets its button once at build time and must keep it."""
    from well_viewer.core.ui_helpers import NO_DATASET, _apply_reason
    from PySide6.QtWidgets import QStackedWidget, QWidget

    empty = EmptyState("something else")
    stack = QStackedWidget()
    qtbot.addWidget(stack)
    stack.addWidget(empty)
    stack.addWidget(QWidget())
    stack._empty_placeholder = empty
    stack._empty_default_icon = "image"
    stack.setCurrentIndex(1)

    fired = []
    empty.setAction("Open a dataset…", lambda: fired.append(1))
    _apply_reason(stack, NO_DATASET)

    assert stack.currentIndex() == 0
    assert empty._action.text() == "Open a dataset…"
    empty._action.click()
    assert fired == [1]


def test_clearing_the_action_takes_the_button_away(qtbot):
    fired = []
    empty = EmptyState("Load a directory.")
    qtbot.addWidget(empty)
    empty.setAction("Open a dataset…", lambda: fired.append(1))
    empty.clearAction()

    assert empty._action.isVisible() is False
    empty._action.click()
    assert fired == []


# ── what a shortfall offers ─────────────────────────────────────────────────

def _record(wells):
    return {"version": 1, "skipped": False, "partial_scan": False,
            "suspended": False, "wells": wells,
            "totals": {"wells": len(wells), "complete": 0, "partial": 0,
                       "missing_csv": 0, "no_output": 0, "intent": 0,
                       "covered_any": 0, "covered_csv": 0},
            "errors": {"files": 0, "by_type": {}}, "findings": []}


def _well(health, *, intent=8, covered=8, failed=()):
    return {"intent": intent, "covered_csv": covered, "covered_any": covered,
            "missing_count": intent - covered, "missing": [], "csv_state": "ok",
            "has_in": True, "has_out_zip": covered > 0, "has_out_folder": False,
            "failed": list(failed), "health": health}


def _ctx_with(wells):
    ctx, _ = _ctx(wells={w: None for w in wells})
    ctx.state.dataset.pipeline_info = {"run_check": _record(wells)}
    return ctx


def test_every_shortfall_offers_to_finish_the_run():
    """Sending the reader to a page that shows the gap again is not a next
    step. Each of these is fixed by running the pipeline over what is missing,
    and rebuilding the run's settings by hand is where the damage happens —
    a mismatched csv_prefix silently reprocesses the whole plate."""
    from well_viewer.features.home import collect as hc

    for wells in (
        {"A01": _well("complete", failed=["F001_0h"])},
        {"A01": _well("partial", covered=4)},
        {"A01": _well("missing", covered=8)},
        {"A01": _well("partial", covered=0)},
    ):
        step = hc.next_step(_ctx_with(wells))
        assert step["action"] == "complete", wells
        assert step["label"] == "Finish this run"


def test_only_the_wells_an_incremental_run_would_skip_are_forced():
    """A partial well is fixed by the incremental diff at a fraction of the cost
    of re-segmenting it. A well with images and no CSV has nothing missing by
    that diff, so it is skipped forever unless named."""
    from well_viewer.features.home import collect as hc

    ctx = _ctx_with({
        "A01": _well("complete"),
        "A02": _well("partial", covered=4),
        "A03": _well("missing", covered=8),
    })
    assert hc.wells_needing_force(ctx) == ["A03"]


def test_a_dataset_with_no_record_forces_nothing():
    from well_viewer.features.home import collect as hc

    ctx, _ = _ctx(wells={"A01": None})
    assert hc.wells_needing_force(ctx) == []


# ── what a Datasets tool is handed ──────────────────────────────────────────

def test_a_datasets_tool_is_given_the_project_folder_not_the_out_dir():
    """Review loads ``<root>/out``; every Datasets tool wants ``<root>``, the
    folder holding both ``in`` and ``out``. Handing over ``out/`` fails their
    check and leaves the user re-picking a folder the app already had open."""
    from pathlib import Path

    from well_viewer.features.home.controller import dataset_root

    ctx, _ = _ctx(wells={"A01": None})
    ctx.state.dataset.data_dir = Path("/data/plate7/out")
    ctx.state.dataset.in_dir = Path("/data/plate7/in")
    assert dataset_root(ctx) == "/data/plate7"


def test_a_flat_csv_folder_is_handed_over_as_itself():
    """No ``in_dir`` means the loader found no in/out layout, so there is no
    parent to prefer."""
    from pathlib import Path

    from well_viewer.features.home.controller import dataset_root

    ctx, _ = _ctx(wells={"A01": None})
    ctx.state.dataset.data_dir = Path("/data/loose_csvs")
    ctx.state.dataset.in_dir = None
    assert dataset_root(ctx) == "/data/loose_csvs"


# ── the recent-datasets list on the placeholder ─────────────────────────────
#
# The one moment this list matters most is launch with nothing loaded, which is
# exactly the moment Home's body is hidden behind the shared placeholder. So the
# rows live on the placeholder itself rather than Home growing a no-dataset
# state of its own — the shared wording and the shared moment of the swap are
# what ``core/empty_state_copy.py`` exists to protect.


class _Settings:
    """``QSettings``' two methods, over a dict, so no test writes to the machine."""

    def __init__(self, initial=None):
        self._d = dict(initial or {})

    def value(self, key, default=None):
        return self._d.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's spelling
        self._d[key] = value


def _ctx_with_recents(paths, *, data_dir=None, loaded=None):
    """A context carrying a settings stub already holding *paths*."""
    from well_viewer.core.recent_datasets import record_open

    settings = _Settings()
    for i, path in enumerate(paths):
        record_open(settings, str(path), Path(path).name, float(i))
    ctx, calls = _ctx()
    ctx.window._qsettings = lambda: settings
    ctx.window._load_path = loaded or (lambda p: calls.append(p))
    ctx.state.dataset.data_dir = data_dir
    return ctx, calls


def test_the_placeholder_offers_the_datasets_that_are_there(qtbot, tmp_path):
    from well_viewer.features.home.controller import _recent_entry_rows

    a, b = tmp_path / "plate_a", tmp_path / "plate_b"
    a.mkdir()
    b.mkdir()
    ctx, _ = _ctx_with_recents([a, b])

    rows = _recent_entry_rows(ctx, 5)
    assert [r[0] for r in rows] == ["plate_b", "plate_a"], "newest is not first"
    assert all(r[3] for r in rows), "a folder that exists came back disabled"


def test_a_dataset_whose_drive_is_gone_is_shown_and_disabled(tmp_path):
    """Not hidden. Dropping the row tells the user their history was lost, when
    what happened is that a drive is unplugged — and the row must come back by
    itself when it is plugged in again."""
    from well_viewer.features.home.controller import _recent_entry_rows

    here = tmp_path / "mounted"
    here.mkdir()
    gone = tmp_path / "unmounted"
    ctx, _ = _ctx_with_recents([here, gone])

    rows = _recent_entry_rows(ctx, 5)
    assert [r[0] for r in rows] == ["unmounted", "mounted"], (
        "the unreachable folder was dropped from the list"
    )
    assert rows[0][3] is False and rows[1][3] is True
    assert "not reachable" in rows[0][4], "the disabled row says nothing about why"
    assert rows[0][2] is None, "an unreachable row still carries a click handler"


def test_the_open_dataset_is_not_offered_back_to_itself(tmp_path):
    """The card sits inside the dataset it would offer to reopen, and the
    in/out layout is where a naive string comparison misses: the list stores the
    folder Review was pointed at, and ``data_dir`` is the ``out`` inside it."""
    from well_viewer.features.home.controller import _recent_entry_rows

    root = tmp_path / "plate_a"
    (root / "out").mkdir(parents=True)
    other = tmp_path / "plate_b"
    other.mkdir()
    ctx, _ = _ctx_with_recents([root, other], data_dir=root / "out")

    rows = _recent_entry_rows(ctx, 5, exclude=ctx.state.dataset.data_dir)
    assert [r[0] for r in rows] == ["plate_b"]


def test_clicking_a_recent_row_opens_it_by_the_browsed_route(qtbot, tmp_path):
    """Asserted at the boundary: what reaches ``_load_path``, not what the row
    computed. A row that renders correctly and calls nothing is the failure this
    guards — §7.6, a control that exists is not a control that works."""
    from well_viewer.features.home.controller import _recent_entry_rows

    root = tmp_path / "plate_a"
    root.mkdir()
    opened = []
    ctx, _ = _ctx_with_recents([root], loaded=lambda p: opened.append(p))

    empty = EmptyState("Load a directory.")
    qtbot.addWidget(empty)
    empty.setEntries(_recent_entry_rows(ctx, 5))

    button = empty._entries_layout.itemAt(0).widget()
    assert button.text().startswith("plate_a")
    button.click()
    # ``open_recent`` defers by 50 ms, the same deferral the folder picker uses.
    qtbot.waitUntil(lambda: bool(opened), timeout=2000)
    assert opened == [root]


def test_a_disabled_row_does_nothing_when_clicked(qtbot, tmp_path):
    from well_viewer.features.home.controller import _recent_entry_rows

    opened = []
    ctx, _ = _ctx_with_recents([tmp_path / "gone"], loaded=lambda p: opened.append(p))
    empty = EmptyState("Load a directory.")
    qtbot.addWidget(empty)
    empty.setEntries(_recent_entry_rows(ctx, 5))

    # Without this the check is vacuous: a row that was dropped rather than
    # disabled leaves nothing to click, and "nothing was opened" is then true
    # for the wrong reason.
    assert empty._entries_layout.count() == 1, (
        "the unreachable folder left no row at all, so this proves nothing "
        "about what clicking a disabled one does"
    )
    button = empty._entries_layout.itemAt(0).widget()
    assert button.isEnabled() is False
    button.click()
    QTest.qWait(120)
    assert opened == []


def test_a_placeholder_has_no_entries_until_it_is_given_some(qtbot):
    empty = EmptyState("Load a directory.")
    qtbot.addWidget(empty)
    assert empty._entries.isVisible() is False
    assert empty._entries_layout.count() == 0


def test_setting_entries_twice_replaces_rather_than_appends(qtbot):
    fired = []
    empty = EmptyState("Load a directory.")
    qtbot.addWidget(empty)
    empty.setEntries([("one", "", lambda: fired.append("one"), True, "")])
    empty.setEntries([("two", "", lambda: fired.append("two"), True, "")])

    assert empty._entries_layout.count() == 1
    empty._entries_layout.itemAt(0).widget().click()
    assert fired == ["two"], "the replaced row is still connected"


def test_an_empty_sequence_takes_the_column_away(qtbot):
    empty = EmptyState("Load a directory.")
    qtbot.addWidget(empty)
    empty.show()
    empty.setEntries([("one", "", lambda: None, True, "")])
    assert empty._entries.isVisible() is True
    empty.setEntries([])
    assert empty._entries.isVisible() is False
    assert empty._entries_layout.count() == 0


def test_a_settings_backend_that_raises_costs_the_list_and_nothing_else():
    """The dataset is already loaded and on screen by the time this runs."""
    from well_viewer.features.home.controller import recent_datasets

    class _Broken:
        def value(self, key, default=None):
            raise OSError("settings file is unreadable")

    ctx, _ = _ctx()
    ctx.window._qsettings = lambda: _Broken()
    assert recent_datasets(ctx) == []
