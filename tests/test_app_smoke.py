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
    assert sorted(loaded_app._well_paths) == ["A01", "A02", "B01", "B02"]
    assert "gfp" in loaded_app._fluor_channels
    assert "mcherry" in loaded_app._fluor_channels
    assert loaded_app._all_timepoints_cache == [0.0, 24.0, 48.0]
    assert all(not df.empty for df in loaded_app._cache.values())


def test_tab_switch_matrix_after_load(loaded_app):
    """Every tab survives a switch with a dataset loaded — exercises the
    per-tab refresh paths in ``_on_tab_change``."""
    nb = loaded_app._notebook
    for name in [n for n in nb.pageNames() if n]:
        assert nb.setCurrentByName(name), f"could not switch to tab {name!r}"
