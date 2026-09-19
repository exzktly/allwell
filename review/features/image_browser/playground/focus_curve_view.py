"""The focus-distribution window: per-cell sharpness, and where the cut falls.

A non-modal ``QDialog`` holding one matplotlib canvas, opened by the Playground
controller after a segmentation. Deliberately the same shape as
:mod:`review.features.image_browser.playground.sweep_curve_view` — same popup,
same toolbar, same repaint-in-place across consecutive runs — because the two
thresholds this application asks a user to choose deserve to look and behave
like one idea.

**Nothing here marks a recommended threshold, and that is measured rather than
timid.** :mod:`review.domain.focus_distribution` carries the numbers: the
obvious automatic rule returns a cut for a plate with nothing wrong with it and
discards two fifths of its good cells, and on a uniformly defocused plate it
keeps three quarters of the cells it exists to catch — with no statistic able
to tell those two cases from the useful ones. What is marked instead is where
the user currently is, with a live count of what that keeps and drops.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QVBoxLayout

from ui import theme


def show_focus_distribution(ctx, dist) -> None:
    """Open (or repaint) the window plotting *dist*.

    A declined distribution is shown as its own sentence on empty axes rather
    than as a blank window or no window at all: the user pressed a button and
    is owed an answer, and "no cell here carried a measurable outline" is an
    answer — and a finding about the frame.
    """
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    popup = getattr(ctx.runtime.playground, "focus_curve_popup", None)
    if popup is None or not popup.isVisible():
        popup = QDialog(ctx.window)
        popup.setWindowTitle("Focus distribution")
        popup.resize(820, 460)
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(8, 8, 8, 8)
        fig = Figure(figsize=(7.8, 4.4), dpi=100)
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas)
        from widgets.mpl_toolbar import MplToolbar
        layout.addWidget(MplToolbar(canvas, popup))
        popup.finished.connect(
            lambda _r: setattr(ctx.runtime.playground, "focus_curve_popup", None))
        popup._fig = fig
        popup._canvas = canvas
        ctx.runtime.playground.focus_curve_popup = popup
        popup.show()

    draw_focus_distribution(popup._fig, dist)
    popup._canvas.draw_idle()
    popup.raise_()
    popup.activateWindow()


def draw_focus_distribution(fig, dist) -> None:
    """Paint *dist* onto *fig*. Pure matplotlib — no Qt, no app state.

    Separate from the window so a test can assert what was *drawn* rather than
    what was computed (CLAUDE.md §7.4 #1): the counts being right is not the
    same as the line being there.

    **A cumulative curve, not a histogram**, and the choice is the point. A
    histogram's shape is an artifact of its bin width, and the question a user
    is here to answer — "at this cut, how much of my frame goes?" — is read
    off a cumulative curve directly and off a histogram only by eye. The
    cumulative form also makes the two degenerate cases visible: a clean frame
    is one smooth rise, and a frame with a genuinely soft population has a
    step. Those are exactly the two an automatic rule could not tell apart,
    which is why they are drawn rather than classified.
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

    if dist.declined or not dist.values:
        # The refusal IS the figure, exactly as on the threshold curve.
        ax.set_axis_off()
        ax.text(0.5, 0.5,
                dist.describe() or "No frame has been measured yet.",
                ha="center", va="center", wrap=True, color=txt_pri,
                fontsize=11, transform=ax.transAxes)
        fig.tight_layout()
        return

    n = len(dist.values)
    # Share of cells at or below each value: read left to right, this is
    # exactly "how much of the frame a cut here would remove".
    share = [100.0 * (i + 1) / n for i in range(n)]
    ax.plot(dist.values, share, color=theme.Colors.accent, lw=1.8,
            label="cells at or below")
    ax.set_ylim(0, 100)

    if dist.cut is not None:
        ax.axvline(float(dist.cut), color=txt_pri, lw=1.2, ls="--",
                   label=f"your cut = {dist.cut:g}")
        # The consequence, on the figure rather than only in the sentence: the
        # number a user is actually choosing between is how many cells go, and
        # a reader saving this plot into a notebook should not have to have
        # read the panel it came from.
        pct = 100.0 * dist.dropped / n if n else 0.0
        ax.axhline(pct, color=txt_pri, lw=0.8, ls=":", alpha=0.6)
        ax.annotate(f"{dist.dropped:,} of {n:,} excluded ({pct:.0f}%)",
                    xy=(float(dist.cut), pct), xytext=(6, 6),
                    textcoords="offset points", color=txt_pri, fontsize=9)

    ax.set_xlabel("Per-cell focus (boundary sharpness)", color=txt_pri)
    ax.set_ylabel("Share of measured cells (%)", color=txt_pri)
    title = dist.describe()
    if dist.subject:
        title = f"{dist.subject}\n{title}"
    ax.set_title(title, color=txt_pri, fontsize=11)
    # The refusal, on the figure. It belongs here rather than only in a
    # tooltip because the absence of a recommendation is the single most
    # surprising thing about this plot, and a user who does not know it was
    # measured will read it as an omission.
    from review.domain.focus_distribution import NO_RECOMMENDATION

    ax.text(0.5, -0.16, NO_RECOMMENDATION, transform=ax.transAxes,
            ha="center", va="top", fontsize=8.5, color=txt_mut, wrap=True)
    leg = ax.legend(loc="best", framealpha=0.85,
                    facecolor=theme.Colors.panel, edgecolor=txt_mut,
                    fontsize=9)
    if leg is not None:
        for t in leg.get_texts():
            t.set_color(txt_pri)
    fig.tight_layout()
