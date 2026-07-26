"""The Cell Gating tab's "Included for analysis" readout.

Nothing in the app reported how many cells the gating actually keeps, so the
only way to check whether a gate had taken effect was to eyeball another plot.
The tab now carries a live count.

The number is the **composed** inclusion — ``df_included_mask``'s AND of manual
curation, QC, crop, and the cell-gating / scatter-gate layer — not
``GatingWorker``'s own tally. Two reasons, both pinned below: the worker counts
the gating layer alone, so pairing that with a QC number gives a line whose
figures do not reconcile; and the worker does not always run (a dataset whose
gates are all at their defaults skips the pass on load).
"""
from __future__ import annotations

import pandas as pd

from well_viewer.features.grouping.cell_gating.gating_compute import (
    format_inclusion_counts,
    inclusion_counts,
)


def _df(n=4, **layers):
    """``n`` cells; pass ``Included=[...]`` / ``QC_Pass=[...]`` etc. to set a
    verdict column. Omitted columns are absent, not all-1 — that distinction is
    the point of ``test_absent_layers_are_all_pass``."""
    cols = {"cell": list(range(n))}
    cols.update(layers)
    return pd.DataFrame(cols)


# ── inclusion_counts ────────────────────────────────────────────────────────

def test_no_frames_is_all_zeros():
    assert inclusion_counts([]) == (0, 0, 0)
    assert inclusion_counts(None) == (0, 0, 0)


def test_absent_layers_are_all_pass():
    """A frame with no verdict columns is fully included — matching
    df_included_mask, where each optional layer fails open."""
    assert inclusion_counts([_df(4)]) == (4, 4, 0)


def test_none_and_empty_frames_are_skipped():
    assert inclusion_counts([None, pd.DataFrame(), _df(3)]) == (3, 3, 0)


def test_each_layer_excludes_on_its_own():
    for layer in ("Included", "QC_Pass", "Crop_Pass", "Gate_Pass"):
        frame = _df(4, **{layer: [1, 1, 0, 0]})
        included, total, _qc = inclusion_counts([frame])
        assert (included, total) == (2, 4), layer


def test_layers_compose_as_an_and():
    """Different layers rejecting different cells removes the union of both."""
    frame = _df(4, Included=[1, 1, 1, 0], Gate_Pass=[1, 1, 0, 1])
    assert inclusion_counts([frame]) == (2, 4, 0)


def test_qc_count_is_a_diagnostic_not_a_disjoint_bucket():
    """The QC failures are mostly *inside* the excluded set, not alongside it —
    which is exactly why the numerator is the composed mask rather than the
    gating worker's own tally. Here one cell fails QC *and* is gated out: it is
    excluded once, and still reported as a QC failure."""
    frame = _df(4, QC_Pass=[1, 0, 1, 1], Gate_Pass=[1, 0, 0, 1])
    included, total, qc_failed = inclusion_counts([frame])
    assert (included, total) == (2, 4)
    assert qc_failed == 1


def test_counts_sum_across_wells():
    a = _df(3, Included=[1, 1, 0])
    b = _df(5, Included=[1, 0, 0, 1, 1], QC_Pass=[1, 1, 1, 0, 1])
    included, total, qc_failed = inclusion_counts([a, b])
    assert total == 8
    assert included == 2 + 2      # b's 4th cell is Included but fails QC
    assert qc_failed == 1


def test_non_numeric_verdicts_do_not_crash():
    frame = _df(3, Included=["1", "", "0"], QC_Pass=["1", "x", "0"])
    included, total, qc_failed = inclusion_counts([frame])
    assert total == 3
    # "" and "x" coerce to NaN: Included fails closed (dropped), QC_Pass fails
    # open (kept, and not counted as a QC failure).
    assert included == 1
    assert qc_failed == 1


# ── format_inclusion_counts ─────────────────────────────────────────────────

def test_blank_when_no_data_is_loaded():
    """Blank, not a row of zeros — the QC readout above it does the same."""
    assert format_inclusion_counts(0, 0, 0) == ""


def test_steady_state_line():
    assert format_inclusion_counts(12345, 20000, 1204) == (
        "Included for analysis: 12,345 / 20,000 cells  ·  1,204 failed QC"
    )


def test_pending_marks_the_numbers_as_stale():
    text = format_inclusion_counts(12345, 20000, 1204, pending=True)
    assert text.endswith("·  updating…")
    assert "12,345 / 20,000" in text


def test_failed_says_the_settings_were_never_applied():
    text = format_inclusion_counts(12345, 20000, 1204, failed=True)
    assert text.endswith("·  last gating pass failed")


def test_pending_wins_over_failed():
    """A fresh pass supersedes the previous failure — the in-flight marker is
    the more current statement."""
    text = format_inclusion_counts(1, 2, 0, pending=True, failed=True)
    assert text.endswith("·  updating…")
