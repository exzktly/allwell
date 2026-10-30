"""Launch a remote run through ``srun`` on a node that is awake, and retry one
that fails to boot.

**Why a node can be granted and then fail.** On a cluster that powers idle
nodes down to save energy (``sinfo`` shows them as ``idle~``), Slurm can grant a
job a node that is asleep and then boot it. ``srun`` waits for the boot —
printing ``srun: error: Nodes <node> are still not ready`` while it does — but
only for as long as the cluster's ``ResumeTimeout`` allows. Past that it prints
``srun: error: Something is wrong with the boot of the nodes``, gives the
allocation back and exits; Slurm marks the node ``DOWN`` and the job ends
``NODE_FAIL``. There is no ``srun`` option to wait longer, and Slurm's own
requeueing applies to ``sbatch`` jobs, not to an ``srun`` step, so nothing
retried: the app saw the stream end and reported the allocation lost.

**What the wrapper does**, all on the login node, in the shell ssh runs:

1. **Prefers nodes that are already powered on.** Before each attempt it asks
   ``sinfo`` for the launcher's partition and excludes every node whose state is
   not one of :data:`AWAKE_STATES` — powered down, powering up, down, drained,
   failing. ``alloc`` and ``comp`` count as awake: they are running and will
   free up, and excluding them would leave a job that cannot start at all
   whenever every awake node happens to be busy. If no node is awake it says so
   and lets Slurm boot one. If the awake nodes cannot satisfy the request —
   say, none of them is the instance type a ``--constraint`` names — it finds
   out first with ``srun --test-only``, which queues nothing, says so in plain
   words and drops the state-based exclusion, rather than letting the real
   ``srun`` fail with ``Requested node configuration is not available``, which
   reads as an error. **Awake is not the same as free**: the preference holds
   only while some awake node is ``idle`` (:data:`FREE_STATES`). With every
   awake node busy, excluding the asleep ones left the job queued behind other
   people's jobs — for as long as they ran — while hundreds of powered-down
   nodes that boot in minutes were ruled out. ``mix`` does not count: it says
   some of a node's CPUs are taken and nothing about its memory or GPUs, and a
   second app instance asking for the node shape the first had just been given
   found that node ``mix`` with no room left, excluded every powered-down node
   on the strength of it, and waited for the first run to end.
   **Even an awake node that fits may only fit later.** ``--test-only`` also
   reports when the job would start; when that is more than
   :data:`START_SLACK_S` away, the awake nodes that fit are busy and the
   exclusion is dropped, so Slurm boots one. The time is read with
   ``SLURM_TIME_FORMAT=standard`` and GNU ``date -d``; a login node whose
   ``date`` cannot read it keeps the preference, and the watchdog below is the
   backstop.
   A node's state can change between the check and the start of the job, so a
   real launch that still fails that way is retried once without the
   exclusion, and a boot failure is what the retry below is for.
2. **Gives up waiting on the awake nodes** after :data:`PENDING_FALLBACK_S`.
   While the exclusion is in force a watchdog runs beside ``srun``: if the job
   is still queued then (``srun`` printed ``queued and waiting`` and not
   ``has been allocated``, and ``squeue`` still lists it pending), it is
   cancelled with ``scancel`` — a ``kill -TERM`` of ``srun`` where there is no
   ``scancel`` — and asked for once more without the exclusion, with a
   ``requesting allocation via:`` line so the Analyze progress view shows the
   wait again. A job cancelled while still pending has written nothing: the
   ``<pidfile>.slurm`` sidecar is written from inside the allocation. There is
   a narrow race — the job allocated between ``squeue`` and ``scancel`` — in
   which a job that had just started is cancelled and asked for again; nothing
   is lost but the start.
3. **Retries a boot failure for as long as it takes**, adding the node(s)
   ``srun`` named to ``--exclude`` each time so Slurm picks another. There is no
   retry count: some clusters' nodes routinely take longer to boot, or to be
   granted, than the cluster's ``ResumeTimeout`` allows, and a run that gave up
   after a fixed number of tries failed on exactly those clusters. The one bound
   is the host's **Max queue wait** (below), which a user who wants no bound
   sets to *Wait indefinitely*. Each retry waits :data:`BOOT_RETRY_PAUSE_S`
   first, so a node that fails at once does not turn the loop into a stream of
   requests to the controller, and prints a line saying which node failed and
   every node that has failed so far (the list for the cluster's admins), then a
   ``requesting allocation via:`` line that puts the Analyze progress view back
   into its queue-wait state. When the exclusions have ruled out every node
   that could take the request, Slurm refuses it ("Requested node configuration
   is not available"); the wrapper then forgets the nodes that failed to boot —
   Slurm may have brought them back by now, and a refusal is not a reason to
   stop — and asks again with only the user's own exclusions.

   Because the loop can now run for hours between ``srun`` processes, the
   wrapper writes its **own** PID to the pidfile at the top of every attempt
   and before each pause; the attempt's ``srun`` then overwrites it. So the ``kill -TERM $(cat
   <pidfile>)`` cancel channel stops the loop itself when Stop lands between
   two attempts, rather than finding the previous ``srun`` already gone and
   letting the loop ask for another node.

Exclusions the user wrote into the launcher (``--exclude`` / ``-x``) are kept:
Slurm takes the *last* ``--exclude`` it is given, so the wrapper removes the
user's from the launcher and merges them into the one it passes.

The launch's output is copied to a file beside the pidfile with ``tee``, so the
shell can read what went wrong, and only lines that begin ``srun:`` are searched.
It is ``tee`` and not a filter such as ``awk '/^srun: /'``: ``mawk``, the
default ``awk`` on Debian and Ubuntu login nodes, fills its whole input buffer
before handling a line, so a filter there held the job's first lines back until
more output arrived, and a run that had just been allocated a node sat silent.
``tee`` passes each line on as it arrives. The copy is truncated before each
attempt and removed when the loop ends. Each attempt still
writes ``srun``'s own PID to the pidfile (a ``sh -c … exec``), so the
``kill -TERM $(cat <pidfile>)`` cancel channel keeps reaching ``srun``.

The wrapper is plain POSIX ``sh``: the command ssh runs is interpreted by the
user's login shell, which need not be bash. ``tests/test_srun_boot_retry.py``
runs it under ``sh`` with stand-in ``srun`` and ``sinfo`` programs.
"""

from __future__ import annotations

import re
import shlex

#: How long the wrapper waits before asking again after a node failed to boot. There is no retry count (the
#: host's Max queue wait is the only bound), so this is what keeps a node that fails at once from turning the loop
#: into a stream of requests to the Slurm controller. Short beside a typical boot, which takes minutes.
BOOT_RETRY_PAUSE_S = 30

#: ``sinfo %t`` states of a node that is powered on. Anything else — ``idle~``,
#: ``idle#``, ``down``, ``drain``, ``fail`` … — is excluded while an awake node
#: exists.
AWAKE_STATES = ("idle", "mix", "alloc", "comp")

#: The awake state that reliably has room for a new job now. ``alloc`` and
#: ``comp`` are awake but full: preferring them means waiting for someone else's
#: job to end. ``mix`` is left out on purpose: it means *some* of a node's CPUs
#: are allocated, and says nothing about its memory or GPUs, so a node running
#: one of this user's own runs reads ``mix`` while having no room at all for a
#: second run of the same size — and counting it as free excluded every
#: powered-down node and queued the second run behind the first for as long as
#: the first ran.
FREE_STATES = ("idle",)

#: How far ahead of now the expected start that ``srun --test-only`` reports may
#: be before the powered-on nodes stop being preferred. Booting a powered-down
#: node takes minutes; a start further away than this means the powered-on
#: nodes are busy, and booting one is the faster way to a node.
START_SLACK_S = 120

#: How long a launch restricted to powered-on nodes may sit in the queue before
#: it is cancelled and asked for again without that restriction. The backstop
#: for what neither check above can see: the cluster changing between the check
#: and the launch, or a login node whose ``date`` cannot read Slurm's times.
PENDING_FALLBACK_S = 180

#: What ``srun`` prints when the node it was granted did not boot. ``Node
#: failure on`` is also what it prints when a node dies *during* the job, so it
#: is a boot failure only before :data:`ALLOCATION_GRANTED` has been printed.
_BOOT_FAILURE_RE = "Something is wrong with the boot of the nodes|Node failure on"

#: The line the job prints from inside its allocation, first thing
#: (``services.remote_commands`` composes it). Once it is in the launch's
#: output the run has started: a failure after it is not a boot failure, and
#: the queue wait is over.
ALLOCATION_GRANTED = "[remote] scheduler: allocation granted"
#: What the wrapper says when Slurm refuses the request outright ("Requested node configuration is not
#: available"): no node it could give matches the scheduler command. ``services.run_evidence`` reads it.
NO_NODE_FITS = "[remote] scheduler: ERROR Slurm has no node that can take this request"

#: What the wrapper exits with when :func:`queue_wait_seconds` passed without a
#: node being granted — distinct from ``srun``'s own codes, so the run reports
#: the wait rather than a pipeline failure.
QUEUE_WAIT_EXIT = 76


def queue_wait_seconds(host) -> int:
    """The host's **Max queue wait** in seconds; ``0`` waits indefinitely.

    ``queue_wait_timeout_min`` is the one setting the Remotes dialog offers for
    it, and this is the one place it becomes a deadline (``wrap_srun_launch``).
    """
    try:
        minutes = int(getattr(host, "queue_wait_timeout_min", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, minutes) * 60


def is_srun(launcher: str) -> bool:
    """True when *launcher* runs ``srun`` (the only launcher this wraps)."""
    try:
        tokens = shlex.split(launcher)
    except ValueError:
        return False
    return bool(tokens) and tokens[0].rsplit("/", 1)[-1] == "srun"


#: Words in an ``srun`` launcher that already decide whether the node is shared:
#: an explicit ``--exclusive`` (bare or ``=user`` / ``=mcs``) is kept as written,
#: and ``--oversubscribe`` / ``-s`` asks to share, which an added
#: ``--exclusive`` would contradict.
_SHARING_WORDS = ("--exclusive", "--oversubscribe", "-s")


def with_exclusive(launcher: str) -> str:
    """*launcher* with ``--exclusive`` added after ``srun``, so Slurm gives the
    job its node to itself.

    Without it Slurm may place another job on the same node whenever the node
    looks to have room by the resources the jobs asked for — and on a cluster
    that does not count memory as a resource (AWS ParallelCluster's default),
    two runs that each size their workers to the whole node fit by CPU count
    and then compete for its memory. Two app instances running at once is the
    ordinary way to get there.

    Unchanged when *launcher* is not ``srun``, cannot be tokenised, or already
    says how the node is shared (:data:`_SHARING_WORDS`). The word is inserted
    into the text rather than the command being rebuilt from tokens, so the
    user's quoting survives exactly as written.
    """
    if not is_srun(launcher):
        return launcher
    tokens = shlex.split(launcher)
    if any(t in _SHARING_WORDS or t.startswith("--exclusive=") for t in tokens[1:]):
        return launcher
    end = re.match(r"\s*\S+", launcher).end()     # the program word, as written
    rest = launcher[end:]
    return f"{launcher[:end]} --exclusive" + (f" {rest.lstrip()}" if rest.strip() else "")


def stray_launcher_words(launcher: str) -> list[str]:
    """Words in an ``srun`` launcher that ``srun`` would run as the program.

    The app appends ``bash -lc <job>`` to the launcher, and ``srun`` stops
    reading options at the first word that is not one, so any such word ends up
    as the program instead of the job: a launcher ending ``--mem=60G
    g4dn.12xlarge`` had ``slurmstepd`` try to ``execve("g4dn.12xlarge")`` and
    fail with exit code 2 once a node finally started — after the run had
    waited out the whole queue.

    Decidable without srun's option table: a word that follows an option
    written with ``=``, or follows another such word, cannot be that option's
    value. A word after a bare option (``-p gpu``, ``--partition gpu``) may be
    its value and is left alone, so a flag followed by a stray word is not
    caught; the common shape — the stray word last, after ``--opt=value`` — is.
    Returns ``[]`` for anything that is not an ``srun`` launcher.
    """
    if not is_srun(launcher):
        return []
    tokens = shlex.split(launcher)[1:]
    stray: list[str] = []
    prev_takes_value = False
    for tok in tokens:
        if tok.startswith("-"):
            prev_takes_value = "=" not in tok and tok != "--"
            continue
        if not prev_takes_value:
            stray.append(tok)
        prev_takes_value = False
    return stray


def stray_words_message(launcher: str) -> str | None:
    """The sentence to show for :func:`stray_launcher_words`, or ``None``."""
    stray = stray_launcher_words(launcher)
    if not stray:
        return None
    words = ", ".join(f"“{w}”" for w in stray)
    return (
        f"The scheduler command has {words} on its own, not attached to an "
        f"option, so srun would try to run it as the program instead of the "
        f"job and the run would fail once a node starts. Attach it to the option "
        f"it belongs to — an instance or node type is usually a constraint, "
        f"e.g. --constraint={stray[0]} — or remove it.")


#: ``srun`` options that take the job's output away from the app: ``--output`` /
#: ``--error`` send it to a file on the cluster, and ``--pty`` puts it on a
#: pseudo-terminal of its own. The app reads the run — every well's progress,
#: the batch driver's hand-offs, the scheduler's own lines — off ``srun``'s
#: stdout, so with any of these it hears nothing and the run looks hung.
_REDIRECT_LONG = ("--output", "--error", "--pty")
_REDIRECT_SHORT = ("-o", "-e")


def redirect_launcher_words(launcher: str) -> list[str]:
    """The words in an ``srun`` launcher that redirect the job's output.

    Both spellings of each option are found — ``--output=f``, ``--output f``,
    ``-o f``, ``-of`` — and ``--pty``. Returns ``[]`` for anything that is not
    an ``srun`` launcher, or cannot be tokenised.
    """
    if not is_srun(launcher):
        return []
    found: list[str] = []
    for tok in shlex.split(launcher)[1:]:
        if tok == "--":
            break
        long = tok.split("=", 1)[0]
        if long in _REDIRECT_LONG or (
                not tok.startswith("--") and tok[:2] in _REDIRECT_SHORT):
            found.append(tok)
    return found


def redirect_words_message(launcher: str) -> str | None:
    """The sentence to show for :func:`redirect_launcher_words`, or ``None``."""
    words = redirect_launcher_words(launcher)
    if not words:
        return None
    listed = ", ".join(f"“{w}”" for w in words)
    return (
        f"The scheduler command has {listed}, which sends the run's output to a "
        f"file or a terminal of its own instead of back to All-Well: the run's "
        f"progress, its per-well results and its errors would never arrive, and "
        f"it would look hung. Remove it: All-Well reads the job's output itself "
        f"and keeps it in the run's log.")


def _split_launcher(tokens: list[str]) -> tuple[list[str], list[str], str]:
    """``(tokens without --exclude, the user's exclusions, the partition)``."""
    kept: list[str] = []
    excluded: list[str] = []
    partition = ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in ("--exclude", "-x") and nxt:
            excluded.append(nxt)
            i += 2
            continue
        if tok.startswith("--exclude="):
            excluded.append(tok.split("=", 1)[1])
        elif tok.startswith("-x") and len(tok) > 2:
            excluded.append(tok[2:])
        else:
            if tok in ("--partition", "-p") and nxt:
                partition = nxt
            elif tok.startswith("--partition="):
                partition = tok.split("=", 1)[1]
            elif tok.startswith("-p") and len(tok) > 2 and not tok.startswith("--"):
                partition = tok[2:]
            kept.append(tok)
        i += 1
    return kept, [e for e in excluded if e], partition


#: srun options that set how many copies of the command it starts, as ``(short, long)``. ``--ntasks`` is folded
#: into ``--cpus-per-task`` (:func:`one_task`); the others are dropped.
_TASK_COUNT_OPTS = (("-n", "--ntasks"), ("-N", "--nodes"), ("", "--ntasks-per-node"))
_CPUS_OPTS = ("-c", "--cpus-per-task")


def _take_option(tokens: list[str], short: str, long: str) -> tuple[list[str], list[str]]:
    """``(tokens without the option, every value it was given)``, for the ``-xN``, ``-x N``, ``--long=N`` and
    ``--long N`` spellings."""
    kept: list[str] = []
    values: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if tok == long or (short and tok == short):
            if nxt is not None:
                values.append(nxt)
            i += 2
            continue
        if tok.startswith(long + "="):
            values.append(tok.split("=", 1)[1])
        elif short and tok.startswith(short) and len(tok) > len(short) and not tok.startswith("--"):
            values.append(tok[len(short):])
        else:
            kept.append(tok)
        i += 1
    return kept, values


def one_task(tokens: list[str]) -> list[str]:
    """*tokens* (an ``srun`` launcher) asking for exactly one task on one node.

    The run is one process that spreads its own workers over the CPUs it is given. Every task ``srun`` starts runs
    the whole job script, so a launch that started five ran five batch drivers over one run directory at once: all
    five found batch 1's staged directory, one moved it into ``in/``, the next deleted that ``in/`` and then failed
    to move a directory that was no longer there ("could not stage its input"). The count need not be on the
    launcher: ``srun`` reads ``SLURM_NTASKS`` / ``SLURM_NNODES`` from the environment just as it reads the flags,
    so the login shell's environment can set it. ``--ntasks=1 --nodes=1`` are written last and explicitly, and
    options on the command line override that environment.

    A ``--ntasks`` written into the launcher is how a job template asks for cores, so it is folded into
    ``--cpus-per-task`` as :func:`services.slurm_batch_script.single_task` does for a JupyterHub profile: ``-n 4
    -c 2`` becomes ``--cpus-per-task=8``. A count that is not a whole number is dropped rather than guessed at.
    """
    out = list(tokens)
    tasks: list[str] = []
    for short, long in _TASK_COUNT_OPTS:
        out, values = _take_option(out, short, long)
        if long == "--ntasks":
            tasks = values
    if tasks:
        out, cpus = _take_option(out, *_CPUS_OPTS)
        n, per = tasks[-1], (cpus[-1] if cpus else "1")
        if n.isdigit() and per.isdigit():
            out.append(f"--cpus-per-task={int(n) * int(per)}")
        elif cpus:
            out.append(f"--cpus-per-task={per}")
    return [*out, "--nodes=1", "--ntasks=1"]


def wrap_srun_launch(launcher: str, inner_quoted: str, remote_pidfile_q: str,
                     err_file_q: str, *, start_slack_s: int = START_SLACK_S,
                     pending_fallback_s: int = PENDING_FALLBACK_S,
                     queue_wait_s: int = 0,
                     boot_retry_pause_s: int = BOOT_RETRY_PAUSE_S) -> str:
    """The login-shell command that runs ``<launcher> bash -lc <inner>`` with the
    awake-node preference, the boot-failure retry and the queue-wait deadline.

    *inner_quoted* is the inner script already quoted for the login shell, and
    must print :data:`ALLOCATION_GRANTED` first thing;
    *remote_pidfile_q* and *err_file_q* are already shell-quoted paths.
    *start_slack_s*, *pending_fallback_s* and *boot_retry_pause_s* are
    :data:`START_SLACK_S`, :data:`PENDING_FALLBACK_S` and
    :data:`BOOT_RETRY_PAUSE_S`; the tests shorten them. *queue_wait_s* is the
    host's Max queue wait (:func:`queue_wait_seconds`), ``0`` for none.

    Raises ``ValueError`` for a launcher that would send the job's output
    anywhere but back to the app (:func:`redirect_words_message`).
    """
    redirect = redirect_words_message(launcher)
    if redirect:
        raise ValueError(redirect)
    tokens, user_excl, partition = _split_launcher(shlex.split(launcher))
    tokens = one_task(tokens)
    launcher_str = shlex.join(tokens)
    awake = "|".join(AWAKE_STATES)
    free = "|".join(FREE_STATES)
    part_q = shlex.quote(partition)
    part_words = '${_aw_part:+-p "$_aw_part"}'
    where = partition or "the default partitions"
    merge = "tr , '\\n' | grep -v '^$' | sort -u | paste -sd, -"
    granted = shlex.quote(ALLOCATION_GRANTED)
    return "".join((
        f"_aw_pid={remote_pidfile_q}; _aw_err={err_file_q}; _aw_rcf={err_file_q}.rc; _aw_pend={err_file_q}.pend; ",
        f"_aw_qto={err_file_q}.qto; ",
        f"_aw_part={part_q}; _aw_x={shlex.quote(','.join(user_excl))}; _aw_ux=$_aw_x; ",
        # The queue wait is timed from the first request, across every attempt below: it is how long the run
        # has waited for a node, whatever the wrapper did in the meantime.
        "_aw_try=1; _aw_awake=1; _aw_failed=''; _aw_t0=$(date +%s); ",
        "while :; do ",
        # Between attempts no srun is running, so the cancel channel (`kill -TERM $(cat <pidfile>)`) must reach
        # this shell: its PID is the pidfile's until the attempt's srun overwrites it.
        'echo $$ > "$_aw_pid"; ',
        _queue_deadline_passed(queue_wait_s),
        # 1. Nodes that are not powered on, while at least one is.
        '_aw_asleep=""; ',
        'if [ "$_aw_awake" = 1 ] && command -v sinfo >/dev/null 2>&1; then ',
        f"_aw_all=$(sinfo -h -N {part_words} -o '%N %t' 2>/dev/null); ",
        # Busy is not available: with every powered-on node running someone
        # else's job, excluding the asleep ones queues the run until a job
        # ends — hours, perhaps — when booting a node takes minutes. So the
        # preference needs a node with room now (`FREE_STATES`).
        f"if printf '%s\\n' \"$_aw_all\" | awk '$2 ~ /^({free})$/ {{f=1}} END {{exit !f}}'; then ",
        f"_aw_asleep=$(printf '%s\\n' \"$_aw_all\" | awk 'NF==2 && $2 !~ /^({awake})$/ {{print $1}}' | {merge}); ",
        # Said by what the listing shows: only a powered-down node (`~`, or one already powering up or down) is
        # something Slurm will boot. With none — a cluster without power saving — the job waits for one to free.
        f"elif printf '%s\\n' \"$_aw_all\" | awk '$2 ~ /[~#%!]/ {{f=1}} END {{exit !f}}'; then "
        f'echo "[remote] scheduler: no free powered-on node in {where} — Slurm will boot one"; ',
        f'else echo "[remote] scheduler: every powered-on node in {where} is busy and none is powered down to boot '
        '— the job waits in the queue for one to free"; fi; ',
        "fi; ",
        f"_aw_ex=$(printf '%s,%s' \"$_aw_x\" \"$_aw_asleep\" | {merge}); ",
        # 1b. Ask Slurm, without queuing anything, whether a powered-on node
        # could ever take this request. If none can, say so in plain words
        # rather than let srun fail with "Requested node configuration is not
        # available" before the real attempt.
        'if [ -n "$_aw_asleep" ]; then ',
        # `standard` pins the time format the expected start is read in, whatever the user's environment sets.
        f'if ! _aw_t=$(SLURM_TIME_FORMAT=standard {launcher_str} --test-only ${{_aw_ex:+--exclude="$_aw_ex"}} '
        "true 2>&1); then ",
        f'_aw_asleep=""; _aw_awake=0; _aw_ex=$(printf \'%s\' "$_aw_x" | {merge}); ',
        'echo "[remote] scheduler: no powered-on node matches this request — Slurm will '
        'start one, which can take several minutes"; ',
        # 1c. A powered-on node could take it, but when? `--test-only` says. Further off than booting one would
        # take means they are busy. A time `date` cannot read (BSD `date` has no `-d`) leaves the preference on,
        # and the pending fallback below is the backstop.
        "else _aw_at=$(printf '%s\n' \"$_aw_t\" | sed -nE 's/.* to start at ([^ ]+).*/\\1/p' | head -n1); ",
        '_aw_s=$(date -d "$_aw_at" +%s 2>/dev/null); _aw_n=$(date +%s 2>/dev/null); ',
        f'if [ -n "$_aw_at" ] && [ -n "$_aw_s" ] && [ -n "$_aw_n" ] && [ $((_aw_s - _aw_n)) -gt {int(start_slack_s)} ]; '
        "then ",
        f'_aw_asleep=""; _aw_awake=0; _aw_ex=$(printf \'%s\' "$_aw_x" | {merge}); ',
        'echo "[remote] scheduler: the powered-on nodes that fit this request are busy until about $_aw_at — '
        'letting Slurm boot one instead"; ',
        "fi; fi; fi; ",
        'if [ -n "$_aw_asleep" ]; then ',
        "echo \"[remote] scheduler: avoiding $(printf '%s' \"$_aw_asleep\" | awk -F, '{print NF}') "
        f"node(s) in {where} that are powered down, booting or out of service\"; fi; ",
        # 2. The launch. srun's PID goes to the pidfile; its output is copied (tee).
        ': > "$_aw_err"; rm -f "$_aw_rcf" "$_aw_pend" "$_aw_qto"; _aw_wd=""; _aw_qd=""; ',
        # 2a. While powered-down nodes are excluded, a watchdog: still queued after `pending_fallback_s`, the job
        # is cancelled — only if Slurm still lists it as pending — and the loop asks again without the exclusion.
        'if [ -n "$_aw_asleep" ]; then ( _aw_w=0; ',
        f'while [ "$_aw_w" -lt {int(pending_fallback_s)} ]; do sleep 1; _aw_w=$((_aw_w+1)); '
        '[ -f "$_aw_rcf" ] && exit 0; done; ',
        "grep -q '^srun: .*has been allocated resources' \"$_aw_err\" && exit 0; ",
        "_aw_j=$(sed -nE 's/^srun: job ([0-9]+) queued and waiting for resources.*/\\1/p' \"$_aw_err\" | tail -n1); ",
        '[ -n "$_aw_j" ] || exit 0; ',
        'if command -v squeue >/dev/null 2>&1; then '
        '[ -n "$(squeue -h -j "$_aw_j" -t PD -o %i 2>/dev/null)" ] || exit 0; fi; ',
        ': > "$_aw_pend"; scancel "$_aw_j" 2>/dev/null || kill -TERM "$(cat "$_aw_pid")" 2>/dev/null; ',
        ') & _aw_wd=$!; fi; ',
        _queue_deadline(queue_wait_s, granted),
        "{ sh -c 'echo $$ > \"$0\"; exec \"$@\"' \"$_aw_pid\" ",
        f"{launcher_str} ${{_aw_ex:+--exclude=\"$_aw_ex\"}} bash -lc {inner_quoted} 2>&1; ",
        'echo $? > "$_aw_rcf"; } ',
        '| tee -a "$_aw_err"; ',
        '[ -n "$_aw_wd" ] && kill "$_aw_wd" 2>/dev/null; [ -n "$_aw_qd" ] && kill "$_aw_qd" 2>/dev/null; ',
        '_aw_rc=$(cat "$_aw_rcf" 2>/dev/null || echo 1); ',
        _queue_deadline_reached(queue_wait_s),
        # Whether the job had started: its first line is in the copy. Nothing after that is a boot failure.
        f'_aw_go=0; grep -qF {granted} "$_aw_err" && _aw_go=1; ',
        # 3. Queued too long on the powered-on nodes: ask again, letting Slurm boot one. Once — no exclusion now.
        'if [ -f "$_aw_pend" ]; then rm -f "$_aw_pend"; _aw_awake=0; ',
        f'echo "[remote] scheduler: still queued for a powered-on node after {int(pending_fallback_s)} s — '
        'cancelled it and asking again without excluding the powered-down ones"; ',
        f'echo "[remote] scheduler: requesting allocation via: {launcher_str}${{_aw_x:+ --exclude=$_aw_x}}"; ',
        "continue; fi; ",
        # 3a. Awake nodes cannot satisfy the request: once, try without that.
        'if [ "$_aw_rc" != 0 ] && [ "$_aw_go" = 0 ] && [ -n "$_aw_asleep" ] '
        "&& grep -q '^srun: .*Requested node configuration is not available' \"$_aw_err\"; then ",
        "_aw_awake=0; ",
        'echo "[remote] scheduler: no powered-on node can take this request — letting Slurm boot one"; ',
        "continue; fi; ",
        # 3b. The node failed after the job started on it: not a boot failure, and not retried — asking again
        # would run the job from its beginning, the batch driver from batch 1 and a single run from its top.
        f"if [ \"$_aw_rc\" != 0 ] && [ \"$_aw_go\" = 1 ] && grep -qE '^srun: .*({_BOOT_FAILURE_RE})' \"$_aw_err\"; "
        "then _aw_bad=$(sed -nE 's/^srun: .*Node failure on ([^ ]+).*/\\1/p' \"$_aw_err\" | head -n1); ",
        'echo "[remote] scheduler: ERROR node ${_aw_bad:-?} failed while the run was going on it — not retried, '
        'because asking again would start the run from its beginning; resume the run to continue from where it '
        'stopped"; ',
        # 3c. The node did not boot: exclude it and ask again.
        f"elif [ \"$_aw_rc\" != 0 ] && grep -qE '^srun: .*({_BOOT_FAILURE_RE})' \"$_aw_err\"; then ",
        "_aw_bad=$(sed -nE 's/^srun: .*Nodes ([^ ]+) are still not ready.*/\\1/p; "
        f"s/^srun: .*Node failure on ([^ ]+).*/\\1/p' \"$_aw_err\" | {merge}); ",
        f"_aw_x=$(printf '%s,%s' \"$_aw_x\" \"$_aw_bad\" | {merge}); ",
        f"_aw_failed=$(printf '%s,%s' \"$_aw_failed\" \"$_aw_bad\" | {merge}); ",
        # No retry count: the host's Max queue wait (its watchdog is timed from the first request) is the bound.
        'echo "[remote] scheduler: node(s) ${_aw_bad:-?} failed to boot — '
        f'requesting another node in {int(boot_retry_pause_s)} s (retry $_aw_try; failed to boot so far: '
        '$_aw_failed)"; ',
        'echo $$ > "$_aw_pid"; ' f'sleep {int(boot_retry_pause_s)}; ',
        f'echo "[remote] scheduler: requesting allocation via: {launcher_str} --exclude=$_aw_x"; ',
        "_aw_try=$((_aw_try+1)); continue; ",
        "fi; ",
        # 3c'. Excluding the nodes that failed to boot has ruled out every node that could take the request.
        # Not a reason to stop: forget them (Slurm may have them back by now) and ask again.
        'if [ "$_aw_rc" != 0 ] && [ "$_aw_go" = 0 ] && [ "$_aw_x" != "$_aw_ux" ] '
        "&& grep -q '^srun: .*Requested node configuration is not available' \"$_aw_err\"; then ",
        '_aw_x=$_aw_ux; ',
        'echo "[remote] scheduler: every node that can take this request has failed to boot at least once '
        f'($_aw_failed) — trying them again in {int(boot_retry_pause_s)} s"; ',
        'echo $$ > "$_aw_pid"; ' f'sleep {int(boot_retry_pause_s)}; ',
        f'echo "[remote] scheduler: requesting allocation via: {launcher_str}${{_aw_x:+ --exclude=$_aw_x}}"; ',
        "continue; fi; ",
        # 3d. Slurm refused the request itself: no node it could ever give matches it. Nothing is retried — the
        # same words would be refused again — so the run says what it asked for beside what the partition's
        # nodes have, which is what the user needs to fix the scheduler command.
        'if [ "$_aw_rc" != 0 ] && [ "$_aw_go" = 0 ] '
        "&& grep -q '^srun: .*Requested node configuration is not available' \"$_aw_err\"; then ",
        f'echo "{NO_NODE_FITS} in {where} — the scheduler command asks for: {launcher_str}"; ',
        f'echo "[remote] scheduler: the nodes in {where} (how many, CPUs, memory in MB, features, GPUs):"; ',
        f"sinfo -h -N {part_words} -o '%c %m %f %G' 2>/dev/null | sort | uniq -c | head -n 12 "
        "| sed 's/^ */[remote] scheduler:   /'; ",
        'echo "[remote] scheduler: change the scheduler command in the Remotes dialog (or pick another '
        'profile) so that every value fits one of these nodes"; fi; ',
        'rm -f "$_aw_err" "$_aw_rcf" "$_aw_pend" "$_aw_qto"; exit "$_aw_rc"; ',
        "done",
    ))


def _queue_deadline(queue_wait_s: int, granted_q: str) -> str:
    """A watchdog beside each attempt: once *queue_wait_s* has passed since the first request with no
    :data:`ALLOCATION_GRANTED` in the launch's output, the job is cancelled — its id from ``srun``'s own
    ``queued`` / ``allocated`` line, else ``srun`` itself is signalled — and ``$_aw_qto`` records it.
    Booting a granted node counts as waiting: the run has not started until the job's first line.
    ``""`` when there is no deadline."""
    if queue_wait_s <= 0:
        return ""
    return (
        '( while :; do sleep 1; [ -f "$_aw_rcf" ] && exit 0; '
        f'grep -qF {granted_q} "$_aw_err" && exit 0; '
        f'[ $(( $(date +%s) - _aw_t0 )) -ge {int(queue_wait_s)} ] && break; done; '
        "_aw_j=$(sed -nE 's/^srun: job ([0-9]+) (queued and waiting|has been allocated).*/\\1/p' "
        '"$_aw_err" | tail -n1); '
        f'grep -qF {granted_q} "$_aw_err" && exit 0; '
        'printf "%s" "$_aw_j" > "$_aw_qto"; '
        'if [ -n "$_aw_j" ] && command -v scancel >/dev/null 2>&1; then scancel "$_aw_j" 2>/dev/null '
        '|| kill -TERM "$(cat "$_aw_pid")" 2>/dev/null; else kill -TERM "$(cat "$_aw_pid")" 2>/dev/null; fi '
        ') & _aw_qd=$!; '
    )


def _max_wait_words(queue_wait_s: int) -> str:
    return f"{queue_wait_s // 60} min" if queue_wait_s % 60 == 0 else f"{queue_wait_s} s"


def _queue_deadline_passed(queue_wait_s: int) -> str:
    """Before an attempt: when *queue_wait_s* has already passed since the first request, stop with
    :data:`QUEUE_WAIT_EXIT`. The watchdog of :func:`_queue_deadline` only lives while an attempt runs and looks
    once a second, so attempts that each fail at once — a node whose boot fails immediately — would otherwise
    retry for ever whatever the host's Max queue wait says. ``""`` when there is no deadline."""
    if queue_wait_s <= 0:
        return ""
    return (
        f'if [ $(( $(date +%s) - _aw_t0 )) -ge {int(queue_wait_s)} ]; then '
        'rm -f "$_aw_err" "$_aw_rcf" "$_aw_pend" "$_aw_qto"; '
        f'echo "[remote] scheduler: ERROR no node was granted within {_max_wait_words(queue_wait_s)}, this host\'s '
        'Max queue wait${_aw_failed:+ (failed to boot: $_aw_failed)} — stopped. Raise Max queue wait for this host '
        'in Remotes (Wait indefinitely never gives up), or start the run again when the cluster is less busy"; '
        f'exit {QUEUE_WAIT_EXIT}; fi; '
    )


def _queue_deadline_reached(queue_wait_s: int) -> str:
    """After an attempt: when :func:`_queue_deadline` fired, say so — naming the setting and the wait —
    and exit :data:`QUEUE_WAIT_EXIT` rather than read the cancelled ``srun``'s failure as anything else."""
    if queue_wait_s <= 0:
        return ""
    wait = _max_wait_words(queue_wait_s)
    return (
        'if [ -f "$_aw_qto" ]; then _aw_qj=$(cat "$_aw_qto"); '
        'rm -f "$_aw_err" "$_aw_rcf" "$_aw_pend" "$_aw_qto"; '
        f'echo "[remote] scheduler: ERROR no node was granted within {wait}, this host\'s Max queue wait — '
        'cancelled job ${_aw_qj:-?} and stopped. Raise Max queue wait for this host in Remotes '
        '(Wait indefinitely never gives up), or start the run again when the cluster is less busy"; '
        f'exit {QUEUE_WAIT_EXIT}; fi; '
    )
