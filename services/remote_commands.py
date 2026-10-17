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
_BATCH_DONE_RE = re.compile(
    r"\[remote\] batch-driver: batch (\d+) finished rc=(-?\d+)")


def _batch_driver_snippet(run_dir: str, n_batches: int, argv: list[str],
                          heartbeat: str = "",
                          per_batch_timeout_s: int = 0,
                          link_blanks: tuple[str, ...] = (),
                          suspend_request: str = "") -> str:
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
    :data:`UPLOAD_WAIT_TIMEOUT_S`. Both exit :data:`_UPLOAD_WAIT_EXIT` so they
    read as that rather than as a pipeline crash.

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
    """
    pipeline = _remote_join(argv)
    waits = int(UPLOAD_WAIT_TIMEOUT_S // UPLOAD_WAIT_POLL_S)
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
    budget = f"timeout {int(per_batch_timeout_s)} " if per_batch_timeout_s else ""
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
    # A subshell in the background, killed as soon as the batch returns: its
    # `exit` ends only itself, never the driver. Killing it does not kill the
    # `sleep` it is waiting on, which then lived out its poll still holding the
    # driver's stdout — so whoever reads that stream (the app's ssh) saw it end
    # up to a whole poll after the driver had. The sleep writes nothing, so it
    # is given no stdout to hold.
    watch_on = (f'( {watch_init}while sleep {MID_BATCH_WATCH_POLL_S} >/dev/null 2>&1; do '
                f'{watch}done ) & _aw_wd=$!; ') if watch else ""
    watch_off = ('kill "$_aw_wd" 2>/dev/null; '
                 if watch else "")
    return (
        f'_aw_i=1; '
        f'while [ "$_aw_i" -le {int(n_batches)} ]; do '
        f'{suspended}'
        f'echo "[remote] batch-driver: waiting for batch $_aw_i of '
        f'{int(n_batches)}"; '
        f'_aw_w=0; {hb_init}'
        f'while [ ! -f {ready} ]; do '
        f'{suspended}'
        f'_aw_w=$((_aw_w+1)); '
        f'{stale}'
        f'if [ "$_aw_w" -gt {waits} ]; then '
        f'echo "[remote] ERROR batch $_aw_i never arrived"; '
        f'exit {_UPLOAD_WAIT_EXIT}; fi; '
        f'sleep {UPLOAD_WAIT_POLL_S}; done; '
        # Swap this batch's staged directory into the place the pipeline
        # reads. `mv` within one filesystem is atomic and instant, so the
        # pipeline cannot see a partially-populated input dir, and it cannot
        # see the previous batch's leftovers either — the whole directory is
        # replaced rather than being emptied well by well as downloads land.
        f'rm -rf {in_q} && mv {staged} {in_q} || {{ '
        f'echo "[remote] ERROR batch $_aw_i: could not stage its input"; '
        f'echo "90" > {done}; echo "{marker_fail}"; '
        f'_aw_i=$((_aw_i+1)); continue; }}; '
        f'{blank_link}'
        f'echo "[remote] batch-driver: batch $_aw_i starting"; '
        f'{watch_on}{budget}{pipeline}; _aw_rc=$?; {watch_off}'
        f'echo "$_aw_rc" > {done}; '
        f'echo "{marker}"; '
        f'_aw_i=$((_aw_i+1)); done; '
        f'echo "[remote] batch-driver: all {int(n_batches)} batch(es) done"'
    )


def _compose_driver_cmd(
    launcher: str,
    remote_pidfile: str,
    driver_script: str,
    *,
    env_selector: EnvSelector = DEFAULT_ENV_SELECTOR,
    scratch_dir: str = "",
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
    return _launch(launcher, inner, remote_pidfile)


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
    return _launch(launcher, inner, remote_pidfile)


def _launch(launcher: str, inner: str, remote_pidfile: str) -> str:
    """``<launcher> bash -lc <inner>`` for the login shell, PID recorded.

    An ``srun`` launcher goes through :func:`services.remote_srun_retry.
    wrap_srun_launch`, which prefers nodes that are powered on and retries a
    node that fails to boot; that wrapper records ``srun``'s own PID. Any other
    launcher is exec'd as it always was, the login shell's PID recorded first.

    ``shlex.split`` → ``join`` normalises whitespace and re-quotes any token
    carrying a shell metacharacter (``--constraint=a|b``), so the login shell
    passes it to the scheduler literally instead of reading ``|`` as a pipe.
    """
    if is_srun(launcher):
        stray = stray_words_message(launcher)
        if stray:
            # Refused here rather than sent: the job would queue, wait for a
            # node, and only then fail with srun's bare "execve(): …: No such
            # file or directory". A saved host is not re-checked by the dialog.
            raise ValueError(stray)
        return wrap_srun_launch(
            launcher, shlex.quote(inner), _remote_quote(remote_pidfile),
            _remote_quote(f"{remote_pidfile}.srun-err"))
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
