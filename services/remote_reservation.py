"""Asking a scheduler for the node before the plate is staged.

On a scheduler-backed host the longest wait of a remote run is often the queue,
and the app used to start it last: the run's job could not be composed until
staging had decided everything the job runs — the run directory (keyed on the
staged archives), the pipeline's arguments, and for a batched run the batch
count its ``--time`` is scaled by. So planning, channel filtering, the
incremental split and the archive rewrites all ran on this machine, end to end,
before the queue wait even began.

A :class:`Reservation` turns that around. As soon as staging knows how many
wells the run can hold — before it plans a single archive — the app queues a job
whose command is not yet written (:func:`services.remote_commands.
_compose_reservation_cmd`). The job, once granted, waits for a *command file*
in its own directory, beating-heart guarded exactly as the upload wait is. When
the run is ready, :meth:`Reservation.hand_over` writes that file — the
single-shot pipeline, or the batch driver — and links the run directory's
pidfile, scheduler sidecar and ``srun`` error file to the reservation's, so
every later step (Stop's ``kill``, the ``scancel`` backstop, the next session's
claim on the run directory, the orphan sweep) finds the job where it always
has. The reservation's heartbeat becomes a link to the run's, so whoever asks
whether the job still has an owner gets the run's answer.

Nothing is lost when the reservation cannot be used. A host with no launcher
never makes one; a reservation the scheduler or the network ended before the
run was ready is reported and the run asks for a node the ordinary way; a run
that turns out to need no node (a cache hit, nothing left to upload, a refusal,
a Stop) releases it. The cost of asking early is that, on those runs, a job was
queued and then cancelled.

The walltime is fixed when the job is queued, so a batched run asks for its
upper bound: every well the plate holds, sliced by the batch size, plus the
nanowell re-anchoring pass (:func:`batches_upper_bound`). Planning only ever
removes wells from a run, so the real count is never larger. The time a granted
node spends waiting for the hand-over is spent from that walltime, as the wait
for an upload already is.
"""

from __future__ import annotations

import base64
import gzip
import math
import os
import posixpath
import queue
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from services.remote_commands import _compose_reservation_cmd, pack_for_login_shell
from services.remote_config import RemoteHost, scale_launcher_time
from services.remote_heartbeat import _UploadHeartbeat
from services.remote_srun_retry import queue_wait_seconds
from services.remote_transport import _remote_quote
from services.remote_wait_snippets import UPLOAD_HEARTBEAT_STALE_S
from services.subprocess_kill import own_process_group_kwargs, signal_subprocess
from services.subprocess_text import TEXT_UTF8
from util.aw_safe import swallow

#: Where reservations live, under the host's staging directory.
RESERVATIONS_DIRNAME = "reservations"
#: The file a waiting job sources once the app has written it.
COMMAND_FILE = "command.sh"
#: A reservation directory whose heartbeat has been silent this long is removed by the next request. The job
#: behind it gave its node back two minutes after its app went quiet; what is left is a few small files.
STALE_RESERVATION_MIN = 2 * 24 * 60
#: The sidecars of a run's pidfile that the reservation writes and the run directory links to.
PIDFILE_SIDECARS = ("", ".slurm", ".srun-err")


@dataclass(frozen=True)
class ReservationPaths:
    """The files one reservation owns, all in one directory of its own."""

    dir: str
    pidfile: str
    heartbeat: str
    command: str


def reservation_paths(host: RemoteHost, token: str) -> ReservationPaths:
    """``{remote_staging_dir}/reservations/<token>/…``."""
    root = f"{host.remote_staging_dir}/{RESERVATIONS_DIRNAME}/{token}"
    return ReservationPaths(dir=root, pidfile=f"{root}/run.pid", heartbeat=f"{root}/heartbeat",
                            command=f"{root}/{COMMAND_FILE}")


def batches_upper_bound(n_wells: int, batch_size: int, *, nanowell: bool) -> int:
    """The most batches a run of *n_wells* can be cut into: ``0`` for a single-shot run (*batch_size* ``0``).

    An upper bound because planning, the local-completion filter and the empty-well blanks only ever take wells
    out of the batches. A nanowell run adds its end-of-run re-anchoring pass, which the driver runs as one more.
    """
    if batch_size <= 0:
        return 0
    return max(1, math.ceil(max(0, n_wells) / batch_size)) + (1 if nanowell else 0)


def _stale_reservations_script(root: str) -> str:
    """Shell that removes the reservation directories under *root* nobody has beaten for
    :data:`STALE_RESERVATION_MIN` minutes. ``find -L`` reads through a handed-over reservation's heartbeat link to
    the run's own, so a run still going — however long — keeps its reservation's files."""
    root_q = _remote_quote(root)
    stale = STALE_RESERVATION_MIN
    return (
        f'for _aw_d in {root_q}/*/; do [ -d "$_aw_d" ] || continue; '
        f'_aw_hb="${{_aw_d}}heartbeat"; '
        f'if [ -e "$_aw_hb" ]; then [ -n "$(find -L "$_aw_hb" -mmin +{stale} 2>/dev/null)" ] || continue; '
        f'else [ -n "$(find "$_aw_d" -maxdepth 0 -mmin +{stale} 2>/dev/null)" ] || continue; fi; '
        f'rm -rf "$_aw_d"; done; true'
    )


def _write_command_script(paths: ReservationPaths, body: str, run_pidfile: str, run_heartbeat: str) -> str:
    """Shell that hands a waiting reservation its run.

    The run directory's pidfile and sidecars are linked to the reservation's first, so the job's id is where every
    later step looks before the job can start the run. Then the command file is written beside its final name and
    renamed into place, so the job never sources half of it. Last, the reservation's heartbeat becomes a link to the
    run's, which the run beats from here on.
    """
    payload = base64.b64encode(gzip.compress(body.encode("utf-8"), mtime=0)).decode("ascii")
    tmp = f"{paths.command}.tmp"
    links = "".join(f"ln -sfn {_remote_quote(paths.pidfile + s)} {_remote_quote(run_pidfile + s)}; "
                    for s in PIDFILE_SIDECARS)
    return (
        f"set -e; mkdir -p {_remote_quote(posixpath.dirname(run_pidfile))}; "
        f"{links}"
        f"printf %s {payload} | base64 -d | gzip -dc > {_remote_quote(tmp)}; "
        f"mv -f {_remote_quote(tmp)} {_remote_quote(paths.command)}; "
        f"ln -sfn {_remote_quote(run_heartbeat)} {_remote_quote(paths.heartbeat)}"
    )


def _release_script(paths: ReservationPaths) -> str:
    """Shell that ends a reservation nobody took: ``srun`` (or the login shell) killed, the job cancelled if it was
    granted, and its directory removed. The job id is the reservation's own — its directory has a random name — so it
    is cancelled without the ownership checks a run directory's sweep needs."""
    pid_q, slurm_q = _remote_quote(paths.pidfile), _remote_quote(f"{paths.pidfile}.slurm")
    return (
        f"kill -TERM $(cat {pid_q} 2>/dev/null) 2>/dev/null; "
        f'_aw_j=$(cat {slurm_q} 2>/dev/null); '
        f'if [ -n "$_aw_j" ] && command -v scancel >/dev/null 2>&1; then scancel "$_aw_j" 2>/dev/null; fi; '
        f"rm -rf {_remote_quote(paths.dir)}; true"
    )


class _LineQueue:
    """Iterates the lines put on a queue until ``None``, so the run's reader can treat it as a file."""

    def __init__(self, q: queue.Queue[str | None]) -> None:
        self._q = q

    def __iter__(self) -> _LineQueue:
        return self

    def __next__(self) -> str:
        item = self._q.get()
        if item is None:
            raise StopIteration
        return item


class ReservedPopen:
    """The reservation's ssh, as the ``Popen`` a launch would have returned.

    The reservation has been reading the ssh's output since it was queued — into the run log while there was no
    run — and goes on reading it here, so the run's own reader gets every line from the hand-over on.
    """

    def __init__(self, popen: subprocess.Popen, lines: queue.Queue[str | None], pump: threading.Thread) -> None:
        self._popen = popen
        self._pump = pump
        self.stdout = _LineQueue(lines)
        self.args = getattr(popen, "args", None)

    @property
    def pid(self) -> int:
        return self._popen.pid

    @property
    def returncode(self) -> int | None:
        return self._popen.returncode

    def poll(self) -> int | None:
        return self._popen.poll()

    def wait(self, timeout: float | None = None) -> int:
        rc = self._popen.wait(timeout=timeout)
        # Every line the ssh wrote is on the queue before the caller drains it.
        self._pump.join(timeout=5)
        return rc

    def terminate(self) -> None:
        self._popen.terminate()

    def kill(self) -> None:
        self._popen.kill()

    def send_signal(self, sig: int) -> None:
        self._popen.send_signal(sig)


class Reservation:
    """A job queued on the host before the run it will run has been decided. See the module docstring.

    *cache* is the run's ``DatasetCache``; its ``_ssh_run`` makes the short control calls and its heartbeat keeps
    the reservation owned. Lines the job prints before the hand-over — the scheduler's queue messages, the
    ``allocation granted`` line — go to *log_fn*, which is the run log; a set *stop_event* releases it.
    """

    def __init__(self, cache: Any, host: RemoteHost, paths: ReservationPaths, launcher: str, *,
                 log_fn: Callable[[str], None], stop_event: threading.Event | None = None) -> None:
        self._cache = cache
        self._host = host
        self.paths = paths
        self.launcher = launcher
        self._log = log_fn
        self._stop = stop_event
        self._lock = threading.Lock()
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._taken = False
        self._released = False
        self._popen: subprocess.Popen | None = None
        self._pump: threading.Thread | None = None
        self._heartbeat: _UploadHeartbeat | None = None
        # The request line has been logged, so the pane is counting a wait that a release must end.
        self._announced = False

    # ── queueing ─────────────────────────────────────────────────────────
    @classmethod
    def request(cls, cache: Any, host: RemoteHost, *, n_wells: int, batch_size: int, nanowell: bool,
                log_fn: Callable[[str], None], stop_event: threading.Event | None = None) -> Reservation | None:
        """Queue a job for a run of up to *n_wells* wells, or ``None`` when the host has no scheduler to queue on.

        Any failure to queue it is logged and answered with ``None``: the run then asks for its node the ordinary
        way once it is ready, which is what it did before reservations existed.
        """
        if not host.compute_launcher.strip():
            return None
        launcher, note = scale_launcher_time(
            host.launch_command, batches_upper_bound(n_wells, batch_size, nanowell=nanowell))
        paths = reservation_paths(host, uuid.uuid4().hex[:16])
        reservation = cls(cache, host, paths, launcher, log_fn=log_fn, stop_event=stop_event)
        try:
            reservation._start(note)
        except Exception as exc:
            log_fn(f"[remote] scheduler: could not ask for the node early ({exc}); it is asked for once the run "
                   "is ready.\n")
            reservation.release("it could not be queued")
            return None
        return reservation

    def _start(self, note: str) -> None:
        root = posixpath.dirname(self.paths.dir)
        swept = self._cache._ssh_run(["sh", "-c", _stale_reservations_script(root)], check=False)
        if swept.returncode != 0:
            # Only housekeeping: what is left is a few small files under the staging directory.
            self._log(f"[remote] scheduler: could not clear old early requests under {root} "
                      f"(exit {swept.returncode}); continuing.\n")
        self._cache._ssh_run(["mkdir", "-p", self.paths.dir], check=True)
        self._heartbeat = _UploadHeartbeat(self._cache, self.paths.heartbeat, log_fn=self._log)
        # One beat before the job can look, so the first thing it sees is a heartbeat that exists.
        self._heartbeat.beat()
        self._heartbeat.start()
        command = _compose_reservation_cmd(self.launcher, self.paths.pidfile, self.paths.command,
                                           self.paths.heartbeat, queue_wait_s=queue_wait_seconds(self._host))
        ssh_argv = self._host.ssh_argv(allocate_tty=True, share_connection=False) + [pack_for_login_shell(command)]
        if note:
            self._log(note)
        self._log(f"[remote] scheduler: early: requesting allocation via: {self.launcher}\n"
                  "[remote] scheduler: asking for the node now, while the plate is prepared here (this can "
                  "queue)…\n")
        self._announced = True
        self._popen = subprocess.Popen(
            ssh_argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **TEXT_UTF8, bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"}, **own_process_group_kwargs())
        self._pump = threading.Thread(target=self._read, daemon=True, name="aw-reservation-reader")
        self._pump.start()
        if self._stop is not None:
            threading.Thread(target=self._watch_stop, daemon=True, name="aw-reservation-stop").start()

    def _read(self) -> None:
        """Forward the job's output: to the run log until the hand-over, to the run's reader after it."""
        assert self._popen is not None and self._popen.stdout is not None
        try:
            for line in self._popen.stdout:
                with self._lock:
                    if self._taken:
                        self._lines.put(line)
                        continue
                self._log(line)
        except (OSError, ValueError) as exc:
            self._log(f"[remote] scheduler: stopped reading the early allocation request ({exc})\n")
        finally:
            self._lines.put(None)

    def _watch_stop(self) -> None:
        """Release the reservation when the run is stopped before it took it."""
        assert self._stop is not None
        while True:
            with self._lock:
                if self._taken or self._released:
                    return
            if self._stop.wait(0.5):
                self.release("the run was stopped")
                return

    # ── state ────────────────────────────────────────────────────────────
    @property
    def taken(self) -> bool:
        return self._taken

    def alive(self) -> bool:
        """Whether the job can still be handed a run: queued or granted, not released, its ssh still connected."""
        return (not self._released and not self._taken and self._popen is not None
                and self._popen.poll() is None)

    def usable(self) -> bool:
        """Whether the job can still take the run; when it cannot, says why and gives it back."""
        if self.alive():
            return True
        if self._taken:
            return False
        rc = self._popen.poll() if self._popen is not None else None
        why = "it was given back" if self._released else f"it ended before the run was ready (exit {rc})"
        self._log(f"[remote] scheduler: the node asked for early cannot take the run — {why}; asking for one "
                  "now.\n")
        self.release("it can no longer take the run")
        return False

    # ── hand-over ────────────────────────────────────────────────────────
    def hand_over(self, body: str, *, run_pidfile: str, run_heartbeat: str) -> ReservedPopen | None:
        """Give the waiting job its run; the ``Popen`` to read it by, or ``None`` when it can no longer take one.

        *body* is the shell the job sources (:func:`services.remote_commands._handover_job_body` or
        ``_handover_driver_body``). *run_pidfile* and *run_heartbeat* are the run directory's own. ``None`` has
        been logged with its reason and the reservation released; the caller launches the ordinary way.
        """
        if not self.usable():
            return None
        script = _write_command_script(self.paths, body, run_pidfile, run_heartbeat)
        try:
            self._cache._ssh_run(["sh", "-c", script], check=True)
        except Exception as exc:
            self._log(f"[remote] scheduler: could not hand the run to the node asked for early ({exc}); asking "
                      "for one now.\n")
            self.release("the hand-over failed")
            return None
        with self._lock:
            self._taken = True
        if self._heartbeat is not None:
            self._heartbeat.stop()
        self._log("[remote] scheduler: the run is handed to the node asked for early.\n")
        assert self._popen is not None and self._pump is not None
        return ReservedPopen(self._popen, self._lines, self._pump)

    # ── release ──────────────────────────────────────────────────────────
    def release(self, reason: str) -> None:
        """End a reservation no run took. Idempotent; a taken one belongs to its run and is left alone."""
        with self._lock:
            if self._taken or self._released:
                return
            self._released = True
        if self._heartbeat is not None:
            self._heartbeat.stop()
        if self._announced:
            self._log(f"[remote] scheduler: giving back the node asked for early — {reason}.\n")
        if self._popen is not None:
            signal_subprocess(self._popen, escalate=True)
        with swallow("releasing an early allocation request"):
            result = self._cache._ssh_run(["sh", "-c", _release_script(self.paths)], check=False)
            if result.returncode != 0:
                # The job still gives its node back on its own once it sees no heartbeat (UPLOAD_HEARTBEAT_STALE_S).
                self._log(f"[remote] scheduler: WARN could not cancel the node asked for early (exit "
                          f"{result.returncode}); it gives the node back by itself within "
                          f"{UPLOAD_HEARTBEAT_STALE_S}s of being granted.\n")
