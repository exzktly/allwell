"""What a batched run takes from the remote before it uploads or computes anything.

A batched run that is stopped, suspended, or dies with its node leaves work on the remote host: wells the
pipeline finished whose output the Fetcher had not yet downloaded, and input archives that were uploaded and never
processed. Restarting such a run used to look only at the local output folder. Everything else was sent and
computed again — including a well whose finished output was sitting on the remote — and when the restart was
incremental (which every resume is, ``analyze/suspend.py``) the staged archives differed, so the run id and with it
the whole run directory were new, and the earlier attempt's directory was not looked at at all.

So before the plate is sliced into batches, the run lists what is already on the remote, in its own run directory
and in every earlier attempt's that the run journal recorded under the **same settings, the same source images and
the same host** (``run_journal.stored_remote_addresses`` — the keys that make reusing an output safe):

* **Finished wells** — a ``<well>_out.zip`` in an output directory. The pipeline writes that archive last and
  atomically (``pipeline.io.compress_images_to_zip``), after the well's CSV, so its presence is the same "this well
  completed" the verifier uses (``FetchedOutputVerifier._list_completed_wells_in``). Such a well is not uploaded or
  run; its output files are brought into this run's output directory on the remote and the Fetcher downloads them
  like any other well's. Never for a forced run, which asked for the wells to be computed again.
* **Input archives** — any other ``<well>.zip`` under a staged batch (``b<i>``), the swapped-in ``in/``, or an
  earlier attempt's input directory. Each is put into the batch directory this run assigns its well to before
  that batch's upload, so rsync's quick check (name, size, mtime) skips it when it is the same archive and sends
  only what differs when it is not — a trimmed incremental archive, say. Nothing is trusted on its name alone.

Files are moved within this run's own directory and hard-linked (copied where a link cannot be made) out of an
earlier attempt's, so an earlier directory is never disturbed in case anything still reads it. Every step is
best-effort: a listing or a link that fails costs an upload, never a result.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from services.remote_transport import _remote_quote
from util.well_token import canonical_well_label

_BATCH_DIR = re.compile(r"b\d+")
#: What a finished well leaves in a remote output directory, as globs on the well label. The nanowell tables live
#: under ``nanowells/`` and are only present for a nanowell run.
_OUTPUT_GLOBS = ("{w}_out.zip", "*_{w}.csv", "*_{w}.csv.gz")
_NANOWELL_FILES = ("{w}.csv", "{w}_grid.json", "{w}_template.npz")


@dataclass
class Leftovers:
    """What the remote already holds for this plate. Paths are as this app spells them (``~`` unexpanded)."""

    #: archive file name (``A01.zip``) → every remote path holding a copy, this run's own directory first.
    inputs: dict[str, list[str]] = field(default_factory=dict)
    #: canonical well → the remote output directory holding its finished output, this run's own first.
    finished: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _Root:
    path: str
    run_dir: bool          # a run directory (``in/``, ``b<i>/``, an output subdirectory) or a bare input directory
    out_name: str = "out"
    own: bool = False


def _roots(own_run_dir: str, own_output_dir: str, earlier: Iterable[dict]) -> list[_Root]:
    roots = [_Root(own_run_dir, True, PurePosixPath(own_output_dir).name, own=True)]
    for addr in earlier:
        out = str(addr.get("remote_output_dir") or "")
        inp = str(addr.get("remote_input_dir") or "")
        if out:
            parent = str(PurePosixPath(out).parent)
            if all(r.path != parent for r in roots):
                roots.append(_Root(parent, True, PurePosixPath(out).name))
        if inp and not any(inp == r.path or inp.startswith(r.path.rstrip("/") + "/") for r in roots):
            roots.append(_Root(inp, False))
    return roots


def find_leftovers(cache, *, own_run_dir: str, own_output_dir: str, earlier: Iterable[dict],
                   log: Callable[[str], None]) -> Leftovers:
    """List the input archives and finished wells under this run's directory and each *earlier* address.

    *earlier* is the run journal's matching addresses (dicts with ``remote_input_dir`` / ``remote_output_dir``).
    One remote script lists every root, each line tagged with its root's index and relative to it, so nothing
    here has to match a ``~``-expanded path against an unexpanded one. A failed listing is "nothing found".
    """
    roots = _roots(own_run_dir, own_output_dir, earlier)
    script = "".join(
        f"if cd {_remote_quote(r.path)} 2>/dev/null; then find . -maxdepth 2 -type f -name '*.zip' "
        f"| sed 's|^|{i}\t|'; fi\n" for i, r in enumerate(roots)) + "true\n"
    result = cache._ssh_script(script, check=False)
    found = Leftovers()
    if result is None or getattr(result, "returncode", 1) != 0:
        return found
    for line in (result.stdout or "").splitlines():
        index, _, rel = line.partition("\t")
        if not index.isdigit() or int(index) >= len(roots):
            continue
        root = roots[int(index)]
        parts = PurePosixPath(rel).parts            # `./out/x.zip` → ("out", "x.zip"): the `.` is normalised away
        if not parts:
            continue
        name = parts[-1]
        sub = parts[0] if len(parts) == 2 else ""
        if root.run_dir and sub == root.out_name:
            if name.endswith("_out.zip"):
                well = canonical_well_label(name[: -len("_out.zip")])
                if well:
                    found.finished.setdefault(well, f"{root.path}/{sub}")
            continue
        if root.run_dir and not (sub == "in" or _BATCH_DIR.fullmatch(sub)):
            continue             # bg/ is the empty-well reference, staged by the run itself; the rest is not input
        if not root.run_dir and sub:
            continue
        found.inputs.setdefault(name, []).append(f"{root.path}/{PurePosixPath(*parts)}")
    if found.inputs or found.finished:
        log(f"[remote] already on the remote: {len(found.finished)} finished well(s) and {len(found.inputs)} "
            f"uploaded input archive(s), from this run or an earlier attempt of it with the same settings.\n")
    return found


def adopt_finished(cache, wells: Iterable[str], found: Leftovers, *, own_output_dir: str, nanowell: bool,
                   log: Callable[[str], None]) -> list[str]:
    """Bring each finished well's output files into this run's output directory; return the wells adopted.

    Within this run's own directory there is nothing to move. From an earlier attempt's they are hard-linked (or
    copied), so the Fetcher downloads — and then deletes — them from this run's directory exactly as it would a
    well this run computed. A well whose files could not all be brought over is left out, and is then uploaded and
    run like any other.
    """
    lines = [f"mkdir -p {_remote_quote(own_output_dir)}"
             + (f" {_remote_quote(own_output_dir + '/nanowells')}" if nanowell else "")]
    adoptable = []
    for well in wells:
        src = found.finished.get(well)
        if src is None:
            continue
        adoptable.append(well)
        if src == own_output_dir:
            continue
        pairs = [(src, g.format(w=well), own_output_dir) for g in _OUTPUT_GLOBS]
        if nanowell:
            pairs += [(f"{src}/nanowells", g.format(w=well), f"{own_output_dir}/nanowells") for g in _NANOWELL_FILES]
        for folder, glob, dest in pairs:
            lines.append(
                f"for f in {_remote_quote(folder)}/{glob}; do [ -f \"$f\" ] || continue; "
                f"ln -f \"$f\" {_remote_quote(dest)}/ 2>/dev/null || cp -p \"$f\" {_remote_quote(dest)}/ "
                f"|| echo 'allwell-adopt-failed: {well}'; done")
    if not adoptable:
        return []
    result = cache._ssh_script("\n".join(lines) + "\n", check=False)
    if result is None or getattr(result, "returncode", 1) != 0:
        return []
    failed = {line.split(": ", 1)[1].strip() for line in (result.stdout or "").splitlines()
              if line.startswith("allwell-adopt-failed: ")}
    adopted = [w for w in adoptable if w not in failed]
    if adopted:
        log(f"[remote] {len(adopted)} well(s) were finished on the remote and not yet downloaded — downloading them "
            f"instead of uploading and running them again: {', '.join(adopted[:12])}"
            + (" …" if len(adopted) > 12 else "") + "\n")
    return adopted


def stage_from_leftovers(cache, batches: list[list], found: Leftovers, *, own_run_dir: str,
                         log: Callable[[str], None]) -> int:
    """Put every archive the remote already holds into the batch directory its well now belongs to.

    Returns how many were placed. A copy in this run's own directory is moved; one in an earlier attempt's is
    hard-linked or copied. rsync's quick check then decides, per archive, whether anything is still to send.
    """
    lines, placed = [], 0
    for index, batch in enumerate(batches, start=1):
        dest = f"{own_run_dir}/b{index}"
        for zip_path in batch:
            copies = found.inputs.get(zip_path.name) or []
            if not copies:
                continue
            src = copies[0]
            placed += 1
            if src == f"{dest}/{zip_path.name}":
                continue                 # already where this batch is uploaded
            q_src, q_dest = _remote_quote(src), _remote_quote(dest)
            move = (f"mv -f {q_src} {q_dest}/" if src.startswith(own_run_dir + "/") else
                    f"{{ ln -f {q_src} {q_dest}/ 2>/dev/null || cp -p {q_src} {q_dest}/; }}")
            lines.append(f"mkdir -p {q_dest} && {move} || true")
    if lines:
        result = cache._ssh_script("\n".join(lines) + "\n", check=False)
        if result is None or getattr(result, "returncode", 1) != 0:
            return 0
    if placed:
        log(f"[remote] {placed} input archive(s) already on the remote were put into this run's batches — rsync "
            f"sends only what differs from them.\n")
    return placed


def remote_done_marker(well: str) -> str:
    """The completion line the Fetcher and the progress count read, for a well adopted from the remote."""
    return f"Well {well} — done. (finished on the remote by an earlier attempt)\n"


# ── the batched run's three call sites (``services.remote_process._BatchedRemoteProcess``) ──

def take_from_remote(proc, zips: list, *, force: bool) -> tuple[list, list[str]]:
    """List the remote, adopt the wells it has finished, and return ``(still to upload, adopted wells)``.

    Called once, after the local-completion filter and before the plate is sliced. The listing is kept on
    *proc* for :func:`stage_leftovers`, which runs once the batches are known.
    """
    from services import run_journal
    from services.remote_run_id import remote_run_dir

    proc._leftovers = Leftovers()
    keys = getattr(proc, "_journal_keys", None)
    if keys is None or not zips:            # an object built without __init__ (a unit test of another path)
        return list(zips), []
    runner, run = proc._runner, proc._runner._run
    own_output = run.remote_output_dir
    try:
        earlier = [a for a in run_journal.stored_remote_addresses(
            proc._local_output_dir, argv_digests=[keys["argv_digest"], *getattr(proc, "_legacy_digests", ())],
            source_key=keys["source_key"],
            host_alias=keys["host_alias"]) if a.get("remote_output_dir") != own_output]
    except OSError:
        earlier = []
    found = find_leftovers(runner._cache, own_run_dir=remote_run_dir(runner.host, proc._run_id),
                           own_output_dir=own_output, earlier=earlier, log=proc._log)
    proc._leftovers = found
    _say_what_the_journal_expected(proc, zips, found)
    if force:
        return list(zips), []
    wanted = [w for w in (canonical_well_label(z.stem) for z in zips) if w and w in found.finished]
    adopted = set(adopt_finished(runner._cache, wanted, found, own_output_dir=own_output,
                                 nanowell=bool(proc._fetcher.run.nanowell), log=proc._log))
    return [z for z in zips if canonical_well_label(z.stem) not in adopted], sorted(adopted)


def nothing_left_to_run(adopted: list[str]) -> str:
    """The log line for a batched run with no well left to upload, by whether any is still to download."""
    if adopted:
        return ("[remote] batched mode: every well already complete locally or on the remote — downloading the "
                f"{len(adopted)} finished there, nothing to run.\n")
    return "[remote] batched mode: every well already complete locally — nothing to do.\n"


def journal_discrepancies(states: dict[str, str], wells: list[str], found: Leftovers) -> dict[str, list[str]]:
    """Wells whose earlier journal record promises something the remote no longer has, by what was promised.

    The per-well journal says where each well got to in the attempts before this one
    (``run_journal.previous_well_states``). It cannot say whether a file is still there — the remote listing
    decides that — but it is what turns "this well is being sent again" into a reason.
    """
    names = {w for w in wells}
    on_remote_inputs = {canonical_well_label(PurePosixPath(n).stem) for n in found.inputs}
    out: dict[str, list[str]] = {"finished": [], "uploaded": [], "fetched": []}
    for well in sorted(names):
        state = states.get(well, "")
        if state in ("analysed", "not-fetched") and well not in found.finished:
            out["finished"].append(well)
        elif state == "uploaded" and well not in on_remote_inputs and well not in found.finished:
            out["uploaded"].append(well)
        elif state == "fetched":
            out["fetched"].append(well)
    return {k: v for k, v in out.items() if v}


_DISCREPANCY_TEXT = {
    "finished": "the journal recorded as finished on the remote, but their output is no longer there — running "
                "them again",
    "uploaded": "the journal recorded as uploaded, but their archives are no longer on the remote — sending them "
                "again",
    "fetched": "the journal recorded as downloaded, but they have no complete CSV here — processing them again",
}


def _say_what_the_journal_expected(proc, zips: list, found: Leftovers) -> None:
    from services import run_journal

    try:
        states = run_journal.previous_well_states(proc._local_output_dir)
    except OSError:
        return
    wells = [w for w in (canonical_well_label(z.stem) for z in zips) if w]
    for kind, listed in journal_discrepancies(states, wells, found).items():
        proc._log(f"[remote] {len(listed)} well(s) {_DISCREPANCY_TEXT[kind]}: {', '.join(listed[:12])}"
                  + (" …" if len(listed) > 12 else "") + "\n")


def announce_adopted(proc, adopted: list[str]) -> None:
    """Hand each adopted well to the Fetcher and to the progress count, as its own completion line would.

    For a nanowell run the release that frees a finished batch is queued behind them as well — these wells are
    in no batch of this run, so no later re-anchoring re-runs them.
    """
    import functools

    from services.remote_fetch_nanowells import release_batch

    for well in adopted:
        line = remote_done_marker(well)
        proc._line_q.put(line)
        proc._fetcher.observe(line)
    if adopted and proc._fetcher.run.nanowell:
        proc._fetcher.after_queued(functools.partial(release_batch, wells=list(adopted), log=proc._log))


def stage_leftovers(proc, run_dir: str) -> None:
    """Put the remote's archives into this run's batch directories, before ``in/`` is cleared."""
    found, batches = getattr(proc, "_leftovers", None), getattr(proc, "_batches", None)
    if found is None or not found.inputs or not batches:
        return
    stage_from_leftovers(proc._runner._cache, batches, found, own_run_dir=run_dir, log=proc._log)
