"""A8 teardown T2 — per-feature runtime holders (AppRuntime).

``ctx.runtime.<feature>`` is a namespace for live, non-persisted objects. Writing
through it stamps ``app._<feature>_<name>`` so the many legacy readers resolve
unchanged while builders stop naming ``ctx.app``. This mirrors the WidgetRegistry
stamp pattern. Runtime holders must never be serialized.
"""
from __future__ import annotations

from types import SimpleNamespace

from well_viewer.core.app_runtime import AppRuntime, _FeatureRuntime


def test_write_through_stamps_the_app():
    app = SimpleNamespace()
    rt = AppRuntime(app)
    sentinel = object()
    rt.smfish.bridge = sentinel
    # Legacy reader path resolves.
    assert app._smfish_bridge is sentinel
    # Namespace read-back resolves the same object.
    assert rt.smfish.bridge is sentinel


def test_missing_field_reads_none():
    app = SimpleNamespace()
    rt = AppRuntime(app)
    assert rt.smfish.never_set is None


def test_prefix_isolates_the_namespace():
    app = SimpleNamespace()
    fr = _FeatureRuntime(app, "_demo_")
    fr.value = 7
    assert app._demo_value == 7
    assert fr.value == 7


def test_persistence_never_imports_app_runtime():
    # Runtime holders are live-object namespaces, never serialized. The
    # persistence layer must not depend on them.
    import pkgutil
    from pathlib import Path

    import well_viewer.persistence as pkg

    root = Path(pkg.__file__).parent
    offenders = []
    for mod in pkgutil.iter_modules([str(root)]):
        src = (root / f"{mod.name}.py")
        if src.exists() and "app_runtime" in src.read_text():
            offenders.append(mod.name)
    assert not offenders, f"persistence modules reference app_runtime: {offenders}"
