"""One verdict layer must never take a whole well down with it.

The row-cache projectors are registered once (`features/registry`) and walked
from **two** places: `data_loading.load_and_project`, when a well is first
read, and `qc.controller.recompute_qc_all`, when a control on the Cell Gating
panel moves. An adversarial review found the two walking the same list under
different failure contracts — `recompute_qc_all` wrapped each projector, and
`load_and_project` did not.

That is the wrong side of the asymmetry. A raise during the recompute costs one
verdict *column*; a raise during the load costs the **well**, because that call
is what decides whether a frame enters the cache at all. The projectors shipped
today are defensive enough that the reviewer could not make it fire, so this
was latent rather than live — which is exactly the kind of thing that stops
being latent the day someone adds a fifth layer.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest


@pytest.fixture
def one_well(tmp_path):
    csv = tmp_path / "A01.csv"
    pd.DataFrame({"nucleus_id": [1, 2, 3], "area_px": [100, 110, 120]}).to_csv(
        csv, index=False)
    ctx = SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(
        cache={}, well_paths={}, data_dir=str(tmp_path), pipeline_info={})))
    return ctx, csv


def _projectors(monkeypatch, *fns):
    import review.data.data_loading as dl

    monkeypatch.setattr(dl, "iter_cache_projectors", lambda: list(fns))
    monkeypatch.setattr(dl, "record_fov_verdict", lambda *a, **k: None)
    return dl


def test_a_raising_projector_does_not_lose_the_well(monkeypatch, one_well, caplog):
    """**The defect.** Unguarded, the exception propagated out of
    `load_and_project` and the well never reached the cache — every plot,
    count and export then missing it entirely.

    Name the wrong implementation this excludes: a bare `try: ... except:
    pass` would also pass "the well is loaded", which is why the log is
    asserted too. A layer that fails silently is the thing CLAUDE.md §5
    forbids, and it would leave a column absent with nothing saying why.
    """
    ctx, csv = one_well

    def _boom(ctx_, label):
        raise RuntimeError("this layer is broken")

    def _fine(ctx_, label):
        ctx_.state.dataset.cache[label]["Later_Pass"] = 1

    dl = _projectors(monkeypatch, _boom, _fine)

    with caplog.at_level("ERROR", logger="review"):
        df = dl.load_and_project(ctx, "A01", str(csv))

    assert df is not None and len(df) == 3, "the well was lost to one bad layer"
    assert "A01" in ctx.state.dataset.cache
    assert any("projector" in r.message or "projector" in r.getMessage()
               for r in caplog.records), (
        "the failure was swallowed without a word, so the missing column has "
        "no explanation anywhere")


def test_a_later_projector_still_runs(monkeypatch, one_well):
    """The point of guarding *each* rather than the loop: a layer that raises
    must not cancel the ones registered after it. Wrapping the whole `for`
    would pass the test above and fail this one."""
    ctx, csv = one_well

    def _boom(ctx_, label):
        raise RuntimeError("this layer is broken")

    def _fine(ctx_, label):
        ctx_.state.dataset.cache[label]["Later_Pass"] = 1

    dl = _projectors(monkeypatch, _boom, _fine)
    df = dl.load_and_project(ctx, "A01", str(csv))

    assert "Later_Pass" in df.columns, (
        "the projector registered after the failing one never ran")


def test_both_walkers_of_the_registry_agree(monkeypatch, one_well):
    """The contract itself, stated against both call sites.

    The panel's re-projection (`reproject_verdicts`, the data half of
    `recompute_qc_all`) has guarded each projector for a while; this asserts
    the loader now does too, so the two cannot drift apart again without a
    test noticing. The loader's loop is ``run_projectors``, which
    ``load_and_project`` and the narrow reads (``review/data/narrow_rows.py``)
    both call.
    """
    import ast

    from tests._repo_scan import parse_source, REPO_ROOT

    for rel, fn_name in (("review/data/data_loading.py", "run_projectors"),
                         ("review/features/qc/controller.py", "reproject_verdicts")):
        fn = next(n for n in ast.walk(parse_source(REPO_ROOT / rel))
                  if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        loops = [n for n in ast.walk(fn) if isinstance(n, ast.For)]
        guarded = [lp for lp in loops
                   if any(isinstance(b, ast.Try) for b in lp.body)]
        assert guarded, (
            f"{rel}::{fn_name} walks the projector registry without guarding "
            f"each call; one layer can take the others down")
