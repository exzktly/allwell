"""What the Playground's focus curve was measured on, and what that licenses.

**Fast lane on purpose.** `tests/test_playground.py` is `pytestmark =
pytest.mark.slow` in its entirety, so a guard living there gates nothing: the
lane that runs on every push and in CI would never see it. Everything here is
pure logic over a hand-built context — no app, no segmentation, no images —
which is exactly the split CLAUDE.md asks new behaviour to be written for.

The defects below came from an adversarial review of the whole per-cell focus
feature, and they share a shape: the plot was **right**, and what a reader was
licensed to conclude from it was not. A correct picture of the wrong
population is worse than a wrong picture, because nothing about it looks
wrong.
"""
from __future__ import annotations

import os

os.environ.setdefault("MPLBACKEND", "Agg")

def _focus_ctx(channel, nuclear, cache=None):
    """A ctx shaped for `_focus_caveats` / `_correction_subject` only."""
    from types import SimpleNamespace

    return SimpleNamespace(
        state=SimpleNamespace(
            dataset=SimpleNamespace(
                pipeline_info={"nuclear_token": nuclear},
                cache=cache if cache is not None else {}),
            playground=SimpleNamespace(
                channel=channel, flatfield_mode="none",
                background_method="sliding_disk")))


def test_a_curve_measured_on_another_channel_says_so(monkeypatch):
    """**The defect.** The tab measures whatever channel is selected here,
    with this tab's own Correction dropdowns — which is the tab's purpose. The
    column Cell Gating's cut applies to is always the NUCLEAR channel with the
    run's settings. Measured on identical optics, geometry and mask, a dim
    textured reporter reads 1.78x the nuclear median with an eightfold p5-p95
    spread, so a cut read off the wrong curve and typed into Cell Gating can
    exclude every cell in the nuclear population.

    Nothing on the figure said so. The channel appeared in the title, where it
    reads as provenance rather than as a warning.
    """
    from review.features.image_browser.playground import controller as pc

    ctx = _focus_ctx("GFP", "dapi")
    monkeypatch.setattr(pc, "_st", lambda c: c.state.playground)
    caveats = pc._focus_caveats(ctx)
    assert any("GFP" in c and "DAPI" in c for c in caveats), caveats


def test_the_nuclear_channel_itself_gets_no_channel_caveat(monkeypatch):
    """A warning on every curve is a warning on none. The ordinary case — the
    user is looking at the channel the cut applies to — must stay silent."""
    from review.features.image_browser.playground import controller as pc

    ctx = _focus_ctx("DAPI", "dapi")
    monkeypatch.setattr(pc, "_st", lambda c: c.state.playground)
    assert not any("cut in Cell Gating applies" in c
                   for c in pc._focus_caveats(ctx))


def test_a_dataset_with_no_focus_column_is_told_the_number_cannot_be_applied(
        monkeypatch):
    """**The defect the user asked about**: a number appearing with no way to
    act on it. This measurement comes from pixels, not the CSV, so the curve
    draws for a dataset carrying no column at all — cut marked, counts live —
    and the number cannot be applied without re-running the plate."""
    import pandas as pd

    from review.features.image_browser.playground import controller as pc

    ctx = _focus_ctx("DAPI", "dapi",
                     cache={"A01": pd.DataFrame({"nucleus_id": [1, 2]})})
    monkeypatch.setattr(pc, "_st", lambda c: c.state.playground)
    assert any("no per-cell focus column" in c for c in pc._focus_caveats(ctx))


def test_a_dataset_that_has_the_column_is_not_told_it_is_missing(monkeypatch):
    import pandas as pd

    from pipeline.field_quality_record import FOCUS_COLUMN
    from review.features.image_browser.playground import controller as pc

    ctx = _focus_ctx("DAPI", "dapi", cache={"A01": pd.DataFrame(
        {"nucleus_id": [1, 2], FOCUS_COLUMN: [0.3, 0.4]})})
    monkeypatch.setattr(pc, "_st", lambda c: c.state.playground)
    assert not any("no per-cell focus column" in c
                   for c in pc._focus_caveats(ctx))


def test_an_empty_cache_says_nothing_about_the_column(monkeypatch):
    """Three answers, not two — the same rule the Cell Gating panel follows.
    Nothing cached is not evidence that the column is absent."""
    from review.features.image_browser.playground import controller as pc

    ctx = _focus_ctx("DAPI", "dapi", cache={})
    monkeypatch.setattr(pc, "_st", lambda c: c.state.playground)
    assert not any("no per-cell focus column" in c
                   for c in pc._focus_caveats(ctx))


def test_the_saved_figures_subject_records_the_correction(monkeypatch):
    """**The defect.** The subject named channel, well, FOV, timepoint and
    mask provenance and NOT the corrections, which change the value. Five of
    the seven methods move `edge_focus` by 2.6 % or less, but `fft_bandpass`
    moves it 1.27x — so a saved figure could not be told from one measured on
    a differently-corrected frame, in the case where it matters most."""
    from review.features.image_browser.playground import controller as pc

    ctx = _focus_ctx("DAPI", "dapi")
    monkeypatch.setattr(pc, "_st", lambda c: c.state.playground)
    subject = pc._correction_subject(ctx)
    assert "none" in subject and "sliding_disk" in subject, subject


def test_the_focus_handler_actually_passes_the_caveats_to_the_figure():
    """**The wiring, which every test above passes without.**

    Removing `caveats=_focus_caveats(ctx)` from the call failed nothing: the
    tests above call the producer directly, so they measure a function that
    could be wired to nothing at all. That is CLAUDE.md §7.8 — the assertion
    weaker than the claim — and it is the third time in this one body of work
    that a fix was mutated and the tests stayed green, which is what makes a
    structural assertion worth its brittleness here.

    Driving `playground_focus_now` end to end would need a segmented frame, a
    real mask and a decoded image; the seam that broke is one keyword at one
    call site, and this is what says it is still there.
    """
    import ast

    from tests._repo_scan import parse_source, REPO_ROOT

    path = (REPO_ROOT / "review" / "features" / "image_browser" /
            "playground" / "controller.py")
    fn = next(n for n in ast.walk(parse_source(path))
              if isinstance(n, ast.FunctionDef) and n.name == "playground_focus_now")

    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "distribution_from_values"]
    assert calls, "the focus handler no longer builds a distribution"

    # The call that draws the real curve is the one that is NOT a refusal.
    # The early return for "no mask on screen" builds an empty distribution
    # with `declined=`, and that correctly carries no caveats: a refusal
    # already occupies the whole figure and states its own reason.
    drawing = [c for c in calls
               if "declined" not in {k.arg for k in c.keywords}]
    assert drawing, "no call builds a distribution from measured values"
    for call in drawing:
        kwargs = {k.arg for k in call.keywords}
        assert "caveats" in kwargs, (
            "the drawn curve is built without caveats, so a curve measured on "
            "a channel the cut does not apply to says nothing about it")


def test_the_focus_handler_puts_the_correction_in_the_subject():
    """Same seam, same reason. The subject is what a saved figure carries."""
    import ast

    from tests._repo_scan import parse_source, REPO_ROOT

    path = (REPO_ROOT / "review" / "features" / "image_browser" /
            "playground" / "controller.py")
    fn = next(n for n in ast.walk(parse_source(path))
              if isinstance(n, ast.FunctionDef) and n.name == "playground_focus_now")
    named = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)}
    assert "_correction_subject" in named, (
        "the figure's subject no longer records which correction produced the "
        "pixels, so a saved curve cannot be told from a differently-corrected "
        "one -- 1.27x for fft_bandpass")
