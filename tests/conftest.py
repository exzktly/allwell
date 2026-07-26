"""Shared test fixtures.

The All-Well runtime is a PySide6 Qt application, so we don't try to spin
up a real ``WellViewerApp`` here. Instead, fixtures expose lightweight
stand-ins that satisfy the duck-typed contract the fold-change helpers
need (``_well_paths``, ``_selections``, ``_aggregate_well``,
``_aggregate_group``, ``_compute_rep_stats``,
``_compute_rep_per_fov_stats``).

Tests are pure-logic — no Qt, no matplotlib rendering — so they run in a
plain ``python -m pytest`` with just numpy + pandas in the venv.
"""

from __future__ import annotations

import logging
import os

# Qt + matplotlib must run headless. Set before any Qt / matplotlib import —
# this conftest is an initial conftest, loaded before plugin pytest_configure.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

# The offscreen container has no Helvetica; matplotlib logs a findfont warning
# per text draw, drowning the test output. The Agg fallback is harmless.
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

from dataclasses import dataclass, field

import pytest
from well_viewer.domain.stats_compute import compute_rep_stats


# ── AggPoint helpers ────────────────────────────────────────────────────────

def make_agg(t: float, mean: float, *,
             spread: float = 0.0, frac: float = 0.5,
             n_above: int = 5, n_total: int = 10,
             frac_spread: float = 0.0,
             n_above_pf_mean: float = 0.0,
             n_above_pf_spread: float = 0.0) -> tuple:
    """Construct a 9-tuple AggPoint matching ``data_loading._aggregate_arrays``."""
    return (t, mean, spread, frac, n_above, n_total,
            frac_spread, n_above_pf_mean, n_above_pf_spread)


# ── MockApp ──────────────────────────────────────────────────────────────────

@dataclass
class MockApp:
    """Duck-typed stand-in for ``WellViewerApp`` for pure-logic tests.

    Tests construct one with a ``per_well_pts`` dict mapping well token →
    list of AggPoints. The helpers compute aggregations / stats from that
    in-memory map. Replicate sets are described via ``selections``.
    """
    per_well_pts: dict[str, list[tuple]]
    selections: list[dict] = field(default_factory=list)
    fc_vs_control_on: bool = False
    fc_control_label: str = ""
    fc_vs_t0_on: bool = False
    fc_baseline_tp: str = ""
    fc_op: str = "ratio"
    fc_op_ctrl: str = "ratio"
    fc_op_t0: str = "ratio"
    fc_order: str = "ctrl_first"

    # Read by ``fold_change.fold_change_state`` etc.
    @property
    def _well_paths(self) -> dict[str, str]:
        return {w: w for w in self.per_well_pts}

    @property
    def _selections(self) -> list[dict]:
        return self.selections

    @property
    def _fc_vs_control_on(self) -> bool:
        return self.fc_vs_control_on

    @property
    def _fc_control_label(self) -> str:
        return self.fc_control_label

    @property
    def _fc_vs_t0_on(self) -> bool:
        return self.fc_vs_t0_on

    def _aggregate_well(self, label: str, *, threshold: float, use_sem: bool,
                        val_col: str,
                        fluor_gates, per_fov_spread: bool = False,
                        tp_col: str = "timepoint_hours") -> list[tuple]:
        return list(self.per_well_pts.get(label, []))

    def _aggregate_group(self, wells: list[str], *, threshold: float,
                         use_sem: bool, val_col: str, fluor_gates,
                         per_fov_spread: bool = False,
                         tp_col: str = "timepoint_hours") -> list[tuple]:
        # Pool-of-cells approximation: average means across wells per tp.
        # The mock doesn't keep per-cell granularity, so this collapses to
        # "mean of per-well means" — that's fine for fold_change tests
        # since they don't compare the two stats numerically.
        by_t: dict[float, list[float]] = {}
        for w in wells:
            for pt in self.per_well_pts.get(w, []):
                by_t.setdefault(pt[0], []).append(pt[1])
        out: list[tuple] = []
        for t in sorted(by_t):
            means = by_t[t]
            avg = sum(means) / len(means) if means else float("nan")
            out.append(make_agg(t, avg))
        return out

    def _compute_rep_stats(self, rset, target_t: float,
                           threshold: float, use_sem: bool) -> tuple:
        """Mean of per-well means + mean of per-well fracs at *target_t*."""
        well_means: list[float] = []
        well_fracs: list[float] = []
        for w in rset.wells:
            for pt in self.per_well_pts.get(w, []):
                if abs(pt[0] - target_t) < 1e-3 * max(1.0, abs(pt[0]),
                                                      abs(target_t)):
                    well_means.append(pt[1])
                    if len(pt) >= 4:
                        well_fracs.append(pt[3])
                    break
        if not well_means:
            return (float("nan"), 0.0, float("nan"), 0.0)
        gm = sum(well_means) / len(well_means)
        gf = sum(well_fracs) / len(well_fracs) if well_fracs else 0.5
        return (gm, 0.0, gf, 0.0)

    def _compute_rep_per_fov_stats(self, rset, target_t: float,
                                   threshold: float, use_sem: bool) -> tuple:
        gm, gerr, gf, ferr = compute_rep_stats(self._ctx,
            rset, target_t, threshold, use_sem,
        )
        return (gm, gerr, gf, ferr, 5.0, 0.0)

    def _compute_rep_n_above(self, rset, target_t: float) -> int:
        return 10

    def _get_thresh_frac_on(self, channel: str) -> float:
        return 50.0

    def _get_all_fluor_gates(self) -> dict:
        return {}

    def _use_fov_spread_active(self) -> bool:
        return False

    @property
    def _use_sem(self) -> bool:
        return True

    @property
    def _active_channel(self) -> str:
        return "gfp"

    @property
    def _active_val_col(self) -> str:
        return "gfp_mean_intensity"

    @property
    def _active_metric(self) -> str:
        return "mean_intensity"

    @property
    def _app_state(self):
        """A real ``AppState`` mirroring this MockApp's current fields.

        Controllers read state from ``app._app_state.<domain>`` after the shim
        migration. Rebuilt on every access (cheap — small dataclasses) so a
        test that mutates a MockApp field after construction (e.g.
        ``fc_vs_control_on``) is reflected the next time a controller reads it.
        """
        from well_viewer.core.app_state import AppState
        from well_viewer.features.registry import attach_feature_state
        st = AppState()
        attach_feature_state(st)
        st.channel.active_channel = "gfp"
        st.channel.active_val_col = "gfp_mean_intensity"
        st.channel.active_metric = "mean_intensity"
        st.selection.selections = self.selections
        st.selection.selected_wells = set(self.per_well_pts)
        st.dataset.well_paths = {w: w for w in self.per_well_pts}
        st.plot.use_sem = True
        # Per-FOV-replicates flag: controllers that migrated onto ``ctx.state``
        # (A8) read the toggle here instead of the ``_use_fov_spread_active``
        # method, so mirror the MockApp override field into typed state.
        st.plot.use_fov_replicates = getattr(self, "_use_fov_replicates", False)
        st.plot.fc_vs_control_on = self.fc_vs_control_on
        st.plot.fc_control_label = self.fc_control_label
        st.plot.fc_vs_t0_on = self.fc_vs_t0_on
        st.plot.fc_baseline_tp = self.fc_baseline_tp
        # ``fc_op`` is the legacy single-op back-compat alias; setting it
        # fans out to both axes. The explicit ``fc_op_ctrl`` / ``fc_op_t0``
        # MockApp fields override afterwards when the test sets them.
        st.plot.fc_op = self.fc_op
        st.plot.fc_op_ctrl = self.fc_op_ctrl if self.fc_op_ctrl != "ratio" else st.plot.fc_op_ctrl
        st.plot.fc_op_t0 = self.fc_op_t0 if self.fc_op_t0 != "ratio" else st.plot.fc_op_t0
        st.plot.fc_order = self.fc_order
        st.plot.bar_order = getattr(self, "_bar_order", None)
        st.plot.line_order_rsets = getattr(self, "_line_order_rsets", [])
        st.plot.line_order_wells = getattr(self, "_line_order_wells", [])
        return st

    @property
    def _ctx(self):
        """A minimal ``ViewerContext``-shaped bundle over this MockApp.

        The ctx-native stats leaf (``aggregate_well`` / ``aggregate_group`` /
        ``_thresh_on_col``, A8 R-leaf) reads ``ctx.app`` for the pure-logic
        aggregation delegate, ``ctx.state`` for the typed slices, and
        ``ctx.runtime`` for the scatter/cell-gating gate flags. A SimpleNamespace
        is enough — the MockApp aggregation path is delegate-driven, so
        ``get_rows(ctx)`` is never reached here.
        """
        from types import SimpleNamespace
        from well_viewer.core.app_runtime import AppRuntime
        return SimpleNamespace(app=self, state=self._app_state,
                               runtime=AppRuntime(self))

    @property
    def _selected_wells(self):
        return set(self.per_well_pts)

    def _rep_sets_active(self):
        from well_viewer.domain.batch_models import ReplicateSet
        out = []
        for s in self.selections:
            if s.get("hidden"):
                continue
            wells = [w for w in (s.get("wells") or []) if w in self._well_paths]
            if wells:
                out.append(ReplicateSet(s.get("name") or "", wells))
        return out

    def _parse_rc(self, label: str):
        # Sort wells by (row letter, col number). Good enough for "A01"-style.
        if not label:
            return (0, 0)
        return (label[0], int(label[1:]) if label[1:].isdigit() else 0)

    def _bar_current_keys(self):
        active_rsets = self._rep_sets_active()
        if active_rsets:
            return [r.name for r in active_rsets]
        return sorted(self.per_well_pts, key=self._parse_rc)

    def _rank_color_well(self, label: str) -> str:
        return "#888888"

    def _rank_color_rset(self, rset) -> str:
        return "#888888"

    def _replicate_display_label(self, rset) -> str:
        return rset.name

    def _bar_well_display_label(self, label: str) -> str:
        return label

    def _well_display_label(self, label: str) -> str:
        return label

    def _selected_labels(self):
        return list(self.per_well_pts)


@pytest.fixture
def mock_app():
    """Mock app with two rep-sets (A01/A02 → CTRL, B01/B02 → TREAT) and
    one solo well C01. The control rep-set means are 100 across time so
    fold-change ratios are easy to verify."""
    pts = {
        "A01": [make_agg(0.0, 100.0), make_agg(1.0, 100.0), make_agg(2.0, 100.0)],
        "A02": [make_agg(0.0, 100.0), make_agg(1.0, 100.0), make_agg(2.0, 100.0)],
        "B01": [make_agg(0.0, 100.0), make_agg(1.0, 200.0), make_agg(2.0, 400.0)],
        "B02": [make_agg(0.0, 100.0), make_agg(1.0, 200.0), make_agg(2.0, 400.0)],
        "C01": [make_agg(0.0, 50.0), make_agg(1.0, 100.0), make_agg(2.0, 200.0)],
    }
    selections = [
        {"name": "CTRL", "wells": ["A01", "A02"], "hidden": False},
        {"name": "TREAT", "wells": ["B01", "B02"], "hidden": False},
    ]
    return MockApp(per_well_pts=pts, selections=selections)


# ── No modal dialogs, ever ───────────────────────────────────────────────────
#
# A modal Qt dialog under the offscreen QPA waits for a click that will never
# come. That is not a slow test — it is an unkillable one: the block happens
# inside Qt's C++ event loop, where pytest's signal-based ``--timeout`` cannot
# reach it. The run can then only end at ``scripts/test_slow.sh``'s wall-clock
# ceiling, which kills the process before pytest writes its JUnit report, so a
# whole batch reports as failed, its innocent batch-mate goes down with it, and
# the rest of the hanging file never runs at all.
#
# ``tests/test_sample_def_autosave.py`` did exactly this by calling
# ``labels_apply_affix`` (which prompts through ``ask_name_dialog``) unpatched,
# and it went unnoticed for as long as the slow lane went unrun. The app has ~65
# such entry points, so relying on nobody ever reaching one is not a plan.
#
# So every dialog opener raises instead. A test that means to exercise a dialog
# path patches the specific call itself — its ``monkeypatch`` is applied after
# this fixture and wins. Everything else gets a named, instant failure that
# points at the call site, instead of a hang nobody can attribute.

_MODAL_CALLS = (
    ("PySide6.QtWidgets", "QMessageBox",
     ("question", "warning", "information", "critical", "about", "aboutQt")),
    ("PySide6.QtWidgets", "QInputDialog",
     ("getText", "getInt", "getDouble", "getItem", "getMultiLineText")),
    ("PySide6.QtWidgets", "QFileDialog",
     ("getOpenFileName", "getOpenFileNames", "getSaveFileName",
      "getExistingDirectory")),
    ("PySide6.QtWidgets", "QColorDialog", ("getColor",)),
    ("PySide6.QtWidgets", "QFontDialog", ("getFont",)),
    ("PySide6.QtWidgets", "QDialog", ("exec", "exec_")),
)


class ModalDialogInTest(AssertionError):
    """A test reached a modal dialog. See the note above ``_MODAL_CALLS``."""


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    pytest.importorskip("PySide6")
    import importlib

    for mod_name, cls_name, methods in _MODAL_CALLS:
        try:
            cls = getattr(importlib.import_module(mod_name), cls_name)
        except (ImportError, AttributeError):  # pragma: no cover - Qt build skew
            continue
        for method in methods:
            if not hasattr(cls, method):
                continue

            def _blocked(*_a, _c=cls_name, _m=method, **_kw):
                raise ModalDialogInTest(
                    f"{_c}.{_m}() opened a modal dialog. Under the offscreen QPA "
                    "it would block forever and pytest's signal timeout cannot "
                    "interrupt it. Patch this call in the test with the answer "
                    "the user would give (see tests/conftest.py::_no_modal_dialogs)."
                )

            monkeypatch.setattr(cls, method, _blocked, raising=False)


# ── Headless Qt fixtures ─────────────────────────────────────────────────────
#
# These construct a real ``WellViewerApp`` on the offscreen Qt platform. They
# are the foundation for structural-refactor verification: nothing exercised
# ``runtime_app.py`` before this harness existed.

@pytest.fixture
def mini_dataset(tmp_path):
    """Materialise the deterministic mini dataset; returns its directory."""
    from tests.fixtures.mini_dataset import build_mini_dataset
    return build_mini_dataset(tmp_path / "mini_dataset")


@pytest.fixture
def viewer_app(qtbot):
    """A freshly-constructed, un-loaded ``WellViewerApp``.

    Lazy centre tabs are NOT drained — callers that need every tab built
    should call ``app._drain_pending_centre_builders()`` (the smoke test
    for the drain itself depends on starting from the un-drained state).
    """
    from well_viewer.runtime_app import WellViewerApp
    app = WellViewerApp()
    qtbot.addWidget(app)
    return app


@pytest.fixture
def loaded_app(viewer_app, mini_dataset):
    """``WellViewerApp`` with the mini dataset loaded (and all tabs drained —
    ``_load_path`` drains pending centre builders as part of loading)."""
    viewer_app._load_path(mini_dataset)
    return viewer_app


@pytest.fixture
def image_table_app(loaded_app):
    """``loaded_app`` with the Image Table sub-tab actually built.

    The Image Table is a lazy *sub*-tab of Image Browser: draining the centre
    builders constructs the Image Browser parent with its default sub-tab
    (Segmentation), so the Image Table's widgets (``_image_table_selector_grid``,
    ``_image_table_zoom_lbl``, …) are only created when its sub-tab is first
    shown. Tests that drive the Image Table controller need those widgets, so
    build the sub-tab explicitly here.
    """
    from well_viewer.features.image_browser.controller import build_image_browser_subtab
    app = loaded_app
    # Image Browser is a lazy-only centre tab (built on first user click, not by
    # the background drain), so force its parent build, then the sub-tab.
    build_pending = app._ctx.runtime.centre.build_pending
    if build_pending is not None:
        build_pending("Image Browser")
    sub = app._ctx.runtime.image_browser.subtabs
    assert sub is not None, "Image Browser parent tab was not built"
    idx = next((i for i in range(sub.count())
                if sub.tabText(i) == "Image Table"), None)
    assert idx is not None, "Image Table sub-tab not found"
    build_image_browser_subtab(app._ctx, idx)
    return app


@pytest.fixture
def mini_dataset_shape(tmp_path):
    """Mini dataset that additionally carries per-cell shape-feature columns."""
    from tests.fixtures.mini_dataset import build_mini_dataset
    return build_mini_dataset(tmp_path / "mini_dataset_shape", with_shape=True)


@pytest.fixture
def loaded_app_shape(viewer_app, mini_dataset_shape):
    """``WellViewerApp`` loaded with the shape-feature mini dataset."""
    viewer_app._load_path(mini_dataset_shape)
    return viewer_app


@pytest.fixture
def mini_dataset_multifov(tmp_path):
    """Mini dataset with 3 FOVs per well/timepoint (per-FOV means differ) so the
    FOV-as-replicate stats path can be exercised."""
    from tests.fixtures.mini_dataset import build_mini_dataset
    return build_mini_dataset(tmp_path / "mini_dataset_multifov", n_fovs=3)


@pytest.fixture
def loaded_app_multifov(viewer_app, mini_dataset_multifov):
    """``WellViewerApp`` loaded with the multi-FOV mini dataset."""
    viewer_app._load_path(mini_dataset_multifov)
    return viewer_app


def pytest_collection_modifyitems(config, items):
    """Auto-mark full-app Qt tests ``slow``.

    Constructing the full ``WellViewerApp`` (the ``viewer_app`` fixture and its
    ``loaded_app`` / ``loaded_app_shape`` dependents) is slow under the offscreen
    QPA platform, so these integration tests don't belong in the fast CI lane.
    Marking by fixture (rather than by hand on each test) keeps ``-m "not slow"``
    a fast, reliable, auto-maintained gate; the full-app tests run in the
    local/manual full pass. The lightweight ``mock_app`` fixture is unaffected.
    """
    slow = pytest.mark.slow
    for item in items:
        if "viewer_app" in getattr(item, "fixturenames", ()):
            item.add_marker(slow)
