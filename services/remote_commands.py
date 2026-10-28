"""The shell commands a remote run sends: the launch line and the batch driver.

Pure string composition — no ssh, no I/O — so every quoting decision here can be
tested by reading the string, and the batch driver by running it in ``bash``.
Split out of ``services/remote_runner.py``. Callers import from here; this
module stays below the runner: it imports the transport's quoting and the
conda / wait snippets, never the runner.
"""

from __future__ import annotations

import base64
import gzip
import re
import shlex

from services.remote_conda import (
    _CONDA_ACTIVATE_SNIPPET,
    DEFAULT_ENV_SELECTOR,
    EnvSelector,
    _conda_env_guard,
)
from services.remote_srun_retry import (
    is_srun,
    stray_words_message,
    wrap_srun_launch,
)
from services.remote_transport import _remote_join, _remote_quote
from services.remote_wait_snippets import (
    _UPLOAD_WAIT_EXIT,
    BATCH_BUDGET_GRACE_S,
    BATCH_EXIT_SETTLE_S,
    BATCH_OVER_BUDGET,
    BATCH_STILL_RUNNING,
    BATCH_TICK_S,
    MID_BATCH_HEARTBEAT_STALE_S,
    MID_BATCH_WATCH_POLL_S,
    UPLOAD_WAIT_POLL_S,
    UPLOAD_WAIT_TIMEOUT_S,
    _heartbeat_stale_snippet,
    _wait_for_upload_snippet,
)


# Characters that would end the assignment and start something else. The value
# comes from the user's own remotes.toml, so this is not about a hostile input —
# it is about a typo taking the whole command down with a shell error nobody can
# read back to a config field.
_SCRATCH_FORBIDDEN = ('"', "`", "$(", ";", "&", "|", "<", ">", "\\", "\n", "\r")


def _remote_scratch_export(value: str) -> str:
    """``export ALLWELL_SCRATCH_DIR=…;`` for the remote, or ``""`` when unset.

    This is the one place a path is deliberately **not** run through
    :func:`_remote_quote`. That helper single-quotes, which is right for a path
    the user typed literally and wrong for this one: the values a cluster user
    reaches for are ``$TMPDIR``, ``$SLURM_TMPDIR`` and ``/scratch/$USER``, and
    single quotes would send the literal string ``$TMPDIR`` to the pipeline,
    which would then create a directory named ``$TMPDIR``. (The batch driver hit
    the same class of bug with ``$_aw_i``.)

    So the value is emitted inside double quotes, where ``$VAR`` expands and
    spaces are still safe — and a leading ``~`` is rewritten to ``$HOME`` first,
    because tilde expansion is exactly what double quotes *do* suppress. That
    combination is the whole reason this cannot reuse either existing helper.

    Raises ``ValueError`` on a value carrying shell syntax, rather than
    composing a command that will fail on the remote with an error that names
    nothing the user can act on.
    """
    value = (value or "").strip()
    if not value:
        return ""
    bad = [c for c in _SCRATCH_FORBIDDEN if c in value]
    if bad:
        raise ValueError(
            f"remote_scratch_dir contains shell syntax ({', '.join(bad)!r}) and "
            f"cannot be used as a directory: {value!r}. Use a plain path; "
            f"environment variables such as $TMPDIR or /scratch/$USER are fine."
        )
    if value == "~":
        value = "$HOME"
    elif value.startswith("~/"):
        value = "$HOME/" + value[2:]
    return f'export ALLWELL_SCRATCH_DIR="{value}"; '


# Per-batch handshake files inside the run directory. The local side writes
# ``ready``; the driver writes ``done`` holding that batch's exit code. Names
# are formatted with the 1-based batch number.
BATCH_READY = ".aw_batch_{i}_ready"
BATCH_DONE = ".aw_batch_{i}_done"

# What the driver prints when a batch ends. The local side reads its own
# progress off this rather than polling the remote for the ``done`` file: the
# stream is already being read, so a marker costs no extra ssh and has no poll
# interval to wait out.
BATCH_DONE_MARKER = "[remote] batch-driver: batch {i} finished rc={rc}"
#: The final pass of a batched nanowell run (``_batch_driver_snippet(final_pass=True)``): present beside a batch's
#: ``ready``, it runs the pipeline with ``--nanowell_reanchor_only`` — or, ``_skip``, does not run it at all, when no
#: batch held a well for the pass.
BATCH_REANCHOR = ".aw_batch_{i}_reanchor"
#: Where a batch of a nanowell run holds, under the run directory, the archives of the wells left to the final
#: pass (``--nanowell_hold_dir``).
HOLD_DIR = "keep"
BATCH_SKIP = ".aw_batch_{i}_skip"
#: Written by the driver once batch *i*'s staged directory has been moved into
#: ``in/``: a replayed pass (the ``srun`` wrapper re-running the driver) then
#: finds the batch's input already in place rather than deleting it.
BATCH_STAGED = ".aw_batch_{i}_staged"
_BATCH_DONE_RE = re.compile(
    r"\[remote\] batch-driver: batch (\d+) finished rc=(-?\d+)")


def _batch_driver_snippet(run_dir: str, n_batches: int, argv: list[str],
                          heartbeat: str = "",
                          per_batch_timeout_s: int = 0,
                          link_blanks: tuple[str, ...] = (),
                          suspend_request: str = "",
                          final_pass: bool = False) -> str:
    """A loop that runs the pipeline once per batch **inside one allocation**.

    Batched mode launched every batch separately, so on a scheduler-backed host
    each one queued on its own: an eight-batch plate paid eight queue waits, and
    between them the node was handed back only to be asked for again. An
    allocation is the expensive thing to obtain and the cheap thing to keep.

    So the job holds the node and the batches come to it. For each batch the
    driver waits for that batch's ``ready`` file, runs the pipeline over the
    input directory as it stands, writes the exit code into the matching
    ``done`` file, and prints :data:`BATCH_DONE_MARKER`.

    **A failed batch does not end the run.** The loop carries on and the local
    side collects the codes, so one bad well does not cost the plate its node —
    the end-of-run tally names which batches failed and the run's own exit code
    reflects that any did.

    Two things *do* end it, both meaning "the app is gone rather than slow": a
    heartbeat that stopped advancing, and a batch that never arrived inside
    :data:`UPLOAD_WAIT_TIMEOUT_S` of polls no fresh heartbeat vouched for — a
    beating app is still uploading, however long that takes. Both exit
    :data:`_UPLOAD_WAIT_EXIT` so they read as that rather than as a pipeline
    crash.

    **A batch past its budget is killed, and says so.** *per_batch_timeout_s*
    runs the pipeline under ``timeout -k``; ``timeout``'s process group is the
    batch's, and whatever of it outlives the batch — the real pipeline orphaned
    by a wrapper that died on the SIGTERM — is killed before the next batch
    starts (``remote_wait_snippets.BATCH_BUDGET_GRACE_S``). The driver prints
    ``BATCH_OVER_BUDGET`` before the batch's marker, and the app reports that
    rather than a bare exit code. While a batch runs the driver prints
    ``BATCH_STILL_RUNNING`` every ``BATCH_TICK_S``.

    **A suspend ends it cleanly.** When *suspend_request* exists the driver
    starts no further batch and exits 0, whether it is between batches or
    waiting for one. The batch in flight has the same path as its
    ``--suspend_file`` and winds itself down, so the node is handed back as soon
    as that batch has written what it finished — rather than being held,
    waiting for a ``ready`` file the app has deliberately stopped sending,
    until the heartbeat or the upload deadline gave up on it.

    **An app that vanishes mid-batch suspends the batch.** The heartbeat check
    above only runs between batches, and an app that crashed or was force-quit
    leaves its ssh child running as an orphan, so the allocation used to be held
    for the rest of the batch — hours on a large one — with nothing left to
    fetch the result. A watchdog beside each batch asks the same question, more
    patiently (:data:`MID_BATCH_HEARTBEAT_STALE_S`), and answers a silent app by
    writing *suspend_request*: the batch winds down keeping what it finished, the
    driver then exits, and a later Resume adopts that work from the remote.

    **A nanowell run's final pass** (*final_pass*) is one more batch after the
    *n_batches* data batches. The app stages into it the archives the batches
    held for the end-of-run re-anchoring and flags it :data:`BATCH_REANCHOR`,
    which adds ``--nanowell_reanchor_only`` to the pipeline's argv, or flags it
    :data:`BATCH_SKIP` when no batch held one, and the driver reports it done
    without running anything. The blank is still linked into the last *data*
    batch, which is the one that processes it.
    """
    pipeline = _remote_join(argv)
    waits = max(1, int(UPLOAD_WAIT_TIMEOUT_S // UPLOAD_WAIT_POLL_S))
    hb_init, stale = _heartbeat_stale_snippet(
        heartbeat,
        "[remote] ERROR the app stopped responding — giving the node back")
    # The batch number is a shell variable, so the filename cannot be quoted
    # whole: `_remote_quote` single-quotes, which would leave the loop watching
    # for a file literally called ".aw_batch_$_aw_i_ready". The directory keeps
    # its quoting (it may contain ~ or spaces) and the leaf is appended in
    # double quotes, which bash concatenates. Braces around the variable are
    # load-bearing too — bare `$_aw_i_ready` names a *different*, unset
    # variable, so every batch would wait on the same empty path.
    in_q = _remote_quote(f"{run_dir}/in")
    staged = _remote_quote(run_dir) + '"/b${_aw_i}"'
    marker_fail = BATCH_DONE_MARKER.format(i="$_aw_i", rc="90")
    # One batch may not outlive the budget the operator sized for one batch.
    # Without this a single wedged well holds the node for the whole run's
    # scaled walltime, which is strictly worse than the per-batch launches this
    # replaces — there, a hang cost one batch's allocation and no more.
    # `-k`: the pipeline answers SIGTERM as a suspend and a hung well never
    # reaches the boundary it winds down at, so SIGKILL follows (see
    # `BATCH_BUDGET_GRACE_S`). It runs in the background so the driver knows
    # `timeout`'s pid, which is also the batch's process group (`timeout` makes
    # one of its own): `timeout` returns as soon as its child does, and a
    # `conda run` wrapper that dies on the SIGTERM leaves the real pipeline in
    # that group as an orphan, still writing `out/` — so whatever is left of
    # the group is given the grace and then killed before the next batch.
    budget_s, grace = int(per_batch_timeout_s or 0), int(BATCH_BUDGET_GRACE_S)
    settle_polls = max(1, int(BATCH_EXIT_SETTLE_S * 10))
    over = BATCH_OVER_BUDGET.format(i="$_aw_i")
    # The empty-well blank lives in `bg/` for the whole run so every batch can
    # build the same correction from it without it crossing the wire again. A
    # blank that still needs processing is hard-linked into the final batch to
    # be run once for its own output — `ln` on one filesystem moves no bytes,
    # and `cp` is the fallback where it cannot.
    #
    # Named one by one rather than globbing `bg/*.zip`: on a re-run some blanks
    # may already be complete locally, and those are in `bg/` as a reference
    # only. Linking the whole directory would reprocess a well the user's own
    # re-run just declined.
    blank_link = (
        f'if [ "$_aw_i" -eq {int(n_batches)} ]; then '
        + "".join(
            f'_aw_b={_remote_quote(f"{run_dir}/bg/{stem}.zip")}; '
            f'[ -e "$_aw_b" ] && {{ ln "$_aw_b" {in_q}"/" 2>/dev/null '
            f'|| cp "$_aw_b" {in_q}"/"; }}; '
            for stem in link_blanks)
        + 'fi; '
    ) if link_blanks else ""
    ready = (_remote_quote(run_dir)
             + '"/' + BATCH_READY.format(i="${_aw_i}") + '"')
    done = (_remote_quote(run_dir)
            + '"/' + BATCH_DONE.format(i="${_aw_i}") + '"')
    marker = BATCH_DONE_MARKER.format(i="$_aw_i", rc="$_aw_rc")
    total = int(n_batches) + (1 if final_pass else 0)

    staged_flag = (_remote_quote(run_dir)
                   + '"/' + BATCH_STAGED.format(i="${_aw_i}") + '"')

    def flag(template: str) -> str:
        return _remote_quote(run_dir) + '"/' + template.format(i="${_aw_i}") + '"'

    # The final pass: the same argv with the re-anchoring alone asked for, run only when the app flagged it so —
    # skipped when it flagged nothing was held, and when its flag is missing (a `touch` that failed must not run
    # the whole pipeline over the held wells). `$_aw_x` is unquoted on purpose: empty, it adds no argument at all.
    # A skipped pass says so, and why, before its marker: "nothing was held" and "the flag never arrived" must not
    # read the same as a pass that ran.
    final = (
        f'_aw_x=""; _aw_skip=""; '
        f'if [ -f {flag(BATCH_SKIP)} ]; then _aw_skip="nothing was held for the end-of-run re-anchoring"; '
        f'elif [ "$_aw_i" -eq {total} ] && [ ! -f {flag(BATCH_REANCHOR)} ]; then '
        f'_aw_skip="the end-of-run re-anchoring was not flagged"; fi; '
        f'if [ -n "$_aw_skip" ]; then echo "[remote] batch-driver: batch $_aw_i skipped — $_aw_skip"; '
        f'echo "0" > {done}; '
        f'echo "{BATCH_DONE_MARKER.format(i="$_aw_i", rc="0")}"; _aw_i=$((_aw_i+1)); continue; fi; '
        f'[ -f {flag(BATCH_REANCHOR)} ] && _aw_x="--nanowell_reanchor_only"; '
    ) if final_pass else ""
    tail = " $_aw_x" if final_pass else ""
    suspended = (
        f'if [ -f {_remote_quote(suspend_request)} ]; then '
        f'echo "[remote] batch-driver: suspended — no further batches"; '
        f'exit 0; fi; '
    ) if suspend_request else ""
    watch_init, watch = _heartbeat_stale_snippet(
        heartbeat,
        "[remote] ERROR the app stopped responding mid-batch — giving the node back",
        stale_s=MID_BATCH_HEARTBEAT_STALE_S, poll_s=MID_BATCH_WATCH_POLL_S,
        on_stale=f"touch {_remote_quote(suspend_request)}; exit 0",
    ) if suspend_request else ("", "")
    # A subshell in the background beside every batch, killed as soon as the
    # batch returns: its `exit` ends only itself, never the driver. It is the
    # watchdog (when there is a heartbeat to watch) and the liveness tick
    # (`BATCH_STILL_RUNNING`, always). Killing it does not kill the `sleep` it
    # is waiting on, which then lived out its poll still holding the driver's
    # stdout — so whoever reads that stream (the app's ssh) saw it end up to a
    # whole poll after the driver had. The sleep writes nothing, so it is given
    # no stdout to hold; the subshell keeps its own, for the tick.
    poll = int(MID_BATCH_WATCH_POLL_S)
    tick_every = max(1, int(BATCH_TICK_S) // max(1, poll))
    still = BATCH_STILL_RUNNING.format(i="$_aw_i", m="$(( ($(date +%s) - _aw_t0) / 60 ))")
    watch_on = (f'_aw_t0=$(date +%s); ( _aw_n=0; {watch_init}while sleep {poll} >/dev/null 2>&1; do '
                f'{watch}_aw_n=$((_aw_n+1)); '
                f'if [ $((_aw_n % {tick_every})) -eq 0 ]; then echo "{still}"; fi; '
                f'done ) & _aw_wd=$!; ')
    watch_off = 'kill "$_aw_wd" 2>/dev/null; '
    if budget_s:
        run = (f'timeout -k {grace} {budget_s} {pipeline}{tail} & _aw_tp=$!; wait "$_aw_tp"; _aw_rc=$?; '
               f'{watch_off}'
               # 124 is `timeout`'s own; 137 is its SIGKILL only once the budget has passed (an OOM kill is too).
               f'if [ "$_aw_rc" -eq 124 ] || {{ [ "$_aw_rc" -eq 137 ] && '
               f'[ "$(( $(date +%s) - _aw_t0 ))" -ge {budget_s} ]; }}; then '
               f'echo "{over} ({budget_s}s) — its pipeline was stopped; the wells it had not finished are missing"; '
               f'fi; '
               # The group is given a moment to empty by itself first (`BATCH_EXIT_SETTLE_S`): a pool's resource
               # tracker outlives its pipeline by milliseconds on every clean batch.
               f'_aw_q=0; while [ "$_aw_q" -lt {settle_polls} ] && kill -0 -- -"$_aw_tp" 2>/dev/null; do '
               f'sleep 0.1; _aw_q=$((_aw_q+1)); done; '
               f'_aw_k=0; while kill -0 -- -"$_aw_tp" 2>/dev/null; do '
               f'if [ "$_aw_k" -eq 0 ]; then echo "[remote] WARN batch $_aw_i: processes of its pipeline outlived it '
               f'— stopping them before the next batch"; kill -TERM -- -"$_aw_tp" 2>/dev/null; fi; '
               f'if [ "$_aw_k" -ge {grace} ]; then kill -KILL -- -"$_aw_tp" 2>/dev/null; sleep 1; break; fi; '
               f'sleep 1; _aw_k=$((_aw_k+1)); done; ')
    else:
        run = f'{pipeline}{tail}; _aw_rc=$?; {watch_off}'
    return (
        f'_aw_i=1; '
        f'while [ "$_aw_i" -le {total} ]; do '
        f'{suspended}'
        # A batch that already finished in this run is not run again. The app
        # clears every `done` before it launches the driver, so one present here
        # was written by this launch — by a pass the srun wrapper replayed.
        f'if [ -f {done} ]; then _aw_rc=$(cat {done}); '
        f'echo "[remote] batch-driver: batch $_aw_i already finished (rc=$_aw_rc) — not run again"; '
        f'echo "{marker}"; _aw_i=$((_aw_i+1)); continue; fi; '
        f'echo "[remote] batch-driver: waiting for batch $_aw_i of '
        f'{total}"; '
        f'_aw_w=0; _aw_fresh=0; {hb_init}'
        f'while [ ! -f {ready} ]; do '
        f'{suspended}'
        f'{stale}'
        # Only the polls no fresh heartbeat vouched for count towards the deadline: a beating app is uploading.
        f'[ "$_aw_fresh" = 1 ] || _aw_w=$((_aw_w+1)); '
        f'if [ "$_aw_w" -gt {waits} ]; then '
        f'echo "[remote] ERROR batch $_aw_i never arrived — no heartbeat said the app was still sending it"; '
        f'exit {_UPLOAD_WAIT_EXIT}; fi; '
        f'sleep {UPLOAD_WAIT_POLL_S}; done; '
        f'{final}'
        # Swap this batch's staged directory into the place the pipeline
        # reads. `mv` within one filesystem is atomic and instant, so the
        # pipeline cannot see a partially-populated input dir, and it cannot
        # see the previous batch's leftovers either — the whole directory is
        # replaced rather than being emptied well by well as downloads land.
        # `in/` is emptied only for a batch whose staged directory is there to
        # replace it. Without one, `in/` may already be this batch's — moved in
        # by a pass that was replayed (`.aw_batch_<i>_staged` says so) — and
        # removing it would delete the batch's input and, in a final pass, the
        # held archives.
        f'if [ -d {staged} ]; then rm -rf {in_q} && mv {staged} {in_q} '
        f'&& {{ : > {staged_flag} || true; }}; '
        f'elif [ -f {staged_flag} ] && [ -d {in_q} ]; then '
        f'echo "[remote] batch-driver: batch $_aw_i: its input is already in place"; '
        f'else false; fi || {{ '
        f'echo "[remote] ERROR batch $_aw_i: could not stage its input"; '
        f'echo "90" > {done}; echo "{marker_fail}"; '
        f'_aw_i=$((_aw_i+1)); continue; }}; '
        f'{blank_link}'
        f'echo "[remote] batch-driver: batch $_aw_i starting"; '
        f'{watch_on}{run}'
        f'echo "$_aw_rc" > {done}; '
        f'echo "{marker}"; '
        f'_aw_i=$((_aw_i+1)); done; '
        f'echo "[remote] batch-driver: all {total} batch(es) done"'
    )


def _compose_driver_cmd(
    launcher: str,
    remote_pidfile: str,
    driver_script: str,
    *,
    env_selector: EnvSelector = DEFAULT_ENV_SELECTOR,
    scratch_dir: str = "",
    queue_wait_s: int = 0,
) -> str:
    """The login-shell command that puts :func:`_batch_driver_snippet` on a node.

    The same shape as :func:`_compose_remote_cmd` — record the login PID, exec
    the launcher, activate conda and record ``$SLURM_JOB_ID`` inside the
    allocation so both cancel paths keep working — but the thing it ends by
    running is the driver loop rather than a single pipeline invocation.

    A separate function rather than another parameter on ``_compose_remote_cmd``
    because that one's exact output is pinned by two test modules, and because
    the driver has no ``cache_hit`` and no upload sentinel: the batches announce
    themselves one at a time, which is what the loop is for.

    With no *launcher* there is no allocation to hold and nothing to save, so
    the driver runs on the login shell — the batched path is then exactly as
    cheap as it was, and the caller keeps its per-batch behaviour either way.
    """
    pidfile_q = _remote_quote(remote_pidfile)
    launcher = launcher.strip()
    guard = f"{_conda_env_guard(env_selector)}; "
    # Both shapes carry it: a batched run on a launcher-less host still writes
    # the pipeline's working set somewhere, and leaving one shape out would put
    # only *some* runs on the configured volume with nothing saying which.
    scratch = _remote_scratch_export(scratch_dir)
    if not launcher:
        return (
            f"echo $$ > {pidfile_q}; "
            f"{_CONDA_ACTIVATE_SNIPPET}; "
            f"{guard}"
            f"{scratch}"
            f"{driver_script}"
        )
    slurm_sidecar_q = _remote_quote(f"{remote_pidfile}.slurm")
    inner = (
        f"{_CONDA_ACTIVATE_SNIPPET}; "
        f"{guard}"
        f"{scratch}"
        f'echo "${{SLURM_JOB_ID:-}}" > {slurm_sidecar_q} 2>/dev/null || true; '
        f'echo "[remote] scheduler: allocation granted — job ${{SLURM_JOB_ID:-?}} '
        f'on ${{SLURMD_NODENAME:-$(hostname)}} '
        f'(partition ${{SLURM_JOB_PARTITION:-?}}) — held for every batch"; '
        f"{driver_script}"
    )
    return _launch(launcher, inner, remote_pidfile, queue_wait_s=queue_wait_s)


def _compose_remote_cmd(
    launcher: str,
    remote_pidfile: str,
    remote_argv: list[str],
    *,
    cache_hit: bool,
    env_selector: EnvSelector = DEFAULT_ENV_SELECTOR,
    upload_sentinel: str = "",
    upload_heartbeat: str = "",
    scratch_dir: str = "",
    queue_wait_s: int = 0,
) -> str:
    """Build the bash command string ssh runs on the remote login node.

    Pure (no IO) so it can be unit-tested without a live ssh. Two shapes:

    **Direct execution** (``launcher`` empty, or ``cache_hit`` — never
    allocate a compute node just to run the ``true`` no-op): the login
    shell records its own PID, activates conda, and execs the pipeline.
    This is byte-identical to the pre-``compute_launcher`` behaviour::

        echo $$ > <pidfile>; <conda activate>; exec <pipeline argv>

    **Scheduled execution** (``launcher`` set, e.g. an ``srun …`` line,
    and not a cache hit): the login shell records its PID then *execs the
    launcher*, which places a fresh allocation on a compute node the
    cluster schedules. Conda activation and the pipeline run **inside**
    that allocation (``bash -lc``) — that's where the env, the shared-FS
    conda install, and any requested GPU live. The job's own
    ``$SLURM_JOB_ID`` is written to ``<pidfile>.slurm`` (on the shared FS,
    so the login-side ``cancel()`` can ``scancel`` it as a second kill
    channel)::

        echo $$ > <pidfile>; exec srun … bash -lc '<conda>; echo $JOBID > <pidfile>.slurm; exec <pipeline argv>'

    The inner script is single-quoted for the *outer* login shell (via
    ``shlex.quote``) so the outer shell passes it through literally and
    the inner ``bash`` is the one that expands ``$HOME`` / ``$SLURM_JOB_ID``
    on the compute node.

    Both shapes check that *env_selector* actually names an environment before
    reaching ``conda run`` (:func:`_conda_env_guard`), so a host that was never
    bootstrapped — or one whose **Remote python** points somewhere that no
    longer exists — fails with a labelled, actionable message instead of
    conda's bare ``EnvironmentLocationNotFound``. A cache hit runs ``true`` and
    needs no environment at all, so it skips the check rather than failing a
    run that was never going to import anything.
    """
    pidfile_q = _remote_quote(remote_pidfile)
    launcher = launcher.strip()
    guard = "" if cache_hit else f"{_conda_env_guard(env_selector)}; "
    # Where the pipeline puts its transient working set on the remote. A cache
    # hit execs `true` and writes nothing, so it needs no scratch at all.
    scratch = "" if cache_hit else _remote_scratch_export(scratch_dir)
    if cache_hit or not launcher:
        # PID echoed first so the cancel path can target the pipeline
        # directly; conda activated on the login shell; then exec.
        return (
            f"echo $$ > {pidfile_q}; "
            f"{_CONDA_ACTIVATE_SNIPPET}; "
            f"{guard}"
            f"{scratch}"
            f"exec {_remote_join(remote_argv)}"
        )

    slurm_sidecar_q = _remote_quote(f"{remote_pidfile}.slurm")
    # The ``[remote] scheduler: …`` echo runs *inside* the allocation, so it
    # only prints once the scheduler has granted a node — it streams back over
    # the same ssh stdout and is the user's "queue wait over, now running on
    # <node>" signal, naming the job id and partition for diagnosis.
    # The wait goes first, before conda and before the pipeline: the job may
    # have been granted its node while the plate was still uploading, which is
    # the point of requesting it early.
    wait_snippet = (_wait_for_upload_snippet(upload_sentinel, upload_heartbeat)
                    if upload_sentinel else "")
    inner = (
        f"{_CONDA_ACTIVATE_SNIPPET}; "
        f"{guard}"
        f"{scratch}"
        f'echo "${{SLURM_JOB_ID:-}}" > {slurm_sidecar_q} 2>/dev/null || true; '
        f'echo "[remote] scheduler: allocation granted — job ${{SLURM_JOB_ID:-?}} '
        f'on ${{SLURMD_NODENAME:-$(hostname)}} '
        f'(partition ${{SLURM_JOB_PARTITION:-?}})"; '
        f"{wait_snippet}"
        f"exec {_remote_join(remote_argv)}"
    )
    return _launch(launcher, inner, remote_pidfile, queue_wait_s=queue_wait_s)


#: The first thing a launched job script does: any copy but the scheduler's first task leaves before it touches the
#: run. The run is one process, and every copy a launch starts runs the whole script — five batch drivers over one
#: run directory raced for batch 1's staged input, and the losers deleted the winner's (``remote_srun_retry.
#: one_task``, which asks ``srun`` for one task, is the fix; this is the backstop for a launcher that is not
#: ``srun``, or a cluster that starts more anyway). The first task says how many it was started beside.
_FIRST_TASK_ONLY = (
    'if [ "${SLURM_PROCID:-0}" != 0 ]; then exit 0; fi; '
    'if [ "${SLURM_STEP_NUM_TASKS:-1}" -gt 1 ] 2>/dev/null; then '
    'echo "[remote] scheduler: WARNING the launch started $SLURM_STEP_NUM_TASKS copies of the job; only the first '
    'runs it and the others exit — ask the scheduler for one task"; fi; '
)


def _launch(launcher: str, inner: str, remote_pidfile: str, *, queue_wait_s: int = 0) -> str:
    """``<launcher> bash -lc <inner>`` for the login shell, PID recorded.

    An ``srun`` launcher goes through :func:`services.remote_srun_retry.
    wrap_srun_launch`, which prefers nodes that are powered on, retries a
    node that fails to boot and gives up after *queue_wait_s* without a node
    (the host's Max queue wait; ``0`` waits indefinitely); that wrapper records
    ``srun``'s own PID. Any other launcher is exec'd as it always was, the login
    shell's PID recorded first, and waits as long as its scheduler makes it.

    ``shlex.split`` → ``join`` normalises whitespace and re-quotes any token
    carrying a shell metacharacter (``--constraint=a|b``), so the login shell
    passes it to the scheduler literally instead of reading ``|`` as a pipe.

    *inner* runs behind :data:`_FIRST_TASK_ONLY`, whatever the launcher.
    """
    inner = _FIRST_TASK_ONLY + inner
    if is_srun(launcher):
        stray = stray_words_message(launcher)
        if stray:
            # Refused here rather than sent: the job would queue, wait for a
            # node, and only then fail with srun's bare "execve(): …: No such
            # file or directory". A saved host is not re-checked by the dialog.
            raise ValueError(stray)
        return wrap_srun_launch(
            launcher, shlex.quote(inner), _remote_quote(remote_pidfile),
            _remote_quote(f"{remote_pidfile}.srun-err"), queue_wait_s=queue_wait_s)
    return (
        f"echo $$ > {_remote_quote(remote_pidfile)}; "
        f"exec {shlex.join(shlex.split(launcher))} bash -lc {shlex.quote(inner)}"
    )


#: What the login shell prints, and the code it exits with, when the packed
#: launch command cannot be unpacked (no ``base64`` or ``gzip`` on the remote).
UNPACK_FAILED_EXIT = 97

_PACKED_RE = re.compile(r"printf %s ([A-Za-z0-9+/=]+) \| base64 -d \| gzip -dc")


def pack_for_login_shell(script: str) -> str:
    """*script* as a short, quote-free command the login shell unpacks and runs.

    The launch command is passed to ``ssh`` as one argument on the local
    command line, and on Windows that line goes through ``list2cmdline`` and
    then Git for Windows' ``ssh.exe``, which parses it again by its own rules
    before sending it. The scripts grew past 8,000 characters — the conda guard,
    the ``srun`` retry wrapper and, for a batched run, the driver loop — with
    hundreds of quotes and seventeen lines, and a batched launch from Windows
    then failed with ``bash: -c: line 17: syntax error: unexpected end of
    file``: the last line, which holds most of the script, arrived with its
    quoting unbalanced. A command that is short and contains only base64
    characters cannot be truncated or re-quoted on the way.

    The script is gzipped (a 9,000-character launch packs to about 3,500) and
    run by ``eval`` in the login shell itself, so ``$$`` is still the login
    shell's PID and ``exec`` still replaces it — the pidfile and the
    ``kill -TERM`` cancel channel are unchanged. If ``base64`` or ``gzip`` is
    missing the shell says so and exits :data:`UNPACK_FAILED_EXIT` rather than
    evaluating nothing and exiting 0, which would read as a run that succeeded.
    """
    blob = base64.b64encode(
        gzip.compress(script.encode("utf-8"), mtime=0)).decode("ascii")
    return (
        f"if _aw_s=$(printf %s {blob} | base64 -d | gzip -dc) "
        '&& [ -n "$_aw_s" ]; then eval "$_aw_s"; else '
        'echo "[remote] ERROR could not unpack the launch command — the remote '
        'needs base64 and gzip"; '
        f"exit {UNPACK_FAILED_EXIT}; fi"
    )


def unpack_login_shell_command(command: str) -> str:
    """The script :func:`pack_for_login_shell` packed into *command*.

    For tests and for diagnosis: the packed form is unreadable in a log or an
    argv, so anything that needs to see what the remote runs reads it here.
    """
    m = _PACKED_RE.search(command)
    if m is None:
        raise ValueError("not a packed launch command")
    return gzip.decompress(base64.b64decode(m.group(1))).decode("utf-8")


def batch_pipeline_argv(remote_argv) -> list[str]:
    """The pipeline argv every batch of a batched remote run is given: the run's own, with what a slice of a plate
    needs added.

    * ``--no_auto_threshold``: each batch sees only its own slice, so the in-pipeline auto-threshold pass would write
      defaults from a fraction of the plate; it is re-run once locally over everything.
    * ``--only_wells_absent_ok`` when the run names wells: the same argv runs every batch, so its ``--only_wells``
      names the whole subset while each batch's input holds only its share, and the pipeline refused the other
      batches' wells as "not in the input" — every batch, so the run failed after a clean upload. Every named well
      was checked against the plate before anything was uploaded (``pipeline_runner.scope_staged_to_only_wells``).
    """
    argv = list(remote_argv)
    if "--no_auto_threshold" not in argv:
        argv.append("--no_auto_threshold")
    if "--only_wells" in argv and "--only_wells_absent_ok" not in argv:
        argv.append("--only_wells_absent_ok")
    return argv
