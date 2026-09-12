"""The profile-derivation generator computes the right physical deletion set.

``scripts/derive_profile.plan_deletions`` uses the *same* ``Profile.includes``
predicate the runtime filter uses, so the stripped tree and the runtime subset
always agree. Exercised against a synthetic catalog + temp tree so it runs
without the real (already-deleted-on-thin) feature packages.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")  # profile loader lives in the review package

from scripts.derive_profile import plan_deletions
from core.profile import Profile


def _touch(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if rel.endswith("/"):
        p.mkdir(parents=True, exist_ok=True)
    else:
        p.write_text("")


def _catalog():
    return [
        {"name": "Statistics", "paths": ["review/features/stats"],
         "tests": ["tests/test_stats_*.py"],
         "keep": ["tests/test_stats_cache_gate_sig.py"]},
        {"name": "Image Table", "parent": "Image Browser",
         "paths": ["review/features/image_browser/image_table"]},
    ]


def test_plan_excludes_features_the_profile_rejects(tmp_path):
    _touch(tmp_path, "review/features/stats/__init__.py")
    _touch(tmp_path, "review/features/image_browser/image_table/__init__.py")
    _touch(tmp_path, "tests/test_stats_state.py")
    _touch(tmp_path, "tests/test_stats_cache_gate_sig.py")  # kept-code test

    # Thin-like profile: Image Browser kept but only its Segmentation sub-tab;
    # Statistics not listed at all.
    profile = Profile(name="thin", features=frozenset({"Image Browser"}),
                      subtabs={"Image Browser": frozenset({"Segmentation"})})
    plan = plan_deletions(_catalog(), profile, tmp_path)

    by_feature = {d.feature: {p.name for p in d.paths} for d in plan}
    # Statistics is excluded — its package + feature test go, but the kept-code
    # test is preserved by the ``keep`` exclusion.
    assert "Statistics" in by_feature
    assert "test_stats_state.py" in by_feature["Statistics"]
    assert "test_stats_cache_gate_sig.py" not in by_feature["Statistics"]
    # Image Table sub-tab excluded (parent kept, but sub-tab not in the allowlist).
    assert "Image Table" in by_feature


def test_full_profile_removes_nothing(tmp_path):
    _touch(tmp_path, "review/features/stats/__init__.py")
    from core.profile import FULL
    assert plan_deletions(_catalog(), FULL, tmp_path) == []


def test_plan_skips_absent_paths(tmp_path):
    # Nothing on disk → nothing to delete, even when the profile excludes it.
    profile = Profile(name="none", features=frozenset(), subtabs={})
    assert plan_deletions(_catalog(), profile, tmp_path) == []
