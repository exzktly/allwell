"""The application's three panes, named once.

`allwell.py` builds Review, Analyze and Datasets into one window and switches
between them with a segmented control whose index has to agree with the stack's
page order. That agreement needs a single roster, and this is it.

It lived in `review/core/tab_controller.py` — the roster of all three panes,
inside one of them, imported back out by the title bar that owns the switch. The
ordering is the shell's business, not Review's: Review does not know the other
two panes exist, and nothing in this tuple is about what Review does.

`pane_mode` is deliberately total. An out-of-range index answers `"review"`
rather than raising, because the caller is a Qt signal handler reacting to a
stack whose page count can change while a rebuild is in flight, and a pane that
falls back to the first one is recoverable where an exception mid-signal is not.
"""
from __future__ import annotations

#: In the order the central stack holds them, which is the order the title bar's
#: segmented control shows. Index and name are the same fact, so they are
#: declared together rather than in two places that can disagree.
PANE_MODES: tuple[str, ...] = ("review", "analyze", "datasets")


def pane_mode(idx: int) -> str:
    """The mode name for a central-pane stack index (out of range → review)."""
    return PANE_MODES[idx] if 0 <= idx < len(PANE_MODES) else "review"


def pane_index(mode: str) -> int:
    """The stack index for a mode name (unknown → 0, i.e. Review).

    The inverse of :func:`pane_mode`, and here rather than at the call site
    because `allwell.py` had written the lookup inline — `PANE_MODES.index(mode)
    if mode in PANE_MODES else 0` — which is the same total-function decision
    made a second time, in a second place, with its own chance of drifting.
    """
    return PANE_MODES.index(mode) if mode in PANE_MODES else 0
