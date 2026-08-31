""""Load these settings" must restore every setting, not eighteen of twenty.

Two options are spelled one way in the Analyze form and the other way in the
sidecar. The form carries `auto_threshold` and `run_check` as positives; the
pipeline records the negatives it was handed on the command line,
`no_auto_threshold` and `no_run_check`. `_apply_pinfo_settings` reads the
sidecar's spelling.

`_previous_settings` prefers the run journal, whose spec holds the *form's*
opts verbatim. So a journal-sourced load handed the applier a dict naming
neither key it was looking for, and both checkboxes silently kept whatever
they were already showing — the two that decide whether the plate gets a gate
at all and whether the run audits itself when it finishes.

Nothing reported an error. The button said it had loaded the settings, and had
loaded all of them but these.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")



def test_the_forms_positives_become_the_sidecars_negatives():
    from analyze.preflight import _as_sidecar_polarity

    got = _as_sidecar_polarity(
        {"auto_threshold": False, "run_check": True, "nuclear_token": "DAPI"})

    assert got["no_auto_threshold"] is True, (
        "a run that turned the gate off is loaded as one that left it on")
    assert got["no_run_check"] is False
    assert got["nuclear_token"] == "DAPI", "unrelated settings were disturbed"


def test_both_polarities_round_trip():
    from analyze.preflight import _as_sidecar_polarity

    for value in (True, False):
        got = _as_sidecar_polarity({"auto_threshold": value})
        assert got["no_auto_threshold"] is (not value)


def test_a_sidecar_shaped_dict_is_left_alone():
    """The other source. Translating a dict that already speaks the applier's
    language would invert it."""
    from analyze.preflight import _as_sidecar_polarity

    already = {"no_auto_threshold": True, "no_run_check": True}
    assert _as_sidecar_polarity(already) == already


def test_an_explicit_negative_wins_over_the_positive():
    """If both are present the applier's own key is authoritative — inventing
    a second answer for a question already answered is how the two spellings
    came to disagree in the first place."""
    from analyze.preflight import _as_sidecar_polarity

    got = _as_sidecar_polarity(
        {"auto_threshold": True, "no_auto_threshold": True})
    assert got["no_auto_threshold"] is True


def test_the_journal_branch_translates_and_the_sidecar_branch_does_not(tmp_path):
    """§7.7 #1 — the helper is worth nothing if the caller skips it.

    L5 was that skip: the journal stores the *form's* spelling
    (``auto_threshold`` / ``run_check``, positive) and the applier reads the
    pipeline's (``no_auto_threshold`` / ``no_run_check``, negative), so
    "Load these settings" silently dropped both. ``_as_sidecar_polarity``
    translates, and this asserts the journal branch calls it while the
    ``pipeline_info.json`` branch — already in the applier's own spelling —
    does not.

    Asserted on what ``_previous_settings`` returns rather than on the source
    text, because the source is what an earlier version of this test read and
    it could not tell a call from a mention.
    """
    from analyze.preflight import _previous_settings
    from services import run_journal as J

    out = tmp_path / "out"
    out.mkdir()
    J.open_run(out, input_dir=str(tmp_path / "in"), mode="local",
               spec={"opts": {"auto_threshold": True, "run_check": False}})

    opts, source = _previous_settings(out)
    assert source == "run journal", source
    assert opts.get("no_auto_threshold") is False, (
        "the journal branch returned the form's own spelling, so the applier "
        "silently drops auto_threshold: " + repr(opts))
    assert opts.get("no_run_check") is True, (
        "the journal branch returned the form's own spelling, so the applier "
        "silently drops run_check: " + repr(opts))


def test_the_coverage_line_counts_the_whole_plate():
    """The denominator and the named shortfall, at the paint seam."""
    from analyze.preflight import AnalyzePreflightMixin
    from pipeline.completeness import build_block

    recorded: list[str] = []

    class _Pane(AnalyzePreflightMixin):
        _preflight_dir = "/out"
        _preflight_coverage = ""
        _preflight_scanning = ""
        _preflight_scan_queued = False
        _preflight_headline_text = ""

        def _render_preflight(self, headline):
            recorded.append(self._preflight_coverage)

        def _start_coverage_scan(self):
            pass

        def _scope_line(self):
            return ""

    recs = {f"A{i:02d}": {"health": "complete", "intent": 4,
                          "covered_any": 4, "covered_csv": 4}
            for i in range(1, 95)}

    pane = _Pane()
    pane._on_coverage_scanned("/out", build_block(recs, partial_scan=True,
                                                  unreadable=2))
    assert recorded, "the coverage line was never rendered"
    line = recorded[-1]

    # The denominator is the plate's, not the subset the scan managed to read.
    # `totals["wells"]` is `len(records)` and a well that raised never enters
    # `records`, so both sides of the fraction shrank together and this read
    # "94 of 94 well(s) are complete" with a hedge after it.
    assert "94 of 96 well(s) are complete" in line, line
    assert "2 well(s) could not be read" in line, line
    assert "floor rather than the plate's" in line, line

    # ...and the clean scan says none of it.
    recorded.clear()
    pane._on_coverage_scanned("/out", build_block(recs))
    assert recorded and "floor rather than" not in recorded[-1], recorded


def test_a_scan_where_every_well_failed_is_not_silent():
    """The worst instance of the defect, and the one left unreported.

    `if not wells: return` preceded the partial check, so a scan that could read
    nothing at all said nothing at all — indistinguishable from a plate with no
    wells in it.
    """
    from analyze.preflight import AnalyzePreflightMixin
    from pipeline.completeness import build_block

    recorded: list[str] = []

    class _Pane(AnalyzePreflightMixin):
        _preflight_dir = "/out"
        _preflight_coverage = ""
        _preflight_scanning = ""
        _preflight_scan_queued = False
        _preflight_headline_text = ""

        def _render_preflight(self, headline):
            recorded.append(self._preflight_coverage)

        def _start_coverage_scan(self):
            pass

        def _scope_line(self):
            return ""

    _Pane()._on_coverage_scanned("/out", build_block({}, partial_scan=True,
                                                     unreadable=96))
    assert recorded, "a scan that read nothing reported nothing"
    assert "96 well(s) could not be read" in recorded[-1], recorded[-1]
