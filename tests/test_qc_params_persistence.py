"""qc_params persistence — now a section of persistence.json (architecture
§5.7), with a one-time migration from the legacy standalone qc_params.json."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from well_viewer.persistence import qc_params
from well_viewer.features.qc.params import QCParams


def _app(tmp_path, pipeline_info=None):
    ds = SimpleNamespace(data_dir=tmp_path, persistence_doc=None,
                         pipeline_info=pipeline_info or {})
    return SimpleNamespace(_app_state=SimpleNamespace(dataset=ds))


def test_saves_into_persistence_json_not_a_sidecar(tmp_path):
    app = _app(tmp_path)
    app._qc_params = QCParams(enabled=True, min_area=20, min_solidity=0.8)
    qc_params.save_to_data_dir(app)
    pj = json.loads((tmp_path / "persistence.json").read_text())
    assert pj["qc_params"]["min_area"] == 20 and pj["qc_params"]["enabled"] is True
    assert not (tmp_path / "qc_params.json").exists()        # no standalone file


def test_round_trips_through_the_section(tmp_path):
    app = _app(tmp_path)
    app._qc_params = QCParams(enabled=True, min_area=33)
    qc_params.save_to_data_dir(app)
    app2 = _app(tmp_path)                                    # fresh cache
    qc_params.load_from_data_dir(app2)
    assert app2._qc_params.min_area == 33 and app2._qc_params.enabled


def test_legacy_sidecar_is_absorbed_then_deleted(tmp_path):
    # the old wrapped standalone file
    (tmp_path / "qc_params.json").write_text(json.dumps(
        {"schema_version": 1, "qc_params": {"enabled": True, "min_area": 42}}))
    app = _app(tmp_path)
    qc_params.load_from_data_dir(app)
    assert app._qc_params.min_area == 42 and app._qc_params.enabled
    # migrated into persistence.json and the legacy file removed
    assert not (tmp_path / "qc_params.json").exists()
    pj = json.loads((tmp_path / "persistence.json").read_text())
    assert pj["qc_params"]["min_area"] == 42


def test_no_persisted_state_seeds_from_pipeline_info(tmp_path):
    app = _app(tmp_path, pipeline_info={"qc_options": {"remove_debris": True,
                                                       "min_area": 15}})
    qc_params.load_from_data_dir(app)
    assert app._qc_params.enabled and app._qc_params.min_area == 15
    # seeding alone doesn't write a file
    assert not (tmp_path / "qc_params.json").exists()
