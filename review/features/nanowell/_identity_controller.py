"""The Nanowells page's **Identity** view: its one composition, and what the page does around it.

:func:`draw_identity` is the composition — reached through ``controller.draw_view(view="identity")``, the one the
page and Batch Export share — and is Qt-free: it reads the well's sidecar when it is not handed one, builds the
comparison (``identity_compute``) and draws the chosen mode (``_identity_render``). The rest is the page's side:
the sidecar and the montages read once per well (the montages on a worker, through the page's own montage store),
the Blink frames resampled once per pair, the Blink timer started and stopped, and the pointer on *Side by side*.

The Blink timer is a ``QTimer`` the view builds (``identity_view``); :func:`blink_tick` stops it on the first tick
after the page is hidden or the view, mode or figure changed, so it can never outlive what it blinks, and
:func:`step_blink` shows a given frame so a test can step it without waiting on the clock.
"""

from __future__ import annotations

import logging

from review.features.nanowell import _identity_render as ir
from review.features.nanowell.image_compute import read_montage, tile_boxes
from review.features.nanowell import identity_compute as ic
from review.features.nanowell.state import IDENTITY_MODES

log = logging.getLogger("review")

#: Handed as *payload* when the composition should read the sidecar itself (a batch figure).
READ = object()
#: How long each Blink frame is shown, in milliseconds: about two a second.
BLINK_MS = 500


def _tile_sizes(data, well: str, timepoint: str) -> dict:
    return {t.tile: (t.width_um, t.height_um) for t in tile_boxes(data, well, timepoint)
            if t.width_um and t.height_um}


def draw_identity(fig, data, *, well: str, timepoint: str, mode: str = "distance", payload=READ, images=None,
                  frames_cache: dict | None = None, note: str = "") -> ir.IdentityDrawn | None:
    """**The one composition of the Identity view**: *well*'s *timepoint* against its reference, in *mode*.

    *payload* is the well's sidecar (``None``: none current); by default it is read here. *images* is
    ``{"reference": Montage | None, "compared": Montage | None, "why": str}`` — what Blink and Side by side draw;
    without them those modes say what they are waiting for. *frames_cache* keeps the Blink frames per
    ``(well, timepoint, reference)``. ``None`` when the figure is a message: no sidecar (with the command that makes
    one), the reference itself, or no reference."""
    from pipeline.nanowell_identity import read_sidecar

    if not well:
        ir.render_identity_message(fig, "Select a well with a nanowell table.")
        return None
    out_dir = getattr(data, "out_dir", None)
    if payload is READ:
        payload = read_sidecar(out_dir, well) if out_dir is not None else None
    pair = ic.identity_pair(data, well, timepoint, payload)
    said = ic.identity_message(pair, out_dir)
    if said:
        ir.render_identity_message(fig, said)
        return None
    lines = ic.summary_lines(pair) + ([note] if note else [])
    mode = mode if mode in IDENTITY_MODES else "distance"
    if mode == "distance" and pair.entry.get("distances") is None:
        mode = "diagnostics"
        lines.append("No address was compared, so there is no distance map: the registration on the square is "
                     "shown instead.")
    if mode == "distance":
        return ir.render_distance(fig, pair, ic.distance_map(pair, _tile_sizes(data, well, timepoint)), lines)
    if mode == "diagnostics":
        return ir.render_diagnostics(fig, pair, ic.diagnostics(pair), lines)
    images = images or {}
    ref_image, other_image = images.get("reference"), images.get("compared")
    if mode == "sides":
        if ref_image is None or other_image is None:
            lines.append(images.get("why") or "The montages are not read: only the nanowells' positions are drawn.")
        return ir.render_sides(fig, pair, ref_image, other_image, lines)
    if pair.forward is None or pair.inverse is None:
        ir.render_identity_message(fig, f"{well} at {timepoint}: the registration on the square located nothing, so "
                                         f"there is nothing to map the montages with — {pair.reason}.")
        return None
    if ref_image is None or other_image is None:
        ir.render_identity_message(fig, images.get("why") or "The montages are not read, so there is nothing to "
                                                              "blink.")
        return None
    key = (well, timepoint, pair.reference)
    frames = (frames_cache or {}).get(key)
    if frames is None:
        frames = ic.blink_frames(pair, ref_image, other_image)
        if frames_cache is not None:
            frames_cache.clear()                     # one pair at a time: a montage pair is megabytes
            frames_cache[key] = frames
    return ir.render_blink(fig, pair, frames, lines)


# ── the page ─────────────────────────────────────────────────────────────────
def page_sidecar(st, well: str):
    """The well's sidecar as the page holds it: read once per well and dataset (``None``: none current)."""
    from pipeline.nanowell_identity import read_sidecar

    if well not in st.identity_sidecars:
        out_dir = getattr(st.data, "out_dir", None)
        st.identity_sidecars[well] = read_sidecar(out_dir, well) if out_dir is not None else None
    return st.identity_sidecars[well]


def page_timepoint(st, well: str) -> str:
    """The timepoint the Identity view compares: the page's choice if the well has it, else the well's first
    contradicted (or not confirmed) timepoint, else its first that is not the reference."""
    tps = st.data.timepoints(well) if st.data is not None else []
    return st.timepoint if st.timepoint in tps else ic.default_timepoint(st.data, well, tps)


def _images(ctx, st, well: str, timepoint: str, reference: str | None, fetch) -> dict:
    """Both montages from the page's montage store, each read on a worker the first time it is asked for, through
    the page's *fetch* (``controller._fetch``, handed in so this module never imports the controller)."""
    out, waiting, why = {}, False, []
    data = st.data
    for name, tp in (("reference", reference), ("compared", timepoint)):
        if not tp:
            out[name] = None
            continue
        if (well, tp) in st.montages:
            montage, problem = st.montages[(well, tp)]
            out[name] = montage
            if montage is None:
                why.append(problem)
            continue
        out[name] = None
        waiting = True
        fetch(ctx, "montages", (well, tp), lambda tp=tp: read_montage(data, well, tp),
              failed=lambda message: (None, f"The montage could not be read: {message}"))
    out["why"] = " ".join(filter(None, ["Reading the montages…" if waiting else "", *why]))
    return out


def page_inputs(ctx, st, well: str, timepoint: str, fetch) -> dict:
    """What the page hands :func:`draw_identity`: its mode, the sidecar it holds, the montages Blink and Side by
    side draw (read only for those modes), and its Blink frames."""
    payload = page_sidecar(st, well)
    images = None
    if st.identity_mode in ("blink", "sides") and payload is not None:
        images = _images(ctx, st, well, timepoint, payload.get("reference"), fetch)
    return {"mode": st.identity_mode, "payload": payload, "images": images, "frames_cache": st.identity_frames}


def after_draw(ctx, st, drawn) -> None:
    """Keep what was drawn, and run the Blink timer exactly while Blink frames are on screen."""
    st.identity_drawn = drawn
    timer = ctx.widgets.get_or_none("_nw_blink_timer")
    if timer is None:
        return
    if _blinking(ctx, st):
        if not timer.isActive():
            timer.start(BLINK_MS)
    else:
        timer.stop()


def leave(ctx, st) -> None:
    """The page drew another view (or nothing): forget the Identity figure and stop its timer."""
    st.identity_drawn = None
    timer = ctx.widgets.get_or_none("_nw_blink_timer")
    if timer is not None:
        timer.stop()


def _blinking(ctx, st) -> bool:
    drawn = st.identity_drawn
    fig = ctx.widgets.get_or_none("_nw_fig")
    visible = ctx.widgets.get_or_none("_nw_page_visible")
    return (st.view == "identity" and st.identity_mode == "blink" and drawn is not None and drawn.mode == "blink"
            and bool(drawn.frames) and fig is not None and drawn.axes["map"].figure is fig
            and callable(visible) and bool(visible()))


def blink_tick(ctx) -> None:
    """The Blink timer's tick: show the other frame — or stop the timer, once there is nothing on screen to blink
    (the page hidden, another view or mode, the figure redrawn as something else)."""
    st = ctx.state.feature_slice("nanowell")
    timer = ctx.widgets.get_or_none("_nw_blink_timer")
    if st is None or not _blinking(ctx, st):
        if timer is not None:
            timer.stop()
        return
    step_blink(ctx, st.identity_drawn.frame + 1)


def step_blink(ctx, k: int) -> int | None:
    """Show Blink frame *k* (0 the reference, 1 the compared timepoint) and repaint; the frame shown, or ``None``
    when Blink is not on screen."""
    st = ctx.state.feature_slice("nanowell")
    drawn = st.identity_drawn if st is not None else None
    if drawn is None or drawn.mode != "blink":
        return None
    shown = ir.show_frame(drawn, k)
    canvas = ctx.widgets.get_or_none("_nw_canvas")
    if canvas is not None:
        canvas.draw_idle()
    return shown


def on_motion(ctx, event) -> None:
    """The pointer over *Side by side*: light the address under it on both images (``identity_compute.hover_target``
    does the finding), or clear it off the images."""
    st = ctx.state.feature_slice("nanowell")
    drawn = st.identity_drawn if st is not None else None
    if drawn is None or drawn.mode != "sides":
        return
    side = next((name for name, ax in drawn.axes.items() if ax is event.inaxes), None)
    target = (ic.hover_target(side, event.xdata, event.ydata, drawn.pair)
              if side is not None and event.xdata is not None else None)
    if ir.show_hover(drawn, target):
        canvas = ctx.widgets.get_or_none("_nw_canvas")
        if canvas is not None:
            canvas.draw_idle()


