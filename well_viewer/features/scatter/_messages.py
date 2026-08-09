"""What the aggregate scatter says when it has nothing to draw.

Split out of ``collect.py`` rather than folded into ``controller.py``, and not
for size: ``collect.py`` is the *pure data collection* half — "no matplotlib
axes or Qt" is the contract ARCHITECTURE §3 states for the suffix and the
module's own docstring repeats — and composing user-facing prose while writing
to the Qt status bar is neither. The controller is the other legitimate home,
but it sits against its line budget, and budgets are a reason to extract a
cohesive module (§4), not a reason to put a side effect somewhere it does not
belong.

So this module owns one job: turning the collector's account of *why* a plot is
empty into the sentence the user reads, on the figure and on the status line.
"""

from __future__ import annotations

from well_viewer.core.safe import swallow


def agg_empty_message(ctx, cut_sink: dict | None) -> str:
    """What to draw when the aggregate collector returned nothing.

    An empty plot used to read "No data available. Please select wells/groups
    and timepoints." whatever the cause, which was a lie in the case that
    actually brought a user here: wells selected, timepoints ticked, counts
    present in the CSVs, and every single point silently dropped because the
    channel's ON cutoff sat above the whole range a spot tally can occupy. The
    message sent them back to a selection that was never wrong.

    So when the collector can say the cells were *cut*, say that instead — name
    the axis, the channel, the cutoff, and how many cells it kept, and point at
    the control that changes it.
    """
    from well_viewer.domain.metric_registry import CUTOFF_COUNT, cutoff_kind_for_column

    axis = next((a for a in ("X", "Y") if a in (cut_sink or {})), None)
    if axis is None:
        # A statement, not a prompt. ``tests/test_no_axes_selection_message.py``
        # keeps imperative selection prompts off the axes — they belong to the
        # tab's shared placeholder, in one wording, in one place. That guard
        # cannot see this string (it resolves constants and f-strings, not a
        # helper's return value), which is a reason to honour the rule here
        # rather than an excuse to ignore it.
        return ("No data at the selected timepoints.\n"
                "No cells matched for the wells on the plot.")
    info = cut_sink[axis]
    channel = str(info.get("channel") or "").upper()
    cutoff = float(info.get("cutoff", 0.0))
    n_total = int(info.get("n_total", 0))
    # Ask the shared predicate rather than re-testing the suffix here; a second
    # count metric would otherwise make this message say "intensity", and an
    # inline ``endswith("_smfish_count")`` is the exact duplication the metric
    # registry was created to collapse.
    is_count = cutoff_kind_for_column(str(info.get("col", ""))) == CUTOFF_COUNT
    quantity = "spot count" if is_count else "intensity"
    knob = "ThreshCountOn" if is_count else "ThreshFracOn"
    detail = (f"Every cell on the {axis} axis was cut by the ON cutoff:\n"
              f"{channel} {quantity} > {cutoff:g} kept 0 of {n_total:,} cells.")
    remedy = f"Lower {knob} for {channel} in\nSample Definitions → Cell Gating."
    # Mirror to the status line too — an empty figure is easy to scroll past,
    # and the remedy is the half worth carrying there.
    with swallow("scatter: status line unavailable for the cut-cells notice"):
        ctx.notify.set_status(f"{detail} {remedy}".replace("\n", " "))
        ctx.state.scatter.cut_status_posted = True
    # Hand-wrapped: matplotlib does not wrap by default, and the unwrapped
    # single line overflowed the card as soon as the window narrowed.
    return f"No points to plot.\n\n{detail}\n\n{remedy}"


def clear_cut_status(ctx) -> None:
    """Take down this tab's cut-cells notice before the aggregate redraws.

    Called once, at the top of ``redraw_scatter_agg_orchestrate``, rather than
    on the branch that succeeds in drawing. Every other exit from that redraw —
    no timepoints available, none ticked, no wells selected, a collector error —
    replaces the figure with a placeholder while leaving the shared status line
    telling the user to lower a cutoff for a plot that is no longer on screen.
    The notice is re-posted by :func:`agg_empty_message` when the cells really
    were all cut, so clearing first costs nothing in the case it describes.

    Only when this tab is the one that posted it: the status bar is persistent
    and shared, so clearing unconditionally would wipe whatever another part of
    the app had put there in the meantime.
    """
    if not getattr(ctx.state.scatter, "cut_status_posted", False):
        return
    ctx.state.scatter.cut_status_posted = False
    with swallow("scatter: status line unavailable to clear the cut-cells notice"):
        ctx.notify.set_status("")
