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
   and lets Slurm boot one, and if the awake nodes cannot satisfy the request
   (``Requested node configuration is not available`` — say, the GPU nodes are
   all asleep) it retries once without the state-based exclusion. A node's
   state can change between the ``sinfo`` and the start of the job, which is
   what the retry below is for.
2. **Retries a boot failure** up to :data:`SRUN_BOOT_RETRIES` times, adding the
   node(s) ``srun`` named to ``--exclude`` each time so Slurm picks another.
   Each retry prints a line saying which node failed, and a ``requesting
   allocation via:`` line that puts the Analyze progress view back into its
   queue-wait state. When the retries are spent it names every node that failed,
   for the cluster's admins.

Exclusions the user wrote into the launcher (``--exclude`` / ``-x``) are kept:
Slurm takes the *last* ``--exclude`` it is given, so the wrapper removes the
user's from the launcher and merges them into the one it passes.

``srun``'s stderr is copied to a file beside the pidfile so the shell can read
what went wrong; only its own ``srun:`` lines are kept, because the pipeline's
log flows through the same stream and can run to megabytes. Each attempt still
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

#: What ``srun`` prints when the node it was granted did not boot.
_BOOT_FAILURE_RE = "Something is wrong with the boot of the nodes|Node failure on"


def is_srun(launcher: str) -> bool:
    """True when *launcher* runs ``srun`` (the only launcher this wraps)."""
    try:
        tokens = shlex.split(launcher)
    except ValueError:
        return False
    return bool(tokens) and tokens[0].rsplit("/", 1)[-1] == "srun"


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
        f"if printf '%s\\n' \"$_aw_all\" | awk '$2 ~ /^({awake})$/ {{f=1}} END {{exit !f}}'; then ",
        f"_aw_asleep=$(printf '%s\\n' \"$_aw_all\" | awk 'NF==2 && $2 !~ /^({awake})$/ {{print $1}}' | {merge}); ",
        'if [ -n "$_aw_asleep" ]; then ',
        "echo \"[remote] scheduler: avoiding $(printf '%s' \"$_aw_asleep\" | awk -F, '{print NF}') "
        f"node(s) in {where} that are powered down, booting or out of service\"; fi; ",
        f'else echo "[remote] scheduler: no powered-on node in {where} — Slurm will boot one"; fi; ',
        "fi; ",
        f"_aw_ex=$(printf '%s,%s' \"$_aw_x\" \"$_aw_asleep\" | {merge}); ",
        # 2. The launch. srun's PID goes to the pidfile; its own lines to the file.
        ': > "$_aw_err"; rm -f "$_aw_rcf"; ',
        "{ sh -c 'echo $$ > \"$0\"; exec \"$@\"' \"$_aw_pid\" ",
        f"{launcher_str} ${{_aw_ex:+--exclude=\"$_aw_ex\"}} bash -lc {inner_quoted} 2>&1; ",
        'echo $? > "$_aw_rcf"; } ',
        "| awk -v f=\"$_aw_err\" '{ print; fflush() } /^srun: / { print >> f; fflush(f) }'; ",
        '_aw_rc=$(cat "$_aw_rcf" 2>/dev/null || echo 1); ',
        # 3a. Awake nodes cannot satisfy the request: once, try without that.
        'if [ "$_aw_rc" != 0 ] && [ -n "$_aw_asleep" ] '
        "&& grep -q 'Requested node configuration is not available' \"$_aw_err\"; then ",
        "_aw_awake=0; ",
        'echo "[remote] scheduler: no powered-on node can take this request — letting Slurm boot one"; ',
        "continue; fi; ",
        # 3b. The node did not boot: exclude it and ask again.
        f"if [ \"$_aw_rc\" != 0 ] && grep -qE '{_BOOT_FAILURE_RE}' \"$_aw_err\"; then ",
        "_aw_bad=$(sed -nE 's/.*Nodes ([^ ]+) are still not ready.*/\\1/p; "
        f"s/.*Node failure on ([^ ]+).*/\\1/p' \"$_aw_err\" | {merge}); ",
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
