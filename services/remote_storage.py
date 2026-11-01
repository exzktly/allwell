"""What a remote host's staging directory holds, and removing what nobody needs.

A remote run leaves two kinds of folder under ``remote_staging_dir``:

* ``datasets/<key>/`` — an uploaded plate, keyed by a content fingerprint of
  its archives (:class:`services.remote_upload.DatasetCache`);
* ``runs/<run_id>/`` — one run's working directory: its ``out/`` (and, for a
  batched run, its ``in/``), pidfile, heartbeat and handshake files
  (:func:`services.remote_run_id.remote_run_dir`).

Both names are hashes, so on the server nothing says which plate a folder came
from. Cancelled jobs, abandoned runs, failures and every change to the
upload's content leave folders behind, and the only way to tell them apart was
to read their contents by hand. This module is what the Manage Remotes
*Storage…* browser (``analyze/remote_storage_dialog.py``) and the run-start
sweep both stand on.

**Labels.** Each run writes a small JSON label the first time it uses a
folder: the project name (the folder that holds the ``in`` folder), the local
input and output paths, the client machine and the time. A run directory keeps
its label at ``runs/<id>/.aw_label.json``. A dataset keeps its label *beside*
the datasets, at ``datasets/.aw_labels/<key>.json``, because a dataset
directory is the pipeline's input directory and must hold nothing but the
plate — the same reason the cache GC's in-use stamps live in
``datasets/.aw_last_used/``. A folder made before labels existed has none and
is shown as unlabelled; nothing else depends on the label.

**Expiry.** A folder expires when it has been left untouched for the host's
``storage_expiry_days`` (0, the default, means never), or at an explicit time
set on that folder in the browser, and a folder marked *keep* never expires.
The per-folder setting lives in ``runs/<id>/.aw_expiry`` or
``datasets/.aw_expiry/<key>``, holding either ``keep`` or an epoch second.
Nothing runs on the server by itself — many clusters forbid ``cron`` on login
nodes — so expired folders are removed by the app: at the start of every run
on that host (:func:`label_and_sweep`), and on request from the browser.

**What is never removed automatically.** A folder that is in use: a run
directory whose heartbeat is fresh, whose pid is alive, whose Slurm job is
still queued or running, or in which anything was written in the last
:data:`RECENT_WRITE_MIN` minutes; and a dataset a run used within the cache
GC's in-use window (:data:`services.remote_cache_gc.IN_USE_WINDOW_S`), because
a run that is still reading it may have started days ago. The run that is
starting is protected too, along with every folder labelled with its local
output directory — the earlier attempts a resume would re-attach to. The
in-use check is made again on the server at the moment of deletion, so a run
that started between the listing and the removal is not touched.

Qt-free; it talks to the host only through a ``DatasetCache``'s
``_ssh_script``, so tests drive it with a fake.
"""

from __future__ import annotations

import json
import re
import shlex
import socket
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from services.remote_cache_gc import IN_USE_STAMP_DIR, IN_USE_WINDOW_S
from services.remote_run_id import HEARTBEAT_DIR, LEGACY_UPLOAD_HEARTBEAT, remote_run_dir
from services.remote_transport import _remote_quote
from services.remote_wait_snippets import MID_BATCH_HEARTBEAT_STALE_S
from services.slurm_ids import squeue_listing

#: The label inside a run directory.
RUN_LABEL = ".aw_label.json"
#: The per-folder expiry setting inside a run directory.
RUN_EXPIRY = ".aw_expiry"
#: Directories, under ``datasets/``, holding one label / expiry file per key.
DATASET_LABEL_DIR = ".aw_labels"
DATASET_EXPIRY_DIR = ".aw_expiry"
#: The expiry value that means "never remove this folder automatically".
KEEP = "keep"
#: A folder written to within this many minutes is treated as in use.
RECENT_WRITE_MIN = 15
#: How long one listing may take. The detailed listing runs ``du`` over every
#: folder, which on a busy shared filesystem holding a few terabytes of plates
#: is minutes, not seconds.
LISTING_TIMEOUT_S = 900.0
#: How long one deletion script may take — ``rm -rf`` of several plates.
DELETE_TIMEOUT_S = 1800.0

KINDS = ("run", "dataset")
#: A folder name this module will act on: one path component, not hidden.
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
#: ``squeue %T`` states of a job that has ended (``services.remote_sweep``).
_ENDED_STATES = "COMPLETED|CANCELLED*|FAILED|TIMEOUT|NODE_FAIL|PREEMPTED|BOOT_FAIL|DEADLINE|OUT_OF_MEMORY"
_DAY = 86400


@dataclass(frozen=True)
class _Staging:
    """The one field :func:`remote_run_dir` reads, so a run folder here is spelled by the funnel."""

    remote_staging_dir: str


def _run_dir(staging_dir: str, run_id: str) -> str:
    return remote_run_dir(_Staging(staging_dir), run_id)


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


def _write_if_absent(path_q: str, text: str) -> str:
    """Shell writing *text* to the (quoted) path unless a file is already there."""
    return f"[ -e {path_q} ] || printf '%s\\n' {shlex.quote(text)} > {path_q} 2>/dev/null\n"


def label_script(staging_dir: str, *, run_id: str, dataset_key: str, label: dict) -> str:
    """Remote bash writing *label* for ``runs/<run_id>`` and (when given)
    ``datasets/<dataset_key>``, each only if that folder exists and has no label
    yet — so the label records when the folder was *first* used."""
    text = json.dumps(label, separators=(",", ":"), sort_keys=True)
    out = ""
    if run_id and _NAME_RE.match(run_id):
        run_dir = _run_dir(staging_dir, run_id)
        out += (f"if [ -d {_remote_quote(run_dir)} ]; then "
                + _write_if_absent(_remote_quote(f"{run_dir}/{RUN_LABEL}"), text).rstrip("\n")
                + "; fi\n")
    if dataset_key and _NAME_RE.match(dataset_key):
        ds = _remote_quote(f"{staging_dir}/datasets/{dataset_key}")
        labels = _remote_quote(f"{staging_dir}/datasets/{DATASET_LABEL_DIR}")
        out += (f"if [ -d {ds} ]; then mkdir -p {labels} 2>/dev/null; "
                + _write_if_absent(_remote_quote(
                    f"{staging_dir}/datasets/{DATASET_LABEL_DIR}/{dataset_key}.json"), text).rstrip("\n")
                + "; fi\n")
    return out


# ── listing ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StorageItem:
    """One folder under ``datasets/`` or ``runs/``, as the remote reported it."""

    kind: str                  # "run" | "dataset"
    name: str
    modified: int              # the folder's own mtime (epoch s, remote clock)
    last_used: int             # newest sign of use (epoch s, remote clock)
    size: int | None = None    # bytes; None when not measured
    active: bool | None = None  # None when not checked
    expiry: str = ""           # "", KEEP, or an epoch second as text
    label: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def project(self) -> str:
        return str(self.label.get("project") or "")

    @property
    def created(self) -> int | None:
        """When the folder was first used, from its label; ``None`` when unlabelled."""
        value = self.label.get("created")
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    @property
    def output_dir(self) -> str:
        return str(self.label.get("output_dir") or "")

    def path(self, staging_dir: str) -> str:
        if self.kind == "run":
            return _run_dir(staging_dir, self.name)
        return f"{staging_dir}/datasets/{self.name}"


def _shell_functions() -> str:
    """Bash defining ``_aw_newest`` and the two in-use predicates, shared by the
    listing and the deletion so both apply one definition of "in use"."""
    return (
        "_aw_mtime() { stat -c %Y -- \"$1\" 2>/dev/null; }\n"
        "_aw_newest() { m=0; for p in \"$@\"; do [ -e \"$p\" ] || continue; "
        "t=$(_aw_mtime \"$p\") || continue; [ \"$t\" -gt \"$m\" ] && m=$t; done; echo \"$m\"; }\n"
        # Files only, and not this module's own: writing a label or an expiry is
        # not a run using the folder, and counting it hid every folder just
        # labelled or given an expiry behind "in use" for the next quarter hour.
        "_aw_recent() { [ -n \"$(find \"$1\" -mindepth 1 -type f -mmin -" + str(RECENT_WRITE_MIN)
        + f" ! -name {RUN_LABEL} ! -name {RUN_EXPIRY} -print -quit 2>/dev/null)\" ]; }}\n"
        # A run is in use while its app beats, while its Slurm job is alive, while
        # its pid is alive (a direct-ssh host), or while anything in it is written.
        "_aw_run_active() { d=$1\n"
        f"  for h in \"$d/{HEARTBEAT_DIR}/alive\" \"$d/{LEGACY_UPLOAD_HEARTBEAT}\"; do\n"
        "    if [ -e \"$h\" ]; then t=$(_aw_mtime \"$h\") && "
        f"[ $((now - t)) -lt {MID_BATCH_HEARTBEAT_STALE_S} ] && return 0; fi\n"
        "  done\n"
        "  if [ -s \"$d/run.pid.slurm\" ]; then\n"
        "    if command -v squeue >/dev/null 2>&1; then\n"
        "      jid=$(head -c 64 \"$d/run.pid.slurm\" | tr -cd '0-9.')\n"
        "      if [ -n \"$jid\" ]; then st=$(" + squeue_listing("jid") + " 2>/dev/null | head -n 1)\n"
        f"        case \"$st\" in ''|{_ENDED_STATES}) ;; *) return 0;; esac; fi\n"
        "    fi\n"
        "  elif [ -s \"$d/run.pid\" ]; then\n"
        "    p=$(head -c 32 \"$d/run.pid\" | tr -cd '0-9')\n"
        "    [ -n \"$p\" ] && kill -0 \"$p\" 2>/dev/null && return 0\n"
        "  fi\n"
        "  _aw_recent \"$d\"; }\n"
        # A dataset is in use while it is being uploaded to or freshly stamped.
        "_aw_dataset_active() { d=$1; s=$2\n"
        "  if [ -e \"$s\" ]; then t=$(_aw_mtime \"$s\") && "
        f"[ $((now - t)) -lt {RECENT_WRITE_MIN * 60} ] && return 0; fi\n"
        "  _aw_recent \"$d\"; }\n"
    )


def listing_script(staging_dir: str, *, detail: bool) -> str:
    """Remote bash printing ``now\\t<epoch>`` and then one line per folder::

        item <kind> <name> <bytes|-> <mtime> <last used> <1|0|-> <expiry> <label JSON>

    tab-separated. *detail* measures each folder's size and checks whether it is
    in use, which walks every tree; without it the listing is a few ``stat``
    calls per folder, cheap enough to run at the start of every run.
    """
    root = _remote_quote(staging_dir)
    size = ('sz=$(du -sb -- "$d" 2>/dev/null | cut -f1); [ -n "$sz" ] || sz=-'
            if detail else "sz=-")
    run_act = ('if _aw_run_active "$d"; then a=1; else a=0; fi' if detail else "a=-")
    ds_act = ('if _aw_dataset_active "$d" "$stamp"; then a=1; else a=0; fi' if detail else "a=-")
    emit = ("printf 'item\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' "
            "\"$kind\" \"$n\" \"$sz\" \"$mt\" \"$used\" \"$a\" \"$exp\" \"$lab\"\n")
    read_exp = "exp=''; [ -f \"$ef\" ] && exp=$(head -c 32 \"$ef\" | tr -cd 'a-z0-9')\n"
    read_lab = "lab=''; [ -f \"$lf\" ] && lab=$(head -c 8192 \"$lf\" | tr -d '\\n\\t\\r')\n"
    return (
        "now=$(date +%s)\n"
        + _shell_functions()
        + f"cd {root} 2>/dev/null || {{ printf 'now\\t%s\\n' \"$now\"; exit 0; }}\n"
        "printf 'now\\t%s\\n' \"$now\"\n"
        "if [ -d runs ]; then for d in runs/*/; do d=${d%/}; [ -d \"$d\" ] || continue\n"
        "  kind=run; n=${d#runs/}\n"
        # Not the run directory's own mtime: writing its label or expiry moves
        # that, and would restart the expiry clock of a folder nobody ran in.
        f"  mt=$(_aw_mtime \"$d\") || mt=0; used=$(_aw_newest \"$d/out\" \"$d/in\" "
        f"\"$d/{HEARTBEAT_DIR}/alive\" \"$d/{LEGACY_UPLOAD_HEARTBEAT}\" \"$d/run.pid\")\n"
        "  [ \"$used\" -gt 0 ] || used=$mt\n"
        f"  {size}; {run_act}\n"
        f"  ef=\"$d/{RUN_EXPIRY}\"; lf=\"$d/{RUN_LABEL}\"\n"
        f"  {read_exp}  {read_lab}  {emit}"
        "done; fi\n"
        "if [ -d datasets ]; then for d in datasets/*/; do d=${d%/}; [ -d \"$d\" ] || continue\n"
        "  kind=dataset; n=${d#datasets/}\n"
        f"  stamp=\"datasets/{IN_USE_STAMP_DIR}/$n\"\n"
        "  mt=$(_aw_mtime \"$d\") || mt=0\n"
        "  if [ -e \"$stamp\" ]; then used=$(_aw_mtime \"$stamp\") || used=$mt; else used=$mt; fi\n"
        f"  {size}; {ds_act}\n"
        f"  ef=\"datasets/{DATASET_EXPIRY_DIR}/$n\"; lf=\"datasets/{DATASET_LABEL_DIR}/$n.json\"\n"
        f"  {read_exp}  {read_lab}  {emit}"
        "done; fi\n"
        "free=$(df -PB1 . 2>/dev/null | awk 'NR==2 {print $4}'); printf 'free\\t%s\\n' \"${free:--}\"\n"
        "exit 0\n"
    )


@dataclass(frozen=True)
class Listing:
    items: tuple[StorageItem, ...]
    now: int                    # the remote's clock when it listed
    free_bytes: int | None = None


def _int_or_none(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def parse_listing(text: str) -> Listing | None:
    """The folders from :func:`listing_script`'s output, or ``None`` when the
    output is not a listing at all (no clock line) — an unknown, never "empty"."""
    items: list[StorageItem] = []
    now: int | None = None
    free: int | None = None
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if parts[0] == "now" and len(parts) == 2 and parts[1].strip().isdigit():
            now = int(parts[1])
        elif parts[0] == "free" and len(parts) == 2:
            free = _int_or_none(parts[1].strip())
        elif parts[0] == "item" and len(parts) == 9 and parts[1] in KINDS and _NAME_RE.match(parts[2]):
            try:
                label = json.loads(parts[8]) if parts[8] else {}
            except ValueError:
                label = {}
            items.append(StorageItem(
                kind=parts[1], name=parts[2], size=_int_or_none(parts[3]),
                modified=_int_or_none(parts[4]) or 0, last_used=_int_or_none(parts[5]) or 0,
                active={"1": True, "0": False}.get(parts[6]),
                expiry=parts[7] if parts[7] == KEEP or parts[7].isdigit() else "",
                label=label if isinstance(label, dict) else {},
            ))
    return None if now is None else Listing(tuple(items), now, free)


def list_storage(cache, *, detail: bool = True) -> tuple[Listing | None, str]:
    """``(listing, error)`` for *cache*'s host; the listing is ``None`` with the
    reason in *error* when the remote could not be asked."""
    result = cache._ssh_script(listing_script(cache.host.remote_staging_dir, detail=detail),
                               check=False, timeout_s=LISTING_TIMEOUT_S)
    if result.returncode != 0:
        return None, f"exit {result.returncode}: {(result.stderr or '').strip() or 'no reason given'}"
    listing = parse_listing(result.stdout)
    return (listing, "") if listing is not None else (None, "the remote returned no listing")


# ── expiry ──────────────────────────────────────────────────────────────────

def effective_expiry(item: StorageItem, host_days: int) -> int | None:
    """When *item* expires (epoch s, remote clock), or ``None`` for never."""
    if item.expiry == KEEP:
        return None
    if item.expiry.isdigit():
        return int(item.expiry)
    if host_days > 0:
        return item.last_used + host_days * _DAY
    return None


def plan_expired(items: Iterable[StorageItem], now: int, host_days: int, *,
                 protect: Callable[[StorageItem], bool] = lambda _i: False) -> list[StorageItem]:
    """The folders the sweep removes: expired, not known to be in use, not
    protected, and — for a dataset — not used within the cache GC's in-use
    window, since a run reading it may have started long ago."""
    out = []
    for item in items:
        due = effective_expiry(item, host_days)
        if due is None or now < due or item.active or protect(item):
            continue
        if item.kind == "dataset" and now - item.last_used < IN_USE_WINDOW_S:
            continue
        out.append(item)
    return out


def expiry_value(*, keep: bool = False, days: int | None = None, now: int | None = None) -> str:
    """The text written to an expiry file: ``keep``, an epoch *days* from *now*,
    or ``""`` (remove the file: the host's default applies again)."""
    if keep:
        return KEEP
    if days is not None:
        return str(int(now if now is not None else time.time()) + int(days) * _DAY)
    return ""


def _expiry_path(staging_dir: str, item: StorageItem) -> str:
    if item.kind == "run":
        return f"{_run_dir(staging_dir, item.name)}/{RUN_EXPIRY}"
    return f"{staging_dir}/datasets/{DATASET_EXPIRY_DIR}/{item.name}"


def set_expiry_script(staging_dir: str, items: Iterable[StorageItem], value: str) -> str:
    """Remote bash setting each folder's expiry to *value* (see :func:`expiry_value`),
    printing ``ok\\t<kind>\\t<name>`` or ``fail\\t<kind>\\t<name>`` per folder."""
    if value and value != KEEP and not value.isdigit():
        raise ValueError(f"not an expiry value: {value!r}")
    lines = []
    for item in _checked(items):
        target = _remote_quote(_expiry_path(staging_dir, item))
        folder = _remote_quote(item.path(staging_dir))
        tag = f"{item.kind}\\t{item.name}"
        if value:
            parent = _remote_quote(_expiry_path(staging_dir, item).rsplit("/", 1)[0])
            act = f"mkdir -p {parent} && printf '%s\\n' {shlex.quote(value)} > {target}"
        else:
            act = f"rm -f {target}"
        lines.append(f"if [ -d {folder} ] && {{ {act}; }} 2>/dev/null; "
                     f"then printf 'ok\\t{tag}\\n'; else printf 'fail\\t{tag}\\n'; fi")
    return "\n".join(lines) + "\nexit 0\n"


# ── deletion ────────────────────────────────────────────────────────────────

def _checked(items: Iterable[StorageItem]) -> list[StorageItem]:
    """*items*, refusing any whose kind or name could reach outside its folder."""
    out = list(items)
    for item in out:
        if item.kind not in KINDS or not _NAME_RE.match(item.name):
            raise ValueError(f"refusing to act on {item.kind!r} folder {item.name!r}")
    return out


def delete_script(staging_dir: str, items: Iterable[StorageItem]) -> str:
    """Remote bash removing each folder unless it is in use *now*, printing
    ``gone``, ``busy`` or ``fail`` then ``\\t<kind>\\t<name>`` per folder. A
    dataset's label, expiry and in-use stamp go with it."""
    lines = ["now=$(date +%s)", _shell_functions().rstrip("\n")]
    for item in _checked(items):
        d = _remote_quote(item.path(staging_dir))
        tag = f"{item.kind}\\t{item.name}"
        if item.kind == "run":
            busy = f"_aw_run_active {d}"
            extra = ""
        else:
            stamp = _remote_quote(f"{staging_dir}/datasets/{IN_USE_STAMP_DIR}/{item.name}")
            busy = f"_aw_dataset_active {d} {stamp}"
            extra = " " + " ".join(_remote_quote(p) for p in (
                f"{staging_dir}/datasets/{IN_USE_STAMP_DIR}/{item.name}",
                f"{staging_dir}/datasets/{DATASET_LABEL_DIR}/{item.name}.json",
                f"{staging_dir}/datasets/{DATASET_EXPIRY_DIR}/{item.name}"))
        lines.append(
            f"if [ ! -e {d} ]; then printf 'gone\\t{tag}\\n'; "
            f"elif {busy}; then printf 'busy\\t{tag}\\n'; "
            f"elif rm -rf -- {d}{extra} 2>/dev/null; then printf 'gone\\t{tag}\\n'; "
            f"else printf 'fail\\t{tag}\\n'; fi")
    return "\n".join(lines) + "\nexit 0\n"


def parse_outcomes(text: str) -> dict[tuple[str, str], str]:
    """``{(kind, name): status}`` from a deletion or expiry script's output."""
    out: dict[tuple[str, str], str] = {}
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[1] in KINDS:
            out[(parts[1], parts[2])] = parts[0]
    return out


def run_outcome_script(cache, script: str, items: list[StorageItem], *,
                       timeout_s: float) -> tuple[dict[tuple[str, str], str], str]:
    """Run a deletion / expiry script on *cache*'s host: ``(outcomes, error)``.
    A folder the output does not mention is reported ``unknown``, never done."""
    result = cache._ssh_script(script, check=False, timeout_s=timeout_s)
    outcomes = parse_outcomes(result.stdout)
    for item in items:
        outcomes.setdefault((item.kind, item.name), "unknown")
    error = "" if result.returncode == 0 else (
        f"exit {result.returncode}: {(result.stderr or '').strip() or 'no reason given'}")
    return outcomes, error


def delete_items(cache, items: Iterable[StorageItem]) -> tuple[dict[tuple[str, str], str], str]:
    """Remove *items* from *cache*'s host (see :func:`delete_script`)."""
    items = _checked(items)
    if not items:
        return {}, ""
    return run_outcome_script(cache, delete_script(cache.host.remote_staging_dir, items), items,
                              timeout_s=DELETE_TIMEOUT_S)


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
                   protect: Callable[[StorageItem], bool] = lambda _i: False) -> list[StorageItem]:
    """Remove every folder :func:`plan_expired` names; the ones removed. *host_days*
    defaults to the host's own ``storage_expiry_days``."""
    days = cache.host.storage_expiry_days if host_days is None else host_days
    due = plan_expired(listing.items, listing.now, days, protect=protect)
    if not due:
        return []
    outcomes, error = delete_items(cache, due)
    removed = []
    for item in due:
        status = outcomes.get((item.kind, item.name), "unknown")
        if status == "gone":
            removed.append(item)
            idle = max(0, listing.now - item.last_used) // _DAY
            log(f"[remote] storage: removed expired {describe(item)}, untouched {idle} day(s)\n")
        elif status == "busy":
            log(f"[remote] storage: kept expired {describe(item)}: it is in use\n")
        else:
            log(f"[remote] storage: could not remove expired {describe(item)} ({status}"
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


def label_and_sweep(cache, run, log: Callable[[str], None]) -> None:
    """Label the folders *run* is using and remove expired ones, in one ssh call
    when nothing has expired. Best-effort: a host that cannot be asked loses only
    the label and the sweep, never the run."""
    staging = cache.host.remote_staging_dir
    dataset_key = (run.cache_key if str(run.remote_input_dir) == f"{staging}/datasets/{run.cache_key}" else "")
    label = make_label(local_input_dir=run.local_input_dir, local_output_dir=run.local_output_dir,
                       run_id=run.run_id, cache_key=run.cache_key)
    script = (label_script(staging, run_id=run.run_id, dataset_key=dataset_key, label=label)
              + listing_script(staging, detail=False))
    result = cache._ssh_script(script, check=False, timeout_s=LISTING_TIMEOUT_S)
    listing = parse_listing(result.stdout) if result.returncode == 0 else None
    if listing is None:
        log(f"[remote] storage: could not list {staging} (exit {result.returncode}); "
            "this run's folders are unlabelled and nothing expired was removed\n")
        return
    remove_expired(cache, listing, log=log, protect=protect_for_run(run, staging))
