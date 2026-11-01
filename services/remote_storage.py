"""What a remote host's staging directory holds, and removing what nobody needs.

A remote run leaves two kinds of folder under ``remote_staging_dir``:

* ``datasets/<key>/`` — an uploaded plate, keyed by a content fingerprint of
  its archives (:class:`services.remote_upload.DatasetCache`);
* ``runs/<run_id>/`` — one run's working directory: its ``out/`` (and, for a
  batched run, its ``in/``), pidfile, heartbeat and handshake files
  (:func:`services.remote_run_id.remote_run_dir`).

Both names are hashes, so on the server nothing says which plate a folder came
from. Cancelled jobs, abandoned runs, failures and every change to the
upload's content leave folders behind. This module is what the Manage Remotes
*Storage…* browser (``analyze/remote_storage_dialog.py``) and the run-start
sweep both stand on.

**Confinement: it can delete nothing but All-Well's own run and dataset
folders, and every rule is enforced twice** — in Python before a script is
built, and again by the script on the server at the moment it acts — so neither
layer alone stands between a bug and somebody's data:

1. *The staging directory must be a real, specific folder*
   (:func:`validate_staging_dir`): absolute or ``~/``-relative, no ``.``/``..``/
   empty component, no control character, no surrounding whitespace, never
   ``/`` or ``~``. Every script ``cd``\\ s into it and refuses when its
   *physical* path is ``/`` or the home folder.
2. *A target is always ``runs/<name>`` or ``datasets/<name>``, relative to that
   folder*, ``<name>`` one plain path component (:data:`_NAME_RE`). On the
   server (``_aw_inside``) it must be a real directory, not a link, inside a
   non-link ``runs``/``datasets``, on the **same filesystem** as its parent (a
   mount point is refused), with a physical path of exactly
   ``<staging>/<top>/<name>``. The ``rm`` itself runs from inside the checked
   ``runs``/``datasets`` (re-checked with ``pwd -P``) on ``./<name>``: renamed
   first to a hidden ``.aw_trash.<name>.<pid>`` (atomic, so a run starting
   mid-removal recreates the folder afresh), then removed with
   ``--one-file-system``; a server whose ``rm`` lacks that flag refuses to
   delete at all. A physical staging path holding a control character is
   refused, since the mount table cannot be matched against it. Links are never listed. A dataset's stamp, label and expiry
   are removed only as plain files in non-link bookkeeping folders. Every file
   this module writes goes through a temporary file and ``mv``, so it never
   writes through a link or a hard link.
3. *Only All-Well's folders, and only your own*: the name has the shape
   All-Well gives (``run-<hex>[-<hex>]``, a hex dataset key) or — an older
   build's run folder — it holds ``.aw_beat``/``.aw_upload_complete``/
   ``.aw_upload_alive``; and the folder is owned by the user the app logs in
   as (``[ -O ]``). Anything else is listed and left alone.
4. *Automatic removal* (the sweep and *Remove expired now*) is decided by the
   server, not by the listing it started from: the folder must carry an
   All-Well label naming that very folder, a run folder must carry the marker
   written once its results were downloaded and verified (``.aw_fetched``, only
   after a full fetch) with nothing but bookkeeping written since,
   its expiry is re-read and re-evaluated against the server's own clock, and
   a dataset a run used within :data:`~services.remote_cache_gc.IN_USE_WINDOW_S`
   is kept.

**In use — kept by every deletion, manual or automatic, checked on the server
at the moment of deletion.** A run folder is in use when its heartbeat is
fresh; when ``squeue`` lists the job in its ``run.pid.slurm`` as alive, **or
cannot say** (``squeue`` missing, failing for any reason other than "invalid
job id", or the sidecar unreadable); when its ``run.pid`` is alive
(``kill -0`` or ``ps -p``, so a process of another user still counts); when
its ``keep/`` holds nanowell wells waiting for re-anchoring; when anything in
it other than this module's own files was written in the last
:data:`RECENT_WRITE_MIN` minutes; or when a run that re-attached to it
(``.aw_borrowed_by``) is itself in use. A dataset is in use when it was
stamped or written in the last :data:`RECENT_WRITE_MIN` minutes, when a run in
use names it in its label, or when any run in use carries no label to say
which dataset it reads. "Now" is the staging filesystem's own clock (the mtime
of a temporary probe file it creates and removes), so a skewed login-node clock
cannot make a fresh heartbeat look stale. A check that cannot run — ``find``
failing, ``squeue`` failing, an unreadable sidecar — answers *in use*. An mtime that cannot be read is never treated as epoch
zero: such a folder is not due.

**Labels.** Each run writes a small JSON label the first time it uses a
folder: the project (the folder holding the ``in`` folder), the local input
and output paths, the client machine and the time. A run folder keeps its
label at ``runs/<id>/.aw_label.json``; a dataset beside the datasets, at
``datasets/.aw_labels/<key>.json``, because a dataset folder is the pipeline's
input folder and holds the plate only. Labels are written with a private umask.
Starting a run in a folder also clears its ``.aw_fetched`` marker and any
dated expiry (``keep`` stays), so a reused folder is judged as fresh work.

**Expiry.** A folder expires once untouched for the host's
``storage_expiry_days`` (0, the default, means never), or at a date set on it
in the browser; ``keep`` never expires. The setting lives in
``runs/<id>/.aw_expiry`` or ``datasets/.aw_expiry/<key>``. Nothing runs on the
server by itself, so expired folders are removed by the app: in the
background after each run on that host starts (:func:`label_and_sweep`), and
on request from the browser. The run that is starting is protected, along
with every folder labelled with its local output directory.

``tests/test_remote_storage_is_confined.py`` runs every script against a home
folder of decoys and checks nothing outside the targets changed;
``tests/test_remote_storage_liveness.py`` holds every in-use rule above.
Qt-free; it talks to the host only through a ``DatasetCache``'s
``_ssh_script``, so tests drive it with a fake.
"""

from __future__ import annotations

import json
import math
import re
import shlex
import socket
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from services.remote_cache_gc import IN_USE_STAMP_DIR, IN_USE_WINDOW_S, LABEL_DIR, USER_EXPIRY_DIR
from services.remote_commands import HOLD_DIR
from services.remote_run_id import HEARTBEAT_DIR, LEGACY_UPLOAD_HEARTBEAT, UPLOAD_SENTINEL, remote_run_dir
from services.remote_transport import _remote_quote
from services.remote_wait_snippets import MID_BATCH_HEARTBEAT_STALE_S
from services.slurm_ids import squeue_listing
from util.aw_safe import swallowed

#: The label inside a run directory.
RUN_LABEL = ".aw_label.json"
#: The per-folder expiry setting inside a run directory.
RUN_EXPIRY = ".aw_expiry"
#: Written into a run folder once its results were downloaded and verified;
#: automatic removal of a run folder requires it.
RUN_FETCHED = ".aw_fetched"
#: One run id per line: the runs that re-attached to this folder's ``out/``.
RUN_BORROWED_BY = ".aw_borrowed_by"
#: Directories, under ``datasets/``, holding one label / expiry file per key —
#: named where the cache GC, which removes them with their dataset, defines them.
DATASET_LABEL_DIR = LABEL_DIR
DATASET_EXPIRY_DIR = USER_EXPIRY_DIR
#: The prefix of the temporary probe file, in the staging folder, whose mtime is the filesystem's "now".
NOW_PROBE = ".aw_now"
#: The expiry value that means "never remove this folder automatically".
KEEP = "keep"
#: A folder written to within this many minutes is treated as in use.
RECENT_WRITE_MIN = 15
#: The detailed listing runs ``du`` over every folder: minutes on a busy shared
#: filesystem holding a few terabytes of plates.
LISTING_TIMEOUT_S = 900.0
#: The run-start label-and-list call: stats only, no tree walks.
RUN_START_TIMEOUT_S = 120.0
#: How long one deletion script may take — ``rm -rf`` of several plates.
DELETE_TIMEOUT_S = 1800.0
#: The most folders one run start's background sweep removes.
SWEEP_MAX_PER_RUN = 25

KINDS = ("run", "dataset")
_TOP = {"run": "runs", "dataset": "datasets"}
#: A folder name this module will act on: one path component, not hidden.
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,199}$")
#: The names All-Well gives its folders: ``run-<cache key prefix>-<argv digest>``
#: (``remote_run_id._run_id_body``; one hex group in older builds) and a hex
#: content fingerprint (``remote_scratch.fingerprint_archives``,
#: ``acquisition_transport.acquisition_cache_key``).
_RUN_NAME_RE = r"^run-[0-9a-f]{8,64}(-[0-9a-f]{8,64})?$"
_DATASET_NAME_RE = r"^[0-9a-f]{16,128}$"
#: Files only All-Well's run machinery writes into a run folder, marking an
#: older build's folder (whose name may have another shape) as All-Well's. Not
#: the label, which anything could carry, and not the generic ``run.pid``.
_RUN_MARKERS = (HEARTBEAT_DIR, UPLOAD_SENTINEL, LEGACY_UPLOAD_HEARTBEAT)
#: This module's own files: writing them is not a run using the folder.
_OWN_FILES = (RUN_LABEL, RUN_EXPIRY, RUN_FETCHED, RUN_BORROWED_BY)
#: ``squeue %T`` states of a job that has ended (``services.remote_sweep``).
_ENDED_STATES = "COMPLETED|CANCELLED*|FAILED|TIMEOUT|NODE_FAIL|PREEMPTED|BOOT_FAIL|DEADLINE|OUT_OF_MEMORY"
_DAY = 86400


class UnsafeStagingDir(ValueError):
    """The host's staging directory is not a folder this module may act inside."""


def validate_staging_dir(staging_dir: str) -> str:
    """*staging_dir* without a trailing slash, or :class:`UnsafeStagingDir`.

    Refuses anything but a specific folder: empty, ``/``, ``~``, a relative or
    ``~user`` path, a ``.``/``..``/empty component, a control or non-printing
    character, and surrounding whitespace — which is refused rather than
    stripped, because the rest of the app uses the value as written and a
    stripped one names a different folder. The server repeats the important
    half: it refuses a folder whose physical path is ``/`` or the home folder.
    """
    s = str(staging_dir or "")
    if any(unicodedata.category(c).startswith("C") for c in s):
        raise UnsafeStagingDir(f"the staging folder {s!r} contains a control character")
    if s != s.strip():
        raise UnsafeStagingDir(f"the staging folder {s!r} begins or ends with a space")
    s = s.rstrip("/")
    if s.startswith("~/"):
        rest = s[2:]
    elif s.startswith("/"):
        rest = s[1:]
    else:
        raise UnsafeStagingDir(f"the staging folder {staging_dir!r} must start with / or ~/")
    if not rest or any(p in ("", ".", "..") for p in rest.split("/")):
        raise UnsafeStagingDir(f"the staging folder {staging_dir!r} is not a specific folder")
    return s


@dataclass(frozen=True)
class _Staging:
    """The one field :func:`remote_run_dir` reads, so a run folder here is spelled by the funnel."""

    remote_staging_dir: str


def _run_dir(staging_dir: str, run_id: str) -> str:
    return remote_run_dir(_Staging(staging_dir), run_id)


def _q(text: str) -> str:
    return shlex.quote(text)


# ── the server-side guards ──────────────────────────────────────────────────

def _preamble(staging_dir: str, *, missing_ok: bool = False) -> str:
    """Bash that enters the staging directory, refuses to go on when it is
    missing (quietly with *missing_ok*) or physically ``/`` or the home folder,
    sets ``now`` from the staging filesystem's own clock, and defines every
    guard and liveness function. Every path after it is relative to the
    physical staging directory ``aw_root``."""
    root = _remote_quote(validate_staging_dir(staging_dir))
    # A listing (*missing_ok*) always prints its clock line, so a refusal reads
    # as a refusal and a missing folder as an empty one — never as "no listing".
    clock = "printf 'now\\t%s\\n' \"$(date +%s)\"; " if missing_ok else ""
    missing = (f"{clock}exit 0" if missing_ok
               else "printf 'root\\tthe staging folder does not exist\\n'; exit 0")
    return (
        "set -u\n"
        "umask 077\n"
        "_aw_mtime() { stat -c %Y -- \"$1\" 2>/dev/null || stat -f %m -- \"$1\" 2>/dev/null; }\n"
        "_aw_dev() { stat -c %d -- \"$1\" 2>/dev/null || stat -f %d -- \"$1\" 2>/dev/null; }\n"
        f"cd -- {root} 2>/dev/null || {{ {missing}; }}\n"
        "aw_root=$(pwd -P) || exit 3\n"
        "aw_home=$(cd ~ 2>/dev/null && pwd -P) || aw_home=/\n"
        "if [ \"$aw_root\" = / ] || [ \"$aw_root\" = \"$aw_home\" ]; then\n"
        f"  {clock}printf 'root\\trefused: the staging folder is / or the home folder (%s)\\n' \"$aw_root\"; exit 0; fi\n"
        # A control character (a newline above all) in the physical path cannot be
        # matched against the mount table, which escapes it; refuse rather than guess.
        "case \"$aw_root\" in *[[:cntrl:]]*)\n"
        f"  {clock}printf 'root\\trefused: the staging folder path contains a control character\\n'; exit 0;; esac\n"
        # The staging filesystem's clock, not the login node's: an NFS server's
        # mtimes are its own, and a skewed client clock made fresh look stale. The
        # probe is a fresh temporary file, so the write can never go through a link.
        "now=$(date +%s)\n"
        f"if aw_p=$(mktemp ./{NOW_PROBE}.XXXXXX 2>/dev/null); then t=$(_aw_mtime \"$aw_p\"); rm -f -- \"$aw_p\"; "
        "[ -n \"$t\" ] && now=$t; fi\n"
        # Where squeue's complaints go, read apart from its answer (see _aw_run_self_active).
        "aw_err=$(mktemp 2>/dev/null) || aw_err=''; [ -z \"$aw_err\" ] || trap 'rm -f -- \"$aw_err\"' EXIT\n"
        + _guard_functions() + _liveness_functions()
    )


def _guard_functions() -> str:
    """``_aw_inside K N`` (a real folder directly inside a real ``K``, same
    filesystem, exact physical path), ``_aw_ours K N``, ``_aw_mine K N``,
    ``_aw_labelled K N``, ``_aw_plain_dir P``, ``_aw_put FILE TEXT`` (write via
    a temporary file and ``mv``, never through a link)."""
    run_markers = " || ".join(f"[ -e \"runs/$2/{m}\" ]" for m in _RUN_MARKERS)
    return (
        "_aw_inside() { case \"$1\" in runs|datasets) ;; *) return 1;; esac\n"
        "  [ -d \"$1\" ] && [ ! -L \"$1\" ] && [ -d \"$1/$2\" ] && [ ! -L \"$1/$2\" ] || return 1\n"
        "  [ \"$(cd -- \"$1/$2\" 2>/dev/null && pwd -P)\" = \"$aw_root/$1/$2\" ] || return 1\n"
        "  local a b; a=$(_aw_dev \"$1/$2\") && b=$(_aw_dev \"$1\") && [ -n \"$a\" ] && [ \"$a\" = \"$b\" ] || return 1\n"
        "  ! _aw_mount at \"$1/$2\"; }\n"
        # A bind mount on the same filesystem keeps its device number, so only the
        # mount table tells it apart. ``at``: P itself is a mount point; ``under``:
        # P or anything inside it is. A table that cannot be searched answers yes;
        # with no table at all (not Linux) only ``mountpoint`` can answer.
        "_aw_mount() { local p=\"$aw_root/$2\" e\n"
        "  if [ -r /proc/self/mountinfo ]; then\n"
        "    e=$(printf '%s' \"$p\" | sed 's/\\\\/\\\\134/g; s/ /\\\\040/g; s/\t/\\\\011/g')\n"
        "    [ -n \"$e\" ] || return 0\n"
        # Through the environment: ``awk -v`` would decode the ``\\040`` escapes again.
        "    AW_P=\"$e\" AW_M=\"$1\" awk 'BEGIN {p = ENVIRON[\"AW_P\"]; m = ENVIRON[\"AW_M\"]} "
        "$5 == p || (m == \"under\" && index($5, p \"/\") == 1) {f = 1} END {exit !f}' /proc/self/mountinfo; return; fi\n"
        "  command -v mountpoint >/dev/null 2>&1 && mountpoint -q -- \"$p\"; }\n"
        "_aw_ours() { if [ \"$1\" = runs ]; then\n"
        f"    [[ $2 =~ {_RUN_NAME_RE} ]] || {run_markers}\n"
        f"  else [[ $2 =~ {_DATASET_NAME_RE} ]]; fi; }}\n"
        "_aw_mine() { [ -O \"$1/$2\" ]; }\n"
        "_aw_labelled() { local l k\n"
        f"  if [ \"$1\" = runs ]; then l=\"runs/$2/{RUN_LABEL}\"; k=run_id\n"
        f"  else l=\"datasets/{DATASET_LABEL_DIR}/$2.json\"; k=cache_key; fi\n"
        # The name is letters, digits and ``. _ -``, so escaping the dots is all
        # it takes to match it literally.
        "  [ -f \"$l\" ] && [ ! -L \"$l\" ] && "
        "grep -qE \"\\\"$k\\\"[[:space:]]*:[[:space:]]*\\\"${2//./\\\\.}\\\"\" \"$l\"; }\n"
        "_aw_plain_dir() { [ -d \"$1\" ] && [ ! -L \"$1\" ]; }\n"
        "_aw_put() { local dir t; dir=$(dirname -- \"$1\"); _aw_plain_dir \"$dir\" && [ ! -L \"$1\" ] && [ ! -d \"$1\" ] || return 1\n"
        "  t=$(mktemp \"$dir/.aw_tmp.XXXXXX\") || return 1\n"
        "  if printf '%s\\n' \"$2\" > \"$t\" && mv -f -- \"$t\" \"$1\"; then return 0; fi; rm -f -- \"$t\"; return 1; }\n"
        "_aw_unput() { local dir; dir=$(dirname -- \"$1\"); [ -e \"$1\" ] || [ -L \"$1\" ] || return 0\n"
        "  _aw_plain_dir \"$dir\" && [ -f \"$1\" ] && [ ! -L \"$1\" ] && rm -f -- \"$1\"; }\n"
        # A dated expiry is cleared when a run uses the folder again; ``keep`` stays.
        "_aw_clear_dated() { [ -f \"$1\" ] && [ ! -L \"$1\" ] && _aw_plain_dir \"$(dirname -- \"$1\")\" || return 0\n"
        "  [[ $(head -c 32 \"$1\") =~ ^[0-9]+$ ]] && rm -f -- \"$1\"; return 0; }\n"
    )


def _liveness_functions() -> str:
    """``_aw_run_active D`` and ``_aw_dataset_active D STAMP NAME``: the one
    definition of "in use", shared by the listing and every deletion. Each
    answers *yes* whenever it cannot tell."""
    not_own = " ".join(f"! -name {f}" for f in _OWN_FILES)
    return (
        "_aw_newest() { local m=0 p t; for p in \"$@\"; do [ -e \"$p\" ] || continue\n"
        "  t=$(_aw_mtime \"$p\") && [ -n \"$t\" ] && [ \"$t\" -gt \"$m\" ] && m=$t; done; echo \"$m\"; }\n"
        # A find that fails (no -quit or -mmin, an unreadable folder) answers *in use*.
        f"_aw_recent() {{ local o; o=$(find \"$1\" -xdev -mindepth 1 -type f -mmin -{RECENT_WRITE_MIN} {not_own} "
        "-print -quit 2>/dev/null) || return 0; [ -n \"$o\" ]; }\n"
        # Downloaded, and nothing but bookkeeping written since: a run that re-used the
        # folder without clearing the marker (an older build, a failed label call)
        # leaves newer files, so the marker no longer vouches for them.
        f"_aw_fetched_current() {{ local o; [ -f \"$1/{RUN_FETCHED}\" ] && [ ! -L \"$1/{RUN_FETCHED}\" ] || return 1\n"
        f"  o=$(find \"$1\" -xdev -mindepth 1 -type f -newer \"$1/{RUN_FETCHED}\" ! -name '.aw_*' ! -name 'run.pid*' "
        f"! -path '*/{HEARTBEAT_DIR}/*' -print -quit 2>/dev/null) || return 1; [ -z \"$o\" ]; }}\n"
        "_aw_run_self_active() { local d=$1 h t z jid o r st p\n"
        f"  for h in \"$d/{HEARTBEAT_DIR}/alive\" \"$d/{LEGACY_UPLOAD_HEARTBEAT}\"; do\n"
        "    [ -e \"$h\" ] || continue; t=$(_aw_mtime \"$h\"); [ -n \"$t\" ] || return 0\n"
        f"    [ $((now - t)) -lt {MID_BATCH_HEARTBEAT_STALE_S} ] && return 0; done\n"
        f"  for z in \"$d/{HOLD_DIR}\"/*.zip; do [ -e \"$z\" ] && return 0; done\n"
        "  if [ -e \"$d/run.pid.slurm\" ]; then\n"
        "    jid=$(head -c 64 \"$d/run.pid.slurm\" 2>/dev/null | tr -cd '0-9.'); [ -n \"$jid\" ] || return 0\n"
        "    command -v squeue >/dev/null 2>&1 || return 0\n"
        # The answer from stdout only, so a warning squeue prints on every call cannot
        # read as a job state; its complaints are read only to recognise a finished job.
        "    o=$(" + squeue_listing("jid") + " 2>\"${aw_err:-/dev/null}\"); r=$?\n"
        "    if [ $r -eq 0 ]; then st=$(printf '%s\\n' \"$o\" | head -n 1)\n"
        f"      case \"$st\" in ''|{_ENDED_STATES}) ;; *) return 0;; esac\n"
        "    else [ -n \"$aw_err\" ] && grep -qi 'invalid job id' \"$aw_err\" || return 0; fi\n"
        "  fi\n"
        "  if [ -s \"$d/run.pid\" ]; then p=$(head -c 32 \"$d/run.pid\" | tr -cd '0-9')\n"
        "    if [ -n \"$p\" ]; then kill -0 \"$p\" 2>/dev/null && return 0; ps -p \"$p\" >/dev/null 2>&1 && return 0; fi\n"
        "  fi\n"
        "  _aw_recent \"$d\"; }\n"
        "_aw_run_active() { local b o; _aw_run_self_active \"$1\" && return 0\n"
        f"  b=\"$1/{RUN_BORROWED_BY}\"; [ -f \"$b\" ] && [ ! -L \"$b\" ] || return 1\n"
        "  while IFS= read -r o || [ -n \"$o\" ]; do\n"
        f"    [[ $o =~ {_RUN_NAME_RE} ]] && [ -d \"runs/$o\" ] && [ ! -L \"runs/$o\" ] && "
        "_aw_run_self_active \"runs/$o\" && return 0\n"
        "  done < \"$b\"; return 1; }\n"
        # Which datasets runs in use are reading, worked out once per script.
        "aw_scanned=0; aw_active_keys=''; aw_active_unknown=0\n"
        "_aw_scan_runs() { [ $aw_scanned = 1 ] && return; aw_scanned=1; local r l k\n"
        "  for r in runs/*; do [ -d \"$r\" ] && [ ! -L \"$r\" ] || continue; _aw_run_active \"$r\" || continue\n"
        f"    l=\"$r/{RUN_LABEL}\"; k=''\n"
        "    if [ -f \"$l\" ] && [ ! -L \"$l\" ]; then k=$(grep -oE '\"cache_key\"[[:space:]]*:[[:space:]]*\"[0-9a-f]+\"' "
        "\"$l\" | grep -oE '[0-9a-f]{16,}' | head -n 1); fi\n"
        "    if [ -n \"$k\" ]; then aw_active_keys=\"$aw_active_keys $k \"; else aw_active_unknown=1; fi\n"
        "  done; }\n"
        "_aw_dataset_active() { local d=$1 s=$2 n=$3 t\n"
        "  if [ -e \"$s\" ]; then t=$(_aw_mtime \"$s\"); [ -n \"$t\" ] || return 0\n"
        f"    [ $((now - t)) -lt {RECENT_WRITE_MIN * 60} ] && return 0; fi\n"
        "  _aw_recent \"$d\" && return 0\n"
        "  _aw_scan_runs; [ $aw_active_unknown = 1 ] && return 0\n"
        "  case \"$aw_active_keys\" in *\" $n \"*) return 0;; esac; return 1; }\n"
        # When a folder was last used; 0 when that cannot be read.
        "_aw_last_used() { if [ \"$1\" = runs ]; then\n"
        f"    _aw_newest \"runs/$2/out\" \"runs/$2/in\" \"runs/$2/{HEARTBEAT_DIR}/alive\" "
        f"\"runs/$2/{LEGACY_UPLOAD_HEARTBEAT}\" \"runs/$2/run.pid\"\n"
        f"  elif [ -e \"datasets/{IN_USE_STAMP_DIR}/$2\" ]; then _aw_newest \"datasets/{IN_USE_STAMP_DIR}/$2\"\n"
        "  else _aw_newest \"datasets/$2\"; fi; }\n"
    )


# ── labels ──────────────────────────────────────────────────────────────────

def project_name(input_dir: Path | str | None) -> str:
    """The project a plate belongs to: the name of the folder holding its ``in`` folder.

    The Analyze in/out layout puts a plate at ``<project>/in``, so the input
    folder's own name says nothing. An input folder with any other name is the
    plate folder itself, and its own name is the most specific thing there is.
    """
    if not input_dir:
        return ""
    p = Path(str(input_dir))
    if p.name.lower() == "in" and p.parent.name:
        return p.parent.name
    return p.name


def make_label(*, local_input_dir: Path | str | None, local_output_dir: Path | str | None,
               run_id: str = "", cache_key: str = "", now: float | None = None,
               client: str | None = None) -> dict:
    """The label a run writes for the folders it creates."""
    return {
        "project": project_name(local_input_dir),
        "input_dir": str(local_input_dir or ""),
        "output_dir": str(local_output_dir or ""),
        "client": client if client is not None else socket.gethostname(),
        "created": int(now if now is not None else time.time()),
        "run_id": run_id,
        "cache_key": cache_key,
    }


def label_script(staging_dir: str, *, run_id: str, dataset_key: str, label: dict,
                 borrowed_run: str = "") -> str:
    """Remote bash recording that a run is starting in its folders.

    For ``runs/<run_id>`` and ``datasets/<dataset_key>`` (when given): write the
    label if there is none yet (so it records the *first* use), and clear any
    dated expiry. For the run folder, and for ``runs/<borrowed_run>`` (the folder
    whose ``out/`` a re-attached run writes into), clear ``.aw_fetched`` — this
    run's results are not downloaded yet. The borrowed folder also records the
    borrower in ``.aw_borrowed_by``, so it counts as in use while the borrower
    is. Acts only on real, All-Well, own folders inside the staging folder.
    """
    text = _q(json.dumps(label, separators=(",", ":"), sort_keys=True))
    out = _preamble(staging_dir)
    if run_id and re.match(_RUN_NAME_RE, run_id):
        d = f"runs/{run_id}"
        out += (f"if _aw_inside runs {_q(run_id)} && _aw_mine runs {_q(run_id)}; then "
                f"[ -e {_q(d + '/' + RUN_LABEL)} ] || _aw_put {_q(d + '/' + RUN_LABEL)} {text}; "
                f"_aw_clear_dated {_q(d + '/' + RUN_EXPIRY)}; _aw_unput {_q(d + '/' + RUN_FETCHED)}; fi\n")
    if borrowed_run and borrowed_run != run_id and re.match(_RUN_NAME_RE, borrowed_run) and run_id:
        d = f"runs/{borrowed_run}"
        out += (f"if _aw_inside runs {_q(borrowed_run)} && _aw_mine runs {_q(borrowed_run)}; then "
                f"b={_q(d + '/' + RUN_BORROWED_BY)}; "
                f"o=$( {{ [ -f \"$b\" ] && [ ! -L \"$b\" ] && grep -vxF {_q(run_id)} \"$b\"; }} 2>/dev/null; "
                f"printf '%s' {_q(run_id)}); _aw_put \"$b\" \"$o\"; "
                f"_aw_clear_dated {_q(d + '/' + RUN_EXPIRY)}; _aw_unput {_q(d + '/' + RUN_FETCHED)}; fi\n")
    if dataset_key and re.match(_DATASET_NAME_RE, dataset_key):
        labels = f"datasets/{DATASET_LABEL_DIR}"
        target = f"{labels}/{dataset_key}.json"
        out += (f"if _aw_inside datasets {_q(dataset_key)} && _aw_mine datasets {_q(dataset_key)}; then "
                f"[ -e {_q(labels)} ] || mkdir {_q(labels)} 2>/dev/null; "
                f"[ -e {_q(target)} ] || _aw_put {_q(target)} {text}; "
                f"_aw_clear_dated {_q(f'datasets/{DATASET_EXPIRY_DIR}/{dataset_key}')}; fi\n")
    return out


def mark_fetched_script(staging_dir: str, run_ids: Iterable[str]) -> str:
    """Remote bash writing ``.aw_fetched`` into each run folder: its results were
    downloaded and verified, so automatic removal may consider it."""
    out = _preamble(staging_dir)
    for rid in run_ids:
        if rid and re.match(_RUN_NAME_RE, rid):
            out += (f"if _aw_inside runs {_q(rid)} && _aw_mine runs {_q(rid)}; then "
                    f"_aw_put {_q(f'runs/{rid}/{RUN_FETCHED}')} \"$now\"; fi\n")
    return out


# ── listing ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StorageItem:
    """One folder under ``datasets/`` or ``runs/``, as the remote reported it."""

    kind: str                  # "run" | "dataset"
    name: str
    modified: int              # the folder's own mtime (epoch s, server clock); 0 when unknown
    last_used: int             # newest sign of use (epoch s, server clock); 0 when unknown
    size: int | None = None    # bytes; None when not measured
    active: bool | None = None  # None when not checked
    expiry: str = ""           # "", KEEP, or an epoch second as text
    label: dict = field(default_factory=dict, compare=False, hash=False)
    marked: bool = False       # the server found it All-Well's
    mine: bool = True          # owned by the user the app logs in as
    fetched: bool = False      # a run folder whose results were downloaded and verified

    @property
    def project(self) -> str:
        return str(self.label.get("project") or "")

    @property
    def created(self) -> int | None:
        """When the folder was first used, from its label; ``None`` when unlabelled or unreadable."""
        value = self.label.get("created")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return int(value) if 0 < value < 1e11 else None

    @property
    def output_dir(self) -> str:
        return str(self.label.get("output_dir") or "")

    @property
    def ours(self) -> bool:
        """All-Well made it: an All-Well name, or (an older run folder) a file
        only All-Well's run machinery writes. The server checks it again."""
        if self.kind == "dataset":
            return bool(re.match(_DATASET_NAME_RE, self.name))
        return self.marked or bool(re.match(_RUN_NAME_RE, self.name))

    @property
    def actionable(self) -> bool:
        """Whether the browser may delete it or set its expiry: All-Well's and yours."""
        return self.ours and self.mine

    @property
    def labelled(self) -> bool:
        """Whether its All-Well label names this very folder — what automatic removal requires."""
        key = "run_id" if self.kind == "run" else "cache_key"
        return self.label.get(key) == self.name

    def path(self, staging_dir: str) -> str:
        if self.kind == "run":
            return _run_dir(staging_dir, self.name)
        return f"{staging_dir}/datasets/{self.name}"


def listing_script(staging_dir: str, *, detail: bool) -> str:
    """Remote bash printing ``now\\t<epoch>`` and then one line per folder::

        item kind name bytes|- mtime|- last_used active(1|0|-) expiry ours mine fetched label_json

    tab-separated, then ``free\\t<bytes>``. *detail* measures sizes and checks
    whether each folder is in use, which walks every tree; without it the
    listing is a few ``stat`` calls per folder. Links, mount points and
    anything not a real folder directly inside a real ``runs``/``datasets``
    are never listed. A staging folder the server refuses lists nothing and
    says why (``root`` line).
    """
    size = ('sz=$(du -sbx -- "$d" 2>/dev/null | cut -f1); [ -n "$sz" ] || sz=-'
            if detail else "sz=-")
    fetched = ('f=0; _aw_fetched_current "$d" && f=1' if detail else f'f=0; [ -f "$d/{RUN_FETCHED}" ] && f=1')
    run_act = ('if _aw_run_active "$d"; then a=1; else a=0; fi' if detail else "a=-")
    ds_act = ('if _aw_dataset_active "$d" "$stamp" "$n"; then a=1; else a=0; fi' if detail else "a=-")
    emit = ("if _aw_ours \"$top\" \"$n\"; then o=1; else o=0; fi; if _aw_mine \"$top\" \"$n\"; then m=1; else m=0; fi\n"
            "  mt=$(_aw_mtime \"$d\"); [ -n \"$mt\" ] || mt=-; used=$(_aw_last_used \"$top\" \"$n\")\n"
            "  printf 'item\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' "
            "\"$kind\" \"$n\" \"$sz\" \"$mt\" \"$used\" \"$a\" \"$exp\" \"$o\" \"$m\" \"$f\" \"$lab\"\n")
    read_exp = "exp=''; [ -f \"$ef\" ] && [ ! -L \"$ef\" ] && exp=$(head -c 32 \"$ef\" | tr -cd 'a-z0-9')\n"
    read_lab = "lab=''; [ -f \"$lf\" ] && [ ! -L \"$lf\" ] && lab=$(head -c 8192 \"$lf\" | tr -d '\\n\\t\\r')\n"
    loop = ("for d in {top}/*; do n=${{d#{top}/}}; top={top}\n"
            "  [[ $n =~ ^[A-Za-z0-9_][A-Za-z0-9._-]{{0,199}}$ ]] || continue; _aw_inside {top} \"$n\" || continue\n")
    return (
        _preamble(staging_dir, missing_ok=True)
        + "printf 'now\\t%s\\n' \"$now\"\n"
        + loop.format(top="runs")
        + "  kind=run\n"
        f"  {size}; {run_act}\n"
        f"  {fetched}\n"
        f"  ef=\"$d/{RUN_EXPIRY}\"; lf=\"$d/{RUN_LABEL}\"\n"
        f"  {read_exp}  {read_lab}  {emit}"
        "done\n"
        + loop.format(top="datasets")
        + "  kind=dataset; f=0\n"
        f"  stamp=\"datasets/{IN_USE_STAMP_DIR}/$n\"\n"
        f"  {size}; {ds_act}\n"
        f"  ef=\"datasets/{DATASET_EXPIRY_DIR}/$n\"; lf=\"datasets/{DATASET_LABEL_DIR}/$n.json\"\n"
        f"  {read_exp}  {read_lab}  {emit}"
        "done\n"
        "free=$(df -PB1 . 2>/dev/null | awk 'NR==2 {print $4}'); printf 'free\\t%s\\n' \"${free:--}\"\n"
        "exit 0\n"
    )


@dataclass(frozen=True)
class Listing:
    items: tuple[StorageItem, ...]
    now: int                    # the staging filesystem's clock when it listed
    free_bytes: int | None = None
    refused: str = ""           # why the server would not act in this staging folder, if it would not


def _int_or_none(text: str) -> int | None:
    return int(text) if text.isdigit() and len(text) < 19 else None


def parse_listing(text: str) -> Listing | None:
    """The folders from :func:`listing_script`'s output, or ``None`` when the
    output is not a listing at all (no clock line) — an unknown, never "empty"."""
    items: list[StorageItem] = []
    now: int | None = None
    free: int | None = None
    refused = ""
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if parts[0] == "now" and len(parts) == 2 and _int_or_none(parts[1].strip()) is not None:
            now = int(parts[1])
        elif parts[0] == "free" and len(parts) == 2:
            free = _int_or_none(parts[1].strip())
        elif parts[0] == "root" and len(parts) == 2:
            refused = parts[1]
        elif parts[0] == "item" and len(parts) == 12 and parts[1] in KINDS and _NAME_RE.match(parts[2]):
            try:
                label = json.loads(parts[11]) if parts[11] else {}
            except ValueError:
                label = {}
            items.append(StorageItem(
                kind=parts[1], name=parts[2], size=_int_or_none(parts[3]),
                modified=_int_or_none(parts[4]) or 0, last_used=_int_or_none(parts[5]) or 0,
                active={"1": True, "0": False}.get(parts[6]),
                expiry=parts[7] if parts[7] == KEEP or _int_or_none(parts[7]) is not None else "",
                marked=parts[8] == "1", mine=parts[9] == "1", fetched=parts[10] == "1",
                label=label if isinstance(label, dict) else {},
            ))
    return None if now is None else Listing(tuple(items), now, free, refused)


def list_storage(cache, *, detail: bool = True) -> tuple[Listing | None, str]:
    """``(listing, error)`` for *cache*'s host; the listing is ``None`` with the
    reason in *error* when the remote could not be asked. A listing the server
    refused comes back empty with the refusal as *error*."""
    try:
        script = listing_script(cache.host.remote_staging_dir, detail=detail)
    except UnsafeStagingDir as exc:
        return None, str(exc)
    result = cache._ssh_script(script, check=False, timeout_s=LISTING_TIMEOUT_S)
    if result.returncode != 0:
        return None, f"exit {result.returncode}: {(result.stderr or '').strip() or 'no reason given'}"
    listing = parse_listing(result.stdout)
    if listing is None:
        return None, "the remote returned no listing"
    return listing, listing.refused


# ── expiry ──────────────────────────────────────────────────────────────────

def effective_expiry(item: StorageItem, host_days: int) -> int | None:
    """When *item* expires (epoch s, server clock), or ``None`` for never — including
    when its last use could not be read, which is never taken as epoch zero."""
    if item.expiry == KEEP:
        return None
    if item.expiry.isdigit():
        return int(item.expiry)
    if host_days > 0 and item.last_used > 0:
        return item.last_used + host_days * _DAY
    return None


def plan_expired(items: Iterable[StorageItem], now: int, host_days: int, *,
                 protect: Callable[[StorageItem], bool] = lambda _i: False) -> list[StorageItem]:
    """The candidates for automatic removal: All-Well's, yours, labelled as
    themselves, a run folder's results downloaded, due, not known to be in use,
    not protected, and — a dataset — not used within the in-use window. The
    server decides again before it removes anything."""
    out = []
    for item in items:
        if not (item.actionable and item.labelled) or item.last_used <= 0:
            continue
        if item.kind == "run" and not item.fetched:
            continue
        due = effective_expiry(item, host_days)
        if due is None or now < due or item.active or protect(item):
            continue
        if item.kind == "dataset" and now - item.last_used < IN_USE_WINDOW_S:
            continue
        out.append(item)
    return out


def expiry_value(*, keep: bool = False, days: int | None = None, now: int | None = None) -> str:
    """The text written to an expiry file: ``keep``, an epoch *days* from *now*,
    or ``""`` (remove the file: the host's rule applies again)."""
    if keep:
        return KEEP
    if days is not None:
        return str(int(now if now is not None else time.time()) + int(days) * _DAY)
    return ""


def _expiry_file(item: StorageItem) -> str:
    if item.kind == "run":
        return f"runs/{item.name}/{RUN_EXPIRY}"
    return f"datasets/{DATASET_EXPIRY_DIR}/{item.name}"


def set_expiry_script(staging_dir: str, items: Iterable[StorageItem], value: str) -> str:
    """Remote bash setting each folder's expiry to *value* (see :func:`expiry_value`),
    printing ``ok``, ``refused`` or ``fail`` then ``\\t<kind>\\t<name>`` per folder."""
    if value and value != KEEP and not value.isdigit():
        raise ValueError(f"not an expiry value: {value!r}")
    lines = [_preamble(staging_dir).rstrip("\n")]
    for item in _checked(items):
        top, n, target = _TOP[item.kind], _q(item.name), _expiry_file(item)
        tag = f"{item.kind}\\t{item.name}"
        make = "" if item.kind == "run" else (
            f"{{ [ -e datasets/{DATASET_EXPIRY_DIR} ] || mkdir datasets/{DATASET_EXPIRY_DIR}; }} && ")
        # Clearing goes through ``_aw_unput``, which checks the parent folder too:
        # a bare ``rm`` on ``datasets/.aw_expiry/<key>`` followed a linked
        # ``.aw_expiry`` and removed a file of the same name wherever it pointed.
        act = (f"_aw_put {_q(target)} {_q(value)}" if value else f"_aw_unput {_q(target)}")
        lines.append(
            f"if ! _aw_inside {top} {n} || ! _aw_ours {top} {n} || ! _aw_mine {top} {n}; then "
            f"printf 'refused\\t{tag}\\n'; "
            f"elif {make}{act} 2>/dev/null; then printf 'ok\\t{tag}\\n'; else printf 'fail\\t{tag}\\n'; fi")
    return "\n".join(lines) + "\nexit 0\n"


# ── deletion ────────────────────────────────────────────────────────────────

def _checked(items: Iterable[StorageItem], *, require_actionable: bool = True) -> list[StorageItem]:
    """*items*, refusing any whose kind or name could reach outside its folder,
    and (by default) any the listing did not report as All-Well's and yours."""
    out = list(items)
    for item in out:
        if item.kind not in KINDS or not isinstance(item.name, str) or not _NAME_RE.match(item.name):
            raise ValueError(f"refusing to act on {item.kind!r} folder {item.name!r}")
        if require_actionable and not item.ours:
            raise ValueError(f"refusing to act on {item.kind} folder {item.name!r}: All-Well did not make it")
        if require_actionable and not item.mine:
            raise ValueError(f"refusing to act on {item.kind} folder {item.name!r}: another user owns it")
    return out


def _due_check(top: str, n: str, host_days: int) -> str:
    """Bash defining ``_aw_due``: true when the folder is due for *automatic*
    removal, judged on the server from its own files and clock."""
    expiry = (f"\"runs/{n}/{RUN_EXPIRY}\"" if top == "runs"
              else f"\"datasets/{DATASET_EXPIRY_DIR}/{n}\"")
    return (
        f"_aw_due() {{ local e='' u; [ -f {expiry} ] && [ ! -L {expiry} ] && "
        f"e=$(head -c 32 {expiry} | tr -cd 'a-z0-9')\n"
        f"  u=$(_aw_last_used {top} {_q(n)}); [ -n \"$u\" ] && [ \"$u\" -gt 0 ] || return 1\n"
        + (f"  [ $((now - u)) -ge {IN_USE_WINDOW_S} ] || return 1\n" if top == "datasets" else "")
        + "  case \"$e\" in keep) return 1;; '') ;; *[!0-9]*) return 1;; *) [ \"$now\" -ge \"$e\" ]; return;; esac\n"
        f"  [ {int(host_days)} -gt 0 ] && [ $((now - u)) -ge {int(host_days) * _DAY} ]; }}\n"
    )


def delete_script(staging_dir: str, items: Iterable[StorageItem], *, automatic: bool = False,
                  host_days: int = 0) -> str:
    """Remote bash removing each folder, printing ``gone``, ``busy``,
    ``refused:<why>`` or ``fail`` then ``\\t<kind>\\t<name>`` per folder.

    The server removes ``<runs|datasets>/<name>`` only when it is a real folder
    directly inside the staging folder on the same filesystem, All-Well's, yours,
    and not in use at that moment. *automatic* (the sweep, *Remove expired now*)
    also requires its label to name it, a run folder's ``.aw_fetched``, and that
    it is due by its own expiry or *host_days*. The removal runs from inside the
    re-checked ``runs``/``datasets``: the folder is first renamed to a hidden
    ``.aw_trash.<name>.<pid>`` — atomic, so a run starting while a large folder is
    being removed recreates it afresh rather than having its new files deleted —
    and the trash is then removed with ``--one-file-system``. Trash an interrupted
    deletion left behind is removed at the start of the next deletion script, under
    the same checks. A dataset's stamp, label and expiry — plain files in non-link
    folders — go with it. A folder already absent is ``gone:absent``.
    """
    lines = [_preamble(staging_dir).rstrip("\n"),
             # Refuse to delete at all where rm cannot be kept on one filesystem.
             "aw_onefs=1; rm -rf --one-file-system -- ./.aw_rm_probe_absent 2>/dev/null || aw_onefs=0",
             # Finish what an interrupted deletion started: its trash was already chosen for removal.
             "if [ $aw_onefs = 1 ]; then for aw_t in runs/.aw_trash.* datasets/.aw_trash.*; do\n"
             "  [ -d \"$aw_t\" ] && [ ! -L \"$aw_t\" ] && [ -O \"$aw_t\" ] || continue\n"
             "  aw_k=${aw_t%%/*}; aw_b=${aw_t#*/}; _aw_plain_dir \"$aw_k\" || continue\n"
             "  [ \"$(_aw_dev \"$aw_t\")\" = \"$(_aw_dev \"$aw_k\")\" ] && ! _aw_mount under \"$aw_t\" || continue\n"
             "  ( cd -- \"$aw_k\" && [ \"$(pwd -P)\" = \"$aw_root/$aw_k\" ] && rm -rf --one-file-system -- \"./$aw_b\" ) 2>/dev/null\n"
             "done; fi"]
    for item in _checked(items):
        top, n = _TOP[item.kind], item.name
        nq, d = _q(n), _q(f"{top}/{n}")
        tag = f"{item.kind}\\t{n}"
        auto = ""
        if automatic:
            lines.append(_due_check(top, n, host_days).rstrip("\n"))
            auto = (f"elif ! _aw_labelled {top} {nq}; then printf 'refused:unlabelled\\t{tag}\\n'; "
                    + (f"elif ! _aw_fetched_current runs/{nq}; then printf 'refused:not-downloaded\\t{tag}\\n'; "
                       if item.kind == "run" else "")
                    + f"elif ! _aw_due; then printf 'refused:not-due\\t{tag}\\n'; ")
        if item.kind == "run":
            busy, extra = f"_aw_run_active {d}", ""
        else:
            stamp = f"datasets/{IN_USE_STAMP_DIR}/{n}"
            busy = f"_aw_dataset_active {d} {_q(stamp)} {nq}"
            extra = "".join(
                f"if _aw_plain_dir {_q(folder)} && [ -f {_q(f)} ] && [ ! -L {_q(f)} ]; then rm -f -- {_q(f)}; fi; "
                for folder, f in ((f"datasets/{IN_USE_STAMP_DIR}", stamp),
                                  (f"datasets/{DATASET_LABEL_DIR}", f"datasets/{DATASET_LABEL_DIR}/{n}.json"),
                                  (f"datasets/{DATASET_EXPIRY_DIR}", f"datasets/{DATASET_EXPIRY_DIR}/{n}")))
        trash = _q(f".aw_trash.{n}") + ".$$"
        # 0 removed; 2 renamed but not wholly removed (the trash is finished next time); else nothing moved.
        remove = (f"( cd -- {top} && [ \"$(pwd -P)\" = \"$aw_root/{top}\" ] && [ ! -L ./{nq} ] "
                  f"&& [ ! -e ./{trash} ] && mv -- ./{nq} ./{trash} "
                  f"&& {{ rm -rf --one-file-system -- ./{trash} || exit 2; }} ) 2>/dev/null")
        lines.append(
            f"if [ ! -e {d} ] && [ ! -L {d} ]; then printf 'gone:absent\\t{tag}\\n'; "
            f"elif ! _aw_inside {top} {nq}; then printf 'refused:outside\\t{tag}\\n'; "
            f"elif ! _aw_ours {top} {nq}; then printf 'refused:not-all-well\\t{tag}\\n'; "
            f"elif ! _aw_mine {top} {nq}; then printf 'refused:not-yours\\t{tag}\\n'; "
            f"elif _aw_mount under {d}; then printf 'refused:holds-a-mount\\t{tag}\\n'; "
            f"{auto}"
            f"elif {busy}; then printf 'busy\\t{tag}\\n'; "
            f"elif [ $aw_onefs = 0 ]; then printf 'refused:rm-cannot-stay-on-one-filesystem\\t{tag}\\n'; "
            f"else {remove}; aw_r=$?; if [ $aw_r = 0 ]; then {extra}printf 'gone\\t{tag}\\n'; "
            f"elif [ $aw_r = 2 ]; then printf 'fail:partly-removed\\t{tag}\\n'; "
            f"else printf 'fail\\t{tag}\\n'; fi; fi")
    return "\n".join(lines) + "\nexit 0\n"


def status_of(outcome: str) -> str:
    """``refused`` from ``refused:not-due`` — the status without its reason."""
    return outcome.split(":", 1)[0]


def parse_outcomes(text: str) -> tuple[dict[tuple[str, str], str], str]:
    """``({(kind, name): outcome}, refusal)`` from a deletion or expiry script's
    output; *refusal* is why the server would not act in the staging folder at all."""
    out: dict[tuple[str, str], str] = {}
    refusal = ""
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[1] in KINDS and status_of(parts[0]) in ("gone", "busy", "refused", "fail", "ok"):
            out[(parts[1], parts[2])] = parts[0]
        elif len(parts) == 2 and parts[0] == "root":
            refusal = parts[1]
    return out, refusal


def run_outcome_script(cache, script: str, items: list[StorageItem], *,
                       timeout_s: float) -> tuple[dict[tuple[str, str], str], str]:
    """Run a deletion / expiry script on *cache*'s host: ``(outcomes, error)``.
    A folder the output does not mention is reported ``unknown``, never done."""
    result = cache._ssh_script(script, check=False, timeout_s=timeout_s)
    outcomes, refusal = parse_outcomes(result.stdout)
    for item in items:
        outcomes.setdefault((item.kind, item.name), "refused" if refusal else "unknown")
    error = refusal or ("" if result.returncode == 0 else (
        f"exit {result.returncode}: {(result.stderr or '').strip() or 'no reason given'}"))
    return outcomes, error


def delete_items(cache, items: Iterable[StorageItem], *, automatic: bool = False,
                 host_days: int = 0) -> tuple[dict[tuple[str, str], str], str]:
    """Remove *items* from *cache*'s host (see :func:`delete_script`)."""
    items = _checked(items)
    if not items:
        return {}, ""
    script = delete_script(cache.host.remote_staging_dir, items, automatic=automatic, host_days=host_days)
    return run_outcome_script(cache, script, items, timeout_s=DELETE_TIMEOUT_S)


def set_expiry(cache, items: Iterable[StorageItem], value: str) -> tuple[dict[tuple[str, str], str], str]:
    """Set *items*' expiry on *cache*'s host (see :func:`set_expiry_script`)."""
    items = _checked(items)
    if not items:
        return {}, ""
    return run_outcome_script(cache, set_expiry_script(cache.host.remote_staging_dir, items, value), items,
                              timeout_s=LISTING_TIMEOUT_S)


def describe(item: StorageItem) -> str:
    """``run run-ab12… (project X)`` — how a log line names a folder."""
    project = f" (project {item.project})" if item.project else " (unlabelled)"
    return f"{item.kind} {item.name}{project}"


def remove_expired(cache, listing: Listing, *, log: Callable[[str], None], host_days: int | None = None,
                   protect: Callable[[StorageItem], bool] = lambda _i: False,
                   limit: int | None = None) -> list[StorageItem]:
    """Remove the folders :func:`plan_expired` names, oldest first, at most *limit*
    of them; the ones removed. *host_days* defaults to the host's own rule. The
    server decides each one again (:func:`delete_script` with ``automatic``)."""
    days = cache.host.storage_expiry_days if host_days is None else host_days
    due = sorted(plan_expired(listing.items, listing.now, days, protect=protect), key=lambda i: i.last_used)
    if limit is not None:
        due = due[:limit]
    if not due:
        return []
    outcomes, error = delete_items(cache, due, automatic=True, host_days=days)
    removed = []
    for item in due:
        outcome = outcomes.get((item.kind, item.name), "unknown")
        status = status_of(outcome)
        if outcome == "gone:absent":
            log(f"[remote] storage: expired {describe(item)} was already gone\n")
        elif status == "gone":
            removed.append(item)
            idle = max(0, listing.now - item.last_used) // _DAY
            log(f"[remote] storage: removed expired {describe(item)}, untouched {idle} day(s)\n")
        elif status == "busy":
            log(f"[remote] storage: kept expired {describe(item)}: it is in use\n")
        elif status == "refused":
            why = outcome.partition(":")[2] or error
            log(f"[remote] storage: kept {describe(item)}: the server declined ({why})\n")
        else:
            log(f"[remote] storage: could not remove expired {describe(item)} ({outcome}"
                f"{'; ' + error if error else ''})\n")
    return removed


# ── the run-start hook ──────────────────────────────────────────────────────

def _run_name_of(remote_dir: str, staging_dir: str) -> str:
    """``run-xyz`` when *remote_dir* is (inside) ``<staging>/runs/run-xyz``, else ``""``."""
    prefix = _run_dir(staging_dir, "")
    if not remote_dir.startswith(prefix):
        return ""
    return remote_dir[len(prefix):].split("/", 1)[0]


def protect_for_run(run, staging_dir: str) -> Callable[[StorageItem], bool]:
    """The folders a starting run must keep: its own run directory, the one it
    re-attached to, its dataset, and anything labelled with its output folder
    (the earlier attempts a later resume would re-attach to)."""
    names = {("run", run.run_id), ("dataset", run.cache_key),
             ("run", _run_name_of(str(run.remote_output_dir), staging_dir))}
    output = str(run.local_output_dir or "")

    def protect(item: StorageItem) -> bool:
        return (item.kind, item.name) in names or bool(output and item.output_dir == output)
    return protect


def label_and_sweep(cache, run, log: Callable[[str], None], *, background: bool = True) -> None:
    """Label the folders *run* is using, list the host, and remove what has
    expired — the removal on a background thread, so it never holds up the run.

    The label-and-list call is bounded by :data:`RUN_START_TIMEOUT_S` and walks
    no trees. Best-effort: a host that cannot be asked loses only the label and
    the sweep, never the run. At most :data:`SWEEP_MAX_PER_RUN` folders go per
    run start.
    """
    staging = cache.host.remote_staging_dir
    try:
        validate_staging_dir(staging)
    except UnsafeStagingDir as exc:
        log(f"[remote] storage: not labelling or removing anything: {exc}\n")
        return
    dataset_key = (run.cache_key if str(run.remote_input_dir) == f"{staging}/datasets/{run.cache_key}" else "")
    label = make_label(local_input_dir=run.local_input_dir, local_output_dir=run.local_output_dir,
                       run_id=run.run_id, cache_key=run.cache_key)
    borrowed = _run_name_of(str(run.remote_output_dir), staging)
    # Two scripts in one call, each in its own subshell: one's refusal cannot stop the other.
    script = ("(\n" + label_script(staging, run_id=run.run_id, dataset_key=dataset_key, label=label,
                                   borrowed_run=borrowed if borrowed != run.run_id else "") + ")\n"
              + "(\n" + listing_script(staging, detail=False) + ")\n")
    result = cache._ssh_script(script, check=False, timeout_s=RUN_START_TIMEOUT_S)
    listing = parse_listing(result.stdout) if result.returncode == 0 else None
    if listing is None or listing.refused:
        why = listing.refused if listing is not None else f"exit {result.returncode}"
        log(f"[remote] storage: could not list {staging} ({why}); "
            "this run's folders are unlabelled and nothing expired was removed\n")
        return
    protect = protect_for_run(run, staging)
    if not plan_expired(listing.items, listing.now, cache.host.storage_expiry_days, protect=protect):
        return

    def sweep() -> None:
        try:
            remove_expired(cache, listing, log=log, protect=protect, limit=SWEEP_MAX_PER_RUN)
        except Exception:
            swallowed("removing expired folders on the remote")

    if background:
        threading.Thread(target=sweep, name="allwell-storage-sweep", daemon=True).start()
    else:
        sweep()


def is_kept(cache, kind: str, name: str) -> bool:
    """Whether the folder is marked *Keep forever* — ``True`` also when that cannot
    be read, so a caller that is about to delete it keeps it instead.

    For the paths that delete without going through this module (a run's own
    Auto-delete, ``cleanup_mode=all``): they honour Keep through this.
    """
    item = StorageItem(kind, name, 0, 0)
    try:
        _checked([item], require_actionable=False)
        script = (_preamble(cache.host.remote_staging_dir)
                  + f"e=''; f={_q(_expiry_file(item))}; [ -f \"$f\" ] && [ ! -L \"$f\" ] && e=$(head -c 8 \"$f\")\n"
                  "printf 'expiry\\t%s\\n' \"$e\"\n")
    except ValueError:
        return True
    try:
        result = cache._ssh_script(script, check=False, timeout_s=RUN_START_TIMEOUT_S)
    except Exception:
        swallowed(f"reading whether {kind} {name} is kept")
        return True
    lines = [ln for ln in (result.stdout or "").splitlines() if ln.startswith("expiry\t")]
    if result.returncode != 0 or not lines:
        return True
    return lines[-1].split("\t", 1)[1].strip() == KEEP


def mark_fetched(cache, run, log: Callable[[str], None]) -> None:
    """Record on the server that *run*'s results were downloaded and verified,
    in its own folder and the one it re-attached to. Best-effort."""
    staging = cache.host.remote_staging_dir
    try:
        script = mark_fetched_script(staging, [run.run_id, _run_name_of(str(run.remote_output_dir), staging)])
    except UnsafeStagingDir:
        return
    result = cache._ssh_script(script, check=False, timeout_s=RUN_START_TIMEOUT_S)
    if result.returncode != 0:
        log(f"[remote] storage: could not mark {run.run_id} as downloaded (exit {result.returncode}); "
            "its folder will not be removed automatically\n")
