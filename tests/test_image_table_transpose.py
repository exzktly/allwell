"""Image Table → Table ▾ → Transpose rows and columns.

Two halves. The swap itself (``state.transpose_spec``) is pure and runs in the fast lane: each axis moves whole, the
Fixed values and filters stay, Plate mode turns the plate on its side, and doing it twice is no change. The menu action is driven
through the real Image Table tab (``image_table_app``, slow lane): a generated table is drawn again with every cell at
its transposed position, and in Plate mode the plate turns — the entry is never disabled.
"""

from __future__ import annotations

import types

import pytest

from review.features.image_browser.image_table.state import Axis, ChannelSpec, TableSpec, transpose_spec


def _spec() -> TableSpec:
    return TableSpec(
        rows=Axis(root="well", channels=[ChannelSpec("GFP"), ChannelSpec("mCherry")], overlay=True),
        cols=Axis(root="timepoint"),
        fixed={"fov": "1"},
        wrap=4,
        filters={"timepoint": {"0", "24"}},
    )


def test_each_axis_moves_whole_and_the_rest_stays():
    spec = _spec()
    assert transpose_spec(spec) is True
    assert spec.rows.root == "timepoint" and spec.rows.channels == [] and spec.rows.overlay is False
    assert spec.cols.root == "well"
    assert [c.channel for c in spec.cols.channels] == ["GFP", "mCherry"]
    assert spec.cols.overlay is True
    assert spec.fixed == {"fov": "1"}
    assert spec.filters == {"timepoint": {"0", "24"}}
    assert spec.wrap == 4


def test_transposing_twice_is_no_change():
    spec = _spec()
    transpose_spec(spec)
    transpose_spec(spec)
    assert spec == _spec()


def test_plate_mode_turns_the_plate_and_leaves_the_axes():
    """Reported: the entry did nothing in Plate mode (it was disabled, with no reason a menu can show)."""
    from review.features.image_browser.image_table.state import spec_from_state, spec_to_state_fields

    spec = _spec()
    spec.plate = True
    assert transpose_spec(spec) is True
    assert spec.rows.root == "well" and spec.cols.root == "timepoint"
    fields = spec_to_state_fields(spec)
    assert (fields["row_dims"], fields["col_dims"]) == (["well_col"], ["well_row"])
    st = types.SimpleNamespace(**fields)
    assert spec_from_state(st).plate_transposed is True, "a saved turned plate comes back turned"
    transpose_spec(spec)
    fields = spec_to_state_fields(spec)
    assert (fields["row_dims"], fields["col_dims"]) == (["well_row"], ["well_col"])


def _cells(ctx) -> list[list[dict]]:
    return [[dict(cell) for cell in row] for row in ctx.runtime.image_table.cells]


@pytest.mark.qt
def test_the_menu_action_redraws_the_generated_table_transposed(image_table_app, monkeypatch):
    """Through the real Table menu: a generated table is redrawn, cell [r][c] now at [c][r].

    The fixture's table is laid out with distinct rows and columns, so a transpose that left the grid as it was, or
    only swapped its shape, could not pass."""
    from review.features.image_browser.image_table.builder_view import builder_flush_pending, builder_spec
    from review.features.image_browser.image_table.controller import image_table_generate

    ctx = image_table_app._ctx
    spec = builder_spec(ctx)
    spec.plate = False
    builder_flush_pending(ctx)
    from review.features.image_browser.image_table.build_layout import image_table_apply_spec

    image_table_apply_spec(ctx, spec, render=False)
    image_table_generate(ctx)
    before = _cells(ctx)
    assert len(before) > 1 and len(before[0]) > 1, "the fixture's table must have a shape to transpose"
    assert ctx.state.image_table.last_render, "the table must be on screen for the transpose to redraw it"
    from review.features.image_browser.image_table import controller as C

    redraws: list[int] = []
    real_generate = C.image_table_generate

    def _spy(*a, **k):
        redraws.append(1)
        return real_generate(*a, **k)

    monkeypatch.setattr(C, "image_table_generate", _spy)

    statuses: list[str] = []
    real_status = ctx.notify.set_status

    def _record(msg, *a, **k):
        statuses.append(str(msg))
        return real_status(msg, *a, **k)

    monkeypatch.setattr(ctx.notify, "set_status", _record)
    act = ctx.widgets.get("_image_table_transpose_act")
    act.trigger()

    # The grid holds at most _AXIS_MAX columns: the fixture's rows beyond that do not fit across, and the
    # status line must say how many images that left out.
    from review.features.image_browser.image_table.build_layout import _AXIS_MAX

    after = _cells(ctx)
    kept = min(len(before), _AXIS_MAX)
    assert len(after) == len(before[0]) and len(after[0]) == kept
    for r, row in enumerate(before[:kept]):
        for c, cell in enumerate(row):
            assert {k: after[c][r][k] for k in ("well", "chan", "tp")} == {k: cell[k] for k in ("well", "chan", "tp")}
    assert redraws == [1], "the table on screen must be drawn again, once"
    lost = (len(before) - kept) * len(before[0])
    expected = "Image Table: rows and columns transposed."
    if lost:
        expected += (f" {lost} image(s) did not fit the column limit — set Wrap to band the columns and show "
                     "them all.")
    assert statuses[-1] == expected


@pytest.mark.qt
def test_the_menu_action_turns_a_generated_plate(image_table_app):
    """Through the real menu in Plate mode: the entry is enabled, and plate cell [r][c] lands at [c][r]."""
    from review.features.image_browser.image_table import builder_view as BV
    from review.features.image_browser.image_table.controller import image_table_generate

    ctx = image_table_app._ctx
    BV._set_plate(ctx, True)
    BV.builder_flush_pending(ctx)
    BV._apply_now(ctx)
    image_table_generate(ctx)
    before = _cells(ctx)
    assert len(before) > 1 and len(before[0]) > 1, "the fixture's plate must have two rows and two columns"
    act = ctx.widgets.get("_image_table_transpose_act")
    assert act.isEnabled()
    act.trigger()
    after = _cells(ctx)
    assert len(after) == len(before[0]) and len(after[0]) == len(before)
    for r, row in enumerate(before):
        for c, cell in enumerate(row):
            assert after[c][r]["well"] == cell["well"]
    BV._set_plate(ctx, False)
