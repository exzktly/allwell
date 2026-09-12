"""Every surface that can produce a short artifact says it is short.

A grid drawn from a TRUNCATED inventory is made entirely of real images. Nothing
counts the gap — no cell is missing, no fallback mark is drawn — so the result
reads as complete and is short by however many frames the scan never reached.
That is §7 of ``CLAUDE.md`` exactly: degraded output must never be
indistinguishable from good output.

The refused scale bar, this branch's row 1, already reaches all five surfaces
through one shared clause. The truncated inventory reached **one** — a fact
A's own review gate found and left as row 4 because it was inherited rather
than introduced. This is that row.

The half worth guarding is not that the sentence exists but that it travels
with its sibling: a saved file that reports a missing scale bar and stays quiet
about missing frames is worse than one that reports neither, because the reader
now believes the line is telling them what is wrong.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from review.features.image_browser.image_table.image_compose import (
    partial_scan_clause,
    partial_scan_report,
)

#: The package whose surfaces produce an artifact from a render pass.
_MODULES = (
    "review/features/image_browser/image_table/controller.py",
    "review/features/image_browser/image_table/export.py",
)

_BAR = "scale_bar_refusal_clause"
_PARTIAL = "partial_scan_clause"


def _ctx(*records):
    return SimpleNamespace(
        state=SimpleNamespace(image_table=SimpleNamespace(
            partial_scans=set(records))))


# ── the clause itself ───────────────────────────────────────────────────────

def test_a_clean_pass_says_nothing():
    assert partial_scan_clause(_ctx()) == ""


def test_the_clause_names_the_wells_the_count_and_the_reason():
    ctx = _ctx((("A01", "gfp", "24h"), "listing stopped at 500 entries"),
               (("B02", "gfp", "24h"), "listing stopped at 500 entries"))
    clause = partial_scan_clause(ctx)

    assert "A01" in clause and "B02" in clause
    assert "2 wells" in clause
    assert "INCOMPLETE" in clause
    assert "listing stopped at 500 entries" in clause


def test_one_well_is_not_called_wells():
    clause = partial_scan_clause(_ctx((("A01", "gfp", "24h"), "stopped short")))
    assert "1 well " in clause and "1 wells" not in clause


def test_a_record_with_no_reason_still_says_something_happened():
    """An empty reason is not an excuse to print a dangling colon."""
    clause = partial_scan_clause(_ctx((("A01", "gfp", "24h"), "")))
    assert "a scan stopped short" in clause


def test_the_wells_are_deduplicated_and_ordered():
    """Four truncated channels in one well is one short well, not four."""
    ctx = _ctx((("B02", "gfp", "24h"), "r"), (("A01", "gfp", "24h"), "r"),
               (("A01", "rfp", "24h"), "r"), (("A01", "dapi", "24h"), "r"))
    clause = partial_scan_clause(ctx)
    assert "2 wells (A01, B02)" in clause


def test_the_clause_is_neutral_about_what_the_artifact_is():
    """"This grid" and "this file" is the caller's framing.

    The two hand-written versions this replaces disagreed on more than framing:
    the Generate line counted the wells and named the reasons, and the saved-PNG
    line did neither — so the artifact that outlives the session carried *less*
    than the transient line, which is backwards.
    """
    clause = partial_scan_clause(_ctx((("A01", "gfp", "24h"), "stopped short")))
    assert "grid" not in clause and "file" not in clause


def test_the_reader_and_the_clause_agree():
    ctx = _ctx((("A01", "gfp", "24h"), "stopped short"))
    wells, reasons = partial_scan_report(ctx)
    clause = partial_scan_clause(ctx)
    assert all(w in clause for w in wells)
    assert all(r in clause for r in reasons)


# ── the sentence reaches the real status lines ──────────────────────────────

def test_the_generate_line_carries_it():
    from review.features.image_browser.image_table.controller import (
        generate_status_message,
    )

    ctx = _ctx((("A01", "gfp", "24h"), "listing stopped short"))
    ctx.state.image_table.scale_bar_refusals = set()
    ctx.state.image_table.raw_fallbacks = set()
    ctx.state.image_table.scan_misses = set()
    cell = {"well": "A01", "chan": "GFP", "tp": "24h", "fov": "1"}

    msg = generate_status_message(ctx, [[cell]], 1, {(0, 0): object()}, 0, 0, [])

    assert "A01" in msg and "INCOMPLETE" in msg


@pytest.mark.parametrize("surface", ["image_table_export", "image_table_copy_png",
                                     "image_table_copy_svg",
                                     "image_table_save_to_gallery"])
def test_each_saved_artifact_surface_appends_it_next_to_the_bar_clause(surface):
    """Read off the source rather than driven: three of these open a file
    dialog or touch the system clipboard, which a test must not do. What is
    checked is the thing that was actually missing — the call — and the guard
    above is what keeps the two calls together.
    """
    import inspect

    from review.features.image_browser.image_table import export

    source = inspect.getsource(getattr(export, surface))
    assert f"{_PARTIAL}(ctx)" in source, f"{surface} never reports a short list"
    assert f"{_BAR}(ctx)" in source
