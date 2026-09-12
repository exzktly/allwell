"""``reset_view`` must write every domain ``VIEW_DOMAINS`` claims.

The view tier's contract is stated in ``persistence/reset_scopes``' own module
docstring: a scope names every storage domain it owns and its clear is
responsible for all of them, live state *and* the file the value is written to.
``reset_view`` claimed ``line_order`` and never wrote it. ``view_state.reset``
emptied the live draw order (two ``PlotState`` fields, cleared generically by
``restore_defaults``), the ``line_order`` section of persistence.json kept the
old order, and the next load read it back — ``line_order.load_from_data_dir``
then calls ``_reorder_selections_by_line_order``, so the reset the user asked
for came back *and* permanently reordered ``selections``.

The assertion is at the boundary (a recording ``ctx.persistence``), not on the
value some function computed, and the second test pins the ordering: the write
has to happen after the live order is cleared, or it persists the very state it
is supposed to drop.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from review.persistence import reset_scopes


@pytest.fixture()
def stub(monkeypatch):
    """A context just rich enough to run ``reset_view``.

    ``view_state.reset`` needs the whole application (widgets, redraw, the
    fold-change sync), so it is replaced by a stand-in that does the two things
    that matter to this test: clear the live draw order, exactly as
    ``restore_defaults(ctx.state.plot)`` does, and record that the ``view_state``
    section was re-saved, which the real ``reset`` does itself rather than
    through ``_save``.
    """
    from review.features.heatmap.state import HeatmapState
    from review.features.multivariate.state import MultivariateState
    from review.persistence import view_state

    written: list[tuple[str, list, list]] = []
    plot = SimpleNamespace(line_order_rsets=["rset-1"], line_order_wells=["A01"])

    class _Persistence:
        def save(self, domain: str) -> None:
            # Capture the live order at the moment of the write, so a save
            # issued before the clear is distinguishable from one issued after.
            written.append(
                (domain, list(plot.line_order_rsets), list(plot.line_order_wells))
            )

    slices = {"heatmap": HeatmapState(), "multivariate": MultivariateState()}
    ctx = SimpleNamespace(
        persistence=_Persistence(),
        runtime=SimpleNamespace(
            heatmap=SimpleNamespace(layouts=["a layout from before the reset"]),
            image_table=SimpleNamespace(reset_cb=None),
        ),
        state=SimpleNamespace(feature_slice=slices.get, plot=plot),
    )

    def _fake_reset(_ctx):
        plot.line_order_rsets = []
        plot.line_order_wells = []
        written.append(("view_state", [], []))

    monkeypatch.setattr(view_state, "reset", _fake_reset)
    return ctx, written


def test_reset_view_writes_every_domain_it_claims(stub):
    ctx, written = stub

    reset_scopes.reset_view(ctx)

    saved = [domain for domain, _r, _w in written]
    missing = [d for d in reset_scopes.VIEW_DOMAINS if d not in saved]
    assert not missing, (
        f"reset_view claims {list(reset_scopes.VIEW_DOMAINS)} but wrote "
        f"{saved}; {missing} stays on disk and is re-applied on the next load"
    )


def test_the_line_order_written_is_the_cleared_one(stub):
    ctx, written = stub

    reset_scopes.reset_view(ctx)

    line_order = [entry for entry in written if entry[0] == "line_order"]
    assert line_order, "line_order was never written"
    _domain, rsets, wells = line_order[-1]
    assert rsets == [] and wells == [], (
        "line_order was written before view_state.reset cleared the live "
        f"order, so the stale order was persisted again: {rsets}, {wells}"
    )
