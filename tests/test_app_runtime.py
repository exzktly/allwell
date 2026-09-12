"""A8 teardown T2/T6 — per-feature runtime holders (AppRuntime).

``ctx.runtime.<feature>`` is a namespace for live, non-persisted objects, backed
by a plain ``name -> object`` attribute-store dict. Writing through it stores the
key ``_<feature>_<name>``; reading resolves it back. The holder keeps **no app
reference** (T6 step 2): the legacy ``app._<feature>_<name>`` stamp is gone. The
store can be *shared* with the ``WidgetRegistry`` so a registered widget and a
same-named runtime field resolve one another. Runtime holders must never be
serialized.
"""
from __future__ import annotations

from types import SimpleNamespace

from review.core.app_runtime import AppRuntime, _FeatureRuntime


def test_write_through_stores_and_reads_back():
    rt = AppRuntime(store={})
    sentinel = object()
    rt.smfish.bridge = sentinel
    assert rt.smfish.bridge is sentinel


def test_missing_field_reads_none():
    rt = AppRuntime(store={})
    assert rt.smfish.never_set is None


def test_store_backed_never_touches_the_app():
    # Production path: an explicit store means the holder owns only that dict
    # and stamps nothing onto the app — the coupling is removed, not relocated.
    app = SimpleNamespace()
    rt = AppRuntime(app=app, store={})
    rt.smfish.bridge = object()
    assert not hasattr(app, "_smfish_bridge")
    assert not hasattr(rt, "_app")


def test_app_default_bridges_legacy_readers():
    # Legacy / test path: no explicit store → the app's own __dict__ backs the
    # namespaces, so a runtime write is visible as ``app._<feature>_<name>``.
    app = SimpleNamespace()
    rt = AppRuntime(app)
    rt.smfish.bridge = 5
    assert app._smfish_bridge == 5


def test_prefix_writes_the_underscored_key():
    store: dict = {}
    fr = _FeatureRuntime(store, "_demo_")
    fr.value = 7
    assert store["_demo_value"] == 7
    assert fr.value == 7


def test_shared_store_bridges_widget_and_runtime_names():
    # A widget registered under its underscored name is reachable through the
    # runtime namespace when both share one store, exactly as when both stamped
    # a single ``app.__dict__``.
    from review.core.widget_registry import WidgetRegistry

    store: dict = {}
    reg = WidgetRegistry(app=None, store=store)
    rt = AppRuntime(store=store)
    canvas = object()
    reg.register("_bar_canvas", canvas)
    assert rt.barplot.canvas is canvas
    # …and the reverse: a runtime write is visible to the widget registry.
    fig = object()
    rt.barplot.fig = fig
    assert reg.get("_bar_fig") is fig


def test_holds_no_app_reference():
    # T6 step 2: the runtime no longer reaches through the god-object.
    rt = AppRuntime(app=object())
    assert not hasattr(rt, "_app")


def test_persistence_never_imports_app_runtime():
    # Runtime holders are live-object namespaces, never serialized. The
    # persistence layer must not depend on them.
    import pkgutil
    from pathlib import Path

    import review.persistence as pkg

    root = Path(pkg.__file__).parent
    offenders = []
    for mod in pkgutil.iter_modules([str(root)]):
        src = (root / f"{mod.name}.py")
        if src.exists() and "app_runtime" in src.read_text():
            offenders.append(mod.name)
    assert not offenders, f"persistence modules reference app_runtime: {offenders}"
