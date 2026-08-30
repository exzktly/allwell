"""The recent-datasets store: capping, dedupe, ordering, and a settings file that lies.

Every one of these is a pure-list property, which is the reason the store takes
its settings object as an argument instead of building a ``QSettings``. A stub
dict is enough to drive the whole module, and no test writes to the machine it
runs on.

The malformed-input cases are not defensive decoration. ``QSettings`` is a
user-writable file that survives across app versions and arrives without a
schema, so it is the one input in the app whose shape nothing upstream
guarantees — and the cost of raising on it is the window failing to open.
"""

from __future__ import annotations

import json

import pytest

from well_viewer.core import recent_datasets as rd


class _Settings:
    """The two methods the store uses, over a plain dict."""

    def __init__(self, initial=None):
        self._d = dict(initial or {})

    def value(self, key, default=None):
        return self._d.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's spelling
        self._d[key] = value


def _entry(path, name="ds", at=1.0):
    return rd.RecentDataset(path=path, name=name, opened_at=at)


# ── ordering, dedupe, capping ───────────────────────────────────────────────

def test_the_dataset_just_opened_is_first():
    out = rd.remember([_entry("/a"), _entry("/b")], "/c", "C", 9.0)
    assert [e.path for e in out] == ["/c", "/a", "/b"]


def test_reopening_a_dataset_moves_it_rather_than_duplicating_it():
    out = rd.remember([_entry("/a"), _entry("/b")], "/b", "B", 9.0)
    assert [e.path for e in out] == ["/b", "/a"]


def test_the_reopened_entry_takes_the_new_name_and_time(tmp_path):
    """A renamed folder is offered under the name it has now, not the one it had."""
    out = rd.remember([_entry(str(tmp_path), name="old", at=1.0)],
                      str(tmp_path), "new", 42.0)
    assert (out[0].name, out[0].opened_at) == ("new", 42.0)


def test_two_spellings_of_one_path_are_one_entry(tmp_path):
    """The file dialog gives an absolute path; a CLI argument may not.

    Deduping on the raw string would put the same dataset in the list twice,
    and the user would have no way to tell the rows apart.
    """
    nested = tmp_path / "plate" / "out"
    nested.mkdir(parents=True)
    weird = tmp_path / "plate" / ".." / "plate" / "out"
    entries = rd.remember([], str(nested), "plate", 1.0)
    entries = rd.remember(entries, str(weird), "plate", 2.0)
    assert len(entries) == 1, [e.path for e in entries]


def test_the_list_is_capped_and_the_new_entry_survives_the_cap():
    entries = []
    for i in range(rd.MAX_RECENTS + 5):
        entries = rd.remember(entries, f"/d{i}", f"d{i}", float(i))
    assert len(entries) == rd.MAX_RECENTS
    assert entries[0].path == f"/d{rd.MAX_RECENTS + 4}", (
        "the cap dropped from the front, so the dataset the user just opened "
        "is the one that did not make the list"
    )


def test_forget_removes_by_resolved_path(tmp_path):
    nested = tmp_path / "plate"
    nested.mkdir()
    entries = rd.remember([], str(nested), "plate", 1.0)
    assert rd.forget(entries, str(tmp_path / "plate" / ".." / "plate")) == []


# ── the settings value is untrusted ─────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    None,                      # never written
    "",                        # written empty
    "   ",
    b"not a string",           # QSettings can hand back bytes
    17,                        # or an int, on a backend that typed it
    "{not json",
    '"a string, validly encoded"',
    "{}",                      # JSON, but not a list
    "[1, 2, 3]",               # a list, but not of entries
    '[{"name": "no path"}]',
    '[{"path": ""}]',
])
def test_an_unreadable_setting_is_an_empty_list_and_never_an_exception(raw):
    assert rd.decode(raw) == []


def test_one_malformed_entry_does_not_erase_the_others():
    raw = json.dumps([
        {"path": "/a", "name": "A", "opened_at": 1.0},
        {"path": 42},
        {"path": "/b", "name": "B", "opened_at": 2.0},
    ])
    assert [e.path for e in rd.decode(raw)] == ["/a", "/b"]


def test_a_missing_timestamp_reads_as_zero_rather_than_dropping_the_entry():
    """The entry is what the user wants back; the timestamp only orders it."""
    out = rd.decode(json.dumps([{"path": "/a", "name": "A"}]))
    assert len(out) == 1 and out[0].opened_at == 0.0


def test_a_boolean_is_not_accepted_as_a_timestamp():
    """``isinstance(True, int)`` is True in Python, which is how a stray flag
    becomes the year 1970 plus one second without anyone noticing."""
    out = rd.decode(json.dumps([{"path": "/a", "name": "A", "opened_at": True}]))
    assert out[0].opened_at == 0.0


def test_the_round_trip_is_exact():
    entries = [_entry("/a", "A", 1.5), _entry("/b", "B", 2.5)]
    assert rd.decode(rd.encode(entries)) == entries


# ── the settings-backed calls ───────────────────────────────────────────────

def test_record_open_reads_writes_and_returns_the_same_list():
    settings = _Settings()
    rd.record_open(settings, "/a", "A", 1.0)
    returned = rd.record_open(settings, "/b", "B", 2.0)
    assert [e.path for e in returned] == ["/b", "/a"]
    assert [e.path for e in rd.load(settings)] == ["/b", "/a"], (
        "the returned list and the stored list disagree, so a caller that "
        "trusts the return value draws something the next launch will not"
    )


def test_load_on_a_settings_object_that_has_never_been_written():
    assert rd.load(_Settings()) == []


# ── what the load controller records ────────────────────────────────────────
#
# Asserted at the boundary: what reaches the settings object, not what
# ``load_path`` computed. The store can be perfect and the list still stay empty
# if nothing ever calls it, which is the whole of what these two check.

def _load_ctx(tmp_path, *, wells, data_dir):
    from types import SimpleNamespace

    settings = _Settings()
    return SimpleNamespace(
        window=SimpleNamespace(_qsettings=lambda: settings),
        state=SimpleNamespace(
            dataset=SimpleNamespace(well_paths=wells, data_dir=data_dir),
        ),
    ), settings


def test_a_load_that_found_wells_is_remembered(tmp_path):
    from well_viewer.core.load_controller import _remember_if_it_loaded

    # Both halves, because that is the structure ``load_path`` requires before
    # it descends into ``out`` and the structure ``dataset_display_name`` looks
    # for before it names the root. A fixture with only ``out/`` describes a
    # flat dataset that happens to be called "out", and would test the other
    # branch while looking like this one.
    root = tmp_path / "plate"
    (root / "out").mkdir(parents=True)
    (root / "in").mkdir()
    ctx, settings = _load_ctx(tmp_path, wells={"A01": root / "out" / "a.csv"},
                              data_dir=root / "out")
    _remember_if_it_loaded(ctx, root)

    stored = rd.load(settings)
    assert [e.path for e in stored] == [str(root)], (
        "the path stored is not the one the user pointed Review at, so "
        "reopening from the list takes a different branch through load_path"
    )
    assert stored[0].name == "plate", (
        "the in/out layout stored the literal folder name 'out' rather than "
        "the dataset root's, which is what dataset_display_name exists to fix"
    )


def test_a_folder_that_yielded_no_wells_is_not_remembered(tmp_path):
    """A wrong directory, or an out/ the pipeline never wrote to.

    Offering it back on the next launch would make this list a record of the
    user's mistakes rather than of their work.
    """
    from well_viewer.core.load_controller import _remember_if_it_loaded

    ctx, settings = _load_ctx(tmp_path, wells={}, data_dir=tmp_path / "empty")
    _remember_if_it_loaded(ctx, tmp_path / "empty")
    assert rd.load(settings) == []


def test_a_settings_backend_that_will_not_write_does_not_cost_the_load(tmp_path):
    """The dataset is loaded and on screen by the time this runs."""
    from types import SimpleNamespace

    from well_viewer.core.load_controller import _remember_if_it_loaded

    class _Broken:
        def value(self, key, default=None):
            return ""

        def setValue(self, key, value):  # noqa: N802 - Qt's spelling
            raise OSError("read-only settings volume")

    ctx = SimpleNamespace(
        window=SimpleNamespace(_qsettings=lambda: _Broken()),
        state=SimpleNamespace(
            dataset=SimpleNamespace(well_paths={"A01": tmp_path / "a.csv"},
                                    data_dir=tmp_path),
        ),
    )
    _remember_if_it_loaded(ctx, tmp_path)  # must not raise
