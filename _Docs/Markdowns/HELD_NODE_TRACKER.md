# Held node — plan and tracker

This is a remediation-style tracker (CLAUDE.md §6.1) for one body of work: letting
a scheduler-backed remote host **hold a node** between runs, so that a run that
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

A **Hold a node** control on a scheduler-backed host in Manage Remotes (and a
matching control in the Analyze pane's run area, beside the server icon that
already shows a run's Slurm job):

- **Hold a node** asks the scheduler for a node with the host's own scheduler
  command (partition, GPUs, CPUs, memory, `--exclusive` when ticked) and keeps
  it. While held, the Remotes row and the run panel show the node name, its GPUs,
  how long it has been held, how long it has been idle, and the walltime left.
- **Every run on that host goes to the held node** instead of queueing. A run that
  fails or is stopped ends only its own processes; the node stays held.
- **Release** gives the node back at once.
- **Release when idle**: a held node that has run nothing for **15 minutes**
  (the default; configurable per host, `0` = never) is released automatically.
  The cluster then powers it down on its own schedule.

### Wording

Slurm's word for giving a node back is **release** (an allocation is *granted*
and *released*); All-Well does not shut the machine down itself. On AWS
ParallelCluster, a released dynamic node is powered down by the cluster after its
own `ScaledownIdletime` (10 minutes by default), and a static node is never
powered down. So the control says what the app actually does:

- Setting: **Release the held node after [15] min idle** (`0` = keep it until
  released or its time limit runs out).
- Hint under it: *Released nodes are powered down by the cluster on its own
  schedule (on AWS ParallelCluster, after its scale-down idle time). You pay for a
  held node while it is idle.*
- Log line on auto-release: `[remote] held node <node> (job <id>) released after
  15 min idle.`

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
   rule — released when the app's heartbeat goes stale — so "never idle-release"
   can never mean "held until walltime with nobody watching".

The hold's walltime is the host's scheduler command's `--time`, or a separate
**Hold for at most** setting (default 8 h) when the command has none. Slurm ends
the job at walltime whatever the watchdog says.

`sbatch` needs the same options as the `srun` launcher. They are translated from
the host's scheduler command by the same token handling
`services/remote_srun_retry` already does (`_split_launcher`, `one_task`,
`gpus_for_tasks`, `with_exclusive`); `--nodes=1 --ntasks=1` are added as they are
for a run. The awake-node preference and the boot-failure retry
(`wrap_srun_launch`) need an `sbatch` equivalent: poll `squeue` for the job's
state and reason, resubmit with `--exclude=<node>` when a node fails to boot, and
honour the host's Max queue wait.

### 2. A run on a held node is a job step

When the host has a live hold, the run skips the early `Reservation` and launches
with the launcher replaced by `srun --jobid=<hold> --overlap --nodes=1 --ntasks=1`
plus the GPU and CPU options needed for the step to see the allocation's GPUs
(whether that is `--whole`, `--gres`, or inherited depends on the Slurm version
and its `SelectType`; row H3 decides it by running the host's **Test** through a
held node and checking Torch sees the GPU).

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
held node, which is exactly the failure the feature exists to prevent, and it
fails silently (the next run simply queues). Row H5 is a guard that enumerates
every `scancel` and `squeue -j` site in `services/` and requires each to accept a
step id.

### 3. One run at a time on a held node

A held node runs one All-Well run at a time; a second run on the same host while
one is running asks for its own node the ordinary way and says so in the log
(`[remote] held node <node> is busy with another run; asking for a node of this
run's own`). Two runs sharing one A10G is the out-of-memory failure that started
this. The busy check is the hold directory's run lock, so it holds across two app
windows.

### 4. Walltime left

A run's own walltime estimate (`scale_launcher_time`, which scales `--time` by the
batch count) is compared with the hold's time left. When the run would not fit,
the app asks for its own node the ordinary way and logs why, rather than starting
a run Slurm will kill part-way. A batched run that fits only some batches is
started on the hold and resumes on its own node later, the way a walltime kill is
resumed today.

### 5. State and where it lives

- **Host settings** (`services/remote_config.RemoteHost`, `remotes.toml`):
  `hold_idle_release_min: int = 15`, `hold_max_hours: float = 8.0`. Written only
  when they differ from the default, like `exclusive_node`.
- **Live hold state** is read from the host, never cached as truth on this
  machine: the hold directory holds the job id, node, GPUs, start time, last-run
  end time. The app keeps a `services/remote_hold.py` `HeldNode` handle per host
  (Qt-free, beside `remote_reservation.py`), refreshed by a light `squeue` poll
  while the Remotes dialog or the run panel shows it. A hold started from another
  window, or before the app was restarted, is found by listing the host's holds
  directory and asking `squeue` whether each job is alive — so reopening the app
  finds the node it left held.
- **UI**: the Remotes dialog row (`analyze/remote_dialog.py`) gets the hold
  controls and the two settings; the run panel's server icon gains a "held"
  state (a distinct shape, not only a colour) and its tooltip adds idle time and
  time left.

### 6. What a held node changes for existing behaviour

- **Max queue wait** applies to the hold request; a run on a held node has no
  queue wait.
- **Reserve the whole node (--exclusive)** applies to the hold.
- **Early request** (`Reservation`) is skipped while a hold is live, and used as
  now otherwise.
- **Scratch on the node's own disk** survives between runs on a held node, so a
  stopped run's part-done well is resumed from the node's disk instead of starting
  that well again. The week-old cleanup is unchanged.
- **Cost**: a held node is billed while idle. The idle release and the visible
  idle timer are the answer; the manual says so plainly.

## Open questions for the user

- Should the **Analyze pane** offer *Hold a node*, or only Manage Remotes?
  (Planned: both; the run panel is where a failed run is seen.)
- When a run on a held node **fails**, should the app offer *Rerun on the held
  node* directly in the failure message? (Planned: yes, as a button beside the
  error.)
- Should a **Datasets job queue** keep one hold for the whole queue? (Planned: yes,
  automatically — queued jobs on the same host use the hold one after another,
  and the idle timer only starts once the queue is empty.)

## Rows

| ID | Item | State | Where | Evidence |
|---|---|---|---|---|
| H1 | Hold job: `sbatch` script with the hold loop, idle watchdog, owner-heartbeat fallback when idle limit is 0 | todo | `services/remote_hold.py`, `services/remote_commands.py` | — |
| H2 | Translate the host's `srun` scheduler command into `sbatch` options; boot-failure retry and Max queue wait for the hold request | todo | `services/remote_srun_retry.py` | — |
| H3 | Run as a step: `srun --jobid --overlap`; decide the GPU/CPU step options by running Test through a held node | todo | `services/remote_commands._launch`, `services/remote_runner.py` | — |
| H4 | Sidecar records `job.step`; `close_remote_job`, liveness, Stop and app-close cancel the step, never the hold | todo | `services/remote_jobs.py`, `services/remote_liveness.py`, `services/pipeline_runner.py` | — |
| H5 | Guard: every `scancel` / `squeue -j` site in `services/` accepts a step id (proved with a planted bare-job `scancel`) | todo | `tests/` | — |
| H6 | Orphan sweep never cancels a live hold, and never treats it as a run allocation | todo | `services/remote_sweep.py` | — |
| H7 | One run at a time: run lock in the hold directory; a second run asks for its own node and says why | todo | `services/remote_hold.py` | — |
| H8 | Walltime check: a run that would not fit the hold's time left asks for its own node | todo | `services/remote_hold.py`, `services/remote_runner.py` | — |
| H9 | Host settings `hold_idle_release_min` (15) and `hold_max_hours` (8), load/save round trip | todo | `services/remote_config.py` | — |
| H10 | Find live holds on reopen: list the holds directory, confirm each with `squeue` | todo | `services/remote_hold.py` | — |
| H11 | Remotes dialog: Hold / Release, node, GPUs, idle and time-left readout, the two settings with the release hint | todo | `analyze/remote_dialog.py` | — |
| H12 | Run panel: held state on the server icon, tooltip idle and time left; *Rerun on the held node* on failure | todo | `analyze/` progress / run panel | — |
| H13 | Datasets job queue uses the hold across jobs; idle timer starts when the queue empties | todo | `services/job_queue.py` | — |
| H14 | Caller-sequence test: hold → run fails → rerun lands on the same job id with no new `sbatch`/`srun` allocation | todo | `tests/` | — |
| H15 | Idle release test: watchdog script exits after the idle limit with no step, and not while a step runs | todo | `tests/` | — |
| H16 | ARCHITECTURE.md (remote section) and README_WellViewer.md §10 describe held nodes, the wording above, and the cost | todo | `_Docs/Markdowns/` | — |
| H17 | Manual check on the real cluster: hold an A10G, fail a run, rerun on the same node, watch the idle release | todo | user's cluster | — |
