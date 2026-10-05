"""The headless report through the real command (CLAUDE.md §7.8 rule 3):
``python allwell_report.py`` in a subprocess, against a dataset with saved plots
filed by the real app — a drawable one and one that cannot be redrawn.

Asserts the manifest, that every file it lists exists and is non-empty
(CLAUDE.md §7.3 #1: a check on an empty artifact is not a check), the exit
code, and that the dataset's own files are byte-identical afterwards.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

REPO = Path(__file__).resolve().parent.parent


def _snapshot(root: Path) -> dict:
    """Every file under *root* except the history, by content hash."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()
            and p.name != "provenance.jsonl"}


@pytest.mark.slow
def test_the_command_draws_the_gallery_and_changes_nothing(loaded_app, mini_dataset, tmp_path):
    app = loaded_app
    ctx = app._ctx
    # The plot is saved with a group the dataset then loses, so replaying it
    # re-adopts the group: the replay genuinely moves live state. Measured, it
    # schedules no save for it (nor for a replay that moves gating), so the
    # report's write suspension is defence in depth that this run does not
    # exercise — ``tests/test_headless_report.py`` pins the suspension itself.
    # What this test pins is the outcome: the dataset is byte-identical.
    assert app._sel_add(name="ReportProbe", wells=["A01", "A02"], source="user")
    app._notebook.setCurrentByName("Bar Plots")
    card = ctx.runtime.saved_plots.cards_by_tab["Bar Plots"]
    card.saveFigureToGallery()
    from review.persistence.reset_scopes import clear_analysis
    assert clear_analysis(ctx, ["sample_definitions"]) == ["sample_definitions"]
    ctx.persistence.flush_all()
    # A record the report cannot redraw: its tab draws no chart.
    from review.features.saved_plots.state import get_store
    store = get_store(ctx)
    store.ensure_loaded(ctx)
    rec = store.records[-1]
    persisted = json.loads((mini_dataset / "persistence.json").read_text())
    persisted["saved_plots"].append(dict(persisted["saved_plots"][0],
                                         id="grid", tab="Image Table", title="Grid"))
    (mini_dataset / "persistence.json").write_text(json.dumps(persisted))
    before = _snapshot(mini_dataset)

    out = tmp_path / "report"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(REPO))
    proc = subprocess.run(
        [sys.executable, str(REPO / "allwell_report.py"), "--dataset", str(mini_dataset),
         "--output", str(out)],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=300)

    manifest = json.loads((out / "manifest.json").read_text())
    assert proc.returncode == 3, (proc.returncode, proc.stdout, proc.stderr[-2000:])
    assert manifest["status"] == "partial"
    by_id = {p["id"]: p for p in manifest["plots"]}
    drawn = by_id[rec.id]
    assert drawn["status"] == "drawn"
    for key in ("png", "svg"):
        path = out / drawn[key]
        assert path.is_file() and path.stat().st_size > 1000, path
    assert by_id["grid"]["status"] == "not_drawn"
    assert "Image Table tab draws no chart" in by_id["grid"]["reason"]
    assert manifest["pdf"] == "report.pdf"
    assert (out / "report.pdf").stat().st_size > 1000
    assert isinstance(manifest["writes_suppressed"], int)

    assert _snapshot(mini_dataset) == before, "the report changed the dataset"
    from util.provenance_log import read_events
    events = [e for e in read_events(mini_dataset).events
              if e["event"] == "report_generated"]
    assert events and events[-1]["detail"]["status"] == "partial"


@pytest.mark.slow
def test_a_build_without_saved_plots_says_so(mini_dataset, tmp_path):
    """The thin profile ships no Saved Plots. The report must say that and
    exit as invalid input — not crash, and not write an empty manifest that
    reads as a dataset with nothing saved."""
    out = tmp_path / "report"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(REPO),
               ALLWELL_PROFILE="thin")
    proc = subprocess.run(
        [sys.executable, str(REPO / "allwell_report.py"), "--dataset", str(mini_dataset),
         "--output", str(out)],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    manifest = json.loads((out / "manifest.json").read_text())
    assert proc.returncode == 2, (proc.stdout, proc.stderr[-2000:])
    assert manifest["status"] == "invalid_input"
    assert manifest["message"] == "Saved Plots is not part of this build"
