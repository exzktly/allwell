"""Destination-field validators and the success-state button swap, shared by the
Datasets tool pages.

Each tool page asks the same two questions of a folder the user has typed —
"is this a dataset?" and "can I write a new one here?" — and does the same thing
to its buttons once a run succeeds. Those were written out per page, four
near-identical copies each, which is how six pages drift one small
inconsistency at a time (§6b tells that story about the log furniture, and
`_common/log_panel.py` is the fix it describes).

The wording of a rejection is deliberately shared too. A user moving between
Subset and Merge Plates should not be told the same thing two different ways.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from widgets._support import set_variant


def is_dataset(root: Path) -> str:
    """``""`` if *root* looks like a dataset, else a short reason to show under
    the field. Structural only — it does not read the sidecar, because this runs
    on every keystroke while the user is still typing a path."""
    if not (root / "in").is_dir() or not (root / "out").is_dir():
        return "Needs an 'in' and an 'out' folder."
    return ""


def is_free_destination(
    root: Path,
    *,
    is_complete: Callable[[object], bool],
    has_unrecognized: Callable[[object], bool],
) -> str:
    """``""`` if *root* is usable as a destination, else a short reason.

    The two predicates come from the caller because each tool binds them to its
    own in-progress marker (see ``_common.dataset_paths.make_dataset_api``): a
    folder holding *this* tool's interrupted attempt is a resume, while the same
    folder holding another tool's leftovers is not.

    Cross-field rules — "the destination must differ from both sources" — stay
    on the page, since they need fields this function cannot see.
    """
    from datasets._common.dataset_paths import DatasetPaths

    dest = DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out")
    if is_complete(dest):
        return "Already holds a finished dataset — choose an empty folder."
    if has_unrecognized(dest):
        return "Already has unrelated files — choose an empty folder."
    return ""


def promote_open_button(open_btn, run_btn) -> None:
    """Make *open_btn* the primary action and demote *run_btn*.

    Called when a run succeeds. The natural next step is opening the result, not
    running the same operation again — and a plain secondary Open button sat
    visually subordinate to a Run button that has nothing useful left to do
    until the inputs change.
    """
    open_btn.setVisible(True)
    set_variant(open_btn, "primary")
    set_variant(run_btn, "secondary")
