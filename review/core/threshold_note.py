"""The note drawn over a "fraction above" / "events above" panel whose ON cutoff cuts nothing.

``channel_state_controller.on_cut_not_set`` decides when; this draws it, in place of the panel's bars or lines, so
an exported figure carries the same sentence the screen shows (CLAUDE.md §7: degraded output must never look like
good output). One helper for the Bar and Line tabs, so they cannot word it differently.
"""

from __future__ import annotations

#: The panels' replacement text. Named so tests and both tabs share it.
ON_CUT_NOT_SET_NOTE = ("ON threshold not set — every cell counts as above it.\n"
                       "Set one in the band or in Cell Gating.")


def blank_for_unset_cut(*axes) -> None:
    """Clear each axis's plotted artists and write :data:`ON_CUT_NOT_SET_NOTE` across it; titles stay."""
    from review.core.plot_style import tokens_for

    for ax in axes:
        if ax is None:
            continue
        for artist in list(ax.lines) + list(ax.patches) + list(ax.collections) + list(ax.texts) + list(ax.containers):
            try:
                artist.remove()
            except (ValueError, NotImplementedError, AttributeError):
                # A container (BarContainer) has no remove of its own on older matplotlib; its patches went above.
                continue
        legend = ax.get_legend()
        if legend is not None:
            legend.remove()
        ax.set_yticks([])
        muted = tokens_for(ax)[2]
        ax.text(0.5, 0.5, ON_CUT_NOT_SET_NOTE, transform=ax.transAxes, ha="center", va="center",
                color=muted, fontsize=9, gid="on_cut_not_set")
