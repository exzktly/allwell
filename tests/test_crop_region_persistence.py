"""crop_region persistence — a section of persistence.json (architecture §5.7).

The region is a normalized (row0, col0, row1, col1) tuple; clearing it removes
the section entirely.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from well_viewer.persistence import crop_region


def _app(tmp_path):
    ds = SimpleNamespace(data_dir=tmp_path, persistence_doc=None, crop_region=None)
    app = SimpleNamespace(_app_state=SimpleNamespace(dataset=ds))
    # _doc.get_section/set_section read ``ctx.state`` (A8); a minimal ctx over the app.
    app._ctx = SimpleNamespace(app=app, state=app._app_state)
    return app


def test_saves_into_persistence_json_section(tmp_path):
    app = _app(tmp_path)
    app._app_state.dataset.crop_region = (2, 4, 10, 20)
    crop_region.save_to_data_dir(app)
    pj = json.loads((tmp_path / "persistence.json").read_text())
    assert pj["crop_region"] == [2, 4, 10, 20]


def test_round_trips_through_the_section(tmp_path):
    app = _app(tmp_path)
    app._app_state.dataset.crop_region = (5, 6, 100, 120)
    crop_region.save_to_data_dir(app)
    app2 = _app(tmp_path)                                    # fresh cache
    crop_region.load_from_data_dir(app2)
    assert app2._app_state.dataset.crop_region == (5, 6, 100, 120)


def test_none_region_removes_the_section(tmp_path):
    app = _app(tmp_path)
    app._app_state.dataset.crop_region = (1, 2, 3, 4)
    crop_region.save_to_data_dir(app)
    assert "crop_region" in json.loads((tmp_path / "persistence.json").read_text())
    app._app_state.dataset.crop_region = None
    crop_region.save_to_data_dir(app)
    assert "crop_region" not in json.loads((tmp_path / "persistence.json").read_text())


def test_load_with_no_section_is_none(tmp_path):
    app = _app(tmp_path)
    crop_region.load_from_data_dir(app)
    assert app._app_state.dataset.crop_region is None


def test_load_ignores_malformed_section(tmp_path):
    (tmp_path / "persistence.json").write_text(json.dumps(
        {"schema_version": 1, "crop_region": [1, 2, 3]}))  # wrong length
    app = _app(tmp_path)
    crop_region.load_from_data_dir(app)
    assert app._app_state.dataset.crop_region is None
