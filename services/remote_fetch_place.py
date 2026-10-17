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


def merge_fetched_csv_dir(fetcher, tmp_csv_dir: Path) -> None:
    """Merge the partial CSVs pulled into *tmp_csv_dir* into the local output, then remove the directory.

    The directory is the one ``_new_transit_dir`` made for this one well's CSVs and holds nothing of the user's.
    """
    try:
        if any(Path(tmp_csv_dir).iterdir()):
            fetcher._set_activity(tmp_csv_dir.name, "merging its CSV into the local one")
            fetcher._merge_fetched_csvs(tmp_csv_dir)
    finally:
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
