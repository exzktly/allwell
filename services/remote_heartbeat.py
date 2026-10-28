"""The run heartbeat: the file a remote run's app touches to say it is still there.

Split out of ``services/remote_process.py``, which is line-budgeted and imports
:class:`_UploadHeartbeat` from here under its old name, so no caller moved. The
remote side that reads the file — the single-shot job's upload wait, the batch
driver's wait between batches and its watchdog beside each batch — is
``services/remote_wait_snippets.py``.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from services.remote_wait_snippets import UPLOAD_HEARTBEAT_S
from util.aw_safe import swallowed

if TYPE_CHECKING:
    from services.remote_upload import DatasetCache


class _UploadHeartbeat:
    """Touches the remote heartbeat for as long as this app owns the run.

    A daemon thread rather than a hook inside the transfer: rsync can be busy
    with one large archive for minutes without returning to any per-file
    callback, and a heartbeat that only advances between files would read as
    dead exactly during the slowest part of the upload.

    Stopping is the point. Whatever ends the run — success, failure, the Stop
    button, the process being killed — takes the beat with it, and a job still
    waiting for its input frees its node about a minute later instead of
    holding it for the full deadline.

    It beats for the whole run, not only the upload, because it is also what
    :meth:`RemoteRunner._sweep_orphaned_allocation` reads to decide whether an
    allocation still has an owner. ``run_id`` is deterministic in the plate, so
    two sessions on the same plate meet at the same sidecar; a beat that
    stopped when the upload finished would make an hour-long compute look
    abandoned and get itself cancelled by the second session.
    """

    def __init__(self, cache: "DatasetCache", path: str,
                 interval_s: float = UPLOAD_HEARTBEAT_S) -> None:
        self._cache = cache
        self._path = path
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        """One touch, now. Called once before the job starts waiting, so the
        first thing it looks at is a heartbeat that already exists.

        Bounded by the beat interval rather than the default: a touch that has
        not landed by the time the next one is due is not going to help, and
        letting them queue up would leave the thread minutes behind while the
        remote reads the silence as an app that died.
        """
        self._cache._ssh_run(["touch", self._path], check=False,
                             timeout_s=self._interval)

    def start(self) -> None:
        """Begin beating. Paired with :meth:`stop` — see the class docstring."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="aw-upload-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop beating, and wait for the thread to notice.

        The whole mechanism is "a beat that stops means the app is gone", so a
        beat that outlives its run is not a leak of one thread — it tells the
        remote a dead run is alive, holds the node for the full deadline, and
        keeps the orphan sweep from ever sweeping.
        """
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def __enter__(self) -> "_UploadHeartbeat":
        self.beat()
        self.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.beat()
            except Exception:
                # A missed beat is not fatal on its own — the job tolerates
                # `UPLOAD_HEARTBEAT_STALE_S` of silence — and an ssh blip must
                # not take down an upload that is otherwise fine.
                swallowed("upload heartbeat")

    def __exit__(self, *_exc) -> None:
        self.stop()
