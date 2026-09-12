"""Audit X7 — a view_state schema bump must migrate the saved doc forward, not
silently discard the user's entire saved view.

``_migrate`` walks a saved doc from its version up to ``SCHEMA_VERSION`` via the
registered step chain, returning ``None`` only when a version genuinely can't be
reached (unknown step, or a doc written by a newer app).
"""
from __future__ import annotations

from review.persistence import view_state as vs


def test_current_version_passes_through():
    out = vs._migrate({"schema_version": vs.SCHEMA_VERSION, "x": 1},
                      vs.SCHEMA_VERSION)
    assert out is not None
    assert out["schema_version"] == vs.SCHEMA_VERSION
    assert out["x"] == 1


def test_missing_version_treated_as_v1():
    # Pre-versioning docs (no schema_version) are treated as the original v1.
    out = vs._migrate({"x": 1}, None)
    assert out is not None
    assert out["schema_version"] == vs.SCHEMA_VERSION


def test_newer_than_known_is_not_downgraded():
    assert vs._migrate({"x": 1}, vs.SCHEMA_VERSION + 5) is None


def test_registered_step_chain_upgrades(monkeypatch):
    # Simulate a future bump: SCHEMA_VERSION 1 -> 2 with a real migration step.
    def _v1_to_v2(state):
        state = dict(state)
        state["migrated_flag"] = True
        return state

    monkeypatch.setattr(vs, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(vs, "_MIGRATIONS", {1: _v1_to_v2})

    out = vs._migrate({"schema_version": 1, "selected_wells": ["A01"]}, 1)
    assert out is not None
    assert out["schema_version"] == 2
    assert out["migrated_flag"] is True
    # User data carried through the migration rather than being dropped.
    assert out["selected_wells"] == ["A01"]


def test_missing_intermediate_step_cannot_migrate(monkeypatch):
    # Bump to v3 but only provide the 1->2 step: the 2->3 gap can't be crossed.
    monkeypatch.setattr(vs, "SCHEMA_VERSION", 3)
    monkeypatch.setattr(vs, "_MIGRATIONS", {1: lambda s: dict(s)})
    assert vs._migrate({"schema_version": 1}, 1) is None
