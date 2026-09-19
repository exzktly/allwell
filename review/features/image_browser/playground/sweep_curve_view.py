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
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QVBoxLayout

from ui import theme


def show_threshold_curve(ctx, curve) -> None:
    """Open (or repaint) the window plotting *curve*.

    A declined curve is shown as its own sentence on empty axes rather than as
    a blank window or no window at all: the user pressed a button and is owed
    an answer, and "no nuclei at any threshold" is an answer.
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
        ctx.runtime.playground.sweep_curve_popup = popup
        popup.show()

    draw_threshold_curve(popup._fig, curve)
    popup._canvas.draw_idle()
    popup.raise_()
    popup.activateWindow()


def draw_threshold_curve(fig, curve) -> None:
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
            marker="o", markersize=3.5, label="nuclei detected")
    if curve.current is not None:
        ax.axvline(float(curve.current), color=txt_pri, lw=1.2, ls="--",
                   label=f"your threshold = {curve.current:g}")
    ax.set_xlim(curve.thresholds[0], curve.thresholds[-1])
    ax.set_ylim(0, max(1, max(curve.counts)) * 1.08)
    ax.set_xlabel("StarDist probability threshold", color=txt_pri)
    ax.set_ylabel("Nuclei detected", color=txt_pri)
    ax.set_title(curve.describe(), color=txt_pri, fontsize=10)
    leg = ax.legend(loc="upper right", framealpha=0.0, facecolor="none",
                    fontsize=9)
    if leg is not None:
        for t in leg.get_texts():
            t.set_color(txt_pri)
    fig.tight_layout()
