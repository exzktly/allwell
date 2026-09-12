"""Image Table builder shelf — spec mutations + the channel-keyed render seam.

Fast (no dataset / no tab build): drives ``builder_view``'s pure spec-mutation
helpers on a lightweight fake ctx, and checks that the four ``image_compose``
render helpers resolve per-channel options from ``channel_opts`` (the seam that
lets display travel with the channel on either axis).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from review.features.image_browser.image_table import builder_view as B
from review.features.image_browser.image_table import image_compose as IC
from review.features.image_browser.image_table.state import (
    ChannelSpec, spec_to_state_fields,
)


def _fake_ctx(*, wells, channels, timepoints=None, fovs=None):
    from review.core.app_runtime import AppRuntime
    from review.features.image_browser.image_table.state import ImageTableState
    info = {"available_timepoints": timepoints or [], "available_fovs": fovs or [],
            "nuclear_token": ""}
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={w: w for w in wells}, pipeline_info=info,
                                cache={}),
        channel=SimpleNamespace(fluor_channels=list(channels), smfish_channels=set(),
                                ratio_metrics=[], seg_channel_token=""),
        image_table=ImageTableState(active_wells=set(wells)),
        image_view=None,
    )
    app._parse_rc = lambda w: (w[0], int(w[1:]))
    app._ctx = SimpleNamespace(app=app, state=app._app_state, runtime=AppRuntime(app))
    return app._ctx


# ── builder_view spec mutations ─────────────────────────────────────────────

def test_default_spec_is_wells_then_channels_rows_times_timepoint_cols():
    """Fresh tab: wells down the rows with their channels nested underneath,
    timepoints across the columns."""
    ctx = _fake_ctx(wells=["A01"], channels=["gfp", "rfp"], timepoints=["T01", "T02"])
    spec = B.builder_spec(ctx)
    assert spec.rows.root == "well"
    assert spec.rows.channels and [c.channel for c in spec.rows.channels] == ["GFP", "RFP"]
    assert spec.rows.dims() == ["well", "channel"]   # root outer, channel inner
    assert spec.cols.root == "timepoint"
    assert spec.channel_axis() == "rows"


def test_move_facet_and_fixed_derivation():
    ctx = _fake_ctx(wells=["A01", "A02"], channels=["gfp"], timepoints=["T01"], fovs=["1", "2"])
    spec = B.builder_spec(ctx)
    # Fixed = every root dim not on an axis. The default puts Well on the rows
    # root (channels nested under it) and Timepoint on the cols root, so FOV is
    # the only dim left to pin.
    assert spec.rows.root == "well"
    assert spec.cols.root == "timepoint"
    assert set(B._fixed_dims(spec)) == {"fov"}
    B._move_facet(ctx, spec, "well", "fixed")  # ×-ing it returns Well to Fixed
    assert spec.rows.root is None
    assert "well" in B._fixed_dims(spec)
    B._move_facet(ctx, spec, "well", "rows")   # and back onto the Rows root
    assert spec.rows.root == "well"
    assert "well" not in B._fixed_dims(spec)


def test_add_remove_channel_and_cohere():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp", "rfp"], timepoints=["T01"])
    spec = B.builder_spec(ctx)
    spec.rows.channels = []                    # start clean
    spec.cols = B.Axis(root="timepoint")
    B._add_channel(ctx, spec, "GFP", "rows")
    B._add_channel(ctx, spec, "RFP", "rows")
    assert [c.channel for c in spec.rows.channels] == ["GFP", "RFP"]
    # dragging a channel to the OTHER axis coheres the whole group there
    B._move_channel(spec, "rows", 0, "cols")
    assert spec.rows.channels == []
    assert [c.channel for c in spec.cols.channels] == ["RFP", "GFP"]
    B._remove_channel(spec, "cols", 0)
    assert [c.channel for c in spec.cols.channels] == ["GFP"]


def test_threshold_channel_flattens_to_binary_token():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01"])
    spec = B.builder_spec(ctx)
    spec.rows.channels = [ChannelSpec("GFP", "threshold")]
    fields = spec_to_state_fields(spec)
    assert fields["active_channels"] == {"GFP (binary)"}
    assert "GFP (binary)" in fields["channel_opts"]


# ── render seam: options resolve from channel_opts by the cell's channel ─────

def test_row_display_opts_from_channel_opts():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"])
    ctx.state.image_table.channel_opts = {
        "GFP": {"channel": "GFP", "render": "mask", "boundary": True, "colormap": "viridis"},
        "DAPI": {"channel": "DAPI", "render": "raw", "boundary": False, "colormap": "Greys"},
    }
    assert IC._row_display_opts(ctx, 0, "GFP") == (False, True, "mask")
    assert IC._row_display_opts(ctx, 0, "DAPI") == (False, False, "")
    assert IC._row_cmap_name(ctx, 0, "GFP") == "viridis"
    assert IC._row_cmap_name(ctx, 0, "DAPI") is None      # Greys → grayscale fast path


def test_threshold_variant_token_resolves_binary():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"])
    ctx.state.image_table.channel_opts = {
        "GFP (binary)": {"channel": "GFP", "render": "threshold", "colormap": "Greys"},
    }
    # keyed by the full "(binary)" token exactly as it appears on the axis
    assert IC._row_display_opts(ctx, 0, "GFP (binary)")[2] == "threshold"


def test_channel_opts_absent_falls_back_to_row_widgets():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"])
    # no channel_opts and no lut widgets → all-off defaults, not an error
    assert IC._row_display_opts(ctx, 0, "GFP") == (False, False, "")
    assert IC._row_log_scale(ctx, 0, "GFP") is False


# ── render is gated on Generate — edits flatten state but never draw ─────────

def test_shelf_apply_never_renders(monkeypatch):
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01"])
    seen = []
    monkeypatch.setattr(B, "image_table_apply_spec",
                        lambda c, spec, *, render=True: seen.append(render))
    B._apply_now(ctx)
    assert seen == [False]   # the debounced edit path flattens spec→state, no draw


def test_generate_notify_clears_dirty_cue():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01"])
    rt = ctx.runtime.image_table
    rt.builder_dirty = True            # an edit left the table stale
    B.builder_notify_rendered(ctx)     # Generate ran → cue clears
    assert rt.builder_dirty is False


def test_wrap_applicable_and_set(qtbot):
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01"])
    spec = B.builder_spec(ctx)
    # flat single-dim axes (channels down rows, timepoint across cols) → wrap ok
    spec.rows = B.Axis(root=None, channels=[ChannelSpec("GFP")])
    spec.cols = B.Axis(root="timepoint")
    assert B._wrap_applicable(spec)
    # An axis nesting a root AND channels bands on the column product, so wrap
    # still applies — this is the reported case (FOV › Channel down the rows,
    # Wells across the columns) where the control used to sit disabled.
    spec.rows = B.Axis(root="fov", channels=[ChannelSpec("GFP")])
    spec.cols = B.Axis(root="well")
    assert B._wrap_applicable(spec)
    # Plate mode pins both axes to the plate's own row/col dims — nothing to band.
    spec.plate = True
    assert not B._wrap_applicable(spec)
    spec.plate = False
    # _set_wrap writes the spec's wrap (→ group_size banding)
    spec.rows = B.Axis(root=None, channels=[ChannelSpec("GFP")])
    B._set_wrap(ctx, 3)
    assert B.builder_spec(ctx).wrap == 3
    B._set_wrap(ctx, -2)
    assert B.builder_spec(ctx).wrap == 0          # clamped ≥ 0


def test_builder_clear_empties_the_shelf(qtbot):
    ctx = _fake_ctx(wells=["A01"], channels=["gfp", "rfp"], timepoints=["T01"])
    spec = B.builder_spec(ctx)
    spec.rows = B.Axis(root="well", channels=[ChannelSpec("GFP")])
    spec.cols = B.Axis(root="timepoint", channels=[ChannelSpec("RFP")])
    spec.filters = {"timepoint": {"T01"}}
    B.builder_clear(ctx)               # the top-half clear
    spec = B.builder_spec(ctx)
    assert spec.rows.root is None and spec.rows.channels == []
    assert spec.cols.root is None and spec.cols.channels == []
    assert spec.filters == {}
    assert ctx.runtime.image_table.builder_dirty is True   # cues a Generate


# ── the ⚙ channel menu — must be parented (else GC'd) + render modes on top ───

def test_channel_menu_is_parented_and_lists_render_modes(qtbot):
    from PySide6.QtWidgets import QWidget
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01"])
    host = QWidget()
    qtbot.addWidget(host)
    menu = B._channel_menu(ctx, ChannelSpec("GFP", "corrected"), host)
    # Regression: QToolButton.setMenu doesn't own the menu, so a parentless
    # QMenu is garbage-collected and the ⚙ opens nothing. It must be parented.
    assert menu.parent() is host
    labels = [a.text() for a in menu.actions()]
    for mode in ("corrected", "raw", "cell mask", "≥ threshold"):
        assert mode in labels          # render modes one click from the chip
    assert "Boundary outline" in labels


def test_recognised_fluorophores_get_standard_colours():
    # Name-based standard shading, matched case-insensitively as a substring.
    assert B._standard_cmap("GFP") == "Greens"
    assert B._standard_cmap("eGFP") == "Greens"
    assert B._standard_cmap("RFP") == "Reds"
    assert B._standard_cmap("mCherry") == "Reds"
    assert B._standard_cmap("BFP") == "Cyan"
    assert B._standard_cmap("DAPI") == "Blues"
    assert B._standard_cmap("NIR") == "Magenta"
    assert B._standard_cmap("NUC+SEG") is None      # unrecognised → nearest-tint
    # Cyan / Magenta are real, selectable LUTs that ramp black→colour.
    from review.features.image_browser.image_table.image_compose import LUT_COLOR_NAMES
    assert "Cyan" in LUT_COLOR_NAMES and "Magenta" in LUT_COLOR_NAMES
    from widgets.lut_cmaps import lut_cmap
    assert tuple(round(x) for x in lut_cmap("Magenta")(1.0)[:3]) == (1, 0, 1)


def test_lut_box_shows_each_channel_once():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp", "rfp"], timepoints=["T01"])
    spec = B.builder_spec(ctx)
    spec.rows.channels = [ChannelSpec("GFP"), ChannelSpec("RFP"), ChannelSpec("GFP")]
    spec.cols = B.Axis(root="timepoint")
    table = B._table_channels(spec)
    # GFP appears twice but collapses to a single LUT entry carrying both specs;
    # editing that row fans out to every instance.
    assert list(table.keys()) == ["GFP", "RFP"]
    assert len(table["GFP"]) == 2 and len(table["RFP"]) == 1
    # A ≥Thresh binary of GFP does NOT add a second row: a binary render has no
    # display range, so GFP stays a single LUT row (its fluorescence instances).
    spec.rows.channels.append(ChannelSpec("GFP", "threshold"))
    table2 = B._table_channels(spec)
    assert set(table2.keys()) == {"GFP", "RFP"}
    assert len(table2["GFP"]) == 2   # only the two fluorescence GFP specs


def test_drop_zones_reject_incompatible_tokens():
    # A zone must not highlight for a token its handler ignores (false affordance).
    assert B._accept_axis("well") and B._accept_axis("newchan:GFP") and B._accept_axis("chan:rows:0")
    assert B._accept_fixed("timepoint")
    assert not B._accept_fixed("chan:rows:0") and not B._accept_fixed("newchan:GFP")
    assert B._accept_tray("chan:rows:0")
    assert not B._accept_tray("well") and not B._accept_tray("newchan:GFP")
    # A channel pill takes channels only; a root must fall through to the zone
    # behind it, which is the widget that knows how to place a root.
    assert B._accept_chan("chan:rows:0") and B._accept_chan("newchan:GFP")
    assert not B._accept_chan("well")


# ── reordering channels within an axis ──────────────────────────────────────

def _rows(spec):
    return [c.channel for c in spec.rows.channels]


def _four_channel_ctx():
    ctx = _fake_ctx(wells=["A01"], channels=["gfp", "rfp", "bfp", "cy5"],
                    timepoints=["T01"])
    spec = B.builder_spec(ctx)
    spec.rows.channels = [ChannelSpec(c) for c in ("GFP", "RFP", "BFP", "CY5")]
    spec.cols = B.Axis(root="timepoint")
    return ctx, spec


def test_channel_dropped_on_a_pill_lands_at_that_position():
    """The reported bug: a channel dragged within its own axis could only ever
    reach the bottom of the list, because the axis zone was the sole drop target
    and its handler appends. Dropping on a pill inserts at that pill instead —
    above it or below it, depending on which half took the drop."""
    ctx, spec = _four_channel_ctx()
    B._move_channel(spec, "rows", 3, "rows", at=0)          # CY5 onto GFP's top half
    assert _rows(spec) == ["CY5", "GFP", "RFP", "BFP"]

    ctx, spec = _four_channel_ctx()
    B._move_channel(spec, "rows", 0, "rows", at=2)          # GFP onto BFP's top half
    assert _rows(spec) == ["RFP", "GFP", "BFP", "CY5"]

    ctx, spec = _four_channel_ctx()
    B._move_channel(spec, "rows", 0, "rows", at=3)          # GFP onto BFP's bottom half
    assert _rows(spec) == ["RFP", "BFP", "GFP", "CY5"]


def test_channel_dropped_on_itself_is_a_no_op():
    for idx in range(4):
        for after in (False, True):
            _, spec = _four_channel_ctx()
            B._move_channel(spec, "rows", idx, "rows", at=idx + (1 if after else 0))
            assert _rows(spec) == ["GFP", "RFP", "BFP", "CY5"], (idx, after)


def test_dropping_on_the_axis_zone_still_appends():
    """The zone's own background has no position to speak of, so a drop there
    keeps meaning "put it at the end"."""
    _, spec = _four_channel_ctx()
    B._move_channel(spec, "rows", 0, "rows")
    assert _rows(spec) == ["RFP", "BFP", "CY5", "GFP"]


def test_new_channel_from_the_tray_can_be_dropped_into_position():
    ctx, spec = _four_channel_ctx()
    B._add_channel(ctx, spec, "DAPI", "rows", at=1)
    assert _rows(spec) == ["GFP", "DAPI", "RFP", "BFP", "CY5"]
    B._add_channel(ctx, spec, "TXR", "rows")
    assert _rows(spec)[-1] == "TXR"


def test_pill_drop_handler_maps_the_hovered_half_to_an_insert_index(monkeypatch):
    """Top half → before the pill, bottom half → after it."""
    ctx, spec = _four_channel_ctx()
    monkeypatch.setattr(B, "_edit", lambda _ctx: None)
    B._on_pill_drop(ctx, "rows", 1, "chan:rows:3", after=False)
    assert _rows(spec) == ["GFP", "CY5", "RFP", "BFP"]

    ctx, spec = _four_channel_ctx()
    monkeypatch.setattr(B, "_edit", lambda _ctx: None)
    B._on_pill_drop(ctx, "rows", 1, "chan:rows:3", after=True)
    assert _rows(spec) == ["GFP", "RFP", "CY5", "BFP"]


def test_channel_pill_is_a_drop_target_that_reports_which_half(qtbot):
    """The pill built for the tree must actually accept drops — the whole reorder
    path exists only if it does — and must report the half it was dropped in."""
    from PySide6.QtCore import QMimeData, QPointF, Qt
    from PySide6.QtGui import QDragEnterEvent, QDropEvent

    ctx, spec = _four_channel_ctx()
    pill = B._chan_pill(ctx, spec, "rows", 2, spec.rows.channels[2])
    qtbot.addWidget(pill)
    pill.resize(200, 40)
    assert pill.acceptDrops()

    # A drag event does not own its QMimeData, so the payloads have to outlive
    # the events themselves or Qt reads freed memory.
    payloads = []

    def _event(cls, y, token):
        mime = QMimeData()
        mime.setData(B._MIME, token.encode("utf-8"))
        payloads.append(mime)
        pos = QPointF(10, y)
        return cls(pos if cls is QDropEvent else pos.toPoint(),
                   Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)

    # Hovering the top half cues an insert above, the bottom half below.
    enter = _event(QDragEnterEvent, 5, "chan:rows:0")
    pill.dragEnterEvent(enter)
    assert enter.isAccepted() and pill.property("dropEdge") == "above"
    pill.dragMoveEvent(_event(QDragEnterEvent, 35, "chan:rows:0"))
    assert pill.property("dropEdge") == "below"
    pill.dragLeaveEvent(None)
    assert pill.property("dropEdge") == "none"

    # A root token is left unaccepted so it propagates out to the axis zone.
    root = _event(QDragEnterEvent, 5, "well")
    pill.dragEnterEvent(root)
    assert not root.isAccepted()

    # And a drop on the bottom half moves the dragged channel below this pill.
    pill.dropEvent(_event(QDropEvent, 35, "chan:rows:0"))
    assert _rows(spec) == ["RFP", "BFP", "GFP", "CY5"]


def test_chip_claims_the_mouse_press_that_starts_a_drag(qtbot):
    """The chip owns the whole gesture: it accepts the press instead of letting
    QFrame ignore it and hand the press to the parent zone, so there is no
    platform in which the move events that start the drag could be routed
    somewhere the chip never sees them."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    chip = B._Chip("newchan:GFP")
    qtbot.addWidget(chip)
    press = QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(4, 4), QPointF(4, 4),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    press.ignore()
    chip.mousePressEvent(press)
    assert press.isAccepted()
    assert chip._press is not None


def test_root_filter_menu_actually_filters(qtbot):
    from PySide6.QtWidgets import QWidget
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01", "T02", "T03"])
    host = QWidget()
    qtbot.addWidget(host)
    menu = B._root_filter_menu(ctx, "timepoint", host)
    checkable = [a for a in menu.actions() if a.isCheckable()]
    # empty filter ⇒ every value shown/ticked
    assert len(checkable) == 3 and all(a.isChecked() for a in checkable)
    checkable[0].setChecked(False)                     # toggled → commit
    st = ctx.state.image_table
    assert st.active_timepoints == {checkable[1].text(), checkable[2].text()}
    assert ctx.runtime.image_table.builder_dirty is True
    boxes = [a for a in menu.actions() if a.isCheckable()]
    [a for a in menu.actions() if a.text() == "Select none"][0].trigger()
    assert not any(a.isChecked() for a in boxes)       # Select none unchecks all
    [a for a in menu.actions() if a.text() == "Select all"][0].trigger()
    assert all(a.isChecked() for a in boxes)           # Select all rechecks all
    assert st.active_timepoints == set()               # all ticked ⇒ show all


def test_channel_menu_is_flat_no_submenus(qtbot):
    # The ⚙ menu must stay flat: an eager addMenu() submenu + keepalive created a
    # GC cycle that could fault under native macOS menus. Colour items live at the
    # top level, and no action carries a child QMenu.
    from PySide6.QtWidgets import QWidget
    host = QWidget()
    qtbot.addWidget(host)
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01"])
    menu = B._channel_menu(ctx, ChannelSpec("GFP", "corrected"), host)
    assert all(a.menu() is None for a in menu.actions())      # no submenus at all
    labels = [a.text() for a in menu.actions()]
    assert "Greens" in labels and "Magenta" in labels         # colour is flat, top-level


def test_channel_menu_actions_mutate_channel_spec(qtbot):
    ctx = _fake_ctx(wells=["A01"], channels=["gfp"], timepoints=["T01"])
    from PySide6.QtWidgets import QWidget
    parent = QWidget()
    qtbot.addWidget(parent)
    cs = ChannelSpec("GFP", "corrected")
    menu = B._channel_menu(ctx, cs, parent)
    [a for a in menu.actions() if a.text() == "cell mask"][0].trigger()
    assert cs.render == "mask"
    [a for a in menu.actions() if a.text() == "Boundary outline"][0].trigger()
    assert cs.boundary is True
