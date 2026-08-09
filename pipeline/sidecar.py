"""The ``pipeline_info.json`` blocks that belong to the user, not to a run.

Most of the sidecar describes how a run was produced — schema, channel tokens,
segmentation parameters — and every run rewrites it. A few blocks describe what
the *user* decided afterwards in Review: cell-gating thresholds, saved sample
definitions, ratios, notes. Those have no author on the pipeline side, so any
code that replaces the sidecar wholesale destroys them unless it carries them
across first.

Two places replace it wholesale, and they used to disagree about this:
``process_microscopy.write_pipeline_info`` (a re-run against the same output dir)
carried the blocks over, while the remote runner's end-of-run fetch (which rsyncs
the remote sidecar over the local one) carried only ``run_report``. A remote run
therefore threw away thresholds the local auto-threshold pass had just written,
along with any sample definitions, ratios and notes the user had saved before
re-running. One list, imported by both, is what keeps them from drifting again.

Qt-free and stdlib-only: the pipeline imports it, so it must stay inside the
pipeline's own dependency set (see the §6.2 self-sufficiency contract), and it is
listed in ``services.remote_runner._REQUIRED_REMOTE_FILES``.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Sidecar keys written by the Review side and owned by the user. Anything that
#: rewrites ``pipeline_info.json`` from run state must preserve these.
#:
#: ``run_report`` and ``run_check`` are deliberately **not** here, and the
#: instinct to add them should be resisted. Both describe one run's output, so
#: carrying them across a rewrite means a run that died before it could describe
#: itself keeps the *previous* run's description — a Home page reporting a plate
#: as 96/96 complete when the run that just finished wrote four wells. Losing a
#: record is recoverable and visible; inheriting a stale one is neither.
USER_SIDE_BLOCKS: tuple[str, ...] = (
    "cell_gating",
    "sample_definitions",
    "ratios",
    "notes",
)


def read_user_blocks(info_path) -> dict:
    """The user-side blocks currently in *info_path*, or ``{}``.

    Missing, unreadable or non-dict sidecars yield an empty mapping — this is a
    best-effort rescue, and failing it must never take a run down with it.
    """
    path = Path(info_path)
    if not path.exists():
        return {}
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(info, dict):
        return {}
    return {k: info[k] for k in USER_SIDE_BLOCKS if k in info}


def restore_user_blocks(info_path, prior: dict | None) -> list[str]:
    """Fold *prior*'s blocks back into a sidecar that was just overwritten.

    Only blocks the new file does **not** already carry are restored, so a run
    that legitimately produced its own (the auto-threshold pass writing
    ``cell_gating``) keeps it. Returns the key names actually restored, so the
    caller can say so rather than doing it silently.
    """
    if not prior:
        return []
    path = Path(info_path)
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(info, dict):
        return []
    restored = [k for k, v in prior.items()
                if k in USER_SIDE_BLOCKS and k not in info and v is not None]
    if not restored:
        return []
    for k in restored:
        info[k] = prior[k]
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(info, indent=2))
        tmp.replace(path)
    except OSError:
        return []
    return restored
