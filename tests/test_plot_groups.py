from __future__ import annotations

from domain.plot_groups import make_plot_group, validate_repair_plot_groups
from domain.selections_format import from_block, to_block


def test_plot_group_normalizes_wells_and_names():
    used_names = {"Treatment"}
    used_ids = {"abc"}

    group = make_plot_group(
        name="Treatment",
        wells=["a1", "A01", "b2", "not-a-well"],
        group_id="abc",
        used_names=used_names,
        used_ids=used_ids,
    )

    assert group["id"] != "abc"
    assert group["name"] == "Treatment 2"
    assert group["wells"] == ["A01", "B02"]


def test_sample_definition_block_round_trips_plot_groups_separately():
    block = to_block(
        [],
        well_labels={},
        notes="",
        plot_groups=[{"id": "pg1", "name": "Fast load", "wells": ["A01", "A02"]}],
    )

    selections, current_id, labels, notes, plot_groups = from_block(block)

    assert selections == []
    assert current_id is None
    assert labels == {}
    assert notes == ""
    assert plot_groups == [{"id": "pg1", "name": "Fast load", "wells": ["A01", "A02"]}]


def test_plot_group_validation_drops_empty_groups():
    groups = validate_repair_plot_groups([
        {"name": "Empty", "wells": []},
        {"name": "Valid", "wells": ["C03"]},
    ])

    assert groups == [{"id": groups[0]["id"], "name": "Valid", "wells": ["C03"]}]


def test_add_current_prefers_the_triggering_drawers_name_field(qapp, monkeypatch):
    """Regression: a second author-capable Plot Groups drawer (Image Table) must
    add using ITS OWN name field, not the shared ``_plot_group_name_edit`` key —
    otherwise enabling Add on the Image Table sidebar broke the main sidebar's Add.
    """
    from types import SimpleNamespace
    from PySide6.QtWidgets import QLineEdit
    from review.core import sidebar_view as SV

    # The "registry" (main sidebar) name field vs. the drawer that was clicked.
    registry_edit = QLineEdit(); registry_edit.setText("MAIN")
    clicked_edit = QLineEdit(); clicked_edit.setText("FROM IMAGE TABLE")

    ctx = SimpleNamespace(
        state=SimpleNamespace(
            selection=SimpleNamespace(selected_wells={"A01", "A02"}, selections=[],
                                      plot_groups=[], active_solo_wells=set()),
            dataset=SimpleNamespace(well_paths={"A01": 1, "A02": 1}),
        ),
        widgets=SimpleNamespace(get_or_none=lambda _n: registry_edit),
        persistence=None,
        notify=SimpleNamespace(set_status=lambda *_a, **_k: None),
    )
    monkeypatch.setattr(SV, "refresh_plot_groups_sidebar", lambda _c: None)

    SV._plot_groups_add_current(ctx, clicked_edit)

    assert len(ctx.state.selection.plot_groups) == 1
    assert ctx.state.selection.plot_groups[0]["name"] == "FROM IMAGE TABLE"
    assert clicked_edit.text() == ""   # the triggering field is cleared, not the registry one
    assert registry_edit.text() == "MAIN"


def _persistence_fake_app(tmp_path, plot_groups):
    from types import SimpleNamespace

    from review.core.dataset_state import DatasetState
    from review.core.selection_state import SelectionState

    app = SimpleNamespace(
        _app_state=SimpleNamespace(
            dataset=DatasetState(data_dir=tmp_path),
            selection=SelectionState(plot_groups=list(plot_groups)),
        ),
    )
    app.window = app; app.state = app._app_state  # mock is its own ctx (B4)
    app._app_state.dataset.well_paths = {
        "A01": tmp_path / "A01.csv", "A02": tmp_path / "A02.csv",
    }
    app._ctx = SimpleNamespace(app=app, state=app._app_state)
    return app


def test_load_keeps_pipeline_info_groups_when_the_doc_has_no_section(tmp_path):
    """A dataset can carry Plot Groups in pipeline_info.json and no
    persistence.json section (folder copied without the sidecar). The
    sample-definitions hydrator runs first; this loader must not wipe it."""
    from review.persistence import plot_groups as persist

    app = _persistence_fake_app(
        tmp_path,
        [{"id": "pg1", "name": "From pipeline_info", "wells": ["A01", "A02"]}],
    )
    assert not (tmp_path / "persistence.json").exists()

    persist.load_from_data_dir(app)

    assert app._app_state.selection.plot_groups == [
        {"id": "pg1", "name": "From pipeline_info", "wells": ["A01", "A02"]}
    ]


def test_plot_groups_persistence_round_trip(tmp_path):
    from types import SimpleNamespace

    from review.core.dataset_state import DatasetState
    from review.core.selection_state import SelectionState
    from review.persistence import plot_groups as persist

    app = SimpleNamespace(
        _app_state=SimpleNamespace(
            dataset=DatasetState(data_dir=tmp_path),
            selection=SelectionState(
                plot_groups=[{"name": "Saved", "wells": ["A01", "A02"]}],
            ),
        ),
    )
    app.window = app; app.state = app._app_state  # mock is its own ctx (B4)
    app._app_state.dataset.well_paths = {"A01": tmp_path / "A01.csv", "A02": tmp_path / "A02.csv"}
    # ``_doc.set_section``/``get_section`` read ``ctx.state`` (the typed AppState);
    # the fake app is its own ctx's app and exposes its state slice.
    app._ctx = SimpleNamespace(app=app, state=app._app_state)

    persist.save_to_data_dir(app)
    app._app_state.selection.plot_groups = []
    persist.load_from_data_dir(app)

    assert app._app_state.selection.plot_groups == [
        {"id": app._app_state.selection.plot_groups[0]["id"], "name": "Saved", "wells": ["A01", "A02"]}
    ]
