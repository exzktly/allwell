"""An Analyze run's two entries in the dataset's provenance log.

``PipelineRunner`` opens and closes the run journal for every run it starts —
local, remote and batched alike — so the same two moments are where the run is
written into the history (:mod:`util.provenance_log`). Emitting here rather
than from ``process_microscopy`` has two reasons, both measured:

- **One writer for every mode.** A remote run's pipeline executes on the worker
  host, and the fetch pulls named files only (``pipeline_info.json``, the
  per-well CSVs and archives, ``.aw_settings/``; ``services/remote_fetch.py``),
  so anything the remote pipeline appended would stay on the host. The client
  is the one process that sees the start and the end of every run.
- **``process_microscopy.py`` has no line headroom** (its budget in
  ``tests/test_monolith_budgets.py`` equals its length), and the runner already
  computes the mode and owns the outcome.

The cost is that a pipeline invoked directly from a terminal
(``python process_microscopy.py …``) writes no run events. That is stated in
ARCHITECTURE.md rather than left for someone to discover.

``run_finished`` carries the ``run_check`` summary only when the block in the
sidecar belongs to *this* run. A run that died before its end-of-run check
leaves the previous run's block in place, and quoting it would describe a
failed run with a healthy plate's totals. The block's own timestamps cannot
settle it — they are written to the second, and two runs of a small plate can
both fit in one — so :func:`begin_run` fingerprints the block as it stands
before the run, and :func:`finish_run` quotes the block only if the fingerprint
has changed. A run that rewrote a byte-identical block is reported as having
none: that errs toward "unknown", never toward the previous run's numbers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from util.provenance_log import append_event, block_digest

_LOG = logging.getLogger("allwell.provenance")

#: The run-scope options a ``run_started`` event records. They say which wells
#: the run was told to touch, which is what makes a selective rerun legible in
#: the history.
SCOPE_KEYS = ("force", "force_wells", "incremental", "only_wells")


def _scope_of(opts: dict) -> dict:
    scope = {}
    for key in SCOPE_KEYS:
        value = opts.get(key)
        if isinstance(value, (list, tuple, set)):
            value = [str(v) for v in value]
        scope[key] = value
    return scope


@dataclass(frozen=True)
class RunMark:
    """What :func:`begin_run` recorded, handed back to :func:`finish_run`."""

    output_dir: Path
    mode: str
    prior_run_check: str | None


def _read_run_check(output_dir: Path | str) -> dict | None:
    path = Path(output_dir) / "pipeline_info.json"
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        _LOG.warning("could not read %s for the provenance log (%s)", path, exc)
        return None
    block = info.get("run_check") if isinstance(info, dict) else None
    return block if isinstance(block, dict) else None


def begin_run(output_dir: Path | str, *, mode: str, input_dir: Path | str,
              argv, opts: dict, now: float | None = None) -> RunMark:
    """Append ``run_started`` and fingerprint the sidecar's current run_check."""
    append_event(output_dir, "run_started", source="analyze", now=now, detail={
        "mode": str(mode),
        "input_dir": str(input_dir),
        "argv": [str(a) for a in (argv or ())],
        "scope": _scope_of(opts or {}),
    })
    return RunMark(output_dir=Path(output_dir), mode=str(mode),
                   prior_run_check=block_digest(_read_run_check(output_dir)))


def run_check_summary(mark: RunMark) -> dict | None:
    """This run's ``run_check`` totals, or ``None`` when it left none.

    ``None`` covers no sidecar, no block, and a block unchanged since
    :func:`begin_run` — the previous run's, left in place by a run that ended
    before its check.
    """
    block = _read_run_check(mark.output_dir)
    if block is None or block_digest(block) == mark.prior_run_check:
        return None
    return {
        "skipped": bool(block.get("skipped")),
        "suspended": bool(block.get("suspended")),
        "totals": dict(block.get("totals") or {}),
        "errors": dict(block.get("errors") or {}),
    }


def finish_run(mark: RunMark, *, outcome: str, now: float | None = None) -> bool:
    """Append ``run_finished``. ``True`` when it reached disk."""
    return append_event(mark.output_dir, "run_finished", source="analyze", now=now,
                        detail={
                            "mode": mark.mode,
                            "outcome": str(outcome),
                            "run_check": run_check_summary(mark),
                        })
