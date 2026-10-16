"""The threshold-curve window: nucleus count against StarDist's probability cut.

A non-modal ``QDialog`` holding one matplotlib canvas, opened by the Playground
controller after a sweep. Modelled on
:mod:`review.features.home.cell_gating.opt_curve_view`, which does the same job
for the other threshold this app chooses — same popup shape, same toolbar, same
repaint-in-place behaviour across consecutive runs, so the two curves in the
application look and behave like one idea.

**Nothing here marks a recommended threshold, and that is the point.**
:mod:`review.domain.threshold_sweep` carries the measurement behind that
decision: the plateau rule this replaced returns either the sweep's zero tail —
suggesting *detect nothing* — or its low-threshold shoulder, which reproduces
the Analyze form's hardcoded default to three decimal places. What the plot
marks instead is where the user currently is, so they can see whether that sits
on a shoulder or on a cliff.

**The curve is clickable, because the real answer is the picture.** A press on
the line (within matplotlib's own pick radius of it) snaps to the nearest
*sampled* threshold — never an interpolated one, which no sweep measured — and
hands that value to the ``on_pick`` callback the controller supplies. The
controller writes it into the Probability box and re-segments the frame through
the Segment button's own path, so the outlines on the image are the ones that
threshold produces. The picked point is ringed on the curve. A press off the
line, on a refusal figure, or while a toolbar zoom/pan mode is armed picks
nothing. The view owns only the hit test; what a pick *does* is the
controller's.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QVBoxLayout

from ui import theme

#: ``gid`` of the measured line, so the hit test reads the points the user can
#: actually see rather than a copy of them held somewhere else.
CURVE_GID = "threshold-curve"
#: ``gid`` of the ring marking the threshold last picked by a click.
PICKED_GID = "picked-threshold"


def show_threshold_curve(ctx, curve, *, on_pick=None, picked=None) -> None:
    """Open (or repaint) the window plotting *curve*.

    A declined curve is shown as its own sentence on empty axes rather than as
    a blank window or no window at all: the user pressed a button and is owed
    an answer, and "no nuclei at any threshold" is an answer.

    *on_pick(threshold)* is called with the sampled threshold nearest a press
    on the line; *picked* is the threshold to ring on the curve, if any.
    """
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    popup = getattr(ctx.runtime.playground, "sweep_curve_popup", None)
    if popup is None or not popup.isVisible():
        popup = QDialog(ctx.window)
        popup.setWindowTitle("Threshold curve")
        popup.resize(820, 460)
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(8, 8, 8, 8)
        fig = Figure(figsize=(7.8, 4.4), dpi=100)
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas)
        from widgets.mpl_toolbar import MplToolbar
        layout.addWidget(MplToolbar(canvas, popup))
        popup.finished.connect(
            lambda _r: setattr(ctx.runtime.playground, "sweep_curve_popup", None))
        popup._fig = fig
        popup._canvas = canvas
        # Connected once per window; the callback it reaches is replaced on
        # every repaint, so a window reused across sweeps never calls a pick
        # handler that belongs to an earlier one.
        canvas.mpl_connect("button_press_event",
                           lambda ev: _on_press(popup, ev))
        ctx.runtime.playground.sweep_curve_popup = popup
        popup.show()

    popup._on_pick = on_pick
    draw_threshold_curve(popup._fig, curve, picked=picked)
    popup._canvas.draw_idle()
    popup.raise_()
    popup.activateWindow()


def _on_press(popup, event) -> None:
    """A press on the curve window: pick the sampled point under it, if any."""
    on_pick = getattr(popup, "_on_pick", None)
    if on_pick is None or getattr(event, "button", None) != 1:
        return
    # An armed zoom or pan mode owns the press; picking as well would
    # re-segment the frame every time the user drags out a zoom box.
    toolbar = getattr(event.canvas, "toolbar", None)
    if toolbar is not None and str(getattr(toolbar, "mode", "") or ""):
        return
    hit = point_at(popup._fig, event)
    if hit is not None:
        on_pick(hit[0])


def point_at(fig, event) -> tuple[float, int] | None:
    """``(threshold, count)`` of the sampled point a press landed on, or None.

    "On" means within matplotlib's own pick radius of the drawn line, so a
    press between two markers still counts; it then snaps to the nearest
    **sampled** point in screen space. An interpolated threshold would be one
    no sweep measured, and the count beside it would be invented. ``None`` for
    a press off the line, outside the axes, or on a figure with no curve on it
    (a refusal, or nothing swept yet).
    """
    import numpy as np
    line = next((ln for ax in fig.axes for ln in ax.lines
                 if ln.get_gid() == CURVE_GID), None)
    if line is None or event.inaxes is not line.axes:
        return None
    if event.x is None or event.y is None or not line.contains(event)[0]:
        return None
    xs, ys = (np.asarray(v, dtype=float) for v in line.get_data())
    if xs.size == 0:
        return None
    px = line.axes.transData.transform(np.column_stack([xs, ys]))
    i = int(np.argmin(np.hypot(px[:, 0] - event.x, px[:, 1] - event.y)))
    return float(xs[i]), int(ys[i])


def draw_threshold_curve(fig, curve, *, picked=None) -> None:
    """Paint *curve* onto *fig*. Pure matplotlib — no Qt, no app state.

    Separate from the window so a test can assert what was *drawn* rather than
    what was computed (CLAUDE.md §7.4 #1): the counts being right is not the
    same as the line being there.
    """
    fig.clear()
    bg = theme.Colors.panel
    txt_pri = theme.Colors.text_primary
    txt_mut = theme.Colors.text_muted
    fig.patch.set_facecolor(bg)
    ax = fig.add_subplot(111)
    ax.set_facecolor(bg)
    for spine in ax.spines.values():
        spine.set_color(txt_mut)
    ax.tick_params(colors=txt_mut)

    if curve.declined or not curve.points:
        # The refusal IS the figure. Drawing empty axes with a title and no
        # explanation is the shortfall this whole feature exists to avoid.
        ax.set_axis_off()
        ax.text(0.5, 0.5, curve.describe() or "No sweep has been run yet.",
                ha="center", va="center", wrap=True, color=txt_pri, fontsize=11,
                transform=ax.transAxes)
        fig.tight_layout()
        return

    ax.plot(curve.thresholds, curve.counts, color=theme.Colors.accent, lw=1.8,
            marker="o", markersize=3.5, label="nuclei detected", gid=CURVE_GID)
    _draw_picked(ax, curve, picked)
    if curve.truncated:
        # A hollow marker where the data stops, so the end of the measurement
        # is distinguishable from the end of the axis.
        ax.plot([curve.thresholds[-1]], [curve.counts[-1]], marker="o",
                markersize=8, markerfacecolor="none",
                markeredgecolor=theme.Colors.warn, markeredgewidth=1.6,
                linestyle="none", label="stopped here")
    if curve.current is not None:
        ax.axvline(float(curve.current), color=txt_pri, lw=1.2, ls="--",
                   label=f"your threshold = {curve.current:g}")
    if curve.model_default is not None:
        # The comparison the feature exists for. Without it the user can read
        # their own value off the curve and has nothing to read it against.
        ax.axvline(float(curve.model_default), color=theme.Colors.text_muted,
                   lw=1.2, ls=":",
                   label=f"model default = {curve.model_default:.3g}")

    # The x-axis spans what was **asked for**, not what was measured. Rescaled
    # to the eight points a stopped sweep got, the curve fills the panel and
    # the picture asserts that the count barely moves on this frame -- which is
    # the conclusion a reader would act on, and an artifact of stopping.
    span = curve.requested_span
    ax.set_xlim(*(span if span else (curve.thresholds[0], curve.thresholds[-1])))
    ax.set_ylim(0, max(1, max(curve.counts)) * 1.08)
    ax.set_xlabel("StarDist probability threshold", color=txt_pri)
    ax.set_ylabel("Nuclei detected", color=txt_pri)
    # Two lines: what was swept, then what the sweep found. The sibling
    # optimizer curve names its subject the same way, and without it three
    # consecutive sweeps repaint into three indistinguishable figures -- which
    # then get saved into notebooks with no attribution at all.
    title = curve.describe()
    if curve.subject:
        title = f"{curve.subject}\n{title}"
    ax.set_title(title, color=txt_pri, fontsize=11)
    # A flat run means the detections are insensitive to the cut, not that they
    # are correct: a frame the model confidently segments as 350 nuclei plus 50
    # pieces of debris draws the same plateau, and a user who came to validate
    # their threshold will read the flat shoulder as validation.
    ax.text(0.5, -0.16,
            "A flat region means the count is insensitive to the threshold — "
            "not that the detections are right. Check the outlines.",
            transform=ax.transAxes, ha="center", va="top", fontsize=8.5,
            color=txt_mut)
    leg = ax.legend(loc="best", framealpha=0.85,
                    facecolor=theme.Colors.panel, edgecolor=txt_mut,
                    fontsize=9)
    if leg is not None:
        for t in leg.get_texts():
            t.set_color(txt_pri)
    fig.tight_layout()


def _draw_picked(ax, curve, picked) -> None:
    """Ring the sampled point a click picked, labelled with what it measured.

    Only a threshold that IS one of the curve's points is ringed: a value that
    matches none of them (a new sweep on a different grid) is not drawn at a
    guessed position.
    """
    if picked is None:
        return
    import numpy as np
    xs = np.asarray(curve.thresholds, dtype=float)
    hits = np.flatnonzero(np.isclose(xs, float(picked)))
    if hits.size == 0:
        return
    i = int(hits[0])
    ax.plot([xs[i]], [curve.counts[i]], marker="o", markersize=9,
            markerfacecolor=theme.Colors.accent2,
            markeredgecolor=theme.Colors.text_primary, markeredgewidth=1.4,
            linestyle="none", gid=PICKED_GID,
            label=f"picked: {xs[i]:g} ({curve.counts[i]} on the curve)")
