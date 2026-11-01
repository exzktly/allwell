"""Job ids and step ids, for the code that cancels or asks about "the run's job".

A run's job id is written to ``<pidfile>.slurm`` from inside its allocation, and four readers act on it: the
end-of-run close (:func:`services.remote_jobs.close_remote_job`), the liveness probe, the orphan sweep and the
clean-up of earlier attempts. Each used to assume the id names a job the run owns outright, so cancelling it was
the right way to end the run.

A run on a **granted node** (``services.remote_hold``) is not that. It is a job *step* of the hold job, and the
sidecar names the step, ``<job>.<step>``. Two things follow, and this module is where both are written down once:

* **A step is cancelled and asked about as a step.** ``scancel 4001.3`` ends the step and leaves the job;
  ``squeue -j 4001.3`` is an error ("Invalid job id"), which every reader above reads as "the job is finished",
  so the step is asked about with ``squeue -s`` instead (:func:`squeue_listing`).
* **The hold job itself is never cancelled by a run's teardown.** If a sidecar ever names a bare hold job id (a
  step launch that wrote ``$SLURM_JOB_ID`` and not the step), cancelling it would release the node the user is
  keeping, silently: the next run would simply queue. :func:`hold_check_shell` defines the shell test every
  canceller asks first, and the one place a hold is cancelled on purpose is ``services.remote_hold`` (Release).

Qt-free, and imports nothing from ``services.remote_hold``, which imports it.
"""

from __future__ import annotations

import shlex

from services.remote_transport import _remote_quote

#: Where holds live, under the host's staging directory (``services.remote_hold`` owns the contents).
HOLDS_DIRNAME = "holds"


def is_step(job_id: str) -> bool:
    """Whether *job_id* is ``<job>.<step>`` rather than a bare job id."""
    return "." in job_id


def job_of(job_id: str) -> str:
    """The job part of *job_id*: ``4001`` for ``4001.3`` and for ``4001``."""
    return job_id.split(".", 1)[0]


def is_step_launcher(launcher: str) -> bool:
    """Whether *launcher* starts a step inside an allocation that already exists (``srun --jobid=<id> …``), which is
    how a run is started on a granted node (:func:`services.remote_hold.step_launcher`)."""
    try:
        tokens = shlex.split(launcher or "")
    except ValueError:
        return False
    return any(tok == "--jobid" or tok.startswith("--jobid=") for tok in tokens)


def launcher_job_id(launcher: str) -> str:
    """The job id a step launcher names, ``""`` when it names none."""
    try:
        tokens = shlex.split(launcher or "")
    except ValueError:
        return ""
    for i, tok in enumerate(tokens):
        if tok.startswith("--jobid="):
            return tok.split("=", 1)[1]
        if tok == "--jobid" and i + 1 < len(tokens):
            return tokens[i + 1]
    return ""


def sidecar_id_shell(launcher: str) -> str:
    """The shell expression a job writes to ``<pidfile>.slurm`` from inside its allocation: the job id, or for a
    step launcher ``<job>.<step>``, so everything that cancels or asks about "the run's job" acts on the step.
    Chosen by the launcher, not by whether ``SLURM_STEP_ID`` happens to be set: an ordinary ``srun`` sets it too, and
    its sidecar must not change."""
    return "${SLURM_JOB_ID:-}.${SLURM_STEP_ID:-}" if is_step_launcher(launcher) else "${SLURM_JOB_ID:-}"


def squeue_listing(jid_var: str, fmt: str = "%T") -> str:
    """A shell command (a brace group, so a caller can add redirections and a pipe, and can put it straight after ``$(``) printing what ``squeue`` says of
    the job or step named by the shell variable *jid_var*.

    A job is asked about with ``squeue -h -j <job> -o <fmt>``. A step is asked about with ``squeue -h -s<job>.<step>
    -o %i`` and prints its own id while it exists (and nothing once it has ended); a step has no state column to
    print, so a caller that compares the output with job states treats any non-empty answer for a step as alive
    (:func:`is_step`). ``squeue``'s exit status is the command's, so a caller that tells "invalid job id" from "could
    not ask" still can. The ``-s`` spelling (option and value in one word) is the one the manual requires.

    **Each ``case`` pattern opens with ``(``.** Every caller puts this inside ``$( … )``, and bash before 5.2
    finds the end of a command substitution by counting parentheses: a bare pattern's ``)`` closes the
    ``$(`` early, and bash 3.2 (macOS's) then fails the whole script with ``syntax error near unexpected
    token `;;'`` — which is how the Storage browser's first listing on such a host failed. The leading ``(``
    is POSIX and balances the count. ``tests/test_remote_scripts_parse_on_old_bash.py`` holds it.
    """
    v = f'"${jid_var}"'
    return f'{{ case {v} in (*.*) squeue -h -s{v} -o %i;; (*) squeue -h -j {v} -o {fmt};; esac; }}'


def hold_check_shell(staging_dir: str) -> str:
    """Shell that defines ``_aw_is_hold ID``: status 0 when ID is one a run's teardown must not cancel — the job id
    of a hold under *staging_dir*, bare or as the hold's ``batch`` / ``extern`` step — and 1 for anything else,
    including a numeric step of a hold (``4001.3``: that is a run's step, and ending it is exactly right).

    A hold is recognised by its directory's ``job`` file (``services.remote_hold.HoldPaths.job``), which stays
    after the hold ends; asking about a finished hold's id only declines a cancel that would have done nothing.
    """
    holds = _remote_quote(f"{staging_dir}/{HOLDS_DIRNAME}")
    return (
        '_aw_is_hold() { _aw_hj=${1%%.*}; _aw_hs=${1#"$_aw_hj"}; '
        f'for _aw_hf in {holds}/*/job; do [ -e "$_aw_hf" ] || continue; '
        'if [ "$(cat "$_aw_hf" 2>/dev/null)" = "$_aw_hj" ]; then '
        'case "$_aw_hs" in .[0-9]*) return 1;; *) return 0;; esac; fi; done; return 1; }; '
    )


def closed_words(marker: str, job_id: str) -> str:
    """The log line for what ``services.remote_jobs.close_remote_job``'s shell reported about *job_id*: ``AW_HOLD``
    (the sidecar named a granted node's own job, which was left granted) or ``AW_SCANCELLED`` (a step, or a job)."""
    if marker == "AW_HOLD":
        return (f"[remote] scheduler: {job_id} is the granted node's own job, not a step of it — left granted; the "
                "run's process was stopped instead.\n")
    if is_step(job_id):
        return f"[remote] scheduler: ended the run's step {job_id}; the granted node stays granted.\n"
    return f"[remote] scheduler: cancelled lingering job {job_id}.\n"
