"""Core code reaches a removable feature's state through ``feature_slice``.

`AppState.__getattr__` raises for a feature slice whose feature is not in
`features.registry.FEATURES`, by design — the message tells you to gate the
access. The bar renderer did not:

    pairwise = list(getattr(ctx.state.stats, "pairwise", []) or [])

That reads as defensive and is not. ``getattr``'s default covers a missing
*attribute*; its first argument is evaluated before ``getattr`` is ever called,
so ``ctx.state.stats`` raises first. A profile build without Statistics hit it
on every bar redraw with the significance toggle on, and the user saw the
AttributeError dialog over a drawn plot.

The same shape one level down: ``saved_plots`` wrote to ``ctx.state.stats``
inside a broad ``except`` that logs at DEBUG, so the failure did not crash —
a restored plot just came back without its comparisons, silently.

This is the third time in this codebase a line that *looks* like a guard has
not been one (``with swallowed():``, ``getattr(QMenu, …)``, this). The check at
the bottom is therefore mechanical rather than a promise to remember.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from tests._repo_scan import REPO_ROOT, python_sources, rel
from tests.conftest import MockApp, make_agg
from review.core.app_state import _FEATURE_STATE_OWNERS as OWNERS
from tests._repo_scan import parse_source


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ctx_without_stats(qapp, monkeypatch):
    """A ctx whose ``stats`` slice is absent, as a profile build's would be."""
    app = MockApp(per_well_pts={"A01": [make_agg(0.0, 100.0)]}, selections=[])
    ctx = app._ctx
    monkeypatch.delitem(ctx.state.__dict__, "stats", raising=False)
    assert ctx.state.feature_slice("stats") is None
    with pytest.raises(AttributeError, match="Statistics"):
        ctx.state.stats  # the behaviour the fix has to survive
    return ctx


def test_the_bar_renderer_draws_with_statistics_disabled(ctx_without_stats):
    """The reported crash. The toggle is on, the feature is gone, and the
    significance overlay simply does not draw."""
    from review.features.barplot.renderer import draw_significance_brackets

    ctx = ctx_without_stats
    ctx.state.plot.show_significance = True
    fig, (ax_mean, ax_frac) = plt.subplots(2, 1)
    try:
        draw_significance_brackets(ctx, [], ax_mean=ax_mean, ax_frac=ax_frac,
                                   target_ts=[0.0])
    finally:
        plt.close(fig)


def test_the_toggle_being_off_is_still_the_first_gate(ctx_without_stats):
    """Order matters: the cheap check stays in front of the slice lookup, so a
    build *with* Statistics does no extra work when the overlay is off."""
    from review.features.barplot.renderer import draw_significance_brackets

    ctx = ctx_without_stats
    ctx.state.plot.show_significance = False
    fig, (ax_mean, ax_frac) = plt.subplots(2, 1)
    try:
        draw_significance_brackets(ctx, [], ax_mean=ax_mean, ax_frac=ax_frac,
                                   target_ts=[0.0])
    finally:
        plt.close(fig)


# ── the mechanical half ──────────────────────────────────────────────────────

def test_no_code_outside_a_feature_reaches_its_slice_by_attribute():
    """``ctx.state.<removable slice>`` outside that feature must be a
    ``feature_slice`` call.

    Both spellings are caught, because both shipped. The plain attribute is the
    honest mistake. ``getattr(ctx.state.stats, "pairwise", [])`` is the
    dishonest one: the default covers a missing *attribute* on an object that
    already exists, and reaching the object is the part that raises — it
    happens first, before ``getattr`` is even called.

    "Removable" comes from ``profiles/full.toml``, which is the catalogue the
    strip tool diffs against, so a feature that becomes removable is covered
    the day it is listed. A slice whose feature is kept forever — ``bar``,
    ``preview``, ``review_image`` — cannot be absent and is not the subject.
    """
    import ast
    import re

    # Which package owns each slice, derived from the registry's own
    # `_state_<slice> = _lazy_state("<module>", ...)` lines rather than a hand
    # roster here — a roster would rot the day a feature moves (§7.5 #3). A
    # feature reaching its *own* slice is fine: if it is disabled, its modules
    # do not run at all.
    home: dict = {}
    reg = parse_source((REPO_ROOT / "review/features/registry.py"))
    for node in ast.walk(reg):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        call = node.value
        if not (isinstance(target, ast.Name)
                and target.id.startswith("_state_")
                and isinstance(call, ast.Call)
                and getattr(call.func, "id", "") == "_lazy_state"
                and call.args and isinstance(call.args[0], ast.Constant)):
            continue
        slot = target.id[len("_state_"):]
        module = str(call.args[0].value)          # review.features.x.state
        home[slot] = module.rsplit(".", 1)[0].replace(".", "/") + "/"
    assert set(home) >= set(OWNERS), (
        f"registry has no _state_ line for {set(OWNERS) - set(home)}")

    # Only the features the strip tool can actually remove.
    catalogue = (REPO_ROOT / "profiles/full.toml").read_text()
    removable = set(re.findall(r'^name = "([^"]+)"', catalogue, re.M)) - {"full"}
    watched = {slot for slot, owner in OWNERS.items() if owner in removable}
    assert watched, "no removable feature owns a state slice — check the parse"

    # `features/registry.py` is the one module whose whole job is holding every
    # feature's descriptor and activation hooks, and the sole importer of
    # features by design (§4). Each access there sits inside a hook that runs
    # only for its own feature — `_activate_statistics` cannot fire without
    # Statistics. Exempted for that reason, with the liveness check §7.5 #10
    # asks for, so it cannot outlive it.
    exempt = "review/features/registry.py"

    offenders: list[str] = []
    exempt_hits = 0
    for path in python_sources():
        where = rel(path)
        try:
            tree = parse_source(path)
        except SyntaxError:  # pragma: no cover - a file we cannot parse
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in watched:
                continue
            base = node.value
            if not (isinstance(base, ast.Attribute) and base.attr == "state"):
                continue
            owner_dir = home.get(node.attr, "")
            if owner_dir and owner_dir in where:
                continue
            if where == exempt:
                exempt_hits += 1
                continue
            offenders.append(
                f"{where}:{node.lineno} state.{node.attr} — {OWNERS[node.attr]} "
                f"is removable, so this raises when it is absent; use "
                f'state.feature_slice("{node.attr}")'
            )
    assert not offenders, (
        "a removable feature's slice reached by attribute from outside it:\n"
        + "\n".join(f"  {o}" for o in sorted(offenders)))
    if any(rel(p) == exempt for p in python_sources()):
        # Only meaningful over the real tree; the bite cases redirect the scan
        # at one synthetic file, where the exempt module is simply not present.
        assert exempt_hits, (
            f"{exempt} no longer reaches any removable feature's slice — drop "
            "the exemption rather than leaving one that describes nothing")


# ── a reduced build says so ──────────────────────────────────────────────────

def test_a_reduced_profile_announces_itself_once(monkeypatch, caplog):
    """A subset build is degraded output, so it must not be silent.

    Until this line existed, an app running a profile simply had fewer tabs and
    then raised ``AttributeError: AppState.<slice> is unavailable`` the first
    time something reached past the gap — with nothing anywhere naming the
    profile. A stray ``active_profile.txt`` left in a working tree by a profile
    build does that permanently, and it is the case this exists for, so the
    message names *where* the choice came from and not only what it was.
    """
    import logging

    from core import profile as profile_mod

    monkeypatch.setattr(profile_mod, "_announced", False)
    monkeypatch.setenv("ALLWELL_PROFILE", "plotting_only")
    with caplog.at_level(logging.WARNING, logger="review"):
        profile_mod.active_profile()
        profile_mod.active_profile()

    hits = [r for r in caplog.records if "feature profile" in r.getMessage()]
    assert len(hits) == 1, "announced once per process, not once per call"
    msg = hits[0].getMessage()
    assert "plotting_only" in msg
    assert "ALLWELL_PROFILE" in msg, "the message has to name where it came from"


def test_the_full_app_says_nothing(monkeypatch, caplog):
    """Silence is correct for the norm — a warning on every launch would be
    noise, and noise is what gets a real warning ignored."""
    import logging

    from core import profile as profile_mod

    monkeypatch.setattr(profile_mod, "_announced", False)
    monkeypatch.delenv("ALLWELL_PROFILE", raising=False)
    monkeypatch.setattr(profile_mod, "_repo_root", lambda: Path(tempfile.mkdtemp()))
    with caplog.at_level(logging.WARNING, logger="review"):
        profile_mod.active_profile()
    assert not [r for r in caplog.records if "feature profile" in r.getMessage()]
