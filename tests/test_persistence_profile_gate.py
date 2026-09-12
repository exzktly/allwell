"""The persistence registry must not import profiled-out feature modules.

``build_persistence_registry`` used to import ``persistence/export_groups.py``
(Batch Export) and ``persistence/multivariate.py`` (Multivariate) unconditionally.
Both are physically stripped from a subset bundle, so a frozen thin build crashed
in ``ReviewApp.__init__`` before the window showed. Registration is now gated
on the active profile; these tests lock that in.

Note: the repo-wide ``test_no_module_scope_imports_of_removable_features`` guard
never caught this — the import was function-scoped and targeted ``persistence/``,
not ``features/``.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.persistence.registry import build_persistence_registry


def test_thin_profile_skips_feature_owned_domains(monkeypatch):
    monkeypatch.setenv("ALLWELL_PROFILE", "thin")
    reg = build_persistence_registry(object())
    # Batch Export / Multivariate are absent from thin → their domains unregistered.
    assert "export_groups" not in reg._save
    assert "multivariate" not in reg._save
    # Always-present core domains stay registered.
    assert "view_state" in reg._save
    assert "ratios" in reg._save


def test_full_profile_registers_feature_owned_domains(monkeypatch):
    monkeypatch.delenv("ALLWELL_PROFILE", raising=False)
    reg = build_persistence_registry(object())
    assert "export_groups" in reg._save
    assert "multivariate" in reg._save
