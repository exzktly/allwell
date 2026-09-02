"""Re-read the completeness record another process may have replaced.

The ``run_check`` block in ``pipeline_info.json`` is the one part of a loaded
dataset's sidecar that Review does not own. The pipeline writes it at the end of
a run, and the Datasets **Validate & Repair** tool writes it at the end of every
scan — including the scan Home's own "Check it now" action sends the user off to
perform. Review holds the sidecar it loaded with, so without a re-read the
dataset the user just had checked goes on reporting the very "no completeness
record" line that prompted them to check it.

**Only ``run_check`` is taken.** Re-reading the whole sidecar would discard the
in-memory user-side blocks — notes, sample definitions, gating, ratios —
including edits typed since the load and not yet saved. One key is read because
one key is the one another process owns.

This is a small JSON read, never a scan, and it is called at the two moments the
record can have changed underneath the page that reports it: when Home is
activated, and when the shell returns to the Review pane from the pane that
writes the record.
"""

from __future__ import annotations

import json
from pathlib import Path

from well_viewer.core.safe import swallow


def resync_run_check(ctx) -> bool:
    """Re-read the completeness record from the sidecar on disk.

    Returns whether the in-memory record changed, so a caller can repaint only
    when there is something new to show.

    A dataset whose sidecar has since been deleted or become unreadable keeps
    whatever record it loaded with rather than losing it to a transient read
    failure. A record *removed* from a sidecar that is otherwise readable is
    dropped, because reporting a stale one as current is the failure mode this
    whole module exists to avoid.
    """
    info = getattr(ctx.state.dataset, "pipeline_info", None)
    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    if not isinstance(info, dict) or data_dir is None:
        return False
    before = info.get("run_check")
    with swallow("completeness record resync"):
        from well_viewer.domain.viewer_state import resolve_pipeline_info_path

        path = resolve_pipeline_info_path(Path(data_dir))
        on_disk = json.loads(path.read_text())
        if not isinstance(on_disk, dict):
            return False
        record = on_disk.get("run_check")
        if isinstance(record, dict):
            info["run_check"] = record
        elif "run_check" in info and "run_check" not in on_disk:
            info.pop("run_check", None)
    return info.get("run_check") != before
