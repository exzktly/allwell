"""The Spatial tab's cell export says when wells were not read while its scope was chosen.

The position columns and the timepoint the export uses are chosen by reading the panels' wells, and a well whose
rows would not read was skipped silently — so both choices could be made without it while the file presented them
as the selection's. The scope now carries a note naming those wells, which the file's ``#`` block prints.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def test_a_well_that_could_not_be_read_is_named_in_the_scope(qtbot, tmp_path, monkeypatch):
    from review.features.spatial.cell_scope_compute import cell_scope
    from tests._minimal_shell import load_dataset, minimal_shell
    from tests.fixtures.mini_dataset import build_mini_dataset

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, build_mini_dataset(tmp_path / "mini", with_shape=True))
    st = ctx.state.feature_slice("spatial")
    st.level, st.timepoint = "cell", None
    st.frames = [{"well": "A01"}, {"well": "A02"}]

    import review.data.data_loading as dl

    real = dl.get_rows

    def get_rows(ctx_, label):
        if label == "A02":
            raise MemoryError("simulated")
        return real(ctx_, label)

    monkeypatch.setattr(dl, "get_rows", get_rows)
    scope = cell_scope(ctx)
    assert ("wells not read when this scope was chosen",
            "A02 could not be read, so the position columns and the timepoint were chosen from the other wells"
            ) in scope.notes
    monkeypatch.undo()
    assert not any(key == "wells not read when this scope was chosen" for key, _v in cell_scope(ctx).notes)
