"""The Identity view's Diagnostics holds together at every size the plot card takes.

Reported: the Diagnostics figure was "a disaster". It placed everything at fixed figure fractions while the card it
draws in has no fixed size — 8 × 6.4 in by default, narrower with the site detail open beside it, short and wide in
a low window, a phone's width in phone layout. At a narrow card the crops' keys sat on top of their axis labels and
the colour bar's label ran up beside the panel title; at a short wide one the pictures shrank to thumbnails with
the empty space between them; at a phone's size the panels were pushed up over the title. And the page's Properties
pass, which every redraw ends with, drew those panels' tick and axis labels at its 18 pt default, so in the app the
axis labels of neighbouring panels ran through one another at every size.

The layout is now worked out from the figure's size (``_identity_diagnostics_layout``), and these tests read the
drawn figure back: every text's window extent, measured with the figure's own renderer after the same Properties
pass the page applies, must lie inside the figure, overlap no other text, and (unless it belongs to a picture)
stay off every picture. Driven with the Identity suite's real synthetic dataset — rendered tiles run through the
pipeline's own identity check — for a confirmed (A01), a contradicted (A02) and a partial (A04) well.
"""

from __future__ import annotations

import itertools

import pytest

from tests.test_nanowell_identity_view import _draw, _montages, _texts, dataset  # noqa: F401  (a fixture)

#: The sizes the plot card is seen at, in inches: the default, the site detail open beside it, a short wide window,
#: a large window, a phone's width.
SIZES = ((8.0, 6.4), (6.0, 6.4), (12.0, 4.5), (10.0, 7.0), (4.0, 6.0))
WELLS = ("A01", "A02", "A04")


def _styled(dataset, well, size):  # noqa: F811
    """The Diagnostics of *well* at *size*, with the montages read, through the Properties pass the page applies."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from review.core.plot_style import DEFAULT_EXPORT_STYLE_PREFS, apply_export_style_prefs
    from review.features.nanowell.controller import draw_view

    out, data, _ref, _other = dataset
    fig = Figure(figsize=size, dpi=100)
    FigureCanvasAgg(fig)
    _hits, drawn = draw_view(fig, "identity", None, data, groups={well: [well]}, metric="n", channel=None,
                             axis="time", timepoint="T1", control="", baseline="earliest", cohort=False, well=well,
                             well_timepoint="T1", identity={"mode": "diagnostics",
                                                            "images": _montages(dataset, well)})
    apply_export_style_prefs(fig, dict(DEFAULT_EXPORT_STYLE_PREFS))
    fig.canvas.draw()
    return fig, drawn


def _in_view(axis, tick) -> bool:
    lo, hi = sorted(axis.get_view_interval())
    span = hi - lo
    return lo - 1e-6 * span <= tick.get_loc() <= hi + 1e-6 * span


def _drawn_texts(fig) -> list[tuple[str, object, object]]:
    """``(name, text, owning axes or None)`` for every non-empty visible text the figure draws: its own texts, each
    axes' texts and axis labels, and the tick labels of the ticks inside each axis's view."""
    out = [(t.get_gid() or "figure text", t, None) for t in fig.texts]
    for ax in fig.axes:
        out += [(t.get_gid() or "axes text", t, ax) for t in ax.texts]
        out += [("x label", ax.xaxis.label, None), ("y label", ax.yaxis.label, None)]
        for axis, name in ((ax.xaxis, "x tick"), (ax.yaxis, "y tick")):
            for tick in axis.get_major_ticks():
                if _in_view(axis, tick):
                    out += [(name, label, None) for label in (tick.label1, tick.label2)]
    return [(name, t, owner) for name, t, owner in out if t.get_visible() and t.get_text().strip()]


def _problems(fig) -> list[str]:
    """Every text outside the figure, every pair of texts that overlap, and every text over a picture it does not
    belong to — named by gid (or kind) and text."""
    renderer = fig.canvas.get_renderer()
    texts = [(name, t, owner, t.get_window_extent(renderer)) for name, t, owner in _drawn_texts(fig)]
    frame = fig.bbox
    said = []
    for name, t, _owner, box in texts:
        if box.x0 < frame.x0 - 0.5 or box.y0 < frame.y0 - 0.5 or box.x1 > frame.x1 + 0.5 or box.y1 > frame.y1 + 0.5:
            said.append(f"outside the figure: {name} {t.get_text()!r}")
    for (n1, t1, _o1, b1), (n2, t2, _o2, b2) in itertools.combinations(texts, 2):
        if min(b1.x1, b2.x1) - max(b1.x0, b2.x0) > 0.5 and min(b1.y1, b2.y1) - max(b1.y0, b2.y0) > 0.5:
            said.append(f"overlap: {n1} {t1.get_text()!r} and {n2} {t2.get_text()!r}")
    pictures = [ax for ax in fig.axes if getattr(ax, "_aw_image_panel", False)]
    for name, t, owner, box in texts:
        for ax in pictures:
            area = ax.get_window_extent(renderer)
            if ax is not owner and min(box.x1, area.x1) - max(box.x0, area.x0) > 0.5 \
                    and min(box.y1, area.y1) - max(box.y0, area.y0) > 0.5:
                said.append(f"over a picture: {name} {t.get_text()!r}")
    return said


@pytest.mark.parametrize("size", SIZES, ids=[f"{w:g}x{h:g}" for w, h in SIZES])
@pytest.mark.parametrize("well", WELLS)
def test_no_text_overlaps_another_or_a_picture_or_leaves_the_figure(dataset, well, size):  # noqa: F811
    fig, drawn = _styled(dataset, well, size)
    assert drawn.mode == "diagnostics" and drawn.crops.looked is not None
    assert _problems(fig) == []


def test_the_pictures_use_the_room_a_short_wide_card_gives_them(dataset):  # noqa: F811
    """At 12 × 4.5 in the three pictures were 0.95 in thumbnails with the space between them: the checks now sit in
    a column beside them, and each picture is a square of the same edge, over 1.8 in (twice what it was)."""
    fig, drawn = _styled(dataset, "A04", (12.0, 4.5))
    edges = [ax.get_window_extent().width / fig.dpi for ax in (drawn.axes[n] for n in ("looked", "found", "surface"))]
    heights = [ax.get_window_extent().height / fig.dpi for ax in (drawn.axes[n] for n in ("looked", "found",
                                                                                          "surface"))]
    assert edges == pytest.approx([edges[0]] * 3, abs=0.02) and heights == pytest.approx(edges, abs=0.02)
    assert edges[0] > 1.8
    checks = [t for t in fig.texts if (t.get_gid() or "").startswith("nw-identity-check-")
              and t.get_gid() != "nw-identity-check-verdict"]
    right_of_pictures = drawn.axes["surface"].get_window_extent().x1
    assert checks and all(t.get_window_extent().x0 > right_of_pictures for t in checks)


def test_the_verdict_reads_first_and_largest(dataset):  # noqa: F811
    """The verdict sits between the title and the pictures, its lead (``Verdict: <verdict> —``) in the largest type
    on the figure; the per-check detail sits under the pictures in smaller type than the reason."""
    from review.features.nanowell import _identity_diagnostics_layout as dl

    fig, drawn = _styled(dataset, "A02", (8.0, 6.4))
    verdict = [t for t in fig.texts if t.get_gid() == "nw-identity-check-verdict"]
    assert verdict[0].get_text() == "Verdict: contradicted —" and verdict[0].get_fontsize() == dl.HEADLINE_PT
    others = [t for t in fig.texts if t.get_gid() not in ("nw-identity-check-verdict", "nw-identity-state-verdict")]
    assert max(t.get_fontsize() for t in others) < dl.HEADLINE_PT
    picture_top = max(ax.get_window_extent().y1 for ax in drawn.axes.values())
    picture_bottom = min(ax.get_window_extent().y0 for ax in drawn.axes.values())
    title = [t for t in fig.texts if t.get_gid() == "nw-identity-title"][0].get_window_extent()
    assert all(picture_top < t.get_window_extent().y0 < title.y0 for t in verdict)
    detail = [t for t in fig.texts if t.get_gid() == "nw-identity-check-addresses"]
    assert all(t.get_window_extent().y1 < picture_bottom and t.get_fontsize() == dl.CHECK_PT for t in detail)
    assert dl.CHECK_PT < dl.REASON_PT < dl.HEADLINE_PT


def test_the_panels_tick_labels_are_drawn_at_the_panel_size_after_the_properties_pass(dataset):  # noqa: F811
    """The Properties pass sized these panels' tick and axis labels at its 18 pt default, which ran neighbouring
    panels' labels through one another; each panel now asks for its own size (``_aw_type_scale``)."""
    from review.features.nanowell import _identity_diagnostics_layout as dl

    _fig, drawn = _styled(dataset, "A01", (8.0, 6.4))
    for ax in drawn.axes.values():
        assert {label.get_fontsize() for label in ax.get_xticklabels()} == {dl.TICK_PT}
        assert ax.xaxis.label.get_fontsize() == dl.TICK_PT


def test_the_accepted_ring_circles_the_match_outline_rather_than_sitting_inside_it(dataset):  # noqa: F811
    """The ring round the square the run accepted was a marker inside the match's outline, in the same place: it is
    now a ring of 0.62 spacing in the picture's own units, outside the outline's corners (0.53 spacing)."""
    from review.features.nanowell import _identity_diagnostics_render as idr

    _out, _data, ref, _other = dataset
    fig, drawn = _draw(dataset, "A01", mode="diagnostics", images=_montages(dataset, "A01"))
    (ring,) = [p for p in drawn.axes["found"].patches if p.get_gid() == "nw-identity-accepted"]
    (outline,) = [p for p in drawn.axes["found"].patches if p.get_gid() == "nw-identity-square-found"]
    assert ring.get_radius() == pytest.approx(idr.ACCEPTED_RING_PITCHES * ref.pitch)
    assert ring.get_radius() > outline.get_width() / 2 * 2 ** 0.5
    assert _texts(fig, "nw-identity-crop-key") == ["the central square", "the match", "the square the run picked at T1"]


def test_a_drawing_says_the_size_it_was_laid_out_for_and_a_resize_asks_for_another(dataset):  # noqa: F811
    """The page redraws Diagnostics once its canvas settles at a new size (``controller.on_canvas_resized`` asks
    ``needs_relayout``); the other modes keep their fractions and are not redrawn."""
    from review.features.nanowell import _identity_controller as idc

    fig, drawn = _draw(dataset, "A01", mode="diagnostics")
    assert drawn.laid_out == (8.0, 6.4) and not idc.needs_relayout(drawn, fig)
    fig.set_size_inches(6.0, 6.4)
    assert idc.needs_relayout(drawn, fig)
    _fig, distance = _draw(dataset, "A01", mode="distance")
    _fig.set_size_inches(6.0, 6.4)
    assert not idc.needs_relayout(distance, _fig) and not idc.needs_relayout(None, fig)


# ── the real page ────────────────────────────────────────────────────────────
@pytest.fixture
def page(qtbot, dataset):  # noqa: F811
    pytest.importorskip("PySide6")
    from tests.test_nanowell_page import _page

    ctx = _page(qtbot, dataset[0])
    ctx.state.selection.selected_wells = {"A01", "A02"}
    return ctx


def test_the_page_lays_diagnostics_out_again_when_its_canvas_settles_at_a_new_size(page, qtbot):
    """Through the real page: Diagnostics chosen on the switches, the montages read, the window made narrower —
    the figure is drawn again for the canvas's new size, and nothing on it overlaps."""
    from review.features.nanowell.identity_view import MODES
    from review.features.nanowell.tab_view import VIEWS
    from tests.test_nanowell_identity_view import _choose
    from tests.test_nanowell_page import _loaded

    ctx = page
    st = _loaded(qtbot, ctx)
    _choose(ctx, "_nw_view_seg", "identity", [k for k, _l in VIEWS])
    _choose(ctx, "_nw_identity_mode_seg", "diagnostics", [k for k, _l in MODES])
    qtbot.waitUntil(lambda: st.identity_drawn is not None and st.identity_drawn.crops is not None
                    and st.identity_drawn.crops.looked is not None, timeout=20000)
    fig, canvas = ctx.widgets.get("_nw_fig"), ctx.widgets.get("_nw_canvas")
    from review.features.nanowell._identity_diagnostics_render import laid_out_size

    assert st.identity_drawn.laid_out == laid_out_size(fig)
    before = st.identity_drawn.laid_out
    ctx.window.resize(ctx.window.width() - 450, ctx.window.height() - 150)
    qtbot.waitUntil(lambda: laid_out_size(fig) != before, timeout=5000)        # the canvas has shrunk
    qtbot.waitUntil(lambda: st.identity_drawn.laid_out == laid_out_size(fig), timeout=5000)
    canvas.draw()
    assert _problems(fig) == []
