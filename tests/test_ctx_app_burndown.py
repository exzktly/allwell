"""Audit A8 — ``ctx.app`` burn-down ratchet.

``ViewerContext`` exists to decouple features from the ``WellViewerApp``
god-object, yet features reach straight back through ``ctx.app`` hundreds of
times, so the abstraction leaks and provides near-zero isolation. There is no
single mechanical fix — it's a strangler migration to typed state / dedicated
context accessors, done cluster by cluster.

This test turns the leak into a **burn-down metric**: the total ``ctx.app``
reference count may only ever go *down*. A change that adds a new ``ctx.app``
reference fails CI, nudging contributors to reach for the typed state slice (or
a purpose-built context accessor) instead of the god-object. When you migrate a
cluster and the count drops, lower ``CTX_APP_BUDGET`` to lock in the win.

Do NOT lower the budget by merely aliasing ``app = ctx.app`` and rewriting
``ctx.app.x`` → ``app.x`` — that games the metric without decoupling anything.
The count should fall because a cluster stopped reaching through to the
god-object, not because the reference was renamed.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_CTX_APP = re.compile(r"\bctx\.app\b")

# Frozen baseline (894 as of the code-review remediation). ONLY MOVES DOWN as
# each feature's widget handles move into the WidgetRegistry:
#   894 → 869  review_csv
#   869 → 811  heatmap
#   811 → 768  distribution
#   768 → 668  scatter (cells + agg)
#   668 → 624  lineplot + multivariate
#   624 → 587  image_table + cell_browser
#   587 → 544  smfish
#   544 → 446  barplot
#   446 → 415  cell_gating (assignments; the 2400-line builder's widget reads
#               keep resolving via the register() app-stamp)
CTX_APP_BUDGET = 415


def _count_ctx_app() -> tuple[int, dict[str, int]]:
    total = 0
    per_file: dict[str, int] = {}
    for p in (REPO_ROOT / "well_viewer").rglob("*.py"):
        n = len(_CTX_APP.findall(p.read_text()))
        if n:
            per_file[str(p.relative_to(REPO_ROOT))] = n
            total += n
    return total, per_file


def test_ctx_app_reference_count_only_shrinks():
    total, per_file = _count_ctx_app()
    assert total <= CTX_APP_BUDGET, (
        f"`ctx.app` references rose to {total}, above the burn-down budget of "
        f"{CTX_APP_BUDGET}. Reach for the typed AppState slice or a dedicated "
        f"context accessor instead of the god-object. The ratchet only moves "
        f"down.\nTop offenders:\n"
        + "\n".join(f"  {n:4d}  {f}" for f, n in
                    sorted(per_file.items(), key=lambda kv: -kv[1])[:8])
    )


def test_budget_is_not_slack():
    # Keep the budget honest: if a migration dropped the count, this fails until
    # CTX_APP_BUDGET is lowered to match (locking the win in).
    total, _ = _count_ctx_app()
    assert total >= CTX_APP_BUDGET - 20, (
        f"`ctx.app` count fell to {total}, well under the budget "
        f"{CTX_APP_BUDGET} — lower CTX_APP_BUDGET to {total} to lock in the "
        f"burn-down."
    )
