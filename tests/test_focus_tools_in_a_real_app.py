"""The focus tools, driven through a real ``ReviewApp``.

**Slow lane on purpose.** Every other test of this feature — about two hundred
assertions across seven files — runs on a hand-built context in the fast lane.
That lane never constructs an application, so it cannot see a control wired to
nothing, a panel that reads state the shell has just emptied, or a surface that
does not repaint. CLAUDE.md §7.8 names the price this repository already paid
for that once: a tab that raised on every real construction while roughly a
hundred and thirty of its own tests were green.

Each test here corresponds to a defect an adversarial review found that **no
fast-lane test could have caught**, and each was watched failing against the
unfixed code before the fix went in.

Run with::

    scripts/test.sh tests/test_focus_tools_in_a_real_app.py -m ""
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import numpy as np

from pipeline.field_quality_record import FOCUS_COLUMN
from review.domain.qc_params import QCParams


def _give_the_plate_focus(app, soft_share=0.35, sharp=0.30, soft=0.02):
    """Write a realistic focus column onto every cached well.

    The fixture dataset predates the column, and this session installs no
    TensorFlow, so the values are injected rather than produced. Their *shape*
    is what matters: a sharp population with a soft corner, which is what a
    focus gradient across a field actually looks like.
    """
    rng = np.random.default_rng(11)
    for label, df in app._app_state.dataset.cache.items():
        if df is None or len(df) == 0:
            continue
        n = len(df)
        n_soft = max(1, int(n * soft_share))
        vals = np.concatenate([
            rng.normal(sharp, 0.04, n - n_soft).clip(0.12, None),
            rng.normal(soft, 0.006, n_soft).clip(0.004, None)])
        rng.shuffle(vals)
        df[FOCUS_COLUMN] = vals[:n]


# ── finding 3: a reset must not make the panel deny the measurement ─────────

def test_a_gating_reset_does_not_claim_the_dataset_was_never_measured(loaded_app):
    """**The defect.** The reset empties the row cache, and every focus
    predicate reads the cache — so the panel announced "This dataset was
    analysed before per-cell focus was measured", which is false, and named a
    remedy (re-run the whole plate) that is not needed.

    ``focus_gate``'s own module docstring calls this the one distinction that
    must never collapse. A fast-lane test cannot reach it: it needs the real
    reset scope, which needs a real app.
    """
    from review.data.focus_gate import focus_status
    from review.persistence.reset_scopes import clear_analysis

    app = loaded_app
    _give_the_plate_focus(app)
    app._app_state.qc.params = QCParams(focus_min=0.10)

    before, _ = focus_status(app._ctx)
    assert "before per-cell focus was measured" not in before, before

    # The dialog's own entry point, which isolates each scope: it logs a scope
    # that raises and carries on, so the returned list is the only evidence the
    # reset ran at all. Asserting it keeps this test from passing vacuously
    # against a gating scope that blew up before it emptied anything.
    assert clear_analysis(app._ctx, ["gating"]) == ["gating"]

    after, _ = focus_status(app._ctx)
    assert "before per-cell focus was measured" not in after, (
        "the reset emptied the cache and the panel now denies the dataset was "
        "measured at all: " + after)


def test_clearing_curation_does_not_deny_the_measurement_either(loaded_app):
    """The same state by a second route, which notifies nothing at all."""
    from review.data.focus_gate import focus_status
    from review.persistence.reset_scopes import clear_curation

    app = loaded_app
    _give_the_plate_focus(app)
    app._app_state.qc.params = QCParams(focus_min=0.10)
    clear_curation(app._ctx)
    after, _ = focus_status(app._ctx)
    assert "before per-cell focus was measured" not in after, after
