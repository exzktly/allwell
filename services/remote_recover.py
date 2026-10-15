"""Bring home what an earlier remote run of this plate already finished.

A resumed remote run is an *incremental* one (``analyze.suspend`` forces the
flag on), and incremental staging decides what to upload from the **local**
output folder alone: a well with no local CSV and no local ``<well>_out.zip``
is uploaded whole and recomputed. That is the right answer for a well nobody
has processed, and exactly the wrong one for a well the previous run finished
on the server and never downloaded — the case a Suspend, a Stop, a dropped
connection or a closed laptop leaves behind once the remote compute is done.
Every well of such a plate was uploaded again and recomputed from scratch,
while its finished output sat in the previous run's remote directory, which an
incremental run deliberately never re-attaches to.

So before staging, this asks the one record that knows where that output went
— the run journal's remote address, keyed on the same images, the same
settings and the same host (:func:`services.run_journal.stored_remote_address`)
— and runs the ordinary end-of-run fetch against that directory: the sidecar
merge, ``errors/``, and the per-well pull of every well the remote holds output
for and the local folder lacks. Once those wells are local, the incremental
split sees them as done and uploads only what is genuinely missing.

The pull is the existing :class:`services.remote_fetch.FetchedOutputVerifier`,
pointed at the recorded directory, rather than a second implementation of
"fetch a well": the verifier is what already turns a finished-but-undownloaded
remote run into local output (``tests/test_a_remote_run_end_to_end.py``), and
two ways of doing that would come to disagree about what "arrived" means.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from services import run_journal
from services.remote_fetch import (
    _WELL_IN_MESSAGE,
    FetchedOutputVerifier,
    _snapshot_local_artifacts,
)
from services.remote_run_id import (
    _forced_from_argv,
    _local_input_dir_from_argv,
    pipeline_argv_digest,
)
from services.remote_run_state import LocalInputMode, OutputFilter, _RemoteRun
from services.remote_scratch import source_fingerprint
from util.aw_safe import swallowed


@dataclass(frozen=True)
class Recovery:
    """What the pre-staging fetch found and did.

    ``ok`` is False only when the earlier run's directory holds output this
    folder lacks and the pull could not bring all of it home. The caller must
    not go on to stage and upload in that case: the wells that failed to arrive
    would be uploaded and recomputed — the very thing this exists to prevent —
    and an incremental run then empties its own remote output directory, which
    can be the one still holding them.
    """

    ok: bool = True
    remote_dir: str = ""
    #: Wells the earlier run left finished output for on the remote.
    on_remote: frozenset[str] = frozenset()
    #: Of those, the ones the local folder was missing before the pull.
    missing_locally: frozenset[str] = frozenset()
    #: Verify's own account of what is still missing after the pull.
    still_missing: tuple[str, ...] = ()


def recover_finished_outputs(
    runner,
    local_output_dir: Path,
    pipeline_argv: list[str],
    *,
    output_filter: OutputFilter,
    log_fn: Callable[[str], None] | None = None,
    stop_event: threading.Event | None = None,
) -> Recovery:
    """Pull every well an earlier run of this plate finished on the remote.

    ``runner`` is the :class:`services.remote_runner.RemoteRunner` the run will
    use; its host, dataset cache and host-level argv extras decide both which
    recorded address applies and how the pull reaches the host.

    Declines — returns an empty, successful :class:`Recovery` — when the run is
    forced (a forced well is to be recomputed, so its old output is not wanted),
    when no recorded address matches all three keys, and when the recorded
    directory holds nothing the local folder lacks.
    """
    log = log_fn or (lambda _m: None)
    local_output_dir = Path(local_output_dir)
    forced, forced_wells = _forced_from_argv(pipeline_argv)
    if forced or forced_wells:
        return Recovery()
    try:
        stored = run_journal.stored_remote_address(
            local_output_dir,
            argv_digest=pipeline_argv_digest(
                pipeline_argv, extras=runner._host_argv_extras(output_filter)),
            source_key=source_fingerprint(_local_input_dir_from_argv(pipeline_argv)),
            host_alias=runner.host.ssh_alias,
        )
    except OSError:
        swallowed(f"could not read the run journal in {local_output_dir}")
        return Recovery()
    remote_dir = str((stored or {}).get("remote_output_dir") or "")
    if not remote_dir:
        return Recovery()

    run = _RemoteRun(
        run_id=str(stored.get("run_id") or ""),
        cache_key=str(stored.get("cache_key") or ""),
        remote_input_dir=str(stored.get("remote_input_dir") or ""),
        remote_output_dir=remote_dir,
        remote_pidfile="",
        local_output_dir=local_output_dir,
        # Every mode now materialises a ``<well>_out.zip``, and the verifier's
        # pull treats all four alike; the mode is not known before staging.
        local_input_mode=LocalInputMode.ZIP,
        local_input_dir=_local_input_dir_from_argv(pipeline_argv),
        output_filter=output_filter,
        pre_run_artifacts=_snapshot_local_artifacts(local_output_dir),
    )
    verifier = FetchedOutputVerifier(runner.host, run, runner._cache,
                                     stop_event or threading.Event())
    host = runner.host.ssh_alias
    log(f"[remote] resume: checking {host}:{remote_dir}, where the previous "
        f"run of this plate wrote its output, for finished wells not yet "
        f"downloaded\n")
    on_remote = frozenset(verifier._list_remote_completed_wells())
    # Asked silently: these are not failures yet, only the question of whether
    # there is anything to pull, and the verify pass below reports per well.
    missing = verifier._verify_local_manifest(log=lambda _m: None)
    # Read out of the messages with verify's own pattern, so "which well is
    # this about" has the one answer the end-of-run retry already uses.
    missing_wells = frozenset(
        m.group(1) for m in (_WELL_IN_MESSAGE.search(t) for t in missing) if m)
    if not missing_wells:
        log(f"[remote] resume: {len(on_remote)} well(s) finished on the remote, "
            f"none missing locally — nothing to download first\n")
        return Recovery(remote_dir=remote_dir, on_remote=on_remote)
    log(f"[remote] resume: {len(missing_wells)} well(s) finished on the remote "
        f"but never downloaded ({_sample(missing_wells)}) — downloading them "
        f"instead of uploading and recomputing them\n")
    verdict = verifier.verify(True, log=log)
    return Recovery(ok=verdict.passed, remote_dir=remote_dir,
                    on_remote=on_remote, missing_locally=missing_wells,
                    still_missing=tuple(verdict.missing))


def _sample(wells: frozenset[str], n: int = 6) -> str:
    shown = sorted(wells)[:n]
    more = len(wells) - len(shown)
    return ", ".join(shown) + (f", … {more} more" if more > 0 else "")
