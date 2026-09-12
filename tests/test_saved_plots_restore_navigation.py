"""Where a restore lands: the saved plot's own tab, not Home.

``view_state.restore`` is shared by two callers with opposite needs. Opening a
dataset lands on Home, because that is the moment the reader most needs to be
told what they just opened. Saved Plots' "Restore settings" is a request to see
one particular figure again, so it must land on the tab that figure came from —
navigating to Home there leaves every restored setting applied to a tab nobody
is looking at, which reads as a restore that did nothing.

The activation is asserted at the boundary (the notebook page actually asked
for) rather than at any value ``restore`` computed on the way, so a snapshot
that reaches the right state but shows the wrong page still fails.
"""
from __future__ import annotations

from types import SimpleNamespace

from review.core.app_runtime import AppRuntime
from review.persistence import view_state


class _FakeNotebook:
    """Records the page activations ``view_state`` asks for."""

    def __init__(self, names):
        self._names = list(names)
        self.calls: list[str] = []

    def pageNames(self):
        return list(self._names)

    def setCurrentByName(self, name):
        self.calls.append(name)
        return name in self._names


def _ctx(names=("Home", "Plotting", "Sample Definitions")):
    """A ctx holding only what ``restore``'s cross-cutting phase reads."""
    nb = _FakeNotebook(names)
    app = SimpleNamespace()
    state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={}),
        selection=SimpleNamespace(
            selected_wells=set(), selections=[], active_solo_wells=set()),
        channel=SimpleNamespace(
            active_channel="", active_metric="", sync_val_col=lambda: None),
    )
    app._app_state = state
    ctx = SimpleNamespace(app=app, state=state, runtime=AppRuntime(app),
                          window=SimpleNamespace(_notebook=nb, _app_state=state))
    app._ctx = ctx
    return ctx, nb


def test_restore_activates_the_snapshots_own_tab():
    """The default: the tab the snapshot names wins, even in a build with Home."""
    ctx, nb = _ctx()
    view_state.restore(ctx, {"active_centre_tab": "Sample Definitions"})
    assert nb.calls == ["Sample Definitions"]


def test_restore_routes_a_plot_subtab_through_its_parent():
    """A plot leaf activates the Plotting rail entry (the sub-tab selection then
    happens on the nested notebook, which this stub does not carry)."""
    ctx, nb = _ctx()
    view_state.restore(ctx, {"active_centre_tab": "Bar Plots"})
    assert nb.calls == ["Plotting"]


def test_dataset_arrival_lands_on_home():
    """``prefer_home`` — the dataset-load path — overrides the saved tab."""
    ctx, nb = _ctx()
    view_state.restore(ctx, {"active_centre_tab": "Bar Plots"}, prefer_home=True)
    assert nb.calls == ["Home"]


def test_dataset_arrival_falls_back_to_the_saved_tab_without_home():
    """A feature-subset build with no Home page keeps the saved tab."""
    ctx, nb = _ctx(names=("Plotting", "Sample Definitions"))
    view_state.restore(ctx, {"active_centre_tab": "Sample Definitions"},
                       prefer_home=True)
    assert nb.calls == ["Sample Definitions"]


def test_dataset_load_asks_for_home(monkeypatch, tmp_path):
    """``load_from_data_dir`` is the caller that opts into Home — asserted on the
    call it makes, so a future caller added beside it cannot inherit the
    behaviour silently."""
    seen = {}

    def _fake_restore(ctx, state, *, prefer_home=False):
        seen["prefer_home"] = prefer_home

    monkeypatch.setattr(view_state, "restore", _fake_restore)
    monkeypatch.setattr(
        view_state._doc, "get_section",
        lambda ctx, name: {"schema_version": view_state.SCHEMA_VERSION})

    ctx, _nb = _ctx()
    ctx.state.dataset.data_dir = tmp_path
    view_state.load_from_data_dir(ctx)
    assert seen == {"prefer_home": True}
