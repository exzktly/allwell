"""The focus-distribution window, shared by the two scopes that draw one.

Two surfaces ask the same question at different scopes and must not answer it
with differently-drawn curves: the Playground plots one *field* of the channel
on screen, and Cell Gating plots a whole *well* from the column the run wrote.
The cut applies at the dataset, so a user comparing the two is comparing scope,
not rendering.

It lives in ``review/core/`` rather than in either feature because the
Playground is a **removable** unit -- ``derive_profile`` deletes that directory
-- and a build without it would otherwise take the Cell Gating panel's button
down with it. The guard that says so names this remedy: move the shared code
down to where every sibling can reach it.

Qt for the window, pure matplotlib for the drawing, and the two are separate so
a test can assert what was **drawn** rather than what was computed.
"""

from __future__ import annotations

import textwrap

from PySide6.QtWidgets import QDialog, QVBoxLayout

from ui import theme

#: Characters per line for the figure's two blocks of prose, measured on the
#: rendered 820x460 window rather than estimated: the title at fontsize 11 and
#: the footnote at 8.5. Named here because they are the one pair of numbers in
#: this module that a change of figure size would invalidate.
_TITLE_WRAP = 92
_NOTE_WRAP = 118


def show_focus_distribution(ctx, dist) -> None:
    """Open (or repaint) the window plotting *dist*.

    A declined distribution is shown as its own sentence on empty axes rather
    than as a blank window or no window at all: the user pressed a button and
    is owed an answer, and "no cell here carried a measurable outline" is an
    answer — and a finding about the frame.
    """
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    popup = getattr(ctx.runtime.review, "focus_curve_popup", None)
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
            lambda _r: setattr(ctx.runtime.review, "focus_curve_popup", None))
        popup._fig = fig
        popup._canvas = canvas
        ctx.runtime.review.focus_curve_popup = popup
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
    # Wrapped explicitly rather than left to matplotlib. ``set_title`` does not
    # wrap at all, and the sentence runs to about 150 characters once the
    # unmeasurable clause and the cut's effect are both in it -- measured on
    # the render, both ends were clipped off the figure, which is a title a
    # reader cannot even tell is incomplete. ~92 characters is what fits at
    # this width and font size.
    title = "\n".join(textwrap.wrap(dist.describe(), _TITLE_WRAP))
    if dist.subject:
        title = f"{dist.subject}\n{title}"
    ax.set_title(title, color=txt_pri, fontsize=11)
    # The refusal, on the figure. It belongs here rather than only in a
    # tooltip because the absence of a recommendation is the single most
    # surprising thing about this plot, and a user who does not know it was
    # measured will read it as an omission.
    from review.domain.focus_distribution import NO_RECOMMENDATION

    # Wrapped here too, and for a worse reason than the title's: matplotlib's
    # ``wrap=True`` wraps to the **figure** width, not to the axes, so a
    # centred string overflowed both edges of the panel -- the first words of
    # two of its three lines were off the picture entirely.
    ax.text(0.5, -0.16, "\n".join(textwrap.wrap(NO_RECOMMENDATION, _NOTE_WRAP)),
            transform=ax.transAxes, ha="center", va="top", fontsize=8.5,
            color=txt_mut)
    leg = ax.legend(loc="best", framealpha=0.85,
                    facecolor=theme.Colors.panel, edgecolor=txt_mut,
                    fontsize=9)
    if leg is not None:
        for t in leg.get_texts():
            t.set_color(txt_pri)
    fig.tight_layout()
