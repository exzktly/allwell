"""Guardrail + behaviour for the reset scopes.

The guard is the important half. Three reset commands used to be written
against the files each one happened to know about, and two of them drifted from
their own dialog text — one promised to clear manual cell curation that lives in
a file it never opened, the other promised to clear plot groups that a second
copy in ``pipeline_info.json`` restored on the reload immediately afterwards.
Nothing failed when that happened, because nothing related "what the user was
told" to "what the code touches".

``test_scopes_partition_every_persistence_domain`` is that relation: every
domain in the persistence registry, plus the two that live in the pipeline
sidecar, must be claimed by exactly one scope. Adding a persistence domain
without deciding which reset owns it now fails here rather than in a bug report.

The behaviour tests are deliberately Qt-light — they drive the scope functions
against a stub context, not the full app — so they run in the fast lane.
"""

from __future__ import annotations

import pytest

from well_viewer.domain.state_defaults import restore_defaults
from well_viewer.persistence import reset_scopes


# ── the partition guard ───────────────────────────────────────────────────


def _registry_domain_names() -> set[str]:
    """Every domain name ``build_persistence_registry`` registers.

    Read off a real registry rather than a hand-copied list: a list here would
    be a second place to update, which is the failure mode under test.
    """
    pytest.importorskip("PySide6")
    from well_viewer.persistence.registry import PersistenceRegistry

    names: set[str] = set()

    class _Recorder(PersistenceRegistry):
        def register(self, name, *, save, load=None):
            names.add(name)
            super().register(name, save=save, load=load)

    import well_viewer.persistence.registry as registry_mod
    real = registry_mod.PersistenceRegistry
    registry_mod.PersistenceRegistry = _Recorder
    try:
        registry_mod.build_persistence_registry(object())
    finally:
        registry_mod.PersistenceRegistry = real
    return names


def _assert_partition(expected: set[str], claimed: frozenset[str]) -> None:
    """The guard's actual check, so the bite test below exercises this code and
    not a paraphrase of it."""
    unclaimed = expected - claimed
    assert not unclaimed, (
        f"persistence domain(s) {sorted(unclaimed)} belong to no reset scope. "
        "Add them to a scope in persistence/reset_scopes.py (or to VIEW_DOMAINS) "
        "so a reset that claims to clear them actually does."
    )
    unknown = claimed - expected
    assert not unknown, (
        f"reset scope(s) claim domain(s) {sorted(unknown)} that no longer exist. "
        "A stale claim makes the partition look complete while covering nothing."
    )


def test_scopes_partition_every_persistence_domain():
    expected = _registry_domain_names() | set(reset_scopes.SIDECAR_DOMAINS)
    _assert_partition(expected, reset_scopes.all_domains())


def test_the_partition_guard_bites(monkeypatch):
    """Prove the guard fails on the shape it exists to catch.

    A guard that has only ever been seen green is a hypothesis. Both directions
    are checked: a domain nobody clears, and a scope clearing something that no
    longer exists.
    """
    expected = _registry_domain_names() | set(reset_scopes.SIDECAR_DOMAINS)

    # A new persistence domain lands and no scope claims it.
    with pytest.raises(AssertionError, match="belong to no reset scope"):
        _assert_partition(expected | {"new_domain"}, reset_scopes.all_domains())

    # A scope still claims a domain that has been removed.
    monkeypatch.setattr(reset_scopes, "VIEW_DOMAINS",
                        (*reset_scopes.VIEW_DOMAINS, "retired_domain"))
    with pytest.raises(AssertionError, match="no longer exist"):
        _assert_partition(expected, reset_scopes.all_domains())


def test_no_domain_is_claimed_twice():
    seen: dict[str, str] = {}
    groups = [("view", reset_scopes.VIEW_DOMAINS)]
    groups += [(s.key, s.domains) for s in reset_scopes.ANALYSIS_SCOPES]
    for owner, domains in groups:
        for domain in domains:
            assert domain not in seen, (
                f"{domain!r} is claimed by both {seen[domain]!r} and {owner!r}. "
                "Two scopes clearing one domain means ticking either reports "
                "success while the other's checkbox silently did nothing."
            )
            seen[domain] = owner


def test_every_scope_has_a_distinct_key_and_real_text():
    keys = [s.key for s in reset_scopes.ANALYSIS_SCOPES]
    assert len(keys) == len(set(keys))
    for scope in reset_scopes.ANALYSIS_SCOPES:
        assert scope.title.strip()
        assert scope.detail.strip()
        assert scope.domains
        assert callable(scope.clear)


def test_dialog_lists_exactly_the_implemented_scopes():
    """The checklist is generated from the table, so it cannot offer a scope
    the reset does not implement — assert the generation, not a copy of it."""
    pytest.importorskip("PySide6")
    import inspect

    from well_viewer.core import reset_scopes_view

    source = inspect.getsource(reset_scopes_view.ResetAnalysisDialog)
    assert "for scope in ANALYSIS_SCOPES" in source, (
        "the dialog must build its rows by iterating ANALYSIS_SCOPES; a "
        "hand-written list is how the old dialogs came to describe state they "
        "did not clear"
    )


# ── restore_defaults ──────────────────────────────────────────────────────


def test_restore_defaults_resets_fields_added_later():
    """The reason PlotState is reset generically: a field added to the slice is
    covered without anybody remembering to extend a reset."""
    from dataclasses import dataclass, field

    @dataclass
    class _Slice:
        scalar: int = 3
        text: str = "on"
        items: list = field(default_factory=list)

    obj = _Slice(scalar=99, text="off", items=["a"])
    changed = restore_defaults(obj)

    assert obj.scalar == 3
    assert obj.text == "on"
    assert obj.items == []
    assert sorted(changed) == ["items", "scalar", "text"]


def test_restore_defaults_honours_keep():
    from dataclasses import dataclass

    @dataclass
    class _Slice:
        kept: str = ""
        dropped: str = ""

    obj = _Slice(kept="gfp", dropped="x")
    restore_defaults(obj, keep=frozenset({"kept"}))

    assert obj.kept == "gfp"
    assert obj.dropped == ""


def test_restore_defaults_rejects_non_dataclass():
    with pytest.raises(TypeError):
        restore_defaults(object())


# ── clear_analysis dispatch ───────────────────────────────────────────────


class _Ctx:
    """Minimal stand-in: ``clear_analysis`` only touches ``persistence`` and the
    scopes' own ``clear`` callables, both of which the tests replace."""

    persistence = None
    window = None


def test_clear_analysis_runs_only_the_requested_scopes(monkeypatch):
    ran: list[str] = []
    scopes = tuple(
        reset_scopes.ResetScope(
            key=k, title=k, detail=k, domains=(k,),
            clear=lambda _ctx, _k=k: ran.append(_k),
        )
        for k in ("alpha", "beta", "gamma")
    )
    monkeypatch.setattr(reset_scopes, "ANALYSIS_SCOPES", scopes)

    done = reset_scopes.clear_analysis(_Ctx(), ["alpha", "gamma"])

    assert ran == ["alpha", "gamma"]
    assert done == ["alpha", "gamma"]


def test_one_failing_scope_does_not_abandon_the_others(monkeypatch):
    """A scope that raises must not take the rest of the user's request with it,
    and must not be reported as done."""
    ran: list[str] = []

    def _boom(_ctx):
        raise RuntimeError("sidecar unreadable")

    scopes = (
        reset_scopes.ResetScope(key="first", title="f", detail="f",
                                domains=("first",),
                                clear=lambda _c: ran.append("first")),
        reset_scopes.ResetScope(key="broken", title="b", detail="b",
                                domains=("broken",), clear=_boom),
        reset_scopes.ResetScope(key="last", title="l", detail="l",
                                domains=("last",),
                                clear=lambda _c: ran.append("last")),
    )
    monkeypatch.setattr(reset_scopes, "ANALYSIS_SCOPES", scopes)

    done = reset_scopes.clear_analysis(_Ctx(), ["first", "broken", "last"])

    assert ran == ["first", "last"]
    assert done == ["first", "last"]
    assert "broken" not in done


def test_unknown_scope_key_is_ignored_not_reported_done(monkeypatch):
    monkeypatch.setattr(reset_scopes, "ANALYSIS_SCOPES", ())
    assert reset_scopes.clear_analysis(_Ctx(), ["nope"]) == []
