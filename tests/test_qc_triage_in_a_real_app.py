"""QC triage through the real app: build the real Home tab, select a row, press
*Exclude field…*, and watch the field's cells leave the analysis (CLAUDE.md
§7.6 #1 — press the button, do not call the function under it).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from pipeline.field_quality import FIELD_QUALITY_BLOCK  # noqa: E402

WELLS = ("A01", "A02", "B01", "B02")
TPS = ("0h", "24h", "48h")
FOVS = ("1", "2", "3")
BAD = ("B02", "2", "24h")


def _fq_block():
    """Twelve fields per timepoint — the scorer compares acquisition metrics
    within a timepoint and declines below eight — every metric varying, and
    one field badly defocused."""
    wells = {}
    for wi, w in enumerate(WELLS):
        fields = []
        for ti, tp in enumerate(TPS):
            for fi, fov in enumerate(FOVS):
                k = wi * 9 + ti * 3 + fi
                focus = 2.0 if (w, fov, tp) == BAD else 27.0 + k * 0.01
                fields.append({"fov": fov, "timepoint": tp, "focus": focus,
                               "sat_frac": 0.0, "dyn_range": 900.0 + k,
                               "nuc_count": 300 + k,
                               "nuc_area_median": 150.0 + k,
                               "bg_level": 100.0 + k})
        wells[w] = {"well": w, "fields": fields, "measured": 9, "measured_of": 9}
    return {"wells": wells, "partial_wells": [], "measured": 36, "measured_of": 36}


@pytest.fixture
def three_fov_app(viewer_app, tmp_path):
    from tests.fixtures.mini_dataset import build_mini_dataset
    viewer_app._load_path(build_mini_dataset(tmp_path / "plate", n_fovs=3))
    return viewer_app


def _kept(app, well):
    from review.data.data_loading import df_included_mask, get_rows
    return int(df_included_mask(get_rows(app._ctx, well)).sum())


def test_pressing_exclude_takes_the_field_out_of_the_analysis(three_fov_app, monkeypatch):
    app = three_fov_app
    ctx = app._ctx
    ctx.state.dataset.pipeline_info[FIELD_QUALITY_BLOCK] = _fq_block()
    before = _kept(app, "B02")

    from review.features.home.tab_view import active_subtabs
    names = active_subtabs(ctx)
    assert "QC Triage" in names
    ctx.runtime.home.subtabs.setCurrentIndex(names.index("QC Triage"))
    ctx.runtime.triage.refresh()

    table = ctx.widgets.get("_triage_table")
    problem = [table.item(r, 4).text() for r in range(table.rowCount())]
    row = next(i for i, text in enumerate(problem) if text.startswith("Flagged by the run"))
    assert tuple(table.item(row, c).text() for c in (1, 2, 3)) == BAD
    table.selectRow(row)

    from review.core import ui_helpers
    monkeypatch.setattr(ui_helpers, "ask_name_dialog", lambda *a, **k: "blurred")
    from PySide6.QtWidgets import QPushButton
    exclude = next(b for b in table.parent().findChildren(QPushButton)
                   if b.text() == "Exclude field…")
    assert exclude.isEnabled()
    exclude.click()

    assert _kept(app, "B02") == before - 6
    assert table.item(row, 5).text() == "field excluded — blurred"
