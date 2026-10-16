"""Bringing a remote run's output home, and proving it arrived.

Two owners, one subject. :class:`Fetcher` pulls each well's ``<well>_out.zip``
and CSVs back the moment the remote pipeline announces it done, overlapping the
download with later wells' compute, and keeps the mid-run ``pipeline_info.json``
current so Review can open the plate while the run is still going.
:class:`FetchedOutputVerifier` runs once the pipeline exits: it pulls the
top-level artefacts the per-well stream does not carry, merges the fetched
sidecar with the local one, checks every expected well's artefacts are on disk,
re-pulls any that are not, and returns a :class:`FetchVerification`.

**Neither deletes anything on the remote except what the run itself proved it
no longer needs.** The ``Fetcher`` removes a well's remote files only after that
well's local artefact is in place; the decision to remove the run's remote
directory belongs to ``RemoteRunner.verify_and_cleanup``, which acts on the
verification's verdict and on nothing else.
"""


from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import contextlib
import errno
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from services.remote_config import (
    RemoteHost,
    resolve_rsync_binary,
    to_rsync_path,
)
# Stdlib-only (fov, tp) done-key helpers — shared with pipeline.io (audit A2).
# This module import must stay imaging-stack-free; incremental_keys is csv/
# zipfile/pathlib only, so it never drags numpy/tifffile into the remote client.
from util.fov import sort_key as fov_sort_key
from util.timepoint import sort_key as tp_sort_key
# The conda-environment resolution cluster, re-exported so every call site in
# this module (and the tests that reach for it) is unchanged by the move.
# The record half of field quality, which is stdlib-only. Importing
# ``pipeline.field_quality`` here would pull numpy and scipy into the remote
# client — a guard walks this module's import graph and says so, because the
# client is sent to hosts where the pipeline's dependencies are not installed.
from pipeline.field_quality_record import (
    merge_fetched_field_quality,
    read_field_quality_block,
)
from pipeline.run_report import (
    merge_fetched_run_report,
    read_run_report_block,
)
from pipeline.settings_record import SETTINGS_DIRNAME
from pipeline.sidecar import (
    install_provisional_sidecar, read_user_blocks, restore_user_blocks)
from util.well_token import canonical_well_label as _canonical_well_label
from services import run_journal
from util.aw_safe import swallow, swallowed
from services.remote_csv import (
    compress_remote_csvs, list_remote_csvs, remote_csv_globs, retire_superseded_csvs)
from util.csv_gz import glob_csvs as _glob_csvs
# The shared "retry an OSError with a backoff, then fail with an actionable
# message" shield. A remote run does as much I/O on the user's own volumes as a
# local one — it reads the whole plate off the input volume to stage the upload
# and writes every fetched well back onto the output volume — so it needs the
# same protection the pipeline's result writes have. ``pipeline.io`` owns the
# pipeline-side wrapper; this module cannot import it (that would drag numpy /
# tifffile into the remote client), which is why the loop lives in ``util``.
from util.retry_io import retry_io, volume_hint

from services.remote_run_state import (
    LocalInputMode,
    OutputFilter,
    _RemoteRun,
    _canonical_well_from_path,
)
from services.remote_staging import input_well_containers
from services.remote_wire import (
    TRANSIENT_PREFIX,
    _BREADCRUMB_INTERVAL_S,
    _RE_MODE_WELLS,
    _RE_WELL_DONE,
    _TRANSFER_ADVICE,
    _WELL_XFER_INTERVAL_S,
    _well_xfer_line,
)
from services.remote_transport import (
    _fmt_secs,
    _rsync_ssh_transport,
    _rsync_with_retry,
    delete_remote_paths,
    pull_small_file,
)
from services.remote_scratch import (
    _new_transit_dir,
    _transit_dir,
    _zip_is_complete,
)


#: Which well a ``_verify_local_manifest`` message is about. That method emits
#: several forms — "missing CSV for A01", "empty CSV for A01",
#: "missing A01_out.zip locally", "missing A01/ output folder locally" — and a
#: trailing ``\b`` fails on the zip form, since ``_`` is a word character and
#: there is no boundary after ``A01``. Anchor on a leading boundary and forbid
#: only a trailing digit, so all four parse without over-matching ``A012``.
#: Named once because two readers now ask the same question of the same list.
_WELL_IN_MESSAGE = re.compile(r"\b([A-H]\d{2})(?![0-9])")



def _classify_fetched_wells(
    remote_wells: list[str],
    local_output_dir: Path,
    *,
    announced: set[str],
    skipped: dict[str, str],
) -> tuple[dict[str, int], list[str]]:
    """Group the remote's wells by what became of them, and describe each one.

    The journal is the preferred source: it recorded each well as the Fetcher
    decided it, so a well that *failed* is reported as failed rather than
    merely absent, and a well the end-of-run retry rescued is reported as
    fetched rather than lost. Reading it back here is also what lets a run
    killed before this method ever runs still have a record of everything it
    got to.

    The in-memory state is the fallback, and it is a real fallback rather than
    a formality. *skipped* is the Fetcher's own ``well -> reason`` map and
    *announced* the wells whose completion marker this run saw; together they
    are exactly what this report was built from before the journal existed. A
    journal that could not be written — a read-only output volume, a full disk
    — must cost the report its *extra* answers, never the ones it already had.

    Returns ``(counts, lines)``: a tally per outcome, and one report line per
    well. Module-level and pure, so the report can be exercised without an ssh
    connection or a live run.
    """
    from services import run_journal

    journal = run_journal.read_journal(local_output_dir)
    recorded = journal.wells if journal is not None else {}
    # Every journal state gets a bucket, plus the one outcome the journal has
    # no word for: a well the remote holds output for that this run never heard
    # about. Seeding from ``WELL_STATES`` rather than a literal list means a
    # state added there cannot fall silently into "never-announced" here.
    counts = {state: 0 for state in run_journal.WELL_STATES}
    counts["never-announced"] = 0
    lines: list[str] = []
    for well in remote_wells:
        entry = recorded.get(well)
        if entry is not None:
            outcome, note = entry.state, entry.reason
        elif well in skipped:
            outcome, note = "skipped", skipped[well]
        elif well in announced:
            outcome, note = "fetched", "this run produced new output"
        else:
            outcome, note = ("never-announced",
                             "no completion marker was seen for this well")
        counts[outcome] += 1
        lines.append(f"{well}  {outcome:<15}{note}")
    return counts, lines



def _snapshot_local_artifacts(local_output_dir: Path) -> dict:
    """Fingerprint the output artefacts already on disk, before the run.

    ``(size, mtime_ns)`` per path. Cheap — one ``stat`` per file, no reads — and
    it is the only way verify can tell "this well's output was replaced" from
    "this well's output is still the one from yesterday", which are otherwise
    the same observation: a file that exists and is non-empty.
    """
    out: dict = {}
    try:
        entries = list(local_output_dir.glob("*"))
    except OSError:
        return out
    for path in entries:
        try:
            if path.is_file():
                st = path.stat()
                out[path.name] = (st.st_size, st.st_mtime_ns)
        except OSError:
            continue
    return out



def _artifact_changed(run, path: Path) -> bool:
    """Whether *path* differs from its pre-run fingerprint.

    A file absent from the snapshot is new, so it changed. Anything that cannot
    be stat'd now is reported as changed rather than failing the run on an
    unreadable file — this check exists to catch output that was silently *not*
    written, not to invent new ways for a good run to fail.
    """
    before = (run.pre_run_artifacts or {}).get(path.name)
    if before is None:
        return True
    try:
        st = path.stat()
    except OSError:
        return True
    return (st.st_size, st.st_mtime_ns) != before



def _scan_output_fov_tps(
    output_dir: Path, *, schema: str, sep: str,
) -> tuple[set[str], set[str]]:
    """``(timepoints, fovs)`` token sets recovered from the merged outputs in
    *output_dir* — the ``<well>_out.zip`` members (and any loose images / output
    well folders). Output member names keep the input stem, so the schema-indexed
    timepoint/FOV tokens come straight off them. Stdlib-only."""
    fields = [f.strip().lower() for f in str(schema or "").split(":")]
    ti = fields.index("timepoint") if "timepoint" in fields else -1
    fi = fields.index("fov") if "fov" in fields else -1
    tps: set[str] = set()
    fovs: set[str] = set()

    def _add(name: str) -> None:
        stem = Path(name).stem
        if not stem or Path(name).suffix.lower() not in (".tif", ".tiff", ".png"):
            return
        parts = stem.split(sep)
        if 0 <= ti < len(parts) and parts[ti].strip():
            tps.add(parts[ti].strip())
        if 0 <= fi < len(parts) and parts[fi].strip():
            fovs.add(parts[fi].strip())

    try:
        entries = list(output_dir.iterdir())
    except OSError:
        return tps, fovs
    for p in entries:
        if p.is_file() and p.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(p, "r") as zf:
                    for member in zf.namelist():
                        _add(Path(member).name)
            except (OSError, zipfile.BadZipFile):
                continue
        elif p.is_file() and p.suffix.lower() in (".tif", ".tiff", ".png"):
            _add(p.name)
        elif p.is_dir():
            try:
                for child in p.iterdir():
                    if child.is_file():
                        _add(child.name)
            except OSError:
                continue
    return tps, fovs



def _tp_sort_key(tok: str):
    r"""Chronological sort key for a timepoint token; unparseable tokens last.

    Delegates to :func:`util.timepoint.sort_key`. This used to inline its own
    parser — ``services/`` may not import ``review`` (ARCHITECTURE §6.2),
    so it could not share the viewer's — and the two drifted: a trailing ``\w*``
    in the unit pattern made it read ``"2hpCh"`` as two hours. ``util`` is
    stdlib-only, so it is importable from here and the divergence goes away.
    """
    return tp_sort_key(tok)



def run_client_side_completeness_check(
    local_input_dir: Path | None, local_output_dir: Path, *,
    log_fn: Callable[[str], None] | None = None,
) -> dict:
    """Record completeness for a remote run, on the client, after the fetch.

    The remote never writes this block — ``_build_remote_argv`` appends
    ``--no_run_check`` to every remote launch. Three reasons, and they compound:
    an incremental run's remote output dir holds only the uploaded split, so a
    record written there would inventory the wrong tree; the fetched sidecar
    overwrites the local one, so the record would be destroyed on arrival
    anyway; and a batched run is N separate pipeline launches, each of which
    would describe its own slice as if it were the plate.

    Running here answers a strictly better question. "Did the remote produce
    it" is what a remote-side check could say; "did it get here" is what this
    one says, and a file that failed to transfer is missing locally whether or
    not it ever existed on the far side.

    **Any ``run_check`` the fetch brought back is dropped first.** The remote's
    suppression writes the same "turned off" stub a user's own
    ``--no_run_check`` would, and that stub riding back would tell the user they
    turned the check off when they did not. Clearing it first means the two
    outcomes here are an honest record or no record at all — and no record
    reads as "not checked", which is what actually happened.

    Best-effort throughout: a remote run's output is already on disk and
    verified, and failing to describe it must not fail it.
    """
    log = log_fn or (lambda _m: None)
    out_dir = Path(local_output_dir)
    info_path = out_dir / "pipeline_info.json"
    try:
        from pipeline.completeness import record_run_check
        from pipeline.io import _safe_atomic_text_write

        info = json.loads(info_path.read_text()) if info_path.exists() else {}
        if not isinstance(info, dict):
            info = {}
        if "run_check" in info:
            info.pop("run_check")
            with _safe_atomic_text_write(info_path) as fh:
                json.dump(info, fh, indent=2)

        schema = [f.strip() for f in str(info.get("schema") or "").split(":") if f.strip()]
        if not schema:
            log("[remote] no schema in the sidecar — skipping the completeness check.\n")
            return {}
        sep = str(info.get("separator") or "_")
        csv_prefix = str(
            (info.get("execution_options") or {}).get("csv_prefix") or "measurements")

        containers = input_well_containers(local_input_dir)

        return record_run_check(
            info_path=info_path,
            output_dir=out_dir,
            in_containers=containers,
            schema=schema,
            sep=sep,
            csv_prefix=csv_prefix,
            started_at=None,
            mode="remote",
            atomic_writer=_safe_atomic_text_write,
            log=_LogFnLogger(log),
        )
    except Exception as exc:
        log(f"[remote] completeness check failed: {exc}\n")
        return {}



class _LogFnLogger:
    """Adapts the remote runner's ``log(text)`` sink to a logger-shaped object.

    ``record_run_check`` writes through ``logging``-style calls because it is
    normally driven from inside the pipeline; here the destination is the GUI's
    line stream, which takes whole lines with a trailing newline.
    """

    def __init__(self, log_fn):
        self._log = log_fn

    def _emit(self, prefix, msg, args):
        text = (msg % args) if args else str(msg)
        self._log(f"{prefix}{text}\n")

    def info(self, msg, *args):
        self._emit("[remote] ", msg, args)

    def warning(self, msg, *args):
        self._emit("[remote] ", msg, args)

    def error(self, msg, *args):
        self._emit("[remote] ", msg, args)



def refresh_pipeline_info_available_from_outputs(
    local_output_dir: Path, *, log_fn: Callable[[str], None] | None = None,
) -> bool:
    """Recompute ``available_timepoints`` / ``available_fovs`` in the local
    ``pipeline_info.json`` from the merged outputs in *local_output_dir*.

    After an incremental remote run the fetched sidecar only knows the
    timepoints/FOVs the remote processed (the uploaded new-FOV split), so the
    Review dropdowns built from it miss the rest of the dataset. The merged
    ``<well>_out.zip`` members carry the full input stem, so scanning the output
    dir recovers every ``(fov, timepoint)``. Union with whatever the sidecar
    already lists (never drop a known value) and write back, preserving every
    other key. Stdlib-only, best-effort: returns True iff the file was rewritten.
    """
    log = log_fn or (lambda _m: None)
    p = Path(local_output_dir) / "pipeline_info.json"
    if not p.exists():
        return False
    try:
        info = json.loads(p.read_text())
        if not isinstance(info, dict):
            return False
        schema = str(info.get("schema") or "")
        sep = str(info.get("separator") or "_")
        if not schema:
            return False
        tps, fovs = _scan_output_fov_tps(Path(local_output_dir), schema=schema, sep=sep)
        tp_union = sorted(set(tps) | set(info.get("available_timepoints") or []),
                          key=_tp_sort_key)
        fov_union = sorted(set(fovs) | set(info.get("available_fovs") or []),
                           key=fov_sort_key)
        if tp_union == list(info.get("available_timepoints") or []) \
                and fov_union == list(info.get("available_fovs") or []):
            return False  # already complete
        info["available_timepoints"] = tp_union
        info["available_fovs"] = fov_union
        tmp = p.with_suffix(p.suffix + ".tmp")
        payload = json.dumps(info, indent=2)

        def _cleanup_tmp() -> None:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

        def _publish() -> None:
            tmp.write_text(payload)
            os.replace(str(tmp), str(p))

        # Lands on the output volume like every other result, so it gets the
        # same shield — losing the sidecar to one transient fault would leave
        # Review's timepoint/FOV dropdowns short of the data that is on disk.
        retry_io(
            _publish, what=p.name, cleanup=_cleanup_tmp,
            advice=_TRANSFER_ADVICE, log_fn=log, path=p,
        )
        log(
            f"[remote] refreshed pipeline_info from merged outputs: "
            f"{len(tp_union)} timepoint(s), {len(fov_union)} FOV(s)\n"
        )
        return True
    except (OSError, json.JSONDecodeError) as exc:
        log(f"[remote] warn: pipeline_info refresh failed: {exc}\n")
        return False


#: How often the mid-run sidecar poller retries, and how long it keeps trying.
#: The remote pipeline writes ``pipeline_info.json`` before any image work, but
#: the job it is in can sit in the scheduler's queue first, so the window has to
#: cover a wait rather than a start-up. The poll itself is one rsync of a file
#: measured in kilobytes.
SIDECAR_POLL_S = 15.0
SIDECAR_POLL_MAX_S = 12 * 3600.0



class Fetcher:
    """Per-well output puller. Triggered by stdout markers from the
    running pipeline; runs each pull in a small thread pool so the
    upload of well N+1 doesn't block the download of well N's output.

    It also pulls ``pipeline_info.json`` once, as early as the remote has one.
    That file is not a per-well output and nothing in the stdout stream
    announces it, so it has its own poller rather than a marker: see
    :meth:`_sidecar_loop`.
    """

    def __init__(
        self,
        host: RemoteHost,
        run: _RemoteRun,
        *,
        output_filter: OutputFilter,
        log_fn: Callable[[str], None] | None = None,
        progress_fn: Callable[[float], None] | None = None,
        delete_after_download: bool = False,
        poll_sidecar: bool = False,
    ):
        self.host = host
        self.run = run
        self.output_filter = output_filter
        self._log = log_fn or (lambda _msg: None)
        self._progress = progress_fn
        self._delete_after_download = delete_after_download
        self._stop = threading.Event()
        self._pending: queue.Queue[str | None] = queue.Queue()
        # (well, step, since) for the drain heartbeat; replaced whole, never mutated.
        self._activity: tuple[str, str, float] | None = None
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()
        # Ends the sidecar poll on its own, without stopping the well fetches:
        # the file lands once and the poller has nothing left to do.
        self._sidecar_done = threading.Event()
        self._sidecar: threading.Thread | None = None
        # Off unless asked. The poller reaches the network on a timer, which a
        # Fetcher built to exercise one method in a unit test must not do; both
        # production sites turn it on, and
        # `tests/test_remote_midrun_sidecar.py` fails if a third one forgets.
        if poll_sidecar:
            self._sidecar = threading.Thread(target=self._sidecar_loop,
                                             daemon=True)
            self._sidecar.start()

    def observe(self, stdout_line: str) -> None:
        """Hand a pipeline-stdout line to the Fetcher.

        Looks for the ``— done.`` marker that the existing
        ``ProgressTracker`` already parses, plus the mode banner that
        announces the well total.
        """
        # v5.4.7: lock the well total to whatever set it FIRST.
        # In single-shot mode the pipeline emits exactly one banner
        # and this is a no-op refinement. In batched mode the
        # ``_BatchedRemoteProcess`` pre-sets ``well_count_total`` to
        # the full plate count and injects a synthetic banner before
        # batch 1; without this guard, each per-batch banner would
        # overwrite the lock with that batch's count, and the
        # Fetcher's per-well log lines would read e.g. "10/5: starting
        # download" once batch 2 began.
        m = _RE_MODE_WELLS.search(stdout_line)
        if m and not self.run.well_count_total:
            self.run.well_count_total = int(m.group(1))
        # v5.2.17: use .search not .match — the line carries a
        # ``YYYY-MM-DD HH:MM:SS  INFO`` prefix from the basicConfig
        # formatter in ``process_microscopy.py``. Without .search the
        # regex never matches and per-well rsync never fires.
        m = _RE_WELL_DONE.search(stdout_line)
        if m:
            well = _canonical_well_label(m.group("well")) or m.group("well").upper()
            self.run.expected_wells.add(well)
            self._pending.put(well)

    def cancel(self) -> None:
        self._stop.set()
        self._sidecar_done.set()
        self._pending.put(None)

    def flush_and_join(self, timeout: float = 60.0) -> None:
        """Signal end-of-stream and wait for outstanding pulls.

        Called by the Popen wrapper's ``wait()`` so the run isn't
        reported complete until every well's output has landed.

        If the fetcher was **cancelled** (Stop pressed → :meth:`cancel`), do
        *not* drain: the user asked to abort, not to wait for the wells still
        queued for download. Just reap the worker — it returns promptly on the
        stop event — and exit, so Stop is immediate instead of blocking on the
        remaining queue.
        """
        # Whatever ends the run ends the sidecar poll with it: from here on
        # `verify_and_cleanup` fetches the finished sidecar, and a poller still
        # running would be racing it for the same path.
        self._sidecar_done.set()
        if self._stop.is_set():
            self._pending.put(None)          # unblock the worker's queue.get()
            self._worker.join(timeout=timeout)
            self._log("[remote] ↓ fetcher cancelled — "
                      "remaining downloads skipped\n")
            return
        pending_count = self._pending.qsize()
        if pending_count:
            self._log(
                f"[remote] ↓ draining fetcher: {pending_count} well(s) "
                f"still queued for download\n"
            )
        self._pending.put(None)
        # Wait for the worker to ACTUALLY finish draining — NOT a fixed 60s cap.
        # The old short timeout let join() return while downloads were still in
        # flight, so wait() returned and verify_and_cleanup deleted the remote
        # mid-fetch: "completed run → deleted cache → then finished fetcher".
        # Each per-well fetch is independently bounded (rsync --timeout + retry
        # cap), so the worker is guaranteed to terminate; block until it does,
        # logging a heartbeat each interval so a long-but-legitimate drain isn't
        # mistaken for a hang (and Stop still breaks out — cancel() sets _stop,
        # the in-flight rsync aborts, the worker exits and this join returns).
        while self._worker.is_alive():
            self._worker.join(timeout=timeout)
            if self._worker.is_alive():
                self._log(self._drain_heartbeat_line())
        # v5.2.18: surface the end-of-stream summary so the user
        # sees a clear "all downloads complete" marker rather than
        # the run silently going quiet.
        total = self.run.well_count_total
        fetched = len(self.run.fetched_wells)
        if total and fetched == total:
            self._log(
                f"[remote] ↓ fetcher: {fetched}/{total} wells downloaded "
                f"to {self.run.local_output_dir}\n"
            )
        elif total:
            self._log(
                f"[remote] ↓ fetcher: {fetched}/{total} wells downloaded "
                f"({total - fetched} missing — verify step will retry)\n"
            )
        else:
            self._log(
                f"[remote] ↓ fetcher: {fetched} well(s) downloaded "
                f"(remote never announced total)\n"
            )

    def _drain_heartbeat_line(self) -> str:
        """The drain's wait line, naming the well and step the worker is on —
        "still downloading…" during the post-download steps contradicted a
        plate that already showed the well fetched."""
        done = len(self.run.fetched_wells)
        tot = self.run.well_count_total
        tally = f"{done}/{tot} wells" if tot else f"{done} well(s)"
        activity = self._activity
        if activity is None:
            return f"[remote] ↓ fetcher still working… ({tally} fetched)\n"
        well, step, since = activity
        return (f"[remote] ↓ fetcher still working… ({tally} fetched) — "
                f"{well}: {step}, {_fmt_secs(time.monotonic() - since)} so far\n")

    def _set_activity(self, well: str, step: str) -> None:
        self._activity = (well, step, time.monotonic())

    # Internals ──────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while True:
            if self._stop.is_set():
                return
            try:
                item = self._pending.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                return
            try:
                self._fetch_one(item)
                self._activity = None
            except Exception as exc:
                self._activity = None
                # A crash in one well's fetch must not kill the fetcher thread
                # — that left the remaining wells silently un-downloaded behind
                # a misleading "N/M downloaded" line (audit E12). Log it and
                # move on to the next item; the verify/retry step re-pulls the
                # gap.
                #
                # The cause goes in the run log, not just the Python logger: a
                # bare "error on 'A01'" is what made a storage fault look like a
                # transfer bug. ``volume_hint`` names the protocol behind the
                # errno when it recognises one.
                import logging as _logging
                _logging.getLogger(__name__).exception(
                    "fetcher: error downloading %r; continuing", item)
                hint = volume_hint(exc)
                self._log(
                    f"[remote] ↓ fetcher: error on {item!r}: {exc}"
                    + (f" — {hint}" if hint else "")
                    + "; continuing\n"
                )

    def _remote_file_size(self, remote_path: str) -> int | None:
        """Byte size of a file on the remote, or ``None`` if it can't be read.

        The denominator for per-well download progress: the heartbeat below
        already counts the bytes that have landed, and this is what they are a
        fraction *of*. One small ssh per well, alongside the ones the Fetcher
        already makes for the CSV listing and the per-well cleanup — cheap
        beside the multi-megabyte transfer it is measuring.

        The script is piped to ``bash -s`` rather than passed as an argument so
        a ``~`` in the staging path still expands on the remote (the same
        lesson ``_rsync_glob`` and ``_delete_well_files_on_remote`` carry), and
        it tries GNU ``stat``, BSD ``stat`` and ``ls`` in turn so it works on a
        Linux cluster or a macOS host without having to know which it is.

        Returns ``None`` on any failure — a missing size means the caller
        reports bytes-so-far and no fraction, never an invented one.
        """
        script = (
            f"stat -c %s -- {remote_path} 2>/dev/null || "
            f"stat -f %z -- {remote_path} 2>/dev/null || "
            f"ls -ln -- {remote_path} 2>/dev/null | awk '{{print $5}}'\n"
        )
        try:
            result = subprocess.run(
                self.host.ssh_argv(allocate_tty=False) + ["bash", "-s"],
                input=script.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        out = (
            result.stdout.decode("utf-8", errors="replace")
            if result.stdout else ""
        )
        for token in out.split():
            if token.isdigit():
                size = int(token)
                return size if size > 0 else None
        return None

    @contextlib.contextmanager
    def _transfer_heartbeat(self, label: str, dest_path: Path,
                            interval: float = 8.0,
                            breadcrumb_interval: float = _BREADCRUMB_INTERVAL_S,
                            total_bytes: int | None = None,
                            bytes_so_far=None,
                            base_bytes: int = 0):
        """Emit a periodic "still transferring…" line showing bytes-so-far while a
        per-well rsync runs, so a long download is visibly *moving* rather than
        looking hung. The per-well fetch rsync is silent (capture_output), so
        without this the user has no way to tell a slow transfer from a stuck one.
        Bytes-so-far = the growing rsync temp/partial file(s) in the dest dir.

        When ``total_bytes`` is known (the remote size of the file being
        pulled, from :meth:`_remote_file_size`) the same count also drives the
        well's dial on the dashboard, emitted on its own much faster cadence so
        the dial moves smoothly while the log and the status line keep their
        slower ones. Without a total the status line still reports bytes-so-far
        and no fraction is claimed — an unknown denominator must not become a
        made-up percentage.

        ``bytes_so_far`` overrides the counter for a transfer that does not land
        in a single file — the CSV half of a well's fetch is a directory of
        them. ``base_bytes`` is added to whatever the counter reports, so a
        second phase continues the *same* gauge rather than restarting it: a
        dial that dropped back toward zero partway through a fetch would read as
        a retry. With both phases measured against one combined denominator the
        number only ever goes up.
        """
        stop = threading.Event()
        t0 = time.monotonic()
        _counter = bytes_so_far

        def _bytes_so_far() -> int:
            if _counter is not None:
                return int(base_bytes) + int(_counter())
            total = int(base_bytes)
            try:
                # rsync streams into a hidden temp ``.{name}.XXXXXX`` (or the
                # partial file) in the dest dir, renaming to dest on completion.
                for p in dest_path.parent.glob(f".{dest_path.name}.*"):
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
            except OSError:
                pass
            try:
                total += dest_path.stat().st_size
            except OSError:
                pass
            return total

        def _beat() -> None:
            # Three cadences, because they answer three different questions.
            # The dial tick is the fastest: it is a position on a gauge, so it
            # has to move often enough to look alive, and it costs one string.
            # The status line is live state — it never reaches the log, so a
            # long transfer no longer writes a line every few seconds and
            # pushes real events out of the log's 10 000-block cap. The
            # breadcrumb is the durable record: a status line cannot answer
            # "was it stuck at 3am?" the next morning, and one line a minute is
            # a record rather than a flood.
            last_breadcrumb = t0
            last_frac = -1.0
            # The loop runs at the dial's cadence, or at the caller's if that
            # is finer — a caller asking for a fast status line must not be
            # slowed to the dial's tick. The status line then fires every Nth
            # tick, counted rather than clock-compared, so its cadence cannot
            # drift or skip on a tick that returns a hair early.
            tick = min(_WELL_XFER_INTERVAL_S, interval)
            ticks_per_status = max(1, int(round(interval / tick)))
            ticks = 0
            if total_bytes:
                self._log(_well_xfer_line(label, "down", 0.0))
            while not stop.wait(tick):
                now = time.monotonic()
                seen = _bytes_so_far()
                ticks += 1
                if total_bytes:
                    frac = round(min(1.0, seen / total_bytes), 3)
                    if frac != last_frac:
                        last_frac = frac
                        self._log(_well_xfer_line(label, "down", frac))
                if ticks % ticks_per_status:
                    continue
                elapsed = _fmt_secs(now - t0)
                mb = seen / (1024 * 1024)
                of_total = (
                    f" / {total_bytes / (1024 * 1024):.1f}" if total_bytes else ""
                )
                self._log(
                    f"{TRANSIENT_PREFIX}Downloading {label} — "
                    f"{mb:.1f}{of_total} MB, {elapsed}\n"
                )
                if now - last_breadcrumb >= breadcrumb_interval:
                    last_breadcrumb = now
                    self._log(
                        f"[remote] ↓ {label}: still transferring… "
                        f"({elapsed}, {mb:.1f} MB so far)\n"
                    )

        th = threading.Thread(target=_beat, daemon=True)
        th.start()
        try:
            yield
        finally:
            stop.set()
            th.join(timeout=1.0)
            # Close the dial's window whatever happened. A well whose fetch
            # failed still has to stop showing a part-filled download; the
            # stage rules take over from here, and the well is only marked
            # downloaded once its CSVs have landed too.
            if total_bytes:
                self._log(_well_xfer_line(label, "down", 1.0))

    def _journal_well(self, well: str, state: str, *, reason: str = "") -> None:
        """Record what just became of *well*, beside the outputs.

        Written at the moment the decision is made rather than gathered at the
        end, which is the whole difference: a fetch that is interrupted has
        already recorded every well it got to, and the end-of-run
        reconciliation — which cannot run at all if the run dies — is then a
        rendering of this rather than the only place it exists.

        Best-effort. A well whose journal entry cannot be written is still
        fetched, and its output on disk is still the authoritative record of
        that.
        """
        from services import run_journal

        try:
            run_journal.record_well(
                self.run.local_output_dir, well, state, reason=reason)
        except OSError:
            swallowed(f"could not journal {well} as {state}")

    def _already_local(self, well: str) -> str | None:
        """Why this well needs no transfer at all, or ``None`` to fetch it.

        A well the remote **skipped** — up to date, or never uploaded by the
        incremental split — emits no ``Well X — done.`` marker, so it is not in
        ``expected_wells`` and this run produced nothing new for it. If its
        local artefacts are already complete there is nothing on the remote
        that this fetch could improve, and pulling it anyway costs a full
        per-well transfer: an ssh size probe, then two ``rsync --timeout=120``
        invocations, each retry-wrapped. That is the shape behind a fetch that
        sits on one well for minutes while the file it is pulling is already on
        disk, correct, and untouched by this run.

        Deliberately narrow. A well this run *did* process is always re-fetched,
        however complete the local copy looks, because a local artefact from an
        earlier run is exactly what the new output is meant to replace.
        """
        run = self.run
        if well in run.expected_wells:
            return None                       # this run produced new output
        if run.force or well in run.force_wells:
            # The user asked for this well to be reprocessed, so the local
            # artefact is the thing being replaced — "it already exists" is the
            # one reason that cannot justify keeping it. A forced well missing
            # from ``expected_wells`` means its completion marker was never
            # seen, which is a reason to look harder, not to skip.
            return None
        if run.output_filter is OutputFilter.FULL:
            local_zip = run.local_output_dir / f"{well}_out.zip"
            if not (local_zip.exists() and _zip_is_complete(local_zip)):
                return None
        if not _glob_csvs(run.local_output_dir, f"*_{well}"):
            return None
        return "already complete locally and untouched by this run"

    def _fetch_one(self, well: str) -> None:
        """Pull a single well's output. The remote pipeline produces
        ``{output_dir}/{well}_out.zip`` and a sibling
        ``{csv_prefix}_{well}.csv`` (see report §2).

        v5.2.18: emits visible progress lines so the user can see
        what's being downloaded. Pre-v5.2.18 the Fetcher worked
        silently — a long fetch looked like the GUI had hung.

        v5.2.28: short-circuit if the stop event fired between the
        ``_loop`` check and now (mid-rsync cancellation is handled
        deeper by ``_rsync_with_retry``).
        """
        if self._stop.is_set():
            return
        skip_reason = self._already_local(well)
        if skip_reason is not None:
            self._log(f"[remote] ⤼ {well}: {skip_reason} — not re-fetched\n")
            self.run.skipped_wells[well] = skip_reason
            self.run.fetched_wells.add(well)
            self._journal_well(well, "skipped", reason=skip_reason)
            return
        # On the output volume, not the home one: this file is the well's
        # output on its way to `local_output_dir`, so the move at the end is a
        # rename rather than a copy across two filesystems, and a full home
        # volume no longer stops a run whose output volume has room.
        tmp_zip = (_transit_dir(self.run.local_output_dir, log_fn=self._log)
                   / f"allwell-{self.run.run_id}-{well}_out.zip")
        zip_src = f"{self.host.ssh_alias}:{self.run.remote_output_dir}/{well}_out.zip"
        csv_glob_src = f"{self.host.ssh_alias}:{remote_csv_globs(self.run.remote_output_dir, well)}"
        if well not in self.run.fetched_wells:     # not transferred yet: shrink it first
            compress_remote_csvs(self.host, self.run.remote_output_dir, well, self._log)

        total = self.run.well_count_total or 0
        position = len(self.run.fetched_wells) + 1
        prefix = f"({position}/{total}) " if total else ""

        # Say *why* this well is being pulled. A fetch that sits on one well
        # for minutes is only diagnosable if the log distinguishes the three
        # reasons a well reaches this point: it announced completion, the run
        # was told to reprocess it, or its local copy is incomplete. The last
        # one, on a well whose files look present, is the interesting case.
        if well in self.run.expected_wells:
            why = "this run produced new output"
        elif self.run.force or well in self.run.force_wells:
            why = "forced reprocess — the local copy is being replaced"
        else:
            why = "not complete locally"
        self._log(
            f"[remote] ↓ {prefix}{well}: starting download from "
            f"{self.host.ssh_alias} ({why})\n")

        # Ask the remote how big the zip is before pulling it. That number is
        # the denominator for this well's download dial; without it the
        # heartbeat can only say how many bytes have arrived, which cannot be
        # turned into a proportion.
        expected_bytes = self._remote_file_size(
            f"{self.run.remote_output_dir}/{well}_out.zip")

        # A well's output arrives as TWO transfers — the zip, then its CSVs —
        # and the dial has to span both or it stops moving partway through a
        # fetch that is still running. So the CSVs are *listed* here, before the
        # zip is pulled, purely to learn their byte total: both halves then
        # measure against one combined denominator and the gauge only goes up.
        # The listing is the same ssh the pull needs anyway, just moved earlier,
        # so this costs no extra round trip. Either size being unknown drops the
        # combined total to None and the dial simply does not run — an unknown
        # denominator must not become an invented one.
        csv_host, csv_files, csv_bytes = self._list_remote_glob(csv_glob_src)
        combined_bytes = (expected_bytes + csv_bytes
                          if expected_bytes and csv_bytes else expected_bytes)

        # Output zip — exists iff the pipeline finished this well. Pull it
        # even under CSV-only mode: we'll discard it after extracting the
        # files we want.
        t0 = time.monotonic()
        self._set_activity(well, "downloading the output zip")
        with self._transfer_heartbeat(well, tmp_zip, total_bytes=combined_bytes):
            ok = self._rsync(
                # ``--timeout`` is rsync's own I/O-idle timeout: if the link
                # stalls (or rsync hangs after the data is across, waiting on a
                # half-closed connection) rsync aborts in 2 min instead of the
                # whole transfer blocking until the 5-min subprocess deadline —
                # which used to leave the final fetch sitting there for minutes.
                [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
                 "-a", "--partial", "--timeout=120", zip_src, to_rsync_path(tmp_zip)]
            )
        # rsync can report a non-zero exit (timeout / connection reset on close)
        # *after* the zip is fully across. Don't throw away a complete file and
        # re-fetch it forever — if the .zip is intact on disk, proceed.
        if not ok and not _zip_is_complete(tmp_zip):
            self._log(
                f"[remote] ↓ {well}: WARN could not pull {well}_out.zip from "
                f"{self.run.remote_output_dir}; will retry at end of run.\n"
            )
            try:
                tmp_zip.unlink()   # don't leave a partial in scratch (audit X9)
            except OSError:
                pass
            self._journal_well(
                well, "not-fetched",
                reason=f"the output zip could not be pulled from "
                       f"{self.run.remote_output_dir}")
            return
        if not ok:
            self._log(
                f"[remote] ↓ {well}: rsync returned an error but the .zip is "
                f"complete on disk — proceeding.\n"
            )

        zip_elapsed = time.monotonic() - t0
        zip_size = tmp_zip.stat().st_size if tmp_zip.exists() else 0
        mb = zip_size / (1024 * 1024)
        mbps = (mb * 8) / zip_elapsed if zip_elapsed > 0 else 0.0
        self._log(
            f"[remote] ↓ {well}: {mb:.1f} MB zip in "
            f"{_fmt_secs(zip_elapsed)} ({mbps:.0f} Mbps)\n"
        )

        # CSVs live at the top of the remote output_dir, not inside the
        # well zip — pull them separately. A failure here isn't fatal:
        # the per-well CSV is the most important file and any retry can
        # re-pull it.
        t1 = time.monotonic()
        csv_listed = len(csv_files)
        self._set_activity(well, "downloading its CSV file(s)")

        def _csv_bytes_landed(dest_dir: Path) -> int:
            """Bytes of this well's CSVs on disk, including rsync's partials —
            the same accounting the zip half uses, over several files."""
            total = 0
            try:
                paths = {*dest_dir.glob(f"*_{well}.csv*"),
                         *dest_dir.glob(f".*_{well}.csv.*")}
            except OSError:
                return 0
            for p in paths:
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
            return total

        def _csv_dir_heartbeat(dest_dir: Path):
            """The dial, continuing across the CSV half of this well's fetch.

            The gauge carries on from the zip's byte count (``base_bytes``)
            against the combined denominator, so the ring keeps climbing rather
            than stopping — or, worse, restarting near zero, which reads as a
            retry. The CSVs land as several files in a directory rather than one
            named file, hence the counter override.
            """
            return self._transfer_heartbeat(
                well, dest_dir, total_bytes=combined_bytes,
                bytes_so_far=lambda: _csv_bytes_landed(dest_dir),
                base_bytes=int(expected_bytes or 0))

        if self.run.incremental:
            # Partial CSV (new FOV rows only) — merge into the local full CSV
            # rather than overwriting it.
            tmp_csv_dir = _new_transit_dir(
                self.run.local_output_dir, f"allwell-csv-{well}-",
                log_fn=self._log)
            try:
                with _csv_dir_heartbeat(tmp_csv_dir):
                    csv_count = self._pull_listed(csv_host, csv_files, tmp_csv_dir)
                if csv_count:
                    self._merge_fetched_csvs(tmp_csv_dir)
            finally:
                # `_new_transit_dir` made this directory for this one well a
                # few lines above and nothing of the user's is ever in it, so
                # removing the whole thing is safe. Exempted by name in
                # tests/test_no_unarchived_delete.py::_SCRATCH_DIR_ALLOWLIST,
                # which otherwise catches this function for reaching an
                # archive writer through `_materialize`.
                shutil.rmtree(tmp_csv_dir, ignore_errors=True)
            # The temp dir is gone by now; name the volume it lived on, which is
            # what the user would have to look at.
            csv_dest = tmp_csv_dir.parent
        else:
            csv_dest = self.run.local_output_dir
            with _csv_dir_heartbeat(csv_dest):
                csv_count = self._pull_listed(csv_host, csv_files, csv_dest)
            retire_superseded_csvs(csv_files, csv_dest)
        csv_elapsed = time.monotonic() - t1
        # Held back until the zip is in place: this line is the half that
        # paints the well fetched on the plate (ProgressTracker._mark_fetch_part).
        csv_line = (
            f"[remote] ↓ {well}: {csv_count} CSV file(s) in "
            f"{_fmt_secs(csv_elapsed)}\n"
            if csv_count > 0 else None
        )
        if csv_count == 0 and csv_listed:
            # The remote HAS the CSVs; every pull of them failed. That is a
            # local-side fault (a full or unwritable destination, or a network
            # mount that dropped its session), and saying "no CSV files found"
            # here would point the user at the wrong machine entirely.
            self._log(
                f"[remote] ↓ {well}: WARN the remote has {csv_listed} CSV "
                f"file(s) but none could be written to {csv_dest} — check that "
                f"volume; the verify step will retry.\n"
            )
        elif csv_count == 0:
            self._log(
                f"[remote] ↓ {well}: WARN no CSV files found at "
                f"{self.run.remote_output_dir}/*_{well}.csv\n"
            )

        # Now materialize the well's output in the user's output_dir
        # according to local input mode.
        self._set_activity(well, f"placing the output in {self.run.local_output_dir}")
        materialized = self._materialize(well, tmp_zip)
        try:
            tmp_zip.unlink()
        except OSError:
            pass
        if not materialized:
            # The local artifact isn't safely in place — do NOT mark the well
            # fetched or delete the remote copy (audit X2); leave it for the
            # end-of-run retry to re-pull.
            landed = f" ({csv_count} CSV file(s) did land)" if csv_line else ""
            self._log(
                f"[remote] ↓ {well}: WARN local materialize failed{landed}; "
                f"keeping the remote copy and leaving the well for retry.\n"
            )
            self._journal_well(
                well, "failed",
                reason="the output arrived but could not be written into the "
                       "output directory")
            return
        if csv_line is not None:
            self._log(csv_line)
        self.run.fetched_wells.add(well)
        self._set_activity(well, "fetching its settings record")
        self._pull_settings_marker(well)
        self._journal_well(well, "fetched")
        total_elapsed = time.monotonic() - t0
        position_done = len(self.run.fetched_wells)
        tally = f"{position_done}/{total} wells" if total else f"{position_done} wells"
        self._log(
            f"[remote] ✓ {well}: materialised to {self.run.local_output_dir} "
            f"(total {_fmt_secs(total_elapsed)}, {tally} fetched)\n"
        )
        if self._progress and self.run.well_count_total > 0:
            self._progress(len(self.run.fetched_wells) / self.run.well_count_total)

        # v5.4.6 — per-well remote cleanup. With the data safely landed
        # locally, both the remote input zip and the remote output zip
        # (plus per-well CSVs) become redundant. Deleting them as each
        # well is fetched bounds peak remote disk to roughly
        # ``(uploaded-but-not-yet-processed inputs) + 1 well's output``
        # — independent of how many wells the plate has. Crucial on
        # constrained remotes where the full plate's outputs would
        # otherwise overflow before the run completes. Failures are
        # logged but non-fatal — an end-of-run ``rm -rf`` via
        # ``verify_and_cleanup`` is the backstop.
        if self._delete_after_download:
            self._set_activity(well, "deleting its files on the remote")
            self._delete_well_files_on_remote(well)

    def _delete_well_files_on_remote(self, well: str) -> None:
        """rm the remote input + output zips + per-well CSVs for ``well`` once
        its download is safe — bounds peak remote disk to about one well's
        output. Non-fatal; ``verify_and_cleanup`` is the backstop."""
        rin, rout = self.run.remote_input_dir, self.run.remote_output_dir
        delete_remote_paths(
            self.host, [f"{rin}/{well}.zip", f"{rout}/{well}_out.zip",
                        f"{rout}/*_{well}.csv", f"{rout}/*_{well}.csv.gz"],
            what=f"per-well files of {well}", log_fn=self._log)

    def _merge_fetched_csvs(self, tmp_csv_dir: Path) -> None:
        """Merge each partial per-well CSV pulled into *tmp_csv_dir* into the
        matching full CSV under ``local_output_dir`` (incremental remote runs).

        The loop itself lives beside `merge_records_into_csv` in `pipeline.io`,
        because deciding what to do when one of these merges fails is a
        property of the merge, not of the remote runner.
        """
        from pipeline.io import merge_fetched_csvs

        merge_fetched_csvs(tmp_csv_dir, self.run.local_output_dir,
                           log_fn=self._log)


    def _materialize(self, well: str, well_zip: Path) -> bool:
        """Place the well's outputs in ``local_output_dir`` so the result
        matches what a local run would have produced.

        Returns True iff the local artifact is now in place. The caller gates
        ``fetched_wells.add`` + remote deletion on this (audit X2): the fetched
        zip is renamed into place with ``os.replace`` (atomic, and a same-volume
        rename because the transit directory sits beside ``dest``); only a
        cross-device rename falls back to copying via a temp path. Either way an
        interrupted step can't leave a truncated ``{well}_out.zip`` — and a
        failure returns False so the only complete (remote) copy is NOT deleted.
        """
        dest = self.run.local_output_dir
        dest.mkdir(parents=True, exist_ok=True)

        if self.output_filter is OutputFilter.CSV_ONLY:
            # Nothing to extract — the CSV was pulled separately. Drop
            # the well zip on the floor.
            return True

        if self.run.incremental:
            # The fetched zip holds only the new FOVs' images. Merge them into
            # the existing local <well>_out.zip instead of overwriting.
            return self._materialize_incremental(well, well_zip, dest)

        # Output is always a per-well .zip now (compression is mandatory), so
        # the fetched zip IS the expected local artifact for every input mode.
        # Drop it in place — previously FOLDER-mode inputs extracted it loose,
        # which left no <well>_out.zip and no <well>/ dir, so the verify step
        # failed and the post-run cleanup (delete local well folders) was
        # skipped.
        final = dest / f"{well}_out.zip"
        tmp = dest / f".{well}_out.zip.tmp"

        def _cleanup_tmp() -> None:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

        def _publish() -> None:
            # A rename, not a copy: the transit dir sits beside ``dest`` for
            # exactly this. Copying rewrote every byte onto the output volume.
            try:
                os.replace(well_zip, final)
                return
            except OSError as exc:
                # Transit fell back to scratch; other faults go to retry_io.
                if exc.errno != errno.EXDEV:
                    raise
            shutil.copyfile(well_zip, tmp)
            os.replace(tmp, final)

        # This is the remote run's equivalent of the pipeline's ``<well>_out.zip``
        # write, and it lands on the same volume — the user's output dir — so it
        # carries the same retry shield. Unguarded, a single dropped SMB session
        # failed the well here with a bare errno even though the data was already
        # safely on disk in scratch.
        try:
            retry_io(
                _publish,
                what=f"{final.name} into {dest}",
                cleanup=_cleanup_tmp,
                advice=_TRANSFER_ADVICE,
                log_fn=self._log,
                path=final,
            )
        except OSError as exc:
            self._log(f"[remote] warn: copying {well}_out.zip failed: {exc}\n")
            return False
        return True

    def _materialize_incremental(self, well: str, well_zip: Path, dest: Path) -> bool:
        """Merge a partial fetched well zip into the existing local output.

        Returns True on a successful merge, False if the partial zip couldn't be
        extracted (so the caller won't mark the well fetched / delete the remote
        copy — audit X2)."""
        from pipeline.io import merge_images_into_zip

        # Beside the destination, for the same reason as the transit zip above:
        # this holds one well's extracted output on its way into `dest`.
        tmp_dir = _new_transit_dir(dest, f"allwell-merge-{well}-",
                                   log_fn=self._log)
        try:
            try:
                with zipfile.ZipFile(well_zip, "r") as zf:
                    zf.extractall(tmp_dir)
            except (OSError, zipfile.BadZipFile) as exc:
                self._log(f"[remote] warn: extracting partial {well}_out.zip failed: {exc}\n")
                return False
            n = merge_images_into_zip(dest / f"{well}_out.zip", tmp_dir)
            self._log(f"[remote] merge: {well}_out.zip → +{n} new image(s)\n")
            return True
        finally:
            # The extraction directory this function made for this one well,
            # holding only what `zf.extractall` just put there. The user's own
            # files are in `well_zip` and in `dest`, neither of which is
            # removed. Exempted by name in
            # tests/test_no_unarchived_delete.py::_SCRATCH_DIR_ALLOWLIST,
            # which otherwise catches this function for calling
            # `pipeline.io.merge_images_into_zip`.
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # Mid-run sidecar ────────────────────────────────────────────────────

    def _pull_settings_marker(self, well: str) -> None:
        """Bring back the record of which configuration produced this well.

        The pipeline writes ``.aw_settings/<WELL>.json`` beside each well's
        output, and the next run reads it to decide whether that output may
        stand — a plate re-run with a different segmenter must not be answered
        "already done". For a remote run the pipeline's output directory is on
        the worker host, and the fetch pulls only ``<well>_out.zip`` and the
        per-well CSV, so without this a remotely-computed plate lands with no
        marker at all: permanently grandfathered, and the settings record inert
        for every remote user.

        Pulled from the worker rather than recomputed here, so there is one
        definition of the digest and the client cannot come to disagree with
        the process that did the work. Per well and after the well has
        materialised, so a well whose fetch failed is not credited with output
        it does not have.
        """
        pull_small_file(
            self.host,
            f"{self.run.remote_output_dir}/{SETTINGS_DIRNAME}/{well}.json",
            self.run.local_output_dir / SETTINGS_DIRNAME / f"{well}.json",
            stop_event=self._stop, what=f"the settings marker for {well}",
        )

    def _sidecar_loop(self) -> None:
        """Pull ``pipeline_info.json`` as soon as the remote has written one.

        Review reads that sidecar for the filename schema, the channel tokens,
        and ``available_timepoints`` / ``available_fovs``; without it a dataset
        opens with no schema metadata and falls back to detecting channels from
        CSV column names. On a remote run it used to arrive only in
        :meth:`RemoteRunner.verify_and_cleanup`, at the very end — so a plate
        opened mid-run had its per-well CSVs (the Fetcher brings those down as
        each well finishes) and nothing to interpret them with.

        The remote writes it *before any image work*, so it exists within
        seconds of the pipeline starting. What it has to wait out is the
        scheduler: the job can queue for hours before that happens. Hence a
        poll rather than a one-shot, and a window measured in hours.

        Two rules make this safe to run alongside everything else that writes
        this file:

        * **It never overwrites.** A local sidecar already present belongs to a
          previous run of this dataset and carries blocks the remote's does not
          — cell-gating thresholds, sample definitions, ratios, notes, the
          run_report. It is authoritative; the poller stands down.
        * **It stamps what it wrote as provisional.** The sidecar a running job
          has produced describes only the input that job was given, which for a
          batched run is one batch's split — so its ``available_timepoints`` /
          ``available_fovs`` are a subset of the plate's, and the run_report is
          absent entirely. ``verify_and_cleanup`` replaces the file wholesale at
          the end of the run and the mark goes with it; a run that never got
          there leaves the mark in place, which is the honest outcome.
        """
        dest = self.run.local_output_dir / "pipeline_info.json"
        if dest.exists():
            return
        src = (f"{self.host.ssh_alias}:"
               f"{self.run.remote_output_dir}/pipeline_info.json")
        deadline = time.monotonic() + SIDECAR_POLL_MAX_S
        while not (self._stop.is_set() or self._sidecar_done.is_set()):
            # Wait *before* the first attempt, not only between attempts. This
            # thread starts in the same breath as the ssh that launches the
            # pipeline, so at t=0 the remote cannot have written the file yet
            # and an immediate pull is a round trip that is guaranteed to miss.
            self._sidecar_done.wait(SIDECAR_POLL_S)
            if self._stop.is_set() or self._sidecar_done.is_set():
                return
            if time.monotonic() > deadline:
                self._log(
                    "[remote] note: pipeline_info.json never appeared on the "
                    f"remote within {SIDECAR_POLL_MAX_S / 3600:.0f}h; Review "
                    "will get it when the run finishes.\n")
                return
            if self._pull_sidecar_once(src, dest):
                return
            self._sidecar_done.wait(SIDECAR_POLL_S)

    def _pull_sidecar_once(self, src: str, dest: Path) -> bool:
        """One quiet attempt. True once the local sidecar exists.

        Quiet on failure by design: the overwhelmingly common failure is "the
        remote has not written it yet", which is not news, and this runs every
        `SIDECAR_POLL_S` seconds. Only the success is worth a line, and it is
        worth one — it is the moment the plate becomes openable mid-run.
        """
        if dest.exists():
            return True
        tmp = dest.with_name(f".{dest.name}.midrun.tmp")
        result = _rsync_with_retry(
            [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
             "-a", src, to_rsync_path(tmp)],
            log_fn=None, timeout=60, stop_event=self._stop,
        )
        if result.returncode != 0 or not tmp.exists():
            with swallow("mid-run sidecar temp cleanup"):
                tmp.unlink()
            return False
        try:
            install_provisional_sidecar(tmp, dest, run_id=self.run.run_id)
        except (OSError, ValueError) as exc:
            self._log(f"[remote] note: could not install the mid-run "
                      f"pipeline_info.json ({exc}); Review will get it when "
                      f"the run finishes.\n")
            with swallow("mid-run sidecar temp cleanup"):
                tmp.unlink()
            return False
        self._log(
            "[remote] ↓ pipeline_info.json (provisional — this run's input "
            "only). The plate can be opened in Review now; wells appear as "
            "they finish.\n")
        return True

    def _rsync(self, argv: list[str]) -> bool:
        # Routed through the shared retry helper (v5.1.1) so transient
        # network glitches during the Fetcher's per-well pull don't
        # silently lose a well's output. Non-transient rsync errors
        # (permission, bad path) still fail immediately.
        #
        # v5.2.28: pass our own stop event so the Stop button can
        # actually interrupt the per-well rsync. Pre-v5.2.28 each
        # in-flight rsync had to run to completion before the
        # Fetcher noticed the stop_event between queue items —
        # which on a slow link looked like Stop did nothing.
        #
        # timeout=None: the per-well fetch argv carries rsync ``--timeout=120``
        # (I/O-idle) and the ssh transport sets ``ConnectTimeout=15``, so stalls
        # and dead connections are already bounded. The old fixed 5-min
        # subprocess wall-clock killed *healthy, still-progressing* transfers of
        # large final zips mid-flight, and --partial resume couldn't always
        # finish within the next window → endless retry/timeout/fail and a
        # forced rerun. Drop the wall-clock and let rsync's own idle timeout be
        # the stall guard.
        result = _rsync_with_retry(
            argv, log_fn=self._log, stop_event=self._stop, timeout=None,
        )
        return result.returncode == 0

    def _list_remote_glob(self, src_glob: str) -> tuple[str, list[str], int | None]:
        """``(host, remote paths, total bytes)`` for a remote glob, in one ssh.

        Split out of :meth:`_rsync_glob` so a caller can know what it is about
        to pull *before* it pulls it — the CSV half of a well's fetch needs its
        byte total up front, or the well's dial has no denominator for that half
        and has to stop moving at the end of the zip.

        The size is a second command in the *same* script, so this costs no
        extra round trip. It is deliberately separate from the path listing:
        ``ls -1`` gives one clean path per line and is what the pull has always
        parsed, while summing sizes needs ``ls -ln``'s columns, which vary
        between GNU and BSD and break on a filename with a space. If the sum is
        missing or unparseable the paths are still returned and the total is
        ``None`` — an unknown denominator means no dial, never an invented one.
        """
        host, path_glob = src_glob.split(":", 1)
        script = (
            f"ls -1 {path_glob} 2>/dev/null || true\n"
            f"echo '---allwell-bytes---'\n"
            f"ls -ln {path_glob} 2>/dev/null | awk '{{s+=$5}} END {{print s+0}}'\n"
        )
        try:
            listing = subprocess.run(
                self.host.ssh_argv(allocate_tty=False) + ["bash", "-s"],
                input=script.encode("utf-8"),
                capture_output=True,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError):
            return host, [], None
        out = (listing.stdout.decode("utf-8", errors="replace")
               if listing.stdout else "")
        head, _, tail = out.partition("---allwell-bytes---")
        files = [ln for ln in head.splitlines() if ln.strip()]
        total: int | None = None
        for token in tail.split():
            if token.isdigit():
                total = int(token) or None
                break
        return host, files, total

    def _pull_listed(self, host: str, files: list[str], dest_dir: Path) -> int:
        """Pull already-listed remote paths into *dest_dir*; return how many
        landed. The transfer half of :meth:`_rsync_glob`, split so the listing
        can happen earlier (see :meth:`_list_remote_glob`)."""
        pulled = 0
        dest_str = to_rsync_path(dest_dir).rstrip("/") + "/"
        for f in files:
            if self._rsync(
                [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
                 "-a", f"{host}:{f}", dest_str]
            ):
                pulled += 1
        return pulled

    def _rsync_glob(self, src_glob: str, dest_dir: Path) -> tuple[int, int]:
        """Pull files matching a glob. Wraps the source in a quoted-glob
        rsync so the remote side expands ``*``, not the local shell.

        Returns ``(listed, pulled)`` — how many files the remote's glob matched,
        and how many of them landed locally. The caller needs both to tell the
        two failures apart: the remote produced no CSVs for this well, or it
        produced them and the *local* write failed. Reporting only the pulled
        count made a dropped session on a network-mounted output dir read as
        "no CSV files found" on the remote, which sent every diagnosis in the
        wrong direction.
        """
        # We list over ssh first; rsync's own glob support requires the source
        # to be quoted in a way that's awkward to spell cross-shell. The listing
        # and the per-file rsync both go through the runner's ssh policy (key,
        # accept-new, batchmode), and the script is piped via stdin so the
        # remote bash expands any ``~`` in the glob normally — shlex.quote here
        # would lose that and the listing would silently return nothing — as
        # bytes, so Windows text mode cannot translate ``\n`` → ``\r\n`` and
        # trip the remote bash with ``$'\r': command not found``.
        host, files, _total = self._list_remote_glob(src_glob)
        if not files:
            return 0, 0
        return len(files), self._pull_listed(host, files, dest_dir)



@dataclass(frozen=True)
class FetchVerification:
    """What :meth:`FetchedOutputVerifier.verify` established about a run.

    ``passed`` is the only thing that may permit remote cleanup. ``reason``
    names why it did not pass — ``"pipeline-failed"`` when the pipeline itself
    exited non-zero, ``"incomplete"`` when local artefacts are still missing
    after the retry — and ``missing`` lists what verification found absent.
    """

    passed: bool
    reason: str = ""
    missing: tuple[str, ...] = ()


class FetchedOutputVerifier:
    """End-of-run verification of one remote run's fetched output.

    Holds its collaborators explicitly — the host, the run record, the dataset
    cache whose ssh it borrows, and the stop flag Stop sets — rather than
    reaching into the runner that built it. It never removes the run's remote
    directory: :meth:`verify` returns a :class:`FetchVerification`, and the
    caller decides.
    """

    def __init__(self, host: RemoteHost, run: _RemoteRun | None, cache,
                 cancelled: threading.Event) -> None:
        self.host = host
        self._run = run
        self._cache = cache
        self._cancelled = cancelled

    def verify(self, ok: bool, *, log: Callable[[str], None]) -> FetchVerification:
        """Pull top-level artefacts, then check every well arrived.

        *ok* is the pipeline's own verdict. The sidecar, the error tracebacks
        and the client-side completeness record are brought home either way —
        a run that failed is exactly the one whose records are worth having —
        but a failed pipeline never passes.
        """
        run = self._run
        assert run is not None
        # Pull pipeline_info.json + any top-level CSVs we may have missed
        # (well CSVs are pulled by the Fetcher, but a top-level summary
        # written at end-of-run wouldn't be).
        # The fetched sidecar OVERWRITES the local one, and a remote output dir
        # starts empty — so its run_report block describes only the wells this
        # run processed (on an incremental run, only the uploaded split). Left
        # alone, that replaces a whole plate's completeness records with a
        # handful, and every skipped well loses the record that said whether its
        # empty output was a measurement or a gap. Capture before, merge after.
        #
        # The user-side blocks have the same problem and a simpler answer. A
        # remote output dir starts empty, so its sidecar never carries the
        # cell-gating thresholds, sample definitions, ratios or notes the user
        # set locally — and a batched run's thresholds are written *locally*, by
        # _run_local_auto_threshold, moments before this fetch overwrites them.
        # Capture before, restore after, for exactly the keys the pipeline's own
        # re-run path preserves.
        info_path = run.local_output_dir / "pipeline_info.json"
        prior_run_report = read_run_report_block(info_path)
        # The field-quality block has the same problem and a worse consequence.
        # Coverage records losing history is a loss of history; field quality
        # reduced to the wells one remote incremental run touched gives every
        # one of those wells a robust z computed against its own eight-well
        # median, and the shortfall accounting cannot catch it — each of those
        # wells is individually complete, so nothing is marked partial and the
        # scorer proceeds.
        prior_field_quality = read_field_quality_block(info_path)
        prior_user_blocks = read_user_blocks(info_path)
        for top in ("pipeline_info.json",):
            self._rsync_optional(
                f"{self.host.ssh_alias}:{run.remote_output_dir}/{top}",
                run.local_output_dir / top,
                log_fn=log,
            )
        if merge_fetched_run_report(info_path, prior_run_report):
            log("[remote] merged this run's completeness records into the "
                "existing pipeline_info run_report.\n")
        if merge_fetched_field_quality(info_path, prior_field_quality, incremental=run.incremental):
            log("[remote] merged this run's field-quality measurements into "
                "the existing pipeline_info field_quality.\n")
        restored = restore_user_blocks(info_path, prior_user_blocks)
        if restored:
            log(f"[remote] restored local pipeline_info block(s) the fetched "
                f"sidecar did not carry: {', '.join(restored)}\n")

        # Per-failure tracebacks. The pipeline writes them remotely and nothing
        # pulled them back, so on a remote run they stayed on the remote host —
        # and Validate & Repair, which reads out/errors/ to say WHY a well is
        # empty, found an empty directory every time. They are small text files
        # and they are usually the fastest answer to "why did this well fail".
        self._rsync_optional(
            f"{self.host.ssh_alias}:{run.remote_output_dir}/errors/",
            run.local_output_dir / "errors",
            log_fn=log,
            # The pipeline creates errors/ only when a well or FOV-group
            # actually fails, so a clean run has none and this note is the
            # expected outcome, not a problem. Saying so here is the difference
            # between a line that reads as bad news and one that reads as none.
            absent_note="The pipeline only creates errors/ when a well or "
                        "FOV-group fails, so a clean run has none.",
        )

        # The fetched pipeline_info.json reflects only the (partial) input the
        # remote saw — for an incremental run that's just the uploaded new-FOV
        # members, so its available_timepoints/available_fovs miss the rest of
        # the dataset and any dropdown built from them goes stale. Recompute both
        # from the *merged* local outputs (the <well>_out.zip members keep the
        # input stem), which hold the complete picture after the fetch.
        refresh_pipeline_info_available_from_outputs(run.local_output_dir, log_fn=log)

        # Completeness, client-side. Must run AFTER the refresh above: that call
        # rewrites available_timepoints/available_fovs from the merged local
        # outputs, and a check reading the sidecar before it would describe
        # fields the very next line repairs. Runs before the `ok` gate too — a
        # run that came back short is exactly the one whose record is worth
        # having, and the shortfall is what the record is for.
        run_client_side_completeness_check(
            run.local_input_dir, run.local_output_dir, log_fn=log)

        if not ok:
            log("[remote] pipeline returned non-zero — skipping cleanup, leaving remote state.\n")
            return FetchVerification(False, reason="pipeline-failed")

        # Verify each expected well's output is present locally.
        # v5.1.1: before declaring the manifest incomplete, give the
        # Fetcher one more pass at any well whose CSV didn't make it
        # back. A transient connection blip during a per-well rsync no
        # longer requires a full pipeline re-run — we re-pull the
        # missing files using the same retry-aware helpers.
        missing = self._verify_local_manifest(log=log)
        if missing:
            attempted = self._retry_missing_wells(missing, log=log)
            missing = self._verify_local_manifest(log=log)
            # Journalled from verify's own verdict, so "did this well arrive"
            # has one answer rather than two that can disagree.
            self._journal_retry_outcomes(attempted, missing)

        # Written *after* the retry, because the retry is what turns a failed
        # pull into a fetched well and the report has to describe the outcome
        # rather than the low-water mark. Run before it, this recorded a
        # rescued well as `not-fetched` permanently: the journal entry the
        # retry wrote was correct and the report rendered from the state three
        # lines earlier. The per-well journal entries are written as each
        # decision is made, so a run that dies before reaching here still has
        # them — this is only the rendering.
        self._reconcile_fetch(log=log)
        if missing:
            log(
                f"[remote] keeping remote files for retry — verification failed: "
                f"{len(missing)} file(s) missing.\n"
            )
            return FetchVerification(False, reason="incomplete", missing=tuple(missing))
        return FetchVerification(True)


    def _rsync_optional(
        self, src: str, dest: Path, *, log_fn: Callable[[str], None],
        absent_note: str = "",
    ) -> None:
        """Pull *src* if the remote has it; say so plainly if it does not.

        ``absent_note`` explains what the absence *means* for this particular
        source, because "not present" alone reads as a fault for a source whose
        absence is the normal, good case. It is deliberately not phrased as a
        verdict on the run: a non-zero rc means the pull did not happen, which
        covers both "there was nothing there" and "there was, and it could not
        be fetched", and this cannot tell those apart.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        result = _rsync_with_retry(
            [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
             "-a", src, to_rsync_path(dest)],
            log_fn=log_fn, timeout=60,
        )
        if result.returncode != 0:
            tail = f" {absent_note}" if absent_note else ""
            log_fn(
                f"[remote] note: {src} not pulled "
                f"(rc={result.returncode}).{tail}\n"
            )


    def _retry_missing_wells(
        self, missing_messages: list[str], *, log: Callable[[str], None],
    ) -> set[str]:
        """Re-pull the per-well outputs for wells whose CSV is missing.

        Closes the "transient drop ate one well's output" hole that
        existed in v5.0.0–v5.1.0: the in-stream Fetcher already gave up
        on a failed pull with a "will retry at end" comment that wasn't
        actually implemented. This is that retry.
        """
        run = self._run
        if run is None:
            return set()
        # Extract well IDs from the manifest messages. _verify_local_manifest
        # emits several forms: "missing CSV for A01", "empty CSV for A01",
        # "missing A01_out.zip locally", "missing A01/ output folder locally".
        # A trailing \b fails on the zip form ("A01_out" — '_' is a word char,
        # so no boundary after A01), which silently dropped zip/folder-only
        # misses from the retry. Anchor on a leading boundary and forbid only a
        # trailing digit, so all four forms parse without over-matching A012…
        wells: set[str] = set()
        for m in missing_messages:
            match = _WELL_IN_MESSAGE.search(m)
            if match:
                wells.add(match.group(1))
        if not wells:
            return set()

        log(f"[remote] end-of-run retry: re-pulling {len(wells)} well(s)…\n")
        host = self.host
        # The Fetcher's per-well rsync got the v6.0.1 wall-clock fix; this
        # backstop path didn't, and was the remaining source of the
        # "7/7 fetched, then endless rsync retry" hang: it re-pulled with the
        # default 5-min subprocess wall-clock (which kills a large, still-
        # progressing {well}_out.zip mid-transfer → --partial resume → killed
        # again → loop) and with no stop event (so Stop couldn't break it).
        # rsync --timeout=120 (I/O-idle) + ssh ConnectTimeout bound real stalls,
        # so timeout=None is safe; stop_event makes Stop interrupt an in-flight
        # re-pull.
        # FOLDER must be included: _verify_local_manifest requires the
        # {well}_out.zip for *any* FULL run (including FOLDER input), so the
        # retry re-pull has to be able to fetch it too — otherwise a
        # FOLDER+FULL run whose out-zip failed to land loops verify→retry
        # forever and remote cleanup never happens (audit G2).
        want_zip = (
            run.output_filter is OutputFilter.FULL
            and run.local_input_mode in (
                LocalInputMode.ZIP, LocalInputMode.FLAT,
                LocalInputMode.MIXED, LocalInputMode.FOLDER)
        )

        def _pull(remote_src: str) -> None:
            _rsync_with_retry(
                [resolve_rsync_binary(), *_rsync_ssh_transport(host),
                 "-a", "--partial", "--timeout=120",
                 f"{host.ssh_alias}:{remote_src}",
                 to_rsync_path(run.local_output_dir).rstrip("/") + "/"],
                log_fn=log, timeout=None, stop_event=self._cancelled,
            )

        # This backstop reports in the SAME per-well lines the in-stream fetch
        # uses, rather than a form of its own. Those two lines are what the
        # progress tracker pairs to turn a well green, so a well rescued here
        # reaches the same state by the same route — no second vocabulary to
        # keep in step with the first, and nothing for the tracker to learn.
        # It logged nothing at all before, which is why a rescued well stayed
        # painted "computed, awaiting fetch" with its data already on disk.
        def _report_zip(well: str, elapsed: float) -> None:
            """The in-stream fetcher's zip line, for a zip this pass secured.

            Emitted whenever the well's zip is complete locally by the time this
            pass is done with it — including the case where it was already
            there. That is not padding: the pair only completes when *both*
            halves are reported, and a well reaches this backstop having
            registered neither, because the in-stream fetcher returns on a
            failed zip pull before it ever gets to the CSVs.
            """
            out_zip = run.local_output_dir / f"{well}_out.zip"
            try:
                mb = out_zip.stat().st_size / (1024 * 1024)
            except OSError:
                return
            log(f"[remote] ↓ {well}: {mb:.1f} MB zip in {_fmt_secs(elapsed)} "
                f"(end-of-run retry)\n")

        def _report_csvs(well: str, elapsed: float) -> None:
            found = _glob_csvs(run.local_output_dir, f"*_{well}")
            if found:
                log(f"[remote] ↓ {well}: {len(found)} CSV file(s) in "
                    f"{_fmt_secs(elapsed)} (end-of-run retry)\n")

        for well in sorted(wells):
            well_t0 = time.monotonic()
            if self._cancelled.is_set():
                log("[remote] end-of-run retry cancelled by Stop.\n")
                return wells
            # Re-pull the per-well CSV(s) only if they're actually missing or
            # empty locally — a present CSV needs no refetch.
            local_csvs = _glob_csvs(run.local_output_dir, f"*_{well}")
            csv_ok = bool(local_csvs) and all(p.stat().st_size > 0 for p in local_csvs)
            if not csv_ok:
                files = list_remote_csvs(host, run.remote_output_dir, well, log,
                                         compress=well not in run.fetched_wells)
                if files is None:
                    continue
                for f in files:
                    _pull(f)
                retire_superseded_csvs(files, run.local_output_dir)
            _report_csvs(well, time.monotonic() - well_t0)
            # Re-pull {well}_out.zip ONLY when the user's output mode wants it
            # AND it isn't already present locally. Pulling a multi-hundred-MB
            # zip just because a CSV was missing is what dragged this backstop
            # into the wall-clock retry loop in the first place.
            if want_zip:
                zip_t0 = time.monotonic()
                out_zip = run.local_output_dir / f"{well}_out.zip"
                # A well the run undertook to reprocess is the exception to
                # "present means nothing to pull": the file sitting there is
                # the one the new output was supposed to replace, so its mere
                # presence is the symptom rather than the all-clear. Re-pull it
                # when it still carries the pre-run size and timestamp — that
                # is the difference between reporting the loss and repairing it.
                stale = (
                    (run.force or well in run.force_wells)
                    and out_zip.exists()
                    and not _artifact_changed(run, out_zip)
                )
                if not out_zip.exists() or out_zip.stat().st_size == 0 or stale:
                    _pull(f"{run.remote_output_dir}/{well}_out.zip")
                _report_zip(well, time.monotonic() - zip_t0)
            # The record of which settings produced it, as the in-stream fetch
            # brings (`Fetcher._pull_settings_marker`): without it a well
            # recovered here reads as grandfathered, and a later run with
            # different settings would keep its numbers as though they matched.
            pull_small_file(
                host, f"{run.remote_output_dir}/{SETTINGS_DIRNAME}/{well}.json",
                run.local_output_dir / SETTINGS_DIRNAME / f"{well}.json",
                stop_event=self._cancelled, what=f"the settings marker for {well}")
        # Whether any of this actually recovered a well is not decided here —
        # see ``_journal_retry_outcomes``, which reads verify's answer.
        return wells


    def _journal_retry_outcomes(
        self, attempted: set[str], still_missing: list[str],
    ) -> None:
        """Record what the end-of-run retry recovered, using verify's answer.

        "Did this well arrive?" has exactly one answer in this class, and it is
        ``_verify_local_manifest``'s: for a FULL run that means the CSV **and**
        the ``{well}_out.zip``, not the CSV alone. Deciding it here from the
        CSV would have the journal call a well fetched while verify was still
        holding the run open for the missing archive — two answers to one
        question, disagreeing in the direction that reports a loss as a
        recovery.

        So this is handed the list verify produced and reads the wells out of
        it, rather than going back to the files itself.
        """
        from services import run_journal

        run = self._run
        if run is None or not attempted:
            return
        unresolved = {m.group(1) for m in
                      (_WELL_IN_MESSAGE.search(t) for t in still_missing) if m}
        for well in sorted(attempted):
            recovered = well not in unresolved
            with swallow(f"journalling the retry result for {well}"):
                run_journal.record_well(
                    run.local_output_dir, well,
                    "fetched" if recovered else "not-fetched",
                    reason=("recovered by the end-of-run retry" if recovered
                            else "the end-of-run retry could not recover it"))


    def _reconcile_fetch(self, *, log: Callable[[str], None]) -> str:
        """Classify every well the remote holds output for, and record it.

        Three outcomes are possible for a well by the time the fetch is over:
        it was transferred, it was skipped because its local copy already
        looked complete, or the run never saw a completion marker for it. The
        first is the normal case; the other two are the ones a user has to be
        able to ask about afterwards, and until now the answer existed only in
        the run log as it streamed. A report written beside the outputs
        outlives the log drawer.

        The classification itself is **read back out of the run journal**,
        which recorded each well at the moment it was decided, rather than
        re-derived here from the in-memory run. Two things follow. A run that
        never reaches this method — killed, or the app closed — has already
        recorded everything it got to, where before it recorded nothing. And
        ``remote_fetch_report.txt`` becomes a rendering of that record rather
        than a second one: one classification, two ways to read it.
        """
        run = self._run
        if run is None:
            return ""
        remote_wells = sorted(self._list_remote_completed_wells())
        lines = [
            f"run_id: {run.run_id}",
            f"force: {run.force}",
            f"force_wells: {', '.join(sorted(run.force_wells)) or '(none)'}",
            f"incremental: {run.incremental}",
            f"wells with output on the remote: {len(remote_wells)}",
            "",
        ]
        counts, well_lines = _classify_fetched_wells(
            remote_wells, run.local_output_dir,
            announced=run.expected_wells, skipped=run.skipped_wells)
        lines.extend(well_lines)
        # Words for the states, and *only* the words. Naming a fixed four here
        # once dropped `failed` — which this run's own Fetcher records when a
        # well arrives and cannot be materialised — so a materialise failure
        # vanished from a tally printed directly under "wells with output on
        # the remote: N" and the numbers stopped adding up to N.
        _PHRASES = {
            "fetched": "fetched",
            "skipped": "skipped (already complete locally)",
            "failed": "failed",
            "not-fetched": "not fetched",
            "analysed": "analysed but not transferred",
            "uploaded": "uploaded only",
            "never-announced": "never announced completion",
        }
        # The order comes from ``WELL_STATES``, not from ``_PHRASES``, and
        # anything ``counts`` holds beyond it is appended under its own name.
        # Iterating the phrase table was the same bug in a second shape: a
        # state recorded by a writer but never spelled here appeared in the
        # per-well lines above and in nothing below them, so the parts silently
        # stopped summing to the whole again.
        ordered = [*run_journal.WELL_STATES, "never-announced"]
        ordered += [k for k in counts if k not in ordered]
        parts = [f"{counts[k]} {_PHRASES.get(k, k)}"
                 for k in ordered if counts.get(k)]
        summary = (
            f"[remote] fetch reconciliation: "
            f"{', '.join(parts) if parts else 'no wells'}"
            f" (of {len(remote_wells)} with output on the remote).\n"
        )
        log(summary)
        report = run.local_output_dir / "remote_fetch_report.txt"
        try:
            report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            swallowed(f"could not write {report}")
            log(f"[remote] could not write the fetch reconciliation: {exc}\n")
        else:
            log(f"[remote] fetch reconciliation written to {report.name}.\n")
        return summary


    def _verify_local_manifest(
        self, *, log: Callable[[str], None]
    ) -> list[str]:
        """Check that every well with output on the remote has its
        full local artifact set.

        Returns the list of missing paths (empty list = all good).

        v5.2.25: also discovers wells from the remote output dir's
        ``*_out.zip`` listing — not just the ``Well X — done.`` stdout
        markers. This catches the cache-hit case where the pipeline
        skipped already-complete wells (no ``done`` marker emitted)
        but their outputs still need to land locally.

        v5.2.29: verify the OUT_ZIP / extracted folder too, not just
        the CSV. Pre-v5.2.29 a resumed run that had pulled the CSV
        but failed mid-zip-rsync would silently leave the well
        flagged "complete" — the user would have the measurements
        but never the QC zip the GUI's Review tab needs.

        The expected local artifacts depend on the user's output
        filter and input mode:
        - Always: ``{csv_prefix}_{well}.csv``
        - FULL filter + ZIP/FLAT mode: also ``{well}_out.zip``
        - FULL filter + FOLDER mode: also ``{well}/`` directory
        - CSV_ONLY filter: just the CSV (zip explicitly discarded)
        """
        run = self._run
        assert run is not None
        all_wells = set(run.expected_wells)
        # Backstop: ask the remote what wells it has ``{well}_out.zip``
        # for. Pipeline-skipped wells aren't in expected_wells (no
        # stdout marker fires for them) but their outputs are still
        # what the user wants locally.
        for w in self._list_remote_completed_wells():
            all_wells.add(w)
        missing: list[str] = []
        # Output is always materialised as <well>_out.zip now (compression is
        # mandatory and the Fetcher keeps the zip for every input mode), so the
        # FULL filter expects that zip regardless of input mode. (Pre-always-zip
        # this branched on FOLDER mode and expected a <well>/ dir, which no
        # longer exists — that mismatch failed verify and skipped cleanup.)
        want_zip_artifact = run.output_filter is OutputFilter.FULL
        # Wells this run undertook to rewrite. A forced run promised all of
        # them; otherwise only the wells that announced completion produced
        # anything new, and a well legitimately left alone must not be failed.
        must_be_replaced = (
            set(all_wells) if run.force
            else (set(run.force_wells) | set(run.expected_wells))
        )
        for well in sorted(all_wells):
            csvs = _glob_csvs(run.local_output_dir, f"*_{well}")
            if not csvs:
                missing.append(f"missing CSV for {well}")
                continue
            if any(p.stat().st_size == 0 for p in csvs):
                missing.append(f"empty CSV for {well}")
                continue
            # v5.2.29: also verify the QC artifact for the user's
            # output mode. A previous run could have pulled the CSV
            # but failed mid-zip-rsync; without this check the well
            # is silently flagged complete and the zip stays remote.
            if want_zip_artifact:
                out_zip = run.local_output_dir / f"{well}_out.zip"
                if not out_zip.exists() or out_zip.stat().st_size == 0:
                    missing.append(f"missing {well}_out.zip locally")
                elif well in must_be_replaced and not _artifact_changed(
                        run, out_zip):
                    # Existence is not the question for a well the run was told
                    # to reprocess: the file that is present may be the one the
                    # new output was supposed to replace. Byte-size and mtime
                    # both unchanged means nothing was written here, and a run
                    # that reports success over that is the failure mode this
                    # check exists for — hours of remote compute discarded with
                    # nothing on screen saying so.
                    missing.append(
                        f"{well}_out.zip was not replaced (same size and "
                        f"timestamp as before the run)")
        if missing:
            for m in missing[:10]:
                log(f"[remote] verify: {m}\n")
            if len(missing) > 10:
                log(f"[remote] verify: … and {len(missing) - 10} more\n")
        else:
            log(
                f"[remote] verify: all {len(all_wells)} wells "
                f"present locally.\n"
            )
        return missing


    def _list_remote_completed_wells(self) -> set[str]:
        """ssh into the remote and list every ``{well}_out.zip`` in
        the run's output dir; return the set of well labels. Used by
        ``_verify_local_manifest`` to discover wells the pipeline
        skipped (no ``Well X — done.`` marker fires when the
        ``output_exists_for_well`` early-exit kicks in).

        Uses ``_ssh_script`` so ``~/.allwell-staging`` expands
        properly on the remote (the v5.2.16 lesson). Any error
        (ssh missing, network drop, transient timeout) degrades
        gracefully to an empty set — verify then falls back to the
        ``expected_wells``-only check just like pre-v5.2.25."""
        run = self._run
        if run is None:
            return set()
        return self._list_completed_wells_in(run.remote_output_dir)


    def _list_completed_wells_in(self, remote_dir: str) -> set[str]:
        """The same listing, for any remote directory rather than this run's.

        Split out so the address the run journal recorded can be *checked*
        before it is used: a directory a previous run wrote to is not this
        run's, and asking whether it still holds output is the whole reason
        re-attaching to it is safe. One implementation, so "what counts as a
        completed well" cannot come to mean two things a few lines apart.
        """
        try:
            listing = self._cache._ssh_script(
                f"ls -1 {remote_dir}/*_out.zip 2>/dev/null || true",
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return set()
        wells: set[str] = set()
        if listing is None or not listing.stdout:
            return wells
        for line in listing.stdout.splitlines():
            stem = Path(line.strip()).name.removesuffix(".zip")
            if stem.endswith("_out"):
                well = stem[:-len("_out")]
                label = _canonical_well_from_path(well)
                if label is not None:
                    wells.add(label)
        return wells
