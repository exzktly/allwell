# Granted node — plan and tracker

This is a remediation-style tracker (CLAUDE.md §6.1) for one body of work: letting
a scheduler-backed remote host **keep a granted node** between runs, so that a run that
fails or is stopped can be rerun on the same node without queueing again. It is
deleted by the pull request that closes its last row.

Counts are derived from the rows below; nothing here is typed as a total.

## The problem

Every remote run on a Slurm host asks for its own node. The run's `srun` is
started from a login-shell ssh session (`services/remote_commands._launch` →
`services/remote_srun_retry.wrap_srun_launch`), the job's `$SLURM_JOB_ID` is
written to `<pidfile>.slurm`, and when the run completes, fails or is stopped the
app cancels that job (`services/remote_jobs.close_remote_job`). The early request
(`services/remote_reservation.Reservation`) already separates *asking for the
node* from *deciding what it runs*, but only for one run: the reservation's job
sources one command file and ends with it.

On a cloud cluster where the instance type is scarce (A10G / g5 on AWS
ParallelCluster), the queue and boot wait dominate. A run that fails after the
node was granted — a CUDA out-of-memory error at model load, a wrong setting, a
Stop — throws the node away, and the rerun waits again.

## What the user gets

A **Request a node** control on each scheduler-backed host in **Manage Remotes**,
and nowhere else (the Analyze pane gets no new control):

- **Request a node** asks the scheduler for a node with the host's own scheduler
  command (partition, GPUs, CPUs, memory, `--exclusive` when ticked) and keeps
  it granted. While granted, the host's row in Manage Remotes shows the node name,
  its GPUs, how long it has been granted, how long it has been idle, and the
  partition's time limit when it has one.
- **Every remote run on that host goes to the granted node** — runs started from
  Analyze, Resume, and every job in the Datasets job queue. Nothing about starting
  a run changes: the run simply does not queue. A run that fails or is stopped
  ends only its own processes; the node stays granted, and running it again (Run,
  or Resume) lands on the same node.
- **The node stays granted while any run is using it**, however long that run
  takes. Only idleness, the Release button, or the partition's own time limit
  ends it.
- **Release** gives the node back at once (refused while a run is using it,
  unless that run is stopped first).
- **Release when idle**: a granted node that has run nothing for **30 minutes**
  (the default; configurable per host, `0` = never) is released automatically.
  The idle clock starts when the last run on it ends, and the Datasets job queue
  counts as one run until it is empty. The cluster then powers the node down on
  its own schedule.

### Wording

Slurm's word for giving a node back is **release** (an allocation is *granted*
and *released*); All-Well does not shut the machine down itself. On AWS
ParallelCluster, a released dynamic node is powered down by the cluster after its
own `ScaledownIdletime` (10 minutes by default), and a static node is never
powered down. So the control says what the app actually does:

- Setting: **Release the granted node after [30] min idle** (`0` = keep it until
  released).
- Hint under it: *Released nodes are powered down by the cluster on its own
  schedule (on AWS ParallelCluster, after its scale-down idle time). You pay for a
  granted node while it is idle.*
- Log line on auto-release: `[remote] granted node <node> (job <id>) released after
  30 min idle.`

"Shut down" is avoided because the app cannot promise it: a static node, or a
cluster with a long scale-down time, keeps the machine running after release.

## Design

### 1. The hold is a Slurm job that does not depend on the app's ssh session

A reservation today is an `srun` client on the login node, tied to an ssh
connection: if the app closes or the network drops, the client dies and so does
the job. A hold must outlive a single run and survive a dropped connection, so it
is submitted with **`sbatch`** (equivalently `salloc --no-shell`), whose
allocation exists independently of any client. The batch script is a small
**hold loop** that runs on the node:

1. Writes the job id, node name, GPU list and start time to the hold's directory
   (`{remote_staging_dir}/holds/<token>/`), and prints the existing
   `allocation granted` line so the dashboard and the server icon read it as they
   already do.
2. Waits for runs. It does not run them itself: each run is a **job step**
   launched from the login node with `srun --jobid=<hold> --overlap …`, so a run's
   output streams back over its own ssh exactly as today, and killing a run kills
   a step, not the allocation.
3. **Idle watchdog.** Every 30 s it checks whether any step other than its own is
   running (`squeue -s -j $SLURM_JOB_ID`, or a lock file each run step holds
   while alive). It records the time the last step ended; when nothing has run for
   the idle limit, it logs the release and exits, which releases the allocation.
   Because the watchdog runs **on the node**, the idle release happens even when
   All-Well has been closed, crashed, or lost its network — the app is not trusted
   to release a node it may never come back to.
4. **Owner heartbeat, optional.** A hold is deliberately allowed to outlive the
   app window, unlike a reservation (which gives its node back two minutes after
   its app goes quiet). The idle limit is what bounds the cost of an abandoned
   hold. With the idle limit set to `0`, the hold falls back to the reservation's
   rule — released when the app's heartbeat goes stale and no run is using it —
   so "never idle-release" can never mean "kept for ever with nobody watching".

**Time limit.** Slurm's `--time` is a hard limit on the whole job: when it
elapses, Slurm signals every step in the job (SIGTERM, then SIGKILL after the
cluster's `KillWait`, 30 s by default) whatever is running, and an ordinary user
cannot extend a running job's limit. A hold that must stay open for as long as
runs keep using it therefore cannot carry a run's `--time`. The hold job drops
any `--time` from the host's scheduler command and asks for the partition's
maximum instead, read with `sinfo -h -p <partition> -o %l`: on a partition whose
limit is `infinite` (AWS ParallelCluster's default) the hold has no time limit at
all and only the idle watchdog or Release ends it. A partition with a finite
limit is shown in Manage Remotes beside the granted node, and §4 below decides
what a run does near it.

`sbatch` needs the same options as the `srun` launcher. They are translated from
the host's scheduler command by the same token handling
`services/remote_srun_retry` already does (`_split_launcher`, `one_task`,
`gpus_for_tasks`, `with_exclusive`); `--nodes=1 --ntasks=1` are added as they are
for a run. The awake-node preference and the boot-failure retry
(`wrap_srun_launch`) need an `sbatch` equivalent: poll `squeue` for the job's
state and reason, resubmit with `--exclude=<node>` when a node fails to boot, and
honour the host's Max queue wait.

### 2. A run on a granted node is a job step

When the host has a live hold, the run skips the early `Reservation` and launches
with the launcher replaced by `srun --jobid=<hold> --overlap --nodes=1 --ntasks=1`
plus the GPU and CPU options needed for the step to see the allocation's GPUs
(whether that is `--whole`, `--gres`, or inherited depends on the Slurm version
and its `SelectType`; row H3 decides it by running the host's **Test** through a
granted node and checking Torch sees the GPU).

The step writes `$SLURM_JOB_ID.$SLURM_STEP_ID` to `<pidfile>.slurm` instead of the
bare job id. Everything that cancels a run then cancels the **step**:

- `services/remote_jobs.close_remote_job` — `scancel <job>.<step>`.
- `services/remote_liveness` (`probe`, `cancel_pending`) — asks about the step.
- `services/remote_sweep` — the orphan sweep must never treat the hold's job as
  an orphaned run allocation, and must not cancel it because a run directory's
  sidecar names it.
- `services/pipeline_runner` Stop path and app-close path.

This is the highest-risk part of the change: today every one of those paths
assumes the sidecar names a job the run owns outright. A missed path cancels the
granted node, which is exactly the failure the feature exists to prevent, and it
fails silently (the next run simply queues). Row H5 is a guard that enumerates
every `scancel` and `squeue -j` site in `services/` and requires each to accept a
step id.

### 3. One run at a time on a granted node

A granted node runs one All-Well run at a time. Two runs sharing one A10G is the
out-of-memory failure that started this, so a second run on the same host while
one is running **waits for the granted node** rather than asking for a node of its
own, and its log says so (`[remote] granted node <node> is busy with another run;
waiting for it`). The host's Max queue wait bounds that wait as it bounds a queue
wait today. The busy check is the hold directory's run lock, so it holds across
two app windows.

### 4. Partition time limit

On a partition with no time limit this section does nothing. On one with a
finite limit, a run's own walltime estimate (`scale_launcher_time`, which scales
`--time` by the batch count) is compared with the time the granted node has left.
When the run would not fit, Slurm would kill it part-way, so the app releases the
granted node, requests a fresh one (with the partition's full limit), and runs
there, logging why — every remote run still goes through a granted node. A run
without a `--time` of its own is assumed to fit.

### 5. State and where it lives

- **Host settings** (`services/remote_config.RemoteHost`, `remotes.toml`):
  `hold_idle_release_min: int = 30`. Written only when it differs from the
  default, like `exclusive_node`.
- **Live hold state** is read from the host, never cached as truth on this
  machine: the hold directory holds the job id, node, GPUs, start time, last-run
  end time. The app keeps a `services/remote_hold.py` `GrantedNode` handle per host
  (Qt-free, beside `remote_reservation.py`), refreshed by a light `squeue` poll
  while Manage Remotes is open and before each run. A hold started from another
  window, or before the app was restarted, is found by listing the host's holds
  directory and asking `squeue` whether each job is alive — so reopening the app
  finds the node it left granted.
- **UI**: only Manage Remotes (`analyze/remote_dialog.py`) changes: the host row
  gets Request a node / Release, the readout, and the idle setting with its hint.
  The run panel's existing server icon already shows the job a run is in, and the
  run log names the granted node it used.

### 6. What a granted node changes for existing behaviour

- **Max queue wait** applies to the hold request; a run on a granted node has no
  queue wait.
- **Reserve the whole node (--exclusive)** applies to the hold.
- **Early request** (`Reservation`) is skipped while a hold is live, and used as
  now otherwise.
- **Scratch on the node's own disk** survives between runs on a granted node, so a
  stopped run's part-done well is resumed from the node's disk instead of starting
  that well again. The week-old cleanup is unchanged.
- **Cost**: a granted node is billed while idle. The idle release and the visible
  idle timer are the answer; the manual says so plainly.

## Decided with the user

- **Where the control lives:** Manage Remotes only. No control in the Analyze run
  panel.
- **No "Rerun on the granted node" button.** Every remote run on the host goes
  through the granted node already, so Run or Resume after a failure lands on the
  same node; a dedicated button would do nothing those don't.
- **Everything remote goes through the granted node**, including every job in the
  Datasets job queue, which counts as one run for the idle clock until it is
  empty.
- **The node stays granted for as long as runs use it.** The idle release
  defaults to **30 minutes**. The hold carries no `--time` of its own (§1, *Time
  limit*), because Slurm's `--time` would end a run in progress when it elapsed.

## Rows

| ID | Item | State | Where | Evidence |
|---|---|---|---|---|
| H1 | Hold job: `sbatch` script with the hold loop, idle watchdog, owner-heartbeat fallback when idle limit is 0 | todo | `services/remote_hold.py`, `services/remote_commands.py` | — |
| H2 | Translate the host's `srun` scheduler command into `sbatch` options; boot-failure retry and Max queue wait for the hold request | todo | `services/remote_srun_retry.py` | — |
| H3 | Run as a step: `srun --jobid --overlap`; decide the GPU/CPU step options by running Test through a granted node | todo | `services/remote_commands._launch`, `services/remote_runner.py` | — |
| H4 | Sidecar records `job.step`; `close_remote_job`, liveness, Stop and app-close cancel the step, never the hold | todo | `services/remote_jobs.py`, `services/remote_liveness.py`, `services/pipeline_runner.py` | — |
| H5 | Guard: every `scancel` / `squeue -j` site in `services/` accepts a step id (proved with a planted bare-job `scancel`) | todo | `tests/` | — |
| H6 | Orphan sweep never cancels a live hold, and never treats it as a run allocation | todo | `services/remote_sweep.py` | — |
| H7 | One run at a time: run lock in the hold directory; a second run waits for the granted node, bounded by Max queue wait, and says so | todo | `services/remote_hold.py` | — |
| H8 | Partition time limit: hold asks for the partition maximum (`sinfo %l`), drops the command's `--time`; a run that would not fit a finite limit gets a fresh granted node | todo | `services/remote_hold.py`, `services/remote_runner.py` | — |
| H9 | Host setting `hold_idle_release_min` (30), load/save round trip | todo | `services/remote_config.py` | — |
| H10 | Find live holds on reopen: list the holds directory, confirm each with `squeue` | todo | `services/remote_hold.py` | — |
| H11 | Remotes dialog: Hold / Release, node, GPUs, idle and time-left readout, the two settings with the release hint | todo | `analyze/remote_dialog.py` | — |
| H12 | Run log names the granted node a run used, and why a run waited for it or got a fresh one (no run-panel control) | todo | `services/remote_runner.py` | — |
| H13 | Datasets job queue goes through the granted node; the queue counts as one run for the idle clock until it is empty | todo | `services/job_queue.py` | — |
| H14 | Caller-sequence test: request a node → run fails → Run again lands on the same job id with no new allocation | todo | `tests/` | — |
| H15 | Idle release test: watchdog script exits after the idle limit with no step, and not while a step runs | todo | `tests/` | — |
| H16 | ARCHITECTURE.md (remote section) and README_WellViewer.md §10 describe granted nodes, the wording above, and the cost | todo | `_Docs/Markdowns/` | — |
| H17 | Manual check on the real cluster: hold an A10G, fail a run, rerun on the same node, watch the idle release | todo | user's cluster | — |
