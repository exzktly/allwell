"""Pure tests for persistence document deletion (the 'reset all saved state'
action). ``_doc.delete`` removes ``persistence.json`` for the active data dir and
clears the in-memory cache, leaving everything else on disk untouched.
"""

from __future__ import annotations

from types import SimpleNamespace


def _app(data_dir):
    return SimpleNamespace(
        _app_state=SimpleNamespace(
            dataset=SimpleNamespace(data_dir=str(data_dir) if data_dir else "",
                                    persistence_doc={"view_state": {"x": 1}}),
        )
    )


def test_delete_removes_file_and_clears_cache(tmp_path):
    from well_viewer.persistence import _doc

    pj = tmp_path / "persistence.json"
    pj.write_text('{"schema_version": 1, "view_state": {"x": 1}}')
    other = tmp_path / "pipeline_info.json"
    other.write_text("{}")

    app = _app(tmp_path)
    removed = _doc.delete(app)

    assert removed is True
    assert not pj.exists()                       # the doc is gone
    assert other.exists()                        # other dataset files untouched
    assert app._app_state.dataset.persistence_doc is None


def test_delete_no_file_is_noop(tmp_path):
    from well_viewer.persistence import _doc

    app = _app(tmp_path)                          # no persistence.json present
    assert _doc.delete(app) is False
    assert app._app_state.dataset.persistence_doc is None


def test_delete_no_data_dir_is_safe():
    from well_viewer.persistence import _doc

    app = _app(None)
    assert _doc.delete(app) is False
