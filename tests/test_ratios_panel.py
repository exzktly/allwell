"""The Home → Ratios editor (``features/home/ratios/tab_view.py``).

A ratio is a virtual channel computed at read time from two
``{channel}_{metric}`` columns, and this panel is the only place a user
defines one. The tests here drive the widget directly against a ctx-shaped
fake rather than a full ``ReviewApp``, so they stay in the fast lane.

Ratios is a removable feature (``profiles/full.toml``), which is the second
reason this file exists: the manifest deletes ``tests/test_ratios_*.py``
along with the package, so without a test file matching that glob the
feature's removal entry would be decorative and its behaviour unguarded.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox, QWidget  # noqa: E402

from review.domain.ratio_models import RatioMetric  # noqa: E402
from review.features.home.ratios.tab_view import (  # noqa: E402
    RatioMetricsPanel,
    build_ratios_inline_panel,
)


def _ratio(name: str = "gfp_over_mcherry", **kw) -> RatioMetric:
    fields = {
        "name": name,
        "numerator_channel": "gfp",
        "numerator_metric": "mean_intensity",
        "denominator_channel": "mcherry",
        "denominator_metric": "mean_intensity",
        "epsilon": 0.0,
    }
    fields.update(kw)
    return RatioMetric(**fields)


class _FakePersistence:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def schedule_save(self, scope: str) -> None:
        self.saved.append(scope)


def _stub_app(ratios=None, channels=("gfp", "mcherry")):
    """App-shaped fake exposing only what the panel reads.

    Deliberately without ``_set_ratio_metrics``: the panel's ``hasattr``
    fallback writes the state object directly, which is the branch a test
    can assert on without standing up the real setter's redraw chain.
    """
    channel = types.SimpleNamespace(
        ratio_metrics=list(ratios or []),
        fluor_channels=list(channels),
    )
    return types.SimpleNamespace(
        _app_state=types.SimpleNamespace(channel=channel),
        _persistence=_FakePersistence(),
    )


def _panel(qtbot, app) -> RatioMetricsPanel:
    parent = QWidget()
    qtbot.addWidget(parent)
    panel = RatioMetricsPanel(app, parent)
    # pytest-qt holds widgets weakly, so the parent has to be kept alive here:
    # dropping it deletes the C++ object and the panel's table with it.
    panel._test_parent = parent
    return panel


def _row_values(panel: RatioMetricsPanel, row: int) -> tuple:
    t = panel._table
    return (
        t.cellWidget(row, 0).text(),
        t.cellWidget(row, 1).currentText(),
        t.cellWidget(row, 2).currentText(),
        t.cellWidget(row, 3).currentText(),
        t.cellWidget(row, 4).currentText(),
        t.cellWidget(row, 5).value(),
    )


def test_the_panel_opens_showing_the_ratios_already_defined(qtbot):
    app = _stub_app([_ratio("a"), _ratio("b", epsilon=1.5)])
    panel = _panel(qtbot, app)

    assert panel._table.rowCount() == 2
    assert _row_values(panel, 0) == (
        "a", "gfp", "mean_intensity", "mcherry", "mean_intensity", 0.0)
    assert _row_values(panel, 1)[0] == "b"
    assert _row_values(panel, 1)[5] == pytest.approx(1.5)


def test_a_reload_replaces_the_rows_rather_than_appending_to_them(qtbot):
    """``refresh_from_app`` runs after every dataset load, so a second call
    must not leave the previous dataset's ratios stacked above the new ones."""
    app = _stub_app([_ratio("a")])
    panel = _panel(qtbot, app)

    app._app_state.channel.ratio_metrics = [_ratio("c"), _ratio("d")]
    panel.refresh_from_app()

    assert panel._table.rowCount() == 2
    assert [_row_values(panel, r)[0] for r in range(2)] == ["c", "d"]


def test_add_offers_the_datasets_own_channels_not_a_hardcoded_one(qtbot):
    app = _stub_app(channels=("cy5", "dapi"))
    panel = _panel(qtbot, app)
    panel._on_add()

    combo = panel._table.cellWidget(0, 1)
    assert [combo.itemText(i) for i in range(combo.count())] == ["cy5", "dapi"]
    assert combo.currentText() == "cy5"


def test_with_no_channels_loaded_add_still_produces_a_usable_row(qtbot):
    """Before a dataset is loaded there are no channels to offer. The row
    still has to be constructible, or the tab is unusable until a load."""
    app = _stub_app(channels=())
    panel = _panel(qtbot, app)
    panel._on_add()

    assert panel._table.rowCount() == 1
    assert panel._table.cellWidget(0, 1).currentText() == "gfp"


def test_apply_commits_the_edited_table_and_asks_for_a_save(qtbot):
    app = _stub_app([_ratio("a")])
    panel = _panel(qtbot, app)
    panel._table.cellWidget(0, 0).setText("renamed")
    panel._table.cellWidget(0, 5).setValue(2.0)

    panel._on_apply()

    (committed,) = app._app_state.channel.ratio_metrics
    assert committed.name == "renamed"
    assert committed.epsilon == pytest.approx(2.0)
    assert app._persistence.saved == ["ratios"]


def test_a_row_left_unnamed_is_dropped_rather_than_committed_blank(qtbot):
    app = _stub_app([_ratio("a")])
    panel = _panel(qtbot, app)
    panel._on_add()
    panel._table.cellWidget(1, 0).setText("   ")

    panel._on_apply()

    assert [r.name for r in app._app_state.channel.ratio_metrics] == ["a"]


def test_two_rows_with_one_name_are_refused_and_nothing_is_committed(qtbot,
                                                                     monkeypatch):
    """A duplicate name would silently shadow the first ratio everywhere a
    plot resolves a virtual channel, so Apply must reject the whole table
    rather than commit the half of it that is unambiguous."""
    warned: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *a, **k: warned.append(a[2] if len(a) > 2 else ""),
        raising=False)

    app = _stub_app([_ratio("a")])
    panel = _panel(qtbot, app)
    panel._on_add()
    panel._table.cellWidget(1, 0).setText("a")

    panel._on_apply()

    assert warned and "Duplicate" in warned[0]
    assert [r.name for r in app._app_state.channel.ratio_metrics] == ["a"]
    assert app._persistence.saved == []


def test_remove_takes_the_current_row_when_nothing_is_selected(qtbot):
    app = _stub_app([_ratio("a"), _ratio("b")])
    panel = _panel(qtbot, app)
    panel._table.setCurrentCell(0, 0)
    panel._table.clearSelection()

    panel._remove_selected()

    assert panel._table.rowCount() == 1
    assert _row_values(panel, 0)[0] == "b"


def test_a_channel_the_dataset_no_longer_has_falls_back_to_the_first(qtbot):
    """A persisted ratio can name a channel a later dataset does not carry.
    The combo cannot show it, and the row must still resolve to something
    real rather than committing an empty channel name."""
    app = _stub_app([_ratio(numerator_channel="gone")], channels=("gfp",))
    panel = _panel(qtbot, app)

    assert _row_values(panel, 0)[1] == "gfp"


def test_the_builder_mounts_the_panel_and_registers_it_for_the_reset_paths(qtbot):
    """``_ratio_panel`` is the key ``channel_state_controller`` looks up to
    refresh the editor after a dataset load, so the builder must register it."""
    registered: dict = {}
    parent = QWidget()
    qtbot.addWidget(parent)
    ctx = types.SimpleNamespace(
        window=_stub_app([_ratio("a")]),
        widgets=types.SimpleNamespace(
            register=lambda name, w: registered.__setitem__(name, w)),
    )

    panel = build_ratios_inline_panel(ctx, parent)

    assert registered["_ratio_panel"] is panel
    assert panel.parent() is parent
