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

* **Finished wells** — a ``<well>_out.zip`` in an output directory **and** the well's completion seal beside it
  (``<out>/.aw_complete/<well>.json``, ``pipeline.run_report.seal_well_complete``). The archive alone proves
  nothing: a well suspended part way — by the user, or by the mid-batch watchdog once the app had gone
  (``remote_commands._batch_driver_snippet``) — or one that failed some of its groups writes its CSV and out.zip
  from the groups it finished, and says "— done." like a whole one. The pipeline seals a well only when its output
  covers every FOV-group its input holds, and withdraws the seal before it rewrites the well. A sealed well is not
  uploaded or run; its output files are brought into this run's output directory on the remote and the Fetcher
  downloads them like any other well's. Never for a forced run, which asked for the wells to be computed again.
* **Wells left part way** — an out.zip with no seal. Not adopted: the well is uploaded and run again, and in a
  re-attached output directory the checkpoints its suspended attempt kept (``pipeline.fov_checkpoint.
  release_staging``) carry its finished groups over; its new output replaces the partial one. An output directory
  written by a build that sealed nothing reads the same way, which costs a recomputed well, never a stale one.
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

from pipeline.run_report import COMPLETE_DIRNAME
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
    #: canonical well → the remote output directory holding its finished output, this run's own first. Finished
    #: means an out.zip *and* its completion seal.
    finished: dict[str, str] = field(default_factory=dict)
    #: canonical well → an output directory holding its out.zip with no seal: what a well left part way leaves.
    part_way: dict[str, str] = field(default_factory=dict)


def run_dir_of(output_dir: str) -> str:
    """A run's directory from its output directory (``…/runs/<id>/out`` → ``…/runs/<id>``)."""
    return str(PurePosixPath(output_dir).parent)


@dataclass(frozen=True)
class _Root:
    path: str
    run_dir: bool          # a run directory (``in/``, ``b<i>/``, an output subdirectory) or a bare input directory
    out_name: str = "out"
    own: bool = False


def _roots(own_run_dir: str, own_output_dir: str, earlier: Iterable[dict]) -> list[_Root]:
    roots = [_Root(own_run_dir, True, PurePosixPath(own_output_dir).name, own=True)]
    if run_dir_of(own_output_dir) != own_run_dir:
        # Re-attached (`reattach_output_dir`): the output directory is an earlier attempt's, and so is the run
        # directory around it — its staged batches and `in/` are this run's to take.
        roots.append(_Root(run_dir_of(own_output_dir), True, PurePosixPath(own_output_dir).name, own=True))
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
    # A run directory's root also lists its output directory's completion seals (`./out/.aw_complete/A01.json`).
    seals = [f"find ./{_remote_quote(f'{r.out_name}/{COMPLETE_DIRNAME}')} -maxdepth 1 -type f -name '*.json' "
             f"2>/dev/null; " if r.run_dir else "" for r in roots]
    script = "".join(
        f"if cd {_remote_quote(r.path)} 2>/dev/null; then {{ find . -maxdepth 2 -type f -name '*.zip'; {seals[i]}}} "
        f"| sed 's|^|{i}\t|'; fi\n" for i, r in enumerate(roots)) + "true\n"
    result = cache._ssh_script(script, check=False)
    found = Leftovers()
    if result is None or getattr(result, "returncode", 1) != 0:
        return found
    archives: list[tuple[str, str]] = []           # (well, output directory), in listing order: own first
    sealed: set[tuple[str, str]] = set()
    for line in (result.stdout or "").splitlines():
        index, _, rel = line.partition("\t")
        if not index.isdigit() or int(index) >= len(roots):
            continue
        root = roots[int(index)]
        parts = PurePosixPath(rel).parts            # `./out/x.zip` → ("out", "x.zip"): the `.` is normalised away
        if not parts:
            continue
        name = parts[-1]
        if root.run_dir and len(parts) == 3 and parts[:2] == (root.out_name, COMPLETE_DIRNAME):
            well = canonical_well_label(name[: -len(".json")]) if name.endswith(".json") else ""
            if well:
                sealed.add((well, f"{root.path}/{root.out_name}"))
            continue
        sub = parts[0] if len(parts) == 2 else ""
        if root.run_dir and sub == root.out_name:
            if name.endswith("_out.zip"):
                well = canonical_well_label(name[: -len("_out.zip")])
                if well:
                    archives.append((well, f"{root.path}/{sub}"))
            continue
        if root.run_dir and not (sub in ("in", "bg") or _BATCH_DIR.fullmatch(sub)):
            continue             # in/, a staged batch, or bg/ (the empty-well reference); nothing else is input
        if not root.run_dir and sub:
            continue
        found.inputs.setdefault(name, []).append(f"{root.path}/{PurePosixPath(*parts)}")
    for well, where in archives:
        if (well, where) in sealed:
            found.finished.setdefault(well, where)
        else:
            found.part_way.setdefault(well, where)
    for well in found.finished:
        found.part_way.pop(well, None)                # a whole copy anywhere wins over a part-way one
    if found.inputs or found.finished:
        log(f"[remote] already on the remote: {len(found.finished)} finished well(s) and {len(found.inputs)} "
            f"uploaded input archive(s), from this run or an earlier attempt of it with the same settings.\n")
    if found.part_way:
        names = sorted(found.part_way)
        log(f"[remote] {len(names)} well(s) were left part way on the remote — their output does not cover the whole "
            f"well, so they are run again, continuing from any checkpoints they kept: {', '.join(names[:12])}"
            + (" …" if len(names) > 12 else "") + "\n")
    return found


def adopt_finished(cache, wells: Iterable[str], found: Leftovers, *, own_output_dir: str, nanowell: bool,
                   log: Callable[[str], None]) -> list[str]:
    """Bring each finished well's output files into this run's output directory; return the wells adopted.

    Within this run's own directory there is nothing to move. From an earlier attempt's they are hard-linked (or
    copied), so the Fetcher downloads — and then deletes — them from this run's directory exactly as it would a
    well this run computed. A well whose files could not all be brought over is left out, and is then uploaded and
    run like any other.
    """
    lines = [f"mkdir -p {_remote_quote(own_output_dir)} {_remote_quote(f'{own_output_dir}/{COMPLETE_DIRNAME}')}"
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
        # The seal travels with the output, so a later resume of *this* directory can adopt the well in turn.
        pairs.append((f"{src}/{COMPLETE_DIRNAME}", f"{well}.json", f"{own_output_dir}/{COMPLETE_DIRNAME}"))
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
                         log: Callable[[str], None], also_own: tuple[str, ...] = (), blanks: list = ()) -> int:
    """Put every archive the remote already holds into the directory this run uploads it to.

    Each batch's wells go to ``b<i>``; the empty-well blanks go to ``bg/``, which every batch's correction reads
    and which a resume, in a new run directory, would otherwise fill by sending the blank again. Returns how many
    were placed. A batch archive in this run's own directory is moved; anything else is hard-linked or copied — a
    blank always, since the copy it is taken from may be the one a batch directory still needs. rsync's quick check
    then decides, per archive, whether anything is still to send.
    """
    lines, placed, placed_names = [], 0, []
    groups = [(f"{own_run_dir}/b{i}", batch, True) for i, batch in enumerate(batches, start=1)]
    if blanks:
        groups.append((f"{own_run_dir}/bg", list(blanks), False))
    for dest, members, may_move in groups:
        for zip_path in members:
            copies = found.inputs.get(zip_path.name) or []
            if not copies:
                continue
            src = copies[0]
            placed += 1
            placed_names.append(zip_path.name)
            if src == f"{dest}/{zip_path.name}":
                continue                 # already where this batch is uploaded
            q_src, q_dest = _remote_quote(src), _remote_quote(dest)
            mine = may_move and any(src.startswith(d + "/") for d in (own_run_dir, *also_own))
            move = (f"mv -f {q_src} {q_dest}/" if mine else
                    f"{{ ln -f {q_src} {q_dest}/ 2>/dev/null || cp -p {q_src} {q_dest}/; }}")
            lines.append(f"mkdir -p {q_dest} && {move} || true")
    if lines:
        result = cache._ssh_script("\n".join(lines) + "\n", check=False)
        if result is None or getattr(result, "returncode", 1) != 0:
            return 0
    for name in placed_names:
        # The upload line the plate paints from: the archive is on the remote. A copy rsync then finds different is
        # sent again, and its own transfer line repaints the well as it goes.
        log(f"[remote] ↑ {name} already on the remote — not sent again\n")
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
    remaining = [z for z in zips if canonical_well_label(z.stem) not in adopted]
    if getattr(run, "reattached", False):
        report_resumable_checkpoints(runner, run, [canonical_well_label(z.stem) or z.stem for z in remaining],
                                     scratch=_recorded_scratch(earlier, own_output),
                                     settings_stamps=_settings_stamps(_remote_argv(proc) + _blank_argv(proc)),
                                     log=proc._log)
    return remaining, sorted(adopted)


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
        if state in ("analysed", "not-fetched") and well not in found.finished and well not in found.part_way:
            # (A part-way well's output IS there; `find_leftovers` has already said why it is run again.)
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
    """Paint each adopted well finished, now; hold its download until the job has its first batch.

    The completion line goes to the progress count at once, so the plate shows every well's state before anything
    else happens. The download waits for :func:`download_adopted`: a resumed run's compute is the long part, so the
    scheduler is asked for a node and handed its first batch before the earlier attempt's output is drained — that
    transfer then fills the queue wait and the compute instead of delaying both, and does not share the link with
    the first batch's upload.
    """
    for well in adopted:
        proc._line_q.put(remote_done_marker(well))
    proc._adopted_pending = list(adopted)


def download_adopted(proc) -> None:
    """Hand the adopted wells to the Fetcher, once. Called when batch 1 is handed to the job, and again (a no-op by
    then) as the run winds down, so a run that launched nothing — or stopped before batch 1 — still drains them.

    For a nanowell run the release that frees them is queued behind them as well — these wells are in no batch of
    this run, so no later re-anchoring re-runs them.
    """
    import functools

    from services.remote_fetch_nanowells import release_batch

    adopted, proc._adopted_pending = list(getattr(proc, "_adopted_pending", ()) or ()), []
    for well in adopted:
        proc._fetcher.observe(remote_done_marker(well))
    if adopted and proc._fetcher.run.nanowell:
        proc._fetcher.after_queued(functools.partial(release_batch, wells=adopted, log=proc._log))


def stage_leftovers(proc, run_dir: str) -> None:
    """Put the remote's archives into this run's batch directories, before ``in/`` is cleared."""
    found, batches = getattr(proc, "_leftovers", None), getattr(proc, "_batches", None) or []
    blank_stems = getattr(proc, "_blank_stems", ())
    blanks = [z for z in getattr(getattr(proc, "_zipped", None), "well_zips", ()) if z.stem in blank_stems]
    if found is None or not found.inputs or not (batches or blanks):
        return
    out = proc._runner._run.remote_output_dir
    stage_from_leftovers(proc._runner._cache, batches, found, own_run_dir=run_dir, log=proc._log,
                         also_own=(run_dir_of(out),) if run_dir_of(out) != run_dir else (), blanks=blanks)


def reattach_output_dir(runner, local_output_dir, keys: dict, legacy_digests, *, fresh: str, forced: bool,
                        log: Callable[[str], None]) -> str:
    """The remote output directory a batched run writes into: the earlier attempt's, when it can be resumed.

    The pipeline keys a well's part-way checkpoints on its output directory (``pipeline.fov_checkpoint``), so a
    resume that writes anywhere else starts every half-finished well again — and every resume would, because its
    staged archives, and so its run id and directory, differ from the attempt it resumes. So the newest address the
    journal recorded under the same settings, source images and host (the current and the earlier builds' digests,
    ``remote_run_id.legacy_argv_digests``) is used when the remote still has it — the same rule the single-shot
    path re-attaches by. Never for a forced run, which asked for everything to be computed again. Returns *fresh*
    whenever anything is in doubt: a lookup or a probe that fails costs a recomputed well, never a wrong one.
    """
    from services import run_journal

    if forced:
        return fresh
    try:
        earlier = run_journal.stored_remote_addresses(
            local_output_dir, argv_digests=[keys["argv_digest"], *legacy_digests],
            source_key=keys["source_key"], host_alias=keys["host_alias"])
    except OSError:
        return fresh
    for addr in earlier:
        out = str(addr.get("remote_output_dir") or "")
        if not out or out == fresh:
            return fresh                  # the newest match is this run's own directory
        probe = runner._cache._ssh_run(["test", "-d", out], check=False)
        if probe is not None and getattr(probe, "returncode", 1) == 0:
            log(f"[remote] resuming in the earlier attempt's output directory {out}: its finished wells, and the "
                f"checkpoints of any well it left part-way, carry over.\n")
            return out
    return fresh


# ── the remote scratch: where a well's part-way checkpoints are ──

_SCRATCH_LINE = re.compile(r"Scratch\s{1,4}:\s+(\S.*?)(?:\s+\(= output dir\))?\s*$")


def note_remote_scratch(proc, line: str) -> None:
    """Record, once per run, the scratch base the remote pipeline names on its ``Scratch  :`` line."""
    if getattr(proc, "_scratch_noted", False) or "Scratch" not in line:
        return
    m = _SCRATCH_LINE.search(line)
    if not m:
        return
    proc._scratch_noted = True
    from services import run_journal

    try:
        run_journal.record_remote_scratch(proc._local_output_dir, m.group(1))
    except OSError:
        pass                              # the record is a convenience; the pipeline finds its own scratch


def _recorded_scratch(earlier: list[dict], output_dir: str) -> str:
    """The scratch base the journal recorded for the attempt that wrote into *output_dir*, or ``""``."""
    for addr in earlier:
        if addr.get("remote_output_dir") == output_dir and addr.get("remote_scratch_dir"):
            return str(addr["remote_scratch_dir"])
    return ""


def background_aliases(proc, run_dir: str, has_blank: bool) -> list[str]:
    """``--background_dir_alias`` for every ``bg/`` an earlier attempt of this run read its blank from.

    The blank's path is part of the pipeline's settings digest, and a batched run keeps it in its own run directory,
    which is new on every attempt — so a resume's digest differed from the attempt it resumed, and the pipeline
    pruned that attempt's part-way checkpoints as another configuration's and started those wells again. The
    journal records each attempt's run directory (its input directory's parent), and the re-attached output
    directory names the first; the pipeline accepts a digest computed with any of these in place of its own.
    """
    from services import run_journal

    keys, run = getattr(proc, "_journal_keys", None), proc._runner._run
    if not has_blank or keys is None:
        return []
    try:
        attempts = run_journal.recorded_remote_attempts(
            proc._local_output_dir, argv_digests=[keys["argv_digest"], *getattr(proc, "_legacy_digests", ())],
            source_key=keys["source_key"], host_alias=keys["host_alias"])
    except OSError:
        attempts = []
    roots = [run_dir_of(run.remote_output_dir)] + [
        run_dir_of(str(a[k])) for a in attempts for k in ("remote_input_dir", "remote_output_dir") if a.get(k)]
    own, dirs = f"{run_dir}/bg", []
    for root in roots:
        if f"{root}/bg" != own and f"{root}/bg" not in dirs:
            dirs.append(f"{root}/bg")
    return [f"--background_dir_alias={d}" for d in dirs]


def _remote_argv(proc) -> list[str]:
    """The pipeline arguments as the host receives them: the local argv plus the flags the host adds."""
    argv = list(getattr(proc, "_pipeline_argv", []) or [])
    extras = getattr(proc._runner, "_host_argv_extras", None)
    if callable(extras):
        try:
            argv += list(extras(getattr(proc, "_output_filter", None)))
        except (AttributeError, TypeError, ValueError):
            pass
    return argv


def _blank_argv(proc) -> list[str]:
    """The blank's ``--background_dir`` and its earlier paths, as ``_start_driver`` will pass them — part of the
    settings the checkpoints were stamped with."""
    from services.remote_run_id import remote_run_dir

    if not getattr(proc, "_blank_stems", ()):
        return []
    run_dir = remote_run_dir(proc._runner.host, proc._run_id)
    return [f"--background_dir={run_dir}/bg", *background_aliases(proc, run_dir, True)]


def _settings_stamps(pipeline_argv: list[str]) -> tuple[str, ...]:
    """The settings stamps this run's checkpoint directories may carry — the current digest's and the earlier
    builds' — derived the way the pipeline derives its settings key, or ``()`` when the argv cannot be parsed."""
    import hashlib

    from pipeline.cli_args import build_parser
    from util.settings_digest import settings_key_aliases, settings_key_for

    try:
        start = next(i for i, a in enumerate(pipeline_argv) if str(a).startswith("--"))
        opts = vars(build_parser().parse_known_args([str(a) for a in pipeline_argv[start:]])[0])
    except (StopIteration, SystemExit, ValueError):
        return ()
    return tuple(hashlib.blake2b(k.encode(), digest_size=4).hexdigest()
                 for k in settings_key_aliases(settings_key_for(opts)))


def report_resumable_checkpoints(runner, run, wells: list[str], *, scratch: str, log: Callable[[str], None],
                                 settings_stamps: tuple[str, ...] = ()) -> None:
    """Say, before anything runs, which wells left part-way will continue from their checkpoints.

    The pipeline names a well's staging directory ``_tmp_images_<output stamp>_<settings stamp>_<well>`` under its
    scratch base (``pipeline.fov_checkpoint._staging_dir_for``); the output stamp hashes the output directory's
    resolved path, which only the host can resolve, so it is asked for (``pwd -P``) rather than guessed. The scratch
    base is the one the journal recorded, else what the host's configuration gives the pipeline. Informational: a
    probe that fails says nothing, and the pipeline decides for itself.
    """
    import hashlib

    if not wells:
        return
    base = scratch or (runner.host.remote_scratch_dir or "~/.allwell/scratch")
    probe = runner._cache._ssh_script(
        f"cd {_remote_quote(run.remote_output_dir)} 2>/dev/null && pwd -P\n", check=False)
    resolved = ((probe.stdout or "").strip().splitlines() or [""])[-1] if probe is not None else ""
    if not resolved:
        return
    stamp = hashlib.blake2b(resolved.encode(), digest_size=6).hexdigest()
    where = base if base.startswith("$") else _remote_quote(base)     # `$TMPDIR` must expand; a path must not split
    listing = runner._cache._ssh_script(
        f"cd {where} 2>/dev/null || exit 0\n"
        f"for d in _tmp_images_{stamp}_*; do\n"
        f"  [ -d \"$d\" ] || continue\n"
        f"  echo \"$d $(ls -A \"$d\" | grep -c '^\\.fov_.*\\.done$')\"\n"
        f"done\n", check=False)
    counts: dict[str, int] = {}
    for line in ((listing.stdout or "") if listing is not None else "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            if settings_stamps and not any(parts[0].startswith(f"_tmp_images_{stamp}_{s}_") for s in settings_stamps):
                continue                  # another configuration's, which this run's pipeline prunes rather than reads
            well = parts[0].rsplit("_", 1)[-1]
            counts[well] = counts.get(well, 0) + int(parts[-1])
    resumable = {w: n for w, n in counts.items() if n and w in set(wells)}
    if resumable:
        log(f"[remote] {len(resumable)} well(s) left part-way will continue from their checkpoints in {base}: "
            + ", ".join(f"{w} ({n} field group(s) done)" for w, n in sorted(resumable.items())) + "\n")
    else:
        log(f"[remote] no part-way checkpoints for this plate in {base} — wells left part-way start again.\n")
