"""Headless smoke tests for ``WellViewerApp``.

Phase 0 of the ``runtime_app`` teardown: the first tests that exercise the
real application object. They are deliberately broad and shallow — they prove
construction, lazy-tab building, tab switching, and the open-dataset golden
path do not raise. Behavioural depth lives in the interaction and
characterization suites.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.qt


def test_construct_app(viewer_app):
    assert viewer_app._notebook is not None
    assert viewer_app._notebook.count() >= 1


def test_drain_builds_all_centre_tabs(viewer_app):
    pending_before = dict(viewer_app._centre_pending_builders)
    assert pending_before, "expected deferred tab builders before drain"

    viewer_app._drain_pending_centre_builders()

    lazy_only = set(viewer_app._centre_lazy_only_titles)
    remaining = set(viewer_app._centre_pending_builders) - lazy_only
    assert not remaining, f"tabs left unbuilt after drain: {sorted(remaining)}"


def test_no_tab_builder_raised_while_draining(viewer_app, caplog):
    """A builder that raises is *caught* — so nothing above notices.

    ``centre_view`` logs the traceback, tells the status bar, and returns. The
    tab is then an empty page, which is indistinguishable from a tab with
    nothing to show, and it is removed from the pending map either way — so
    ``test_drain_builds_all_centre_tabs`` above passes with a tab that is
    completely broken. That is exactly what happened: Response Curves raised on
    every real app construction for as long as it existed, while its own ~130
    tests were green, because every one of them drove the tab's parts through a
    stub ctx and none of them called the builder.

    The fast lane cannot see this at all — it never builds an app — so this is
    the only place the failure can surface.
    """
    import logging

    with caplog.at_level(logging.ERROR, logger="well_viewer"):
        viewer_app._drain_pending_centre_builders()

    failures = [r for r in caplog.records if "Deferred build" in r.getMessage()]
    assert not failures, "tab builders raised:\n" + "\n".join(
        f"  {r.getMessage()}\n{r.exc_text or ''}" for r in failures)


def test_tab_switch_matrix(viewer_app):
    """Switching to every centre page fires ``_on_tab_change`` without error."""
    viewer_app._drain_pending_centre_builders()
    nb = viewer_app._notebook
    names = [n for n in nb.pageNames() if n]
    assert names, "notebook exposed no named pages"

    for name in names:
        assert nb.setCurrentByName(name), f"could not switch to tab {name!r}"
        assert nb.currentName() == name


def test_open_dataset_golden_path(loaded_app):
    assert sorted(loaded_app._app_state.dataset.well_paths) == ["A01", "A02", "B01", "B02"]
    assert "gfp" in loaded_app._app_state.channel.fluor_channels
    assert "mcherry" in loaded_app._app_state.channel.fluor_channels
    assert loaded_app._app_state.dataset.all_timepoints_cache == [0.0, 24.0, 48.0]
    assert all(not df.empty for df in loaded_app._app_state.dataset.cache.values())


def test_tab_switch_matrix_after_load(loaded_app):
    """Every tab survives a switch with a dataset loaded — exercises the
    per-tab refresh paths in ``_on_tab_change``."""
    nb = loaded_app._notebook
    for name in [n for n in nb.pageNames() if n]:
        assert nb.setCurrentByName(name), f"could not switch to tab {name!r}"
