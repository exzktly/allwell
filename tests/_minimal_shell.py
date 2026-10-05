"""A real ``ViewerContext`` around a bare window, for one-feature tests.

A full-app test builds ``ReviewApp`` — every tab, the rail, the title bar, the
persistence registry, about 1,800 QObjects — and none of it is ever reclaimed
in-process, which is why those tests live in the slow lane, run a file or two
per process. Most of that is irrelevant to a test whose subject is one feature:
the feature reaches the app only through its ``ViewerContext``.

This builds that context the way ``ReviewApp.__init__`` does — the feature
registry, ``AppState`` with every feature's state slice attached, one attribute
store shared by the widget registry and the runtime namespaces, an
``EventBus`` — around a plain ``QMainWindow`` standing in as ``ctx.window``,
which is all a feature uses the window for: a parent for its dialogs and a host
for the widget handles the registry stamps onto it. Nothing is stubbed: the
state, the registry and the runtime are the production classes.

:func:`load_dataset` loads a dataset folder through the same calls
``review.core.load_controller`` makes — the sidecar reader, the repository's
per-well load, the timepoint and FOV caches, and the channel recalculation —
without the progress dialogs, the persistence restores and the sidebar repaint,
which belong to the shell this leaves out. A test whose subject *is* one of
those (loading, persistence, navigation, the sidebar, the band) keeps using the
real app.

Row Q3 of the remediation plan (deleted when its last row closed; ``git log --
_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md`` recovers it).
"""

from __future__ import annotations

import logging
from pathlib import Path


def minimal_shell(qtbot):
    """``(ctx, window)``: a production ``ViewerContext`` with no ``ReviewApp``."""
    from PySide6.QtWidgets import QMainWindow

    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.core.event_bus import EventBus
    from review.core.widget_registry import WidgetRegistry
    from review.features.registry import attach_feature_state, build_feature_registry
    from review.viewer_context import ViewerContext

    window = QMainWindow()
    qtbot.addWidget(window)
    features = build_feature_registry()
    state = AppState()
    attach_feature_state(state, features)
    store: dict = {}
    ctx = ViewerContext(
        state=state,
        bus=EventBus(),
        widgets=WidgetRegistry(window, store=store),
        runtime=AppRuntime(store=store),
        features=features,
        _window=window,
    )
    return ctx, window


def load_dataset(ctx, path: Path) -> None:
    """Load a flat dataset folder the way ``load_controller`` does, minus the
    shell: sidecar, per-well CSVs through the repository, caches, channels."""
    from review.core.channel_state_controller import recalculate_threshold
    from review.core.load_controller import (
        _looks_like_well_measurement_csv,
        _resolve_fov_tp_extractor,
    )
    from review.data.data_loading import extract_well_token
    from review.domain.viewer_state import read_pipeline_info

    ds = ctx.state.dataset
    ds.in_dir = None
    extractor, _fluor, smfish, info = read_pipeline_info(
        path, logger=logging.getLogger("review"), check_parent=True)
    ds.fov_tp_extractor = _resolve_fov_tp_extractor(extractor, info)
    ds.pipeline_info = info
    ctx.state.channel.smfish_channels = smfish
    ds.begin_load(path)
    for csv in sorted(path.glob("*.csv")):
        if csv.name.startswith(".") or not _looks_like_well_measurement_csv(csv):
            continue
        token = extract_well_token(csv.stem)
        ctx.state.repo.load_well(token or csv.stem, csv)
    ds.rebuild_timepoints()
    ds.rebuild_fovs()
    recalculate_threshold(ctx)
