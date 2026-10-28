"""Finishing a downloaded well off the Fetcher's transfer thread.

The Fetcher pulls one well at a time. For an incremental run each well's download is followed by a merge into the
local output — the partial CSV into the well's ``.csv.gz``, the partial ``<well>_out.zip`` into the local one — and
only after that is the remote copy deleted. Done on the transfer thread, one large well's merge held every later
well's download, and every later well's remote copy, behind it: the run sat at 99% on its first well for half an
hour while the remote filled. :class:`Placer` takes the merge and everything after it (marking the well fetched,
deleting its remote files) onto a thread of its own, so transfers keep going while it works.

The queue is bounded (:data:`MAX_PENDING`): each waiting well holds its downloaded zip in the local transit
directory, so a merge that is slower than the link makes the transfer thread wait rather than fill the local disk.
A remote copy is still deleted only after its well is in place locally — the order that makes a failed merge
recoverable from the remote.
"""

from __future__ import annotations

import os
import queue
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

#: Downloaded wells allowed to wait for their merge. Each holds about one well's output in the transit directory.
MAX_PENDING = 2


class Placer:
    """One thread running the place-into-output step of each incremental well, in arrival order."""

    def __init__(self, log: Callable[[str], None]) -> None:
        self._log = log
        self._q: queue.Queue = queue.Queue(maxsize=MAX_PENDING)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def submit(self, task: Callable[[], None]) -> None:
        """Queue *task*; blocks while :data:`MAX_PENDING` wells are already waiting."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="allwell-placer")
            self._thread.start()
        while not self._stop.is_set():
            try:
                self._q.put(task, timeout=1.0)
                return
            except queue.Full:
                continue

    def drain(self, timeout: float = 60.0, heartbeat: Callable[[], str] | None = None) -> None:
        """Wait for every queued well to be placed. Returns at once when nothing was ever queued."""
        if self._thread is None:
            return
        self._q.put(None)
        while self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive() and heartbeat is not None:
                self._log(heartbeat())

    def cancel(self) -> None:
        self._stop.set()
        if self._thread is not None:
            try:
                self._q.put_nowait(None)
            except queue.Full:
                pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                task = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            if task is None:
                return
            try:
                task()
            except Exception as exc:
                # One well's failure must not stop the wells behind it; the well is then not marked fetched, its
                # remote copy is kept, and the verify step retries it.
                import logging as _logging
                _logging.getLogger(__name__).exception("placer: error placing a fetched well; continuing")
                self._log(f"[remote] ↓ placing a fetched well failed: {exc}; continuing\n")


def merge_fetched_csv_dir(fetcher, tmp_csv_dir: Path) -> list[str]:
    """Merge the partial CSVs pulled into *tmp_csv_dir* into the local output; return the names that did not merge.

    The directory is removed once every partial in it has merged. When one has not — the local CSV would not read
    or write, an EIO on the output volume — it is **kept**, and the caller must not mark the well fetched or delete
    its remote copy: the partial and the remote copy are then the only places this run's new rows exist (tracker
    row A6). The directory is the one ``_new_transit_dir`` made for this one well's CSVs and holds nothing of the
    user's.
    """
    failed: list[str] = []
    try:
        if any(Path(tmp_csv_dir).iterdir()):
            fetcher._set_activity(tmp_csv_dir.name, "merging its CSV into the local one")
            failed = list(fetcher._merge_fetched_csvs(tmp_csv_dir) or ())
    except OSError as exc:
        failed = [f"{tmp_csv_dir.name} ({exc})"]
    if not failed:
        shutil.rmtree(tmp_csv_dir, ignore_errors=True)
    return failed


def discard_transit_dir(tmp_csv_dir: Path) -> None:
    """Remove the directory ``_new_transit_dir`` made for one well's partial CSVs, unmerged. Only for a well whose
    CSVs did not all arrive, whose remote copy is therefore kept whole; it holds nothing of the user's."""
    shutil.rmtree(tmp_csv_dir, ignore_errors=True)


def place_whole(src: Path, target: Path) -> None:
    """Move a downloaded file into place unchanged — the case where there is nothing local to merge it with.

    A rename when the transit directory shares the target's volume (the normal case, ``_transit_dir``); otherwise
    copied to a hidden sibling and renamed, so a half-copied file is never visible under the real name.
    """
    try:
        os.replace(src, target)
        return
    except OSError:
        pass
    part = target.with_name(f".{target.name}.part")
    shutil.copyfile(src, part)
    os.replace(part, target)
    Path(src).unlink(missing_ok=True)


def local_output_mb(run, well: str) -> float | None:
    """How much of *well*'s fetched output is in place locally, in MB, or ``None`` when none of it is.

    For the "zip" half of the end-of-run retry's report, which is what paints a well downloaded on the plate. The
    output is the zip itself in ZIP/FLAT mode, its extraction ``<well>_out/`` in FOLDER mode, and nothing at all
    under CSV-only — and reporting only the first left a well rescued in either of the others uncoloured for good.
    """
    from services.remote_run_state import OutputFilter
    from util.disk_space import directory_bytes

    out_zip, folder = Path(run.local_output_dir) / f"{well}_out.zip", Path(run.local_output_dir) / f"{well}_out"
    try:
        if out_zip.is_file():
            return out_zip.stat().st_size / (1024 * 1024)
    except OSError:
        return None
    if folder.is_dir():
        return directory_bytes(folder) / (1024 * 1024)
    if getattr(run, "output_filter", None) == OutputFilter.CSV_ONLY:
        return 0.0
    return None


def set_aside_unreadable_zip(target: Path, well: str, *, log: Callable[[str], None]) -> str:
    """Move an unreadable local ``<well>_out.zip`` aside before an incremental download takes its place; return
    the shortfall that verification then reports for the well.

    An incremental fetch brings only the new fields' images, so placing it over the local archive drops every
    earlier field's — and because the well's CSV still lists those fields as done (``pipeline.incremental_keys``),
    no incremental run will ever produce them again. The archive is kept as ``.<name>.unreadable`` (never deleted),
    the loss is written to ``errors/`` beside the run's own failure records as well as logged, and verification
    fails the run rather than report a plate that has lost images as finished (tracker row B6). The CSV twin of
    this is ``pipeline.io._merge_onto_cut_csv``.
    """
    aside = target.with_name(f".{target.name}.unreadable")
    message = (f"the local {target.name} could not be read, so this incremental download (the new fields only) "
               f"replaced it and the earlier fields' images are no longer in it; the unreadable archive is kept as "
               f"{aside.name}. Re-run well {well} with --force (Force in Analyze) to restore them.")
    try:
        os.replace(target, aside)
    except OSError as exc:
        message += f" (Setting it aside failed: {exc}.)"
    try:
        errors = target.parent / "errors"
        errors.mkdir(parents=True, exist_ok=True)
        (errors / f"{well}_local_zip_unreadable.txt").write_text(f"Well: {well}\n\n{message}\n", encoding="utf-8")
    except OSError as exc:
        log(f"[remote] WARNING {well}: could not record the loss in errors/ ({exc})\n")
    log(f"[remote] WARNING {well}: {message}\n")
    return message
