"""The headless report's cover: provenance, settings, completeness, and per plot
what changed after it was saved (``review/features/saved_plots/_cover.py``).

Fixtures are built so the right and wrong answers differ (CLAUDE.md §7.8 rule
2): edits land *between* two plots' save times, so a sentence that ignored the
save time, or counted the report's own history lines as changes, names the
wrong plot. The PDF tests read the text the pages actually draw, not the cover
object handed to them (§7.4 #1).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import pytest

from review.features.saved_plots._cover import (
    DOMAIN_LABELS,
    build_cover,
    history_rows,
    since_saved_sentence,
)
from util.provenance_log import append_event, read_events


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def _local(text: str) -> str:
    """How the cover prints a UTC instant: this machine's local time."""
    moment = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def _saved_at(text: str) -> str:
    """A saved plot's ``created_at``: naive, in local time, as the app writes it."""
    moment = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    return moment.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def _rec(rid, title, created, tab="Bar Plots"):
    return SimpleNamespace(id=rid, title=title, tab=tab, created_at=_saved_at(created),
                           summary="")


_RUN_CHECK = {"skipped": False, "suspended": False,
              "totals": {"wells": 94, "unreadable": 2, "complete": 90, "partial": 3,
                         "missing_csv": 1},
              "errors": {"files": 4, "by_type": {"MemoryError": 4}}}


def _history(d):
    """Run on 03-01, gates edited on 03-02, rerun on 04-01, report on 04-03."""
    append_event(d, "run_started", source="analyze", now=_ts("2026-03-01T10:00"),
                 detail={"mode": "local", "input_dir": "/in", "argv": [],
                         "scope": {"incremental": False}})
    append_event(d, "run_finished", source="analyze", now=_ts("2026-03-01T12:30"),
                 detail={"mode": "local", "outcome": "succeeded", "run_check": _RUN_CHECK})
    for minute in range(3):
        append_event(d, "analysis_changed", source="review",
                     now=_ts(f"2026-03-02T09:0{minute}"),
                     detail={"domain": "cell_gating", "before": "a", "after": "b"})
    append_event(d, "analysis_changed", source="review", now=_ts("2026-03-02T09:10"),
                 detail={"domain": "sample_definitions", "before": None, "after": "c"})
    append_event(d, "run_started", source="analyze", now=_ts("2026-04-01T10:00"),
                 detail={"mode": "remote", "input_dir": "/in", "argv": [],
                         "scope": {"incremental": True, "only_wells": ["B03", "C07"]}})
    append_event(d, "run_finished", source="analyze", now=_ts("2026-04-01T11:00"),
                 detail={"mode": "remote", "outcome": "succeeded", "run_check": None})
    append_event(d, "report_generated", source="report", now=_ts("2026-04-03T09:00"),
                 detail={"output": "/r", "status": "complete", "drawn": 1, "not_drawn": 0})
    return read_events(d)


_INFO = {
    "schema": "well:fov:channel:timepoint", "separator": "_", "nuclear_token": "dapi",
    "fluor_tokens": ["gfp", "mcherry"], "available_timepoints": ["0h", "24h", "48h"],
    "available_fovs": ["1", "2", "3", "4"], "segmentation_method": "stardist",
    "execution_options": {"segmentation_method": "stardist", "stardist_prob_thresh": 0.5,
                          "cellpose_model": "cpsam", "min_nucleus_area_px": 40,
                          "flatfield": False},
    "run_check": {"finished_at": "2026-04-01T11:00:00+00:00", "mode": "remote",
                  "host": "hpc-3", "totals": _RUN_CHECK["totals"],
                  "errors": _RUN_CHECK["errors"]},
}


def _cover(read, records, outcomes, info=_INFO):
    return build_cover(info=info, read=read, records=records, outcomes=outcomes,
                       dataset_name="plate-01", dataset_path="/data/plate-01",
                       n_wells=94, n_cells=1234, app_version="1.2.3")


# ── since saved ─────────────────────────────────────────────────────────────


def test_a_plot_saved_before_the_edits_names_each_change(tmp_path):
    read = _history(tmp_path)
    text = since_saved_sentence(_saved_at("2026-03-01T15:00"), read.events,
                                history_known=True)
    assert text == (
        f"Redrawn from the dataset as it is now. Since this plot was saved "
        f"({_local('2026-03-01T15:00')}): re-analysed once (last "
        f"{_local('2026-04-01T11:00')}); changed cell gating, sample definitions.")


def test_a_plot_saved_after_every_change_says_nothing(tmp_path):
    """The report's own ``report_generated`` line, after the save, is not a change."""
    read = _history(tmp_path)
    assert since_saved_sentence(_saved_at("2026-04-02T00:00"), read.events,
                                history_known=True) == ""


def test_a_history_that_starts_after_the_plot_says_so(tmp_path):
    read = _history(tmp_path)
    text = since_saved_sentence(_saved_at("2026-01-01T00:00"), read.events,
                                history_known=True)
    assert text.startswith(f"The dataset's history begins {_local('2026-03-01T10:00')}")
    assert "changes before then are not recorded" in text


def test_only_the_plot_saved_before_the_rerun_carries_a_sentence(tmp_path):
    read = _history(tmp_path)
    early = _rec("early", "Early", "2026-03-01T15:00")
    late = _rec("late", "Late", "2026-04-02T00:00")
    cover = _cover(read, [early, late], {"early": ("drawn", ""), "late": ("drawn", "")})
    assert set(cover.since_saved) == {"early"}


# ── history rows ────────────────────────────────────────────────────────────


def test_history_pairs_runs_and_collapses_edits(tmp_path):
    rows = history_rows(_history(tmp_path).events)
    assert [r.what for r in rows] == [
        "Analysis run (local) succeeded: 90 of 96 well(s) complete, 4 error records",
        "Analysis edited: cell gating ×3, sample definitions",
        "Analysis run (remote) succeeded: no completeness record for this run "
        "[only B03, C07, incremental]",
        "Headless report: complete (1 drawn, 0 not drawn)",
    ]
    assert rows[1].when == _local("2026-03-02T09:10")


def test_a_run_with_no_recorded_end_is_its_own_row(tmp_path):
    append_event(tmp_path, "run_started", source="analyze", now=_ts("2026-03-01T10:00"),
                 detail={"mode": "local", "input_dir": "/in", "argv": [], "scope": {}})
    rows = history_rows(read_events(tmp_path).events)
    assert [r.what for r in rows] == ["Analysis run (local) started; no end was recorded"]


def test_every_recorded_analysis_domain_has_words():
    from review.persistence.provenance import STORED_AT
    assert set(STORED_AT.values()) <= set(DOMAIN_LABELS)


# ── warnings: degraded input is stated ──────────────────────────────────────


def test_no_history_file_is_a_warning(tmp_path):
    cover = _cover(read_events(tmp_path), [], {})
    assert cover.history_status == "No history recorded for this dataset."
    assert any("No history (provenance.jsonl)" in w for w in cover.warnings)


def test_unreadable_history_lines_are_counted(tmp_path):
    _history(tmp_path)
    with open(tmp_path / "provenance.jsonl", "ab") as fh:
        fh.write(b'{"torn\n')
    cover = _cover(read_events(tmp_path), [], {})
    assert "Incomplete: 1 line could not be read." in cover.history_status
    assert "The dataset's history is incomplete: 1 line could not be read." in cover.warnings


def test_an_incomplete_plate_and_error_records_are_warnings(tmp_path):
    cover = _cover(_history(tmp_path), [], {})
    assert ("Plate incomplete: 3 partial, 1 missing their table. Plots drawn from "
            "those wells use fewer fields than the plate holds.") in cover.warnings
    assert any("4 error records in errors/" in w for w in cover.warnings)
    assert ("Wells", "90 of 96 well(s) complete; 2 well(s) could not be read — their "
            "state is unknown, not incomplete") in cover.last_run


def test_no_completeness_record_is_a_warning(tmp_path):
    info = {k: v for k, v in _INFO.items() if k != "run_check"}
    cover = _cover(_history(tmp_path), [], {}, info=info)
    assert cover.last_run == ()
    assert any(w.startswith("No completeness record") for w in cover.warnings)


def test_a_plot_not_drawn_is_listed_with_its_reason(tmp_path):
    recs = [_rec("a", "Bars", "2026-05-01T00:00"),
            _rec("b", "Grid", "2026-05-01T00:00", tab="Image Table")]
    cover = _cover(_history(tmp_path), recs,
                   {"a": ("drawn", ""), "b": ("not_drawn", "draws no chart")})
    assert ("  Grid", "draws no chart") in cover.report
    assert ("1 saved plot could not be redrawn and is not in this PDF "
            "(listed under Report).") in cover.warnings


def test_settings_skip_options_of_another_method(tmp_path):
    cover = _cover(_history(tmp_path), [], {})
    labels = dict(cover.settings)
    assert labels["StarDist probability threshold"] == "0.5"
    assert labels["Flat-field correction"] == "off"
    assert "Cellpose model" not in labels


def test_analysis_state_names_the_last_change_per_domain(tmp_path):
    cover = _cover(_history(tmp_path), [], {})
    assert cover.analysis == (
        ("Sample definitions", f"last changed {_local('2026-03-02T09:10')}"),
        ("Cell gating", f"last changed {_local('2026-03-02T09:02')}"),
    )


# ── the PDF draws it ────────────────────────────────────────────────────────


@pytest.fixture
def drawn_text(monkeypatch):
    """Every string each saved PDF page draws, page by page."""
    from matplotlib.backends.backend_pdf import PdfPages

    pages: list[list[str]] = []
    real = PdfPages.savefig

    def spy(self, figure=None, **kw):
        pages.append([t.get_text() for t in figure.texts])
        return real(self, figure, **kw)

    monkeypatch.setattr(PdfPages, "savefig", spy)
    return pages


def test_the_pdf_cover_prints_the_provenance(tmp_path, drawn_text):
    from review.features.saved_plots._report import build_saved_plots_report

    early = _rec("early", "Early", "2026-03-01T15:00")
    late = _rec("late", "Late", "2026-04-02T00:00")
    cover = _cover(_history(tmp_path), [early, late],
                   {"early": ("drawn", ""), "late": ("drawn", "")})
    out = tmp_path / "r.pdf"
    assert build_saved_plots_report([early, late], lambda r: None, out, cover=cover) == 2
    assert out.read_bytes()[:5] == b"%PDF-"

    cover_pages, plot_pages = drawn_text[:-2], drawn_text[-2:]
    cover_text = "\n".join(t for page in cover_pages for t in page).replace("\n", " ")
    assert "Needs attention" in cover_text
    assert "Analysis edited: cell gating ×3, sample definitions" in cover_text
    assert "hpc-3" in cover_text
    assert any("Since this plot was saved" in t for t in plot_pages[0])
    assert not any("Since this plot was saved" in t for t in plot_pages[1])


def test_without_a_cover_the_pdf_keeps_its_title_page(tmp_path, drawn_text):
    from review.features.saved_plots._report import build_saved_plots_report

    build_saved_plots_report([], lambda r: None, tmp_path / "r.pdf", dataset_name="d")
    assert len(drawn_text) == 1
    assert "Saved Plots Report" in drawn_text[0]
