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
   only while some awake node is ``idle`` or ``mix`` (:data:`FREE_STATES`).
   With every awake node busy, excluding the asleep ones left the job queued
   behind other people's jobs — for as long as they ran — while hundreds of
   powered-down nodes that boot in minutes were ruled out. (A ``mix`` node
   without room for this particular request can still hold the job in the
   queue; Slurm's expected start would tell, but reading it pushed the launch
   command past its size budget, see ``pack_for_login_shell``.) A node's state can change between the check and the start
   of the job, so a real launch that still fails that way is retried once
   without the exclusion, and a boot failure is what the retry below is for.
2. **Retries a boot failure** up to :data:`SRUN_BOOT_RETRIES` times, adding the
   node(s) ``srun`` named to ``--exclude`` each time so Slurm picks another.
   Each retry prints a line saying which node failed, and a ``requesting
   allocation via:`` line that puts the Analyze progress view back into its
   queue-wait state. When the retries are spent it names every node that failed,
   for the cluster's admins.

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

import shlex

#: How many times a launch that fails to boot its node is retried.
SRUN_BOOT_RETRIES = 3

#: ``sinfo %t`` states of a node that is powered on. Anything else — ``idle~``,
#: ``idle#``, ``down``, ``drain``, ``fail`` … — is excluded while an awake node
#: exists.
AWAKE_STATES = ("idle", "mix", "alloc", "comp")

#: The awake states with room for a new job now. ``alloc`` and ``comp`` are
#: awake but full: preferring them means waiting for someone else's job to end.
FREE_STATES = ("idle", "mix")

#: What ``srun`` prints when the node it was granted did not boot.
_BOOT_FAILURE_RE = "Something is wrong with the boot of the nodes|Node failure on"


def is_srun(launcher: str) -> bool:
    """True when *launcher* runs ``srun`` (the only launcher this wraps)."""
    try:
        tokens = shlex.split(launcher)
    except ValueError:
        return False
    return bool(tokens) and tokens[0].rsplit("/", 1)[-1] == "srun"


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


def wrap_srun_launch(launcher: str, inner_quoted: str, remote_pidfile_q: str,
                     err_file_q: str) -> str:
    """The login-shell command that runs ``<launcher> bash -lc <inner>`` with the
    awake-node preference and the boot-failure retry.

    *inner_quoted* is the inner script already quoted for the login shell;
    *remote_pidfile_q* and *err_file_q* are already shell-quoted paths.
    """
    tokens, user_excl, partition = _split_launcher(shlex.split(launcher))
    launcher_str = shlex.join(tokens)
    awake = "|".join(AWAKE_STATES)
    free = "|".join(FREE_STATES)
    part_q = shlex.quote(partition)
    part_words = '${_aw_part:+-p "$_aw_part"}'
    where = partition or "the default partitions"
    merge = "tr , '\\n' | grep -v '^$' | sort -u | paste -sd, -"
    total = SRUN_BOOT_RETRIES + 1
    return "".join((
        f"_aw_pid={remote_pidfile_q}; _aw_err={err_file_q}; _aw_rcf={err_file_q}.rc; ",
        f"_aw_part={part_q}; _aw_x={shlex.quote(','.join(user_excl))}; ",
        "_aw_try=1; _aw_awake=1; ",
        "while :; do ",
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
        f'if ! {launcher_str} --test-only ${{_aw_ex:+--exclude="$_aw_ex"}} true >/dev/null 2>&1; then ',
        f'_aw_asleep=""; _aw_awake=0; _aw_ex=$(printf \'%s\' "$_aw_x" | {merge}); ',
        'echo "[remote] scheduler: no powered-on node matches this request — Slurm will '
        'start one, which can take several minutes"; ',
        "fi; fi; ",
        'if [ -n "$_aw_asleep" ]; then ',
        "echo \"[remote] scheduler: avoiding $(printf '%s' \"$_aw_asleep\" | awk -F, '{print NF}') "
        f"node(s) in {where} that are powered down, booting or out of service\"; fi; ",
        # 2. The launch. srun's PID goes to the pidfile; its output is copied (tee).
        ': > "$_aw_err"; rm -f "$_aw_rcf"; ',
        "{ sh -c 'echo $$ > \"$0\"; exec \"$@\"' \"$_aw_pid\" ",
        f"{launcher_str} ${{_aw_ex:+--exclude=\"$_aw_ex\"}} bash -lc {inner_quoted} 2>&1; ",
        'echo $? > "$_aw_rcf"; } ',
        '| tee -a "$_aw_err"; ',
        '_aw_rc=$(cat "$_aw_rcf" 2>/dev/null || echo 1); ',
        # 3a. Awake nodes cannot satisfy the request: once, try without that.
        'if [ "$_aw_rc" != 0 ] && [ -n "$_aw_asleep" ] '
        "&& grep -q '^srun: .*Requested node configuration is not available' \"$_aw_err\"; then ",
        "_aw_awake=0; ",
        'echo "[remote] scheduler: no powered-on node can take this request — letting Slurm boot one"; ',
        "continue; fi; ",
        # 3b. The node did not boot: exclude it and ask again.
        f"if [ \"$_aw_rc\" != 0 ] && grep -qE '^srun: .*({_BOOT_FAILURE_RE})' \"$_aw_err\"; then ",
        "_aw_bad=$(sed -nE 's/^srun: .*Nodes ([^ ]+) are still not ready.*/\\1/p; "
        f"s/^srun: .*Node failure on ([^ ]+).*/\\1/p' \"$_aw_err\" | {merge}); ",
        f"_aw_x=$(printf '%s,%s' \"$_aw_x\" \"$_aw_bad\" | {merge}); ",
        f'if [ "$_aw_try" -le {SRUN_BOOT_RETRIES} ]; then ',
        'echo "[remote] scheduler: node(s) ${_aw_bad:-?} failed to boot — '
        f'requesting another node (retry $_aw_try of {SRUN_BOOT_RETRIES})"; ',
        f'echo "[remote] scheduler: requesting allocation via: {launcher_str} --exclude=$_aw_x"; ',
        "_aw_try=$((_aw_try+1)); continue; fi; ",
        f'echo "[remote] scheduler: ERROR nodes failed to boot on all {total} attempts: '
        '$_aw_x — the cluster cannot start a node here; send these names to its '
        'admins, or add --exclude for them to the launcher"; ',
        "fi; ",
        'rm -f "$_aw_err" "$_aw_rcf"; exit "$_aw_rc"; ',
        "done",
    ))
