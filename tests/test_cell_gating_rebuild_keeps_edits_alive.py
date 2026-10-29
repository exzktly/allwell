"""A Cell Gating table rebuild must leave no deleted line-edit in the edit maps.

Regression guard. The ThreshFracOn / FluorGating / ThreshCountOn line-edits are
created once per channel and cached in ``ctx.runtime.cell_gating``, so a rebuild
can re-parent them into the new table rather than lose the user's keystrokes.
The rebuild deleted the old table, though, and the cached edits are the table's
*children*: every edit the new table re-parented survived, and every edit for a
channel the new table has no row for (a dataset loaded with a different channel
set — reported when Review opened a dataset while an analysis was running — or a
ratio removed) died with the old table while staying in the map. The next
ThreshFracOn edit then walked the map and raised ``RuntimeError: Internal C++
object (PySide6.QtWidgets.QLineEdit) already deleted`` at
``cell_gating_on_threshold_frac_on_change``'s ``float(edit.text())``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

READOUT = "gfp"
DROPPED = "cy5"
NUCLEAR = "rfp"


def _ctx(channels, smfish=()):
    return SimpleNamespace(
        state=SimpleNamespace(
            channel=SimpleNamespace(
                fluor_channels=list(channels),
                ratio_metrics=[],
                smfish_channels=set(smfish),
                seg_channel_token=NUCLEAR,
            ),
            cell_gating=SimpleNamespace(
                thresh_frac_on={}, fluor_gates={}, thresh_count_on={}),
        ),
    )


def _flush_deletes():
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_a_channel_that_leaves_the_table_takes_no_dead_edit_with_it():
    pytest.importorskip("PySide6")
    import shiboken6
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    from review.features.home.cell_gating.controller import (
        _cell_gating_build_channel_controls,
        _cell_gating_sync_gate_state,
    )

    _qapp = QApplication.instance() or QApplication([])
    inner, thresh_inner = QWidget(), QWidget()
    QVBoxLayout(inner)
    QVBoxLayout(thresh_inner)
    try:
        # The count column only has a field for an smFISH channel, so make the
        # dropped channel one: all three maps then hold an edit for it.
        ctx = _ctx([READOUT, DROPPED, NUCLEAR], smfish=[DROPPED])
        ctx.runtime = SimpleNamespace(cell_gating=SimpleNamespace(
            inner=inner, thresh_inner=thresh_inner,
            fluor_gate_edits={}, thresh_frac_edits={}, thresh_count_edits={},
        ))
        _cell_gating_build_channel_controls(ctx)
        rt = ctx.runtime.cell_gating
        assert DROPPED in rt.thresh_frac_edits
        assert DROPPED in rt.thresh_count_edits
        kept = rt.thresh_frac_edits[READOUT]
        rt.thresh_frac_edits[DROPPED].setText("12.5")
        _cell_gating_sync_gate_state(ctx)

        # The next dataset has no DROPPED channel; rebuild and let Qt run the
        # deferred deletes the rebuild queued, as the event loop would.
        ctx.state.channel.fluor_channels = [READOUT, NUCLEAR]
        _cell_gating_build_channel_controls(ctx)
        _flush_deletes()

        dead = sorted(
            f"{name}[{ch}]"
            for name in ("fluor_gate_edits", "thresh_frac_edits", "thresh_count_edits")
            for ch, edit in getattr(rt, name).items()
            if not shiboken6.isValid(edit)
        )
        assert dead == [], f"deleted line-edits left in the maps: {dead}"
        # A surviving channel keeps its very widget (the user's keystrokes) ...
        assert rt.thresh_frac_edits[READOUT] is kept
        # ... the dropped channel has no row, so no edit, and its value stays on
        # the typed slice, which is the source of truth.
        assert set(rt.thresh_frac_edits) == {READOUT, NUCLEAR}
        assert ctx.state.cell_gating.thresh_frac_on[DROPPED] == 12.5
        # The exact call the report raised from: it reads every edit's text.
        for edit in rt.thresh_frac_edits.values():
            float(edit.text())

        # And the channel coming back gets a live row seeded from the slice.
        ctx.state.channel.fluor_channels = [READOUT, DROPPED, NUCLEAR]
        _cell_gating_build_channel_controls(ctx)
        _flush_deletes()
        assert rt.thresh_frac_edits[DROPPED].text() == "12.5"
        assert all(shiboken6.isValid(e) for e in rt.thresh_frac_edits.values())
    finally:
        inner.deleteLater()
        thresh_inner.deleteLater()
        _flush_deletes()
