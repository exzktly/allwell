"""crop_region persistence — a section of persistence.json (architecture §5.7).

The section holds the normalized (row0, col0, row1, col1) region and the frames
it is scoped to; clearing the region removes the section entirely. A real
``DatasetState`` stands in for the dataset half, so the scope fields and their
reset behave exactly as they do in the app.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from well_viewer.core.dataset_state import DatasetState
from well_viewer.persistence import crop_region


def _app(tmp_path):
    ds = DatasetState(data_dir=tmp_path)
    app = SimpleNamespace(_app_state=SimpleNamespace(dataset=ds))
    app.window = app; app.state = app._app_state  # mock is its own ctx (B4)
    # _doc.get_section/set_section read ``ctx.state`` (A8); a minimal ctx over the app.
    app._ctx = SimpleNamespace(app=app, state=app._app_state)
    return app


def test_saves_into_persistence_json_section(tmp_path):
    app = _app(tmp_path)
    app._app_state.dataset.crop_region = (2, 4, 10, 20)
    crop_region.save_to_data_dir(app)
    pj = json.loads((tmp_path / "persistence.json").read_text())
    assert pj["crop_region"]["region"] == [2, 4, 10, 20]
    # An un-armed scope is written as three nulls, not as an omitted key: the
    # loader has to tell "every frame" from "a scope that failed to save".
    assert pj["crop_region"]["scope"] == {"wells": None, "fovs": None, "tps": None}


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


def test_scope_round_trips_with_the_region(tmp_path):
    """The scope is what a saved crop *means*. Dropping it on reload would widen
    a two-well crop to the whole plate and move every number downstream."""
    app = _app(tmp_path)
    ds = app._app_state.dataset
    ds.crop_region = (5, 6, 100, 120)
    ds.crop_scope_wells = {"B02", "A01"}
    ds.crop_scope_tps = {"24"}
    crop_region.save_to_data_dir(app)
    app2 = _app(tmp_path)
    crop_region.load_from_data_dir(app2)
    ds2 = app2._app_state.dataset
    assert ds2.crop_scope_wells == {"A01", "B02"}
    assert ds2.crop_scope_fovs is None      # un-armed stays un-armed: all FOVs
    assert ds2.crop_scope_tps == {"24"}


def test_legacy_bare_region_loads_as_an_unscoped_crop(tmp_path):
    """Sections written before the crop had a scope are a bare list, and they
    meant every frame — which is what an un-armed scope means."""
    (tmp_path / "persistence.json").write_text(json.dumps(
        {"schema_version": 1, "crop_region": [1, 2, 3, 4]}))
    app = _app(tmp_path)
    crop_region.load_from_data_dir(app)
    ds = app._app_state.dataset
    assert ds.crop_region == (1, 2, 3, 4)
    assert (ds.crop_scope_wells, ds.crop_scope_fovs, ds.crop_scope_tps) == (
        None, None, None)


def test_malformed_scope_widens_rather_than_narrows(tmp_path):
    """A scope that cannot be read must fail to the documented default (every
    frame). Narrowing to a guess would silently drop cells from the analysis."""
    (tmp_path / "persistence.json").write_text(json.dumps(
        {"schema_version": 1,
         "crop_region": {"region": [1, 2, 3, 4], "scope": {"wells": "A01"}}}))
    app = _app(tmp_path)
    crop_region.load_from_data_dir(app)
    ds = app._app_state.dataset
    assert ds.crop_region == (1, 2, 3, 4)
    assert ds.crop_scope_wells is None


def test_load_clears_a_previous_scope(tmp_path):
    """Loading a dataset whose crop is unscoped must not inherit the scope the
    state happened to be holding."""
    (tmp_path / "persistence.json").write_text(json.dumps(
        {"schema_version": 1, "crop_region": {"region": [1, 2, 3, 4]}}))
    app = _app(tmp_path)
    app._app_state.dataset.crop_scope_wells = {"H12"}
    crop_region.load_from_data_dir(app)
    assert app._app_state.dataset.crop_scope_wells is None


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
