# Granted node — plan and tracker

This is a tracker in the sense of CLAUDE.md §6.1, for one body of work: letting a
scheduler-backed remote host **keep a granted node** between runs, so that a run
that fails or is stopped can be run again on the same node without queueing for
a new one. It is written to be handed to another assistant session that has not
seen the conversation that produced it, so it states the decisions, the reasons
for them, the code each part touches, and how each part is proved.

The pull request that closes the last row deletes this file and says so in its
body. Counts are derived from the rows at the end; no total is typed anywhere.

Read before starting: `CLAUDE.md` (all of it — §2 on test lanes, §6 on
version bumps and PR cadence, §7.5–§7.8 on proving guards and tests), and the
remote-host sections of `_Docs/Markdowns/ARCHITECTURE.md` and
`_Docs/Markdowns/README_WellViewer.md` §10 ("Running on a remote host").
Line numbers below were read on the tree this plan was written against; the tree
moves, so re-find each by its symbol before editing.

---

## 1. Why

Every remote run on a Slurm host asks for its own node and gives it back when
the run ends, whether the run succeeded, failed or was stopped. On a cloud
cluster where the node type is scarce (the motivating case: an A10G GPU node,
AWS `g5`, on AWS ParallelCluster), the queue and boot wait can be the longest
part of a run. A run that fails after its node was granted — the motivating
failure was a CUDA out-of-memory error at Cellpose model load, caused by another
process already holding the GPU — throws the node away, and running it again
waits in the queue again.

The user wants to ask for a node once, keep it while they work, and have every
remote run on that host use it, with firm guarantees that a node is never left
running (and billed) by accident.

## 2. Decisions already made with the user

These are settled. Do not reopen them; if implementation shows one cannot be met,
stop and report rather than substituting something else.

1. **Wording.** The node is **granted** (Slurm's own word for an allocation it has
   given) and **released** when given back. The control is **Request a node**;
   the button that gives it back is **Release**. Never "hold", "shut down" or
   "reserve" in user-facing text — "Reserve the whole node (--exclusive)" is an
   existing, different setting. Internally the Slurm job that keeps the node is
   called the **hold job**, and the module is `services/remote_hold.py`.
2. **Where the control lives.** Only in **Manage Remotes**
   (`analyze/remote_dialog.py`). The Analyze pane gets no new control.
3. **Every remote run on the host goes through the granted node** while one is
   granted: a run started from Analyze, a Resume, and every job of the Datasets
   job queue. Starting a run does not change; it simply does not queue. There is
   no "Rerun on the granted node" button — after a failure, Run or Resume
   already lands on the same node.
4. **The node stays granted while runs use it**, up to its time limit.
5. **Idle release, default 30 minutes**, editable per host. `0` turns idle release
   off (the other release rules still apply).
6. **Time limit, default 24 hours**, editable per host in the host's profile in
   Manage Remotes. It is the hold job's Slurm `--time`. It is separate from the
   `--time` a run's own scheduler command carries, which stays where it is (in
   the Scheduler command text) and keeps its current meaning for runs.
7. **Nothing is left open by accident.** Quitting All-Well releases the node; an
   app that disappears (crash, laptop asleep, network lost) has its node released
   within two minutes when no run is using it.
8. **One run at a time on a granted node.** A second run on the same host while
   one is using the node waits for it rather than asking for a node of its own
   (two runs on one A10G is the out-of-memory failure that started this).

## 3. What the user sees

### 3.1 Manage Remotes

Each host whose **Run compute** is *On a node it schedules* (it has a
`compute_launcher`, and that launcher is `srun`) gets, in `ManageRemotesDialog`
(`analyze/remote_dialog.py`, class at ~line 1504), beside the existing Add / Edit
/ Delete / Test / Bootstrap buttons:

- **Request a node** — enabled when the selected host is scheduler-backed and has
  no granted or queued node. Pressing it starts the hold job and shows its
  progress for that host: *Queued (3 min)…*, *Node starting…*, then *Granted*.
  For a host whose launcher is not `srun` the button is disabled with the tooltip
  *Requesting a node needs a Slurm (srun) scheduler command.*
- **Release** — enabled while a node is granted or queued for the selected host.
  While a run is using the node, pressing it asks for confirmation: *A run is
  using this node. Releasing it stops that run. Release anyway?* (a modal; see §9
  on tests and modals).
- A **readout** for the selected host while granted, one line each:
  - `Node gpu-dy-g5xlarge-1 · job 41822 · 1× NVIDIA A10G`
  - `Granted 1 h 12 min ago · 22 h 48 min left` (against the time limit)
  - `Idle 4 min — released after 30 min idle` (or `In use by a run` while busy;
    or `Idle release off` when the setting is *Never*)
  - After it ends: `Released — idle 30 min` (the hold job's own reason, §5.1).

### 3.2 The host profile (Add… / Edit…, `RemoteFormDialog`)

Inside the existing *scheduled* box (`analyze/remote_dialog.py`, near the
`_exclusive_cb` checkbox at ~line 374 and the `_queue_wait_spin` at ~line 402),
add a small **Granted node** group with two fields:

- **Time limit** — a spin box in hours, range 1–720, default **24**, suffix
  ` h`. Tooltip: *How long a node from "Request a node" stays granted at most.
  Slurm ends the node at this limit even if a run is still going; a run that would
  not fit in the time left gets a fresh node instead. If your cluster's partition
  allows less, its limit is used.*
- **Release when idle after** — a spin box in minutes, range 0–1440, default
  **30**, suffix ` min`, special value text *Never* for 0. Tooltip: *A granted
  node that no run has used for this long is released. "Never" keeps it until you
  press Release, quit All-Well, or the time limit runs out.*
- A hint label under the group (wrapping, the theme's muted text colour via
  `ui.theme` — never a hardcoded colour or pixel size, CLAUDE.md §5): *A released
  node is powered down by the cluster on its own schedule (on AWS
  ParallelCluster, after its scale-down idle time). A granted node is billed
  while idle.*

Both values are saved per host in `remotes.toml` (§6.1). The scheduler command's
own `--time`, which applies to each run, stays editable in the Scheduler command
text exactly as now.

### 3.3 The run log

Every remote run on a host with a granted node says which node it used:

- `[remote] scheduler: running on the granted node gpu-dy-g5xlarge-1 (job 41822) — no queue wait.`
- `[remote] scheduler: the granted node gpu-dy-g5xlarge-1 is busy with another run; waiting for it.`
- `[remote] scheduler: this run needs about 9 h and the granted node has 3 h 10 min left; releasing it and requesting a fresh one.`

The hold job's own end, read from its `ended` file (§5.1) and shown in Manage
Remotes and in the next run's log on that host:
`[remote] granted node gpu-dy-g5xlarge-1 (job 41822) released after 30 min idle.` /
`… released: All-Well stopped answering 2 min ago and no run was using it.` /
`… released on request.` / `… reached its 24 h time limit.`

### 3.4 The four ways a granted node ends

All four are enforced on the node (by the hold job's watchdog) or by Slurm, so
none depends on the app still running:

| What happens | Node released | Enforced by |
|---|---|---|
| Release pressed, or All-Well quits | at once | the app cancels the hold job; the watchdog's heartbeat rule is the backstop |
| All-Well gone (crash, sleep, network lost) and no run using the node | within 2 min of the last heartbeat | the watchdog on the node |
| All-Well open, no run using the node | after the idle setting (default 30 min) | the watchdog on the node |
| Time limit reached (default 24 h) | at the limit, even mid-run | Slurm |

An idle setting of *Never* turns off only the third row.

## 4. Slurm facts this design rests on

Each of these decided a part of the design; state them in ARCHITECTURE.md when
documenting it.

- **`--time` is a hard limit on the whole job.** When it elapses Slurm signals
  every step in the job (SIGTERM, then SIGKILL after the cluster's `KillWait`,
  30 s by default). An ordinary user cannot extend a running job's limit
  (`scontrol update TimeLimit` may only lower it). Hence the explicit time limit
  for the hold job, and the "will the run fit" check in §5.5.
- **A partition can cap `--time`.** `sinfo -h -p <partition> -o %l` prints the
  cap (`infinite` on AWS ParallelCluster by default, or e.g. `2-00:00:00`). A
  request above the cap is rejected or left pending depending on the cluster's
  `EnforcePartLimits`, so the hold job asks for `min(time limit, partition cap)`.
- **An `sbatch` allocation does not depend on any client.** An `srun` started
  from a login shell — which is how runs and the early `Reservation` launch today
  — dies with its ssh session and takes its allocation with it. A batch job's
  allocation lives until its batch script exits, it is cancelled, or `--time`
  runs out.
- **A job step can be launched into an existing allocation** from the login node
  with `srun --jobid=<id> --overlap …`. Its output streams back to that `srun`
  client exactly as a run's output does today. Cancelling the step
  (`scancel <id>.<step>`) or killing its `srun` client ends the step only; the
  allocation and its batch script are untouched. `--overlap` lets the step share
  the CPUs the batch script holds (the batch script only sleeps).
- **Resource inheritance inside a step is version-dependent.** A step is given
  the job's GRES by default, but CPU binding defaults changed across Slurm
  20.11–23.x (`--exact`, `--whole`). Row H3 settles it on the user's cluster
  rather than from memory (CLAUDE.md §7.3 #4).
- **A pending batch job runs no code until granted.** So "the app went away while
  the node was still queued" is handled when the batch script first starts (it
  checks the heartbeat before anything else) and by the app cancelling a queued
  hold on quit.

## 5. Design

### 5.1 The hold job (rows H1, H2)

New module **`services/remote_hold.py`**, Qt-free: `services/` may not import Qt,
`review` or `analyze` (`tests/test_architecture_contracts.py`). It sits beside
`services/remote_reservation.py` and should reuse its building blocks:
`_UploadHeartbeat` (`services/remote_heartbeat.py`) for beating, `_remote_quote`
(`services/remote_transport.py`) for quoting, `pack_for_login_shell`
(`services/remote_commands.py`) for long commands, and the same short
control-call path the reservation uses (`cache._ssh_run`). Every new module path
the remote worker needs must be in the upload manifest
(`services/remote_manifest.py`; `tests/test_remote_manifest.py` checks it) — the
hold script is generated text, so this probably adds nothing, but check.

**Directory on the host:** `{remote_staging_dir}/holds/<host-key>/`, where
`<host-key>` is a short, filesystem-safe hash of the host's name and `ssh_target`.
One granted node per host per user: Request is refused while a live hold exists.

| File | Written by | Meaning |
|---|---|---|
| `job` | app, after `sbatch` | the hold's Slurm job id |
| `granted` | hold script, on start | `node=…`, `gpus=…`, `cpus=…`, `granted_at=<epoch>`, `limit_s=…` |
| `heartbeat` | app, every 30 s | its mtime is the app's last sign of life |
| `busy` | app, at the start of each run | present while a run is using, or preparing to use, the node; refreshed by the run's heartbeat |
| `status` | hold script, every poll | `state=busy\|idle` and `idle_s=<seconds>`; the app shows the idle time from it, so it never compares its clock with the node's |
| `release` | app | its presence asks the hold script to exit now |
| `ended` | hold script, on exit | one line saying why (`idle 30 min`, …) |
| `log` | Slurm (`sbatch -o`) | the batch job's own output |

**Submitting.** `sbatch --parsable --job-name=allwell-hold --nodes=1 --ntasks=1
--time=<limit> -o <dir>/log <translated options> <dir>/hold.sh`, sent over ssh.
The options are translated from the host's scheduler command
(`RemoteHost.launch_command`, `services/remote_config.py` ~line 217, which already
adds `--exclusive` when that box is ticked) with the token helpers in
`services/remote_srun_retry.py` (`_split_launcher`, `one_task`, `gpus_for_tasks`,
`with_exclusive`). Rules:

- Keep: partition, account, qos, constraint, gres / gpus / gpus-per-task
  (through `gpus_for_tasks`), cpus-per-task, mem / mem-per-cpu, exclusive /
  oversubscribe, nodelist / exclude.
- Drop: the program word (`srun`), any `--time` / `-t` (replaced by the hold's
  own), `--pty`, output and error redirection (`redirect_launcher_words` lists
  them), `--nodes` / `-N`, and `--ntasks` / `-n` (replaced by 1 and folded into
  CPUs the way `one_task` already does).
- Unknown options are kept, and the submission's stderr is shown verbatim if
  `sbatch` rejects one.

**Time limit.** `min(host.granted_time_limit_h, partition cap)`. The partition is
the scheduler command's `-p` / `--partition`, else the cluster default (the
`sinfo -h -o "%P %l"` row whose name ends in `*`). Parse with
`services/remote_config.parse_slurm_time` (~line 1133; `infinite` / `UNLIMITED` →
no cap) and render with `render_slurm_time`. When the cap lowers the limit, say
so in the readout and the log: `Time limit 24 h, lowered to 12 h by partition
gpu's limit.`

**Queue wait and boot failure.** The request honours the host's **Max queue
wait** (`queue_wait_seconds(host)`, `services/remote_srun_retry.py` ~line 170):
the app polls `squeue -h -j <id> -o "%T|%r|%N"` every 15 s; a job still pending
past the limit is cancelled and reported as `no node was granted within 30 min,
this host's Max queue wait`. A node that fails to boot is retried with
`--exclude=<node>`, mirroring `wrap_srun_launch`'s boot retry
(`services/remote_srun_retry.py` ~line 422). Read that function and
`tests/test_srun_boot_retry.py` for the signals it already recognises, and reuse
its constants (`BOOT_RETRY_PAUSE_S`, `START_SLACK_S`, `PENDING_FALLBACK_S`). The
polling has to run on the app's side because a pending job runs no code.

**The hold script.** A POSIX `sh` script, composed by one function in
`services/remote_hold.py` and written into the hold directory, so tests can run
it under `sh` with a fake `squeue`, a fake `nvidia-smi` and a controllable clock,
the way `services/remote_wait_snippets.py` is tested. Every interval and
tolerance is a parameter of the composing function, so tests can shorten them.

1. **On start:** write `granted` (node from `$SLURMD_NODENAME`, GPUs from
   `nvidia-smi -L` when it exists, CPUs from `$SLURM_CPUS_ON_NODE`, `granted_at`,
   `limit_s`, `job`) and `status`, and print the existing `ALLOCATION_GRANTED`
   line (`services/remote_srun_retry.py` ~line 149) with the job, node and
   partition, so code that already reads that line reads it here too. A hold
   whose app is already gone when the node is granted needs no separate check:
   the loop's first pass finds the heartbeat stale (or missing for the check's
   tolerance) and ends it.
2. **Every poll (15 s):**
   - `release` exists → `ended` = `released on request`; exit 0. (A SIGTERM that
     arrives with `release` already written is reported the same way: the app
     writes `release` and then `scancel`s.)
   - `busy` exists and is fresher than `MID_BATCH_HEARTBEAT_STALE_S` → in use;
     `status` = busy; the idle clock restarts. A `busy` older than that is a run
     whose app died mid-run: count it as not busy. The run's own existing
     watchdogs already end such a run; this only stops the hold waiting on a
     dead marker.
   - Not busy and `heartbeat` stale (`UPLOAD_HEARTBEAT_STALE_S`) → `ended` =
     `All-Well stopped answering (2 min without a heartbeat) and no run was using
     it`; exit 0.
   - Not busy, idle limit > 0, and idle time ≥ idle limit → `ended` = `idle 30
     min`; exit 0.
   Both staleness checks are `remote_wait_snippets._heartbeat_stale_snippet`, the
   one the upload wait and the batch driver already use (it reads "now" from the
   file server that stamped the file, copes with a non-GNU `stat`, and tolerates
   a file that has not appeared yet), so the clock-skew risk in §10 is already
   covered for the heartbeat. The idle clock and elapsed time use the node's own
   `date` against the node's own `date`.
3. **On SIGTERM** (the time limit or `scancel`): write `ended` = `reached its
   <n> h time limit` when elapsed ≥ `limit_s` − 60 s, else `cancelled`; exit. The
   script naps with `sleep & wait`, so the signal is answered at once.

The batch script exiting releases the allocation. Nothing else is needed for the
node to be given back; the cluster powers it down on its own schedule.

**The app's side — `GrantedNode`** in `services/remote_hold.py`, one per host,
kept in a small module-level registry keyed by host name (acceptable in
`services/`; it must not be a loose `app._*` attribute, CLAUDE.md §5):

- `request(host, log_fn) -> GrantedNode` — creates the directory, beats once,
  starts the heartbeat thread, writes `hold.sh`, submits, then polls until
  granted, failed, or the queue wait runs out. Runs on a worker thread. The
  dialog hears about state changes through a queued Qt signal on the dialog side
  (CLAUDE.md §5: a worker thread has no event loop, so never
  `QTimer.singleShot(0, …)` from it).
- `find(host) -> GrantedNode | None` — for a host with no live handle (another
  window, or a crash inside the two-minute window): reads `job`, asks
  `squeue -h -j <id> -o %T`, and adopts a live hold by starting its heartbeat
  thread. A dead one has its `ended` read into the log and its directory removed.
- `state()` — `queued`, `starting`, `granted`, `busy` or `ended`, with the readout
  fields (node, GPUs, CPUs, `granted_at`, `limit_s`, `last_used`, the ended
  reason). One ssh call reading all the files. Called every 10 s while Manage
  Remotes is open and once before each run; never more often.
- `release(reason)` — writes `release`, then `scancel -u "$USER" <id>` behind the
  `_only_my_job` check (`services/remote_jobs.py` ~line 26), stops the heartbeat
  thread. Idempotent.
- `acquire_for_run(run_heartbeat_path) -> RunLease | None` — creates `busy`
  atomically (`mkdir` of a lock directory, or `set -C` noclobber; two windows
  must not both succeed) and arranges for it to be refreshed with the run's own
  heartbeat. `None` when busy; the caller waits (§5.4). `RunLease.release()`
  removes it. The lease is taken **at the start of the run, before staging and
  upload**, not when the pipeline starts: a large plate can take longer than the
  idle limit to prepare and upload, and the idle clock must not run during that.
  It is released in the run's `finally`, on every exit path.

### 5.2 A run on a granted node is a job step (row H3)

When the host's `GrantedNode` is `granted` and the run holds the lease, its
launcher is `srun --jobid=<id> --overlap --nodes=1 --ntasks=1 <step options>`
instead of `host.launch_command`. Introduce **one seam** that every launch site
calls, so no site can forget it — `services/remote_hold.effective_launcher(host)`
returning either `host.launch_command` or the step launcher. The sites that read
`host.launch_command` today (from `grep -rn "launch_command\b" services analyze`;
re-run it):

| Site | What it does | Change |
|---|---|---|
| `services/remote_runner.py` `_compose_launch` (~639) | single-shot launch | use the seam |
| `services/remote_runner.py` ~392, ~667 (`_scheduler_handoff_lines`) | the queue hand-off log lines | log the granted-node line (§3.3) instead |
| `services/remote_process.py` ~747 (`scale_launcher_time` for batches) | batched driver launch (`_compose_driver_cmd`, ~756) | use the seam; a step carries no `--time` of its own (the allocation's limit governs) |
| `services/remote_process.py` ~700 (`launcher_time_seconds`) | per-batch time estimate | unchanged — still read from the scheduler command; feeds §5.5 |
| `services/remote_reservation.py` ~263 (`Reservation.request`) | the early request | skipped while a node is granted; the call is `services/pipeline_runner.py` ~546 (`reserve_allocation`) |
| `services/scheduler_probe.py` ~182 | the Test button's probe | add a mode that runs the probe through the granted node (row H3) |

**`wrap_srun_launch`** (`services/remote_srun_retry.py` ~422) wraps every `srun`
launcher with the awake-node preference, the boot retry and the queue deadline.
None of those applies to a step in an existing allocation, and the awake-node
preference may add node-selection words that `srun --jobid` rejects. Add an
explicit branch for a launcher carrying `--jobid` that records the PID and runs
the step directly, with a test that such a launcher comes out with none of the
retry machinery.

**Step options.** Which of `--whole`, `--gres=gpu:<n>`, `--cpus-per-task=<n>` the
step needs to see the GPU and the CPUs is settled in H3 by running the Test probe
(`services/scheduler_probe.ProbeRun`) through a granted node on the user's
cluster and reading what it reports (Torch sees CUDA; the CPU count). Start from:
the step inherits the job's GRES by default, and passes `--cpus-per-task` equal to
the allocation's (`cpus` in the `granted` file) so the pipeline's worker count is
what it would have been on its own node.

**The step's sidecar.** The inner scripts write `${SLURM_JOB_ID}` to
`<pidfile>.slurm` (`services/remote_commands.py` ~401, ~489, ~526). A run on a
granted node must write `${SLURM_JOB_ID}.${SLURM_STEP_ID}`, so everything that
cancels "the run's job" cancels the step. Pass an explicit flag from the step
launch branch to the composers so only granted-node runs write the step form;
do not switch on `SLURM_STEP_ID` being set, because ordinary `srun` launches
set it too, and their sidecars must not change.

### 5.3 Cancelling a run must never cancel the granted node (rows H4, H5, H6)

This is the highest-risk part. Today every teardown path assumes the sidecar
names a job the run owns outright, and cancels it. On a granted node that job is
the hold, so one missed path releases the node — silently, because the next run
simply queues again, which reads as the feature not working rather than as a bug.
Every site below must cancel the **step** (or kill the run's own processes) and
leave the hold alone.

Enumerate with `grep -rn "scancel\|squeue" services`. At the time of writing that
finds mentions in `pipeline_runner.py`, `remote_jobs.py`, `remote_liveness.py`,
`remote_sweep.py`, `remote_srun_retry.py`, `remote_reservation.py`,
`remote_reuse.py`, `run_evidence.py`, `remote_commands.py`, `remote_config.py`,
`remote_runner.py` and `remote_suspend.py`; many are comments, and the H5 guard
decides which are calls. The known call paths:

- **`services/remote_jobs.close_remote_job`** (~77): reads the sidecar and
  `scancel`s it. For `job.step`, `scancel <job>.<step>` ends only the step. The
  owner check `_only_my_job` runs `squeue -j`, which takes a job id, not a step
  id: give it the part before the dot and give `scancel` the whole id. Its log
  lines say "step" for a step.
- **`services/remote_jobs.kill_remote_pipeline`** (~49): kills the PID in the
  pidfile, which is the run's `srun` client or pipeline. Verify it can never name
  the batch script's PID.
- **`services/remote_liveness`** (`probe` ~76, `cancel_pending` ~122): asks
  whether the run's job is alive and cancels a pending one. For a step: alive =
  `squeue -h -s -j <job> -o %i` lists `<job>.<step>`. A step has no pending state,
  so `cancel_pending` must never cancel the hold when given a step id.
- **`services/remote_reuse._REMOVE_IF_IDLE`** (the clean-up of earlier attempts' run
  directories): not in the first version of this plan. It asked `squeue -j` about the
  sidecar's id, which says "Invalid job id" for a step, which it reads as a finished
  job — and removed the run directory a running step was writing into. Now asks as a
  step (`squeue_listing`).
- **`services/remote_sweep`** (`sweep_orphaned_allocation` ~52,
  `sweep_allocations` ~149, `claim_run_dirs` ~261): finds jobs a crashed session
  left through run-directory sidecars, and cancels them. It must (a) cancel only
  the step for a `job.step` sidecar, and (b) never cancel a job id that is a live
  hold of this user — read the holds directory's `job` files and exclude them.
- **`services/pipeline_runner.py`**: the Stop path (`_signal_run`, ~1046), the
  end-of-run close (~1549), and app close (`wait_for_teardown` ~1924, called
  from `allwell.py` ~172). These call the functions above; check that each
  reaches only the step.
- **`services/remote_reservation.Reservation.release`** (~409): not reached while
  a node is granted, because the early request is skipped; assert that in a test.
- **`services/remote_suspend.py`**: Suspend asks the pipeline to stop at a
  checkpoint and exit. On a granted node the step exits and the node stays
  granted, which is right; verify nothing on the Suspend path cancels the job.

**Guard (H5).** An AST guard in the style of the existing ones (use
`tests/_repo_scan.parse_source`, never `ast.parse(path.read_text())`; CLAUDE.md
§2 rule 2) that finds every string literal in `services/` containing `scancel`
and requires its enclosing function to be on a roster with a written reason
saying how it handles a step id. Allowlist the function, not the file (§7.5 #10),
and add a liveness check that every roster entry still names a function that
contains a `scancel`. Add a case to `tests/test_guards_bite.py`: a synthetic
function with a bare `scancel "$_jid"` and no roster entry must fail the guard.
Prove it with `scripts/prove_guard.sh` against `origin/main` — the `scancel`
sites exist on the base, so this proof measures something (§7.5 #1) — and check
that the site count it prints matches your own count.

### 5.4 One run at a time (row H7)

`acquire_for_run` returning `None` means another run holds the lease. The run
logs the busy line (§3.3) and retries every 15 s until it gets the lease, bounded
by the host's Max queue wait (`0` = wait indefinitely) and honouring Stop. When
the wait runs out it fails with the queue-wait wording, naming the busy node. It
never falls back to asking for a node of its own (decision 8).

The Datasets job queue already runs one remote job at a time
(`services/job_lanes.py`; `services/job_queue.py` ~778), so its jobs take the
lease one after another without contending. Two app windows are what the lease
protects against.

### 5.5 The time limit and long runs (row H8)

Before the step launch, compare the run's own estimate with the time the node has
left (`granted_at + limit_s − now`, from `state()`):

- Single-shot: `launcher_time_seconds(host.compute_launcher)`
  (`services/remote_config.py` ~1170) — the scheduler command's own `--time`.
- Batched: the per-batch time (`services/remote_process.py` ~700) times the batch
  upper bound (`services/remote_reservation.batches_upper_bound`).
- No `--time` in the scheduler command: no estimate; the run starts on the
  granted node. If the limit ends it, it ends like any walltime kill today and
  Resume continues it.

When the estimate exceeds the time left, log the line in §3.3, release the node,
request a fresh one (with a fresh time limit), and run there. Never start a run
Slurm is known to kill part-way.

### 5.6 App close (row H10)

`allwell.py` (`_stop_analyze_runner`, ~line 160–175) stops a busy runner and
waits for its teardown. After that, release every granted or queued hold this app
instance has a handle for, bounded — reuse `_wait_with_notice` so the user sees
why closing takes a moment, with a total budget of at most 10 s. A release that
cannot reach the host is acceptable: the watchdog's two-minute heartbeat rule
releases the node anyway. A **Suspended** run is not using the node, so its node
is released on quit too; Resume later requests a node the ordinary way, or the
user presses Request a node first.

A hold still **queued** at quit is cancelled the same way; if that cancel does not
land, the start-up heartbeat check (§5.1 step 1) ends it the moment it is granted.

### 5.7 Reopening the app (row H10)

When Manage Remotes opens, and before a run on a scheduler-backed host, call
`GrantedNode.find(host)` for hosts with no live handle. Because quitting releases
holds, this mainly finds a node granted from another open window, or one left by
a crash still inside its two-minute window. A found hold is adopted, so it keeps
being used rather than released out from under a live user.

## 6. Settings and data

### 6.1 `RemoteHost` fields (row H9)

In `services/remote_config.py` (`class RemoteHost` ~line 95), beside
`exclusive_node` (~172) and `queue_wait_timeout_min` (~200):

```python
granted_time_limit_h: int = 24
"""How long a node from Manage Remotes' "Request a node" stays granted at most:
the hold job's Slurm --time, lowered to the partition's limit when that is
shorter. Separate from the scheduler command's own --time, which describes one
run. Slurm ends the node at this limit even mid-run, so a run that would not fit
in the time left gets a fresh node (services.remote_hold)."""

granted_idle_release_min: int = 30
"""Minutes a granted node may go unused before the hold job releases it; 0 never
releases it for idleness (the other release rules still apply)."""
```

Serialise in `_format_block` (~714) only when different from the default, as
`exclusive_node` is (~737); parse in `_parse_entry` (~909) with the dataclass
default as the fallback and `int()` coercion, as `queue_wait_timeout_min` is
(~940). Clamp on load to the form's ranges (1–720 h, 0–1440 min), so a
hand-edited file cannot ask Slurm for nonsense. Tests:
`tests/test_remote_config.py`, `tests/test_remote_config_writer.py`.

### 6.2 The form (row H11)

`RemoteFormDialog` (`analyze/remote_dialog.py` ~185): add the two spin boxes to
the scheduled box after the queue-wait row (~412), and read them back where the
host is built (~670, next to `exclusive_node=` and `queue_wait_timeout_min=`).
Tests: `tests/test_remote_dialog.py`, `tests/test_remote_dialog_defects.py`. A
test that sets a spin box and asserts what reached the saved `RemoteHost` is
*behaviour* and must not join the UI roster (CLAUDE.md §2), or CI stops running it.

## 7. Documentation (row H16)

- **README_WellViewer.md §10** (~line 1690): a new subsection *Keeping a node
  between runs*, in the user's terms — Request a node, what the readout shows,
  that every run on the host uses it, one run at a time, the four release rules
  as the table in §3.4, the two settings, that an idle granted node is billed,
  and that the time limit stops a run in progress (and Resume continues it).
- **ARCHITECTURE.md**, the remote section that covers `remote_reservation`: the
  hold job, its directory and files, the launch seam, the step-only cancellation
  rule and its guard, the lease taken before staging, and the Slurm facts in §4.
  Present tense; no version markers
  (`tests/test_doc_version_sync.py::test_no_introduced_in_version_markers`).
- Reconcile the existing paragraphs that say every run asks for its own node
  (README §10 "A run is always one task on one node", "Max queue wait", "A node
  that fails while the run is going is not asked for again") so they do not
  contradict the new section (CLAUDE.md §7.3 #6). Grep the docs for "its own
  node" and "asks for" after writing.

## 8. Order of work

Each step is one commit (or a few) with its tests, and updates its row here in
the same commit. Push to the session branch after each; open the PR only at the
end (CLAUDE.md §2, §6). Stacking on the branch that carries this file
(`claude/held-node-plan`) is fine; it has no PR yet.

1. H9 settings and the H11 form fields — small and visible.
2. H1 hold script and H15 its tests under `sh` (no cluster needed).
3. H2 submit, poll, boot retry and queue wait, with fake transports (CLAUDE.md §2
   rule 3: no test spawns ssh; use the `offline_transport` fixture).
4. H4, H5, H6 — step-only cancellation and its guard — **before** any run is
   routed to a granted node, so the routing can never cancel a hold.
5. H3 launch seam, H7 lease, H8 time check, H12 log lines.
6. H10 app close and reopen; the Manage Remotes buttons and readout (H11).
7. H13 job-queue check and the H14 caller-sequence test.
8. H16 docs, the version bump, the full verification in §9, then the PR.
9. H17 on the user's cluster: the user runs it; the PR body lists what to look
   for.

## 9. Verification

- **Lanes.** This is remote work: `python scripts/lane_needed.py remote` will say
  yes; run `scripts/test.sh --remote`, the guard family (the list in CLAUDE.md
  §2), `ruff check .`, and the full `scripts/test.sh` fast lane (the change
  touches shared launch and teardown code, so it is broad). The Manage Remotes
  widgets are UI, so also run `scripts/test.sh --ui`, which CI does not run.
- **No modal dialogs in tests.** The Release confirmation is a modal; the autouse
  `_no_modal_dialogs` fixture fails a test that reaches one. Put the decision
  behind a function the test can stub, as other confirmations in
  `analyze/remote_dialog.py` do.
- **Prove each behaviour test fails without its feature** (CLAUDE.md §7.7 #2,
  §7.8): for each row, name the edit that would break it, make it on HEAD in a
  worktree, and watch the test fail with an `AssertionError` that says the right
  thing. Record the edit and the failure in the row's Evidence column.
- **Name the wrong implementation that still passes** each test before keeping it
  (§7.8). The likely ones here: a release test whose fake clock never passes the
  idle limit; a step-cancel test whose sidecar holds a bare job id, so it cannot
  tell `scancel <job>` from `scancel <job>.<step>`; a lease test with one caller,
  which cannot fail on a non-atomic create.
- **Caller sequence (H14).** Drive the real call order — request a node, start a
  run (lease, step launch), make it fail, run again — through `RemoteRunner` and
  `PipelineRunner` with a fake transport, and assert that the second run's
  launcher is the step launcher for the *same* job id and that no `sbatch` or
  plain `srun` allocation was issued in between. This is the test that fails if
  any teardown path cancels the hold.
- **Version.** One patch bump in `version.py` for the PR; run
  `git diff origin/main -- version.py` first, and again before the push that
  opens the PR (CLAUDE.md §6).

## 10. Risks and unknowns

- **Step options for the GPU** (H3) can only be settled on the user's cluster.
  The Test-through-the-granted-node probe is the deliverable that settles it; do
  not hard-code a guess without saying so in the PR body.
- **`srun --jobid` from the login node** needs the cluster to allow steps from
  outside the allocation (it does by default). If it is refused, the fallback is
  for the hold script to run a command file itself — the existing `Reservation`
  hand-over pattern (`services/remote_reservation.py` `hand_over`, ~383) — with
  output read from a file. That is a larger change: stop and report before
  taking it.
- **Clock skew** between the node and the login node affects the mtime
  comparisons. The hold script compares mtimes with the node's own `date +%s`,
  while the files are touched from the login node over the shared filesystem; a
  skew of more than ~30 s would shorten or lengthen the two-minute rule. Measure
  it on the cluster in H17 (`date +%s` on both) and widen the tolerance if
  needed.
- **A stalling shared filesystem** delays heartbeat touches. The reasoning around
  `BEAT_TIMEOUT_S` in `services/remote_heartbeat.py` already bounds how long a
  beat may take; the two-minute rule must keep the same margin, and a test
  should hold that sum, as `tests/test_a_slow_heartbeat_is_measured_and_tolerated.py`
  does for the run heartbeat.

## 11. Rows

| ID | Item | State | Where | Evidence |
|---|---|---|---|---|
| H1 | Hold script: start-up heartbeat check, `granted` file, loop with the `release`, busy, heartbeat-silent (2 min, no run) and idle-limit rules, `ended` reason, SIGTERM handling | done | `services/remote_hold.py` | `tests/test_remote_hold_script.py` runs the script under `sh`. Mutated on HEAD, each fails with its own message: ignoring `busy` ("released while a run was using it", "released for a silent app while a run was using the node"); trusting a stale `busy` (still running after 25 s); idle 0 meaning at once ("idle 0 (Never) released the node for idleness"); every SIGTERM the time limit (`'reached its 1 h time limit' == 'cancelled'`) |
| H2 | Submit with `sbatch`: translate the scheduler command's options, drop its `--time`, time limit = min(setting, partition cap), poll `squeue`, Max queue wait, boot-failure retry with `--exclude` | done | `services/remote_hold.py`, `services/remote_srun_retry.py` | `tests/test_remote_hold_request.py` runs the real scripts under bash with stand-in sbatch/scontrol/scancel/sinfo/squeue. Mutated on HEAD: keeping the command's `--time` (`['--time=4:00…','--time=2-00:00:00'] == ['--time=2-00:00:00']`); boot retry without `--exclude` (`['--exclude=slow1'] == ['--exclude=slow1,dead-1']`); no queue-wait check (`the request kept waiting`); release without scancel (`the queued job must be cancelled`, 3 tests). It also caught `state_script` exiting 1 when a file was missing. Boot failure is read from `scontrol show job -o` (BOOT_FAIL / NODE_FAIL), not `sacct`, because `sacct` needs accounting enabled. |
| H3 | Launch seam (`effective_launcher`) at every launch site; `wrap_srun_launch` branch for `--jobid`; step runs write a `job.step` sidecar; GPU/CPU step options settled by Test through the granted node | partial | `services/remote_runner.py`, `services/remote_process.py`, `services/remote_commands.py`, `services/remote_srun_retry.py`, `services/scheduler_probe.py` | Done: `RemoteRunner.launcher()` is the one seam (`step_target` set → `step_launcher(StepTarget)`); `_launch` starts a `--jobid` launcher plainly, not through `wrap_srun_launch` (the plan's "branch in the wrapper" became a branch in `_launch`, the wrapper's only caller); `<job>.<step>` sidecar chosen by the launcher, not by `SLURM_STEP_ID`; hand-off line announces no queue wait; `ProbeRun(host, launcher=…)` for Test through the node. `tests/test_a_run_on_a_granted_node_is_a_step.py` (15 tests); mutated on HEAD each fails: step through the wrapper, bare job id in the sidecar, request line for a step, launcher ignoring the node. `tests/test_every_launch_goes_through_the_launcher_seam.py` roster guard: `prove_guard.sh` on origin/main fires on `_compose_launch`, `spawn`, `_launch_driver`, the three sites converted. **Not done:** nothing sets `step_target` yet (H7); the step's CPU/GRES options are my default (`--cpus-per-task` and `--gres` equal to the job's, read from `scontrol`) and are verified only on the user's cluster (H17); the Test-through-the-node button is H11. |
| H4 | `close_remote_job`, liveness `probe` / `cancel_pending`, Stop, end-of-run and app-close teardown cancel only the step | done | `services/remote_jobs.py`, `services/remote_liveness.py`, `services/pipeline_runner.py` | `tests/test_a_run_never_cancels_the_granted_node.py` (19 tests, real shell against stand-in ssh/squeue/scancel whose `squeue` rejects `-j <job>.<step>` as Slurm does). Mutated on HEAD, each fails its own test: cancelling the job part (`closing_a_run_on_a_granted_node_ends_its_step_and_leaves_the_node`); no hold check in `close_remote_job` (`a_sidecar_naming_the_nodes_own_job_is_never_cancelled`); owner check on the step id (`a_step_of_a_job_that_is_someone_elses_is_not_cancelled`); probe asking `squeue -j` (`a_run_whose_step_is_listed_is_alive`); a hold check that refuses everything fails 8 tests including `an_ordinary_runs_job_is_still_cancelled`. New `services/slurm_ids.py` holds the shared `squeue_listing` / `hold_check_shell`. Not covered by a test: the stand-in `squeue -s` is written from the manual, not from a cluster — H17 checks `squeue -s<job>.<step>` on the real one. |
| H5 | Guard: every `scancel` call site in `services/` is on a per-function roster saying how it handles a step id; liveness check; `test_guards_bite.py` case; proved with `scripts/prove_guard.sh` | done | `tests/` | `tests/test_every_job_cancel_names_how_it_handles_a_step.py` (roster per site + liveness) and 8 cases in `tests/test_guards_bite.py`. `scripts/prove_guard.sh` against origin/main: PROVEN, AssertionError naming `services/remote_liveness.py::_script` and `services/remote_reuse.py::<module>:_REMOVE_IF_IDLE`, the two step-unaware sites this change converted (a third, `remote_sweep._target_script`, shares a roster key with its `scancel`). Found while writing the tests: `remote_reuse._REMOVE_IF_IDLE` is a `str.format` template, so the helper's braces had to be doubled — a `KeyError` no existing test reached. |
| H6 | Orphan sweep cancels only the step for a `job.step` sidecar and never a live hold's job id | done | `services/remote_sweep.py` | same file as H4: `the_sweep_of_a_leftover_step_cancels_the_step_and_not_the_node`, `..._does_not_cancel_a_live_step_whose_app_is_beating`, `..._forgets_a_step_that_has_ended`, `..._leaves_a_sidecar_that_names_the_node_itself`, `..._still_cancels_an_ordinary_leftover_job`; sweeping `squeue -j` or dropping the `AW_HOLDID` branch each fails its test. |
| H7 | Run lease: atomic `busy` taken before staging, refreshed by the run heartbeat, released on every exit; a second run waits (bounded by Max queue wait, honours Stop) and never asks for a node of its own | done | `services/remote_hold.py`, `services/pipeline_runner.py` | `tests/test_a_run_claims_the_granted_node.py` (20 tests, real claim script under bash against the shared fake cluster `tests/_hold_cluster.py`). Mutated on HEAD, each fails its test: a busy node returning None instead of waiting (`..._second_run_waits_for_the_node_and_never_asks_for_its_own`, `..._stays_busy_ends_the_wait_with_the_queue_wait...`); trusting a stale marker; release leaving the marker (3 tests). **Limit:** the atomic-create test races 40 processes six times and fails a check-then-create claim in about one run in three — a race cannot be forced from outside the script, so it is evidence of the property, not proof. Wiring: `RemoteRunner.claim_granted_node` is the facade (like `reserve_allocation`); `GrantedRun` (services/remote_hold.py) claims before anything is staged, `_EarlyAllocation` skips the early request while a node is claimed, `PipelineRunner` releases it after the run's step is closed. Twenty-odd existing tests used hand-built `RemoteRunner` stand-ins that needed `claim_granted_node` / `launcher` added; their `launcher` follows the stand-in's host, as the real one does. |
| H8 | Time check: a run whose estimate exceeds the time left gets a fresh granted node; with no `--time`, the run starts and Resume continues it after a limit kill | done | `services/remote_hold.py`, `services/remote_runner.py` | same file: `..._that_would_outlast_the_node_gets_a_fresh_one_and_runs_there` (release, one new sbatch, the run's target is the new job; mutating `fits` to always true or dropping the re-claim after the request each fails it), `..._that_fits_...stays_on_the_node`, `..._with_no_time_cannot_be_compared...`, `..._time_left_is_read_on_the_nodes_clock`. The state script now prints the node's `date +%s` so time left never compares two machines' clocks. A run on a granted node whose scheduler command has no `--time` is not compared (it starts, and Resume continues it after a limit kill). |
| H9 | `RemoteHost.granted_time_limit_h` (24) and `granted_idle_release_min` (30): defaults, saved only when changed, parsed, clamped | done | `services/remote_config.py` | `tests/test_remote_config_writer.py` (round trip, written only when changed, clamped on load): mutating the writer's default check, or the clamp, fails 3 tests each; no clamp = 3 of 5 fail |
| H10 | App close releases this instance's granted and queued holds within a bounded wait; Manage Remotes and the run path adopt a live hold through `find` | done | `allwell.py`, `services/remote_hold.py` | `tests/test_quitting_gives_back_the_granted_node.py` (9 tests). Mutated on HEAD, each fails its test: releasing every handle (cancels a node only found — `..._does_not_cancel_a_node_it_only_found`); releasing none (4 tests); `attach` not beating (`..._found_granted_is_beaten_for_...`); the close never calling the hook (AST read of `AllWellApp.closeEvent`, which also checks it runs after `_stop_analyze_runner`, with the notice); a join with no deadline (`..._cannot_hold_the_close_up`). **Changed from the plan:** quitting releases only nodes this window *requested* (`GrantedNode.owned`); one it only found via `attach()` loses this window's heartbeat and ends two minutes after the last beat, so closing one window cannot cancel a node another is using. Adopt-on-reopen is `GrantedNode.attach()`, called by every run's claim; Manage Remotes calls it when it opens (H11). |
| H11 | Host profile: Time limit (h) and Release when idle after (min, *Never*) with the hint; Manage Remotes: Request a node / Release, state and readout, confirmation while a run uses the node | done | `analyze/remote_dialog.py` | Form half: see H9. Panel half: `analyze/granted_node_panel.py` (its own module; `remote_dialog.py` only mounts it and passes the selected host) with `tests/test_manage_remotes_granted_node.py` (11 tests, the real panel and `GrantedNode` against the stand-in cluster; only the transport is replaced). Mutated on HEAD, each fails its test: Request connected to nothing; Release not asking before stopping a run; `attach` not beating for a found node; Test probing the host's own launcher instead of the node's; Request offered for any host (3 tests). Readout text is `remote_hold.describe_state` (Qt-free, matches the plan's wording). The Test button is a separate **Test the node** that opens the existing scheduler Test through `step_launcher`. | **Changed after merging main's "no text inside entry fields" guard:** the two spin boxes are bare numbers, with the unit and what 0 means in their labels (*Time limit (hours)*, *Release when idle after (min, 0 = never)*), not a suffix and a *Never* word.
| H12 | Run log lines: running on the granted node, busy and waiting, fresh node for time; the hold's `ended` reason surfaced | done | `services/remote_runner.py`, `services/remote_hold.py` | Lines asserted in tests: `running on the granted node (job N) — no queue wait` (test_a_run_on_a_granted_node_is_a_step), `using <node> (<gpus>), job N`, `the granted node is busy with another run; waiting for it`, `needs about 12 h 0 min and the granted node has … left; releasing it and requesting a fresh one`, `was released: <reason>; this run asks for a node of its own`. An unreachable host gets a warning that says no raw ssh text (quoting it made the run's headline read a connection failure it had not had; caught by test_a_remote_run_says_how_it_ended). |
| H13 | Datasets job queue: every remote job goes through the granted node; consecutive jobs take the lease in turn | done | `services/job_queue.py` (verify; likely no change) | Verified by reading, no code change: `datasets/job_queue` → `analyze.run_spec.start_from_queue` → `start_run` → `PipelineRunner._run_pipeline_remote`, the same claim as a run from the Analyze form. Each job claims and releases the lease in turn (`services/job_lanes` already runs one remote job at a time). **Simplified from the plan:** the queue does not hold the lease across jobs, so the idle clock restarts between jobs; with the default 30 min the gap is seconds, and a user who sets the idle limit to a minute or two could see the node released between two slow-to-start jobs. |
| H14 | Caller-sequence test: request → run fails → run again uses the same job id, with no new allocation in between | done | `tests/` | `tests/test_a_failed_run_is_rerun_on_the_same_granted_node.py` (3 tests): a real `PipelineRunner._run_pipeline_remote` and `RemoteRunner` on `FakeRemote` with the stand-in scheduler. Request → run 1 claims the node before staging, launches `srun --jobid=4001 --overlap … --cpus-per-task=8 --gres=gpu:1`, fails; the close cancels only `4001.3`; the node is still RUNNING and not busy; run 2 launches on the same job; one `sbatch` in all; no early request. A second scenario writes the bare job id to the sidecar and the node is still not cancelled. Mutated on HEAD, each fails: no claim, claim after staging (fails through the early-request assertion; the ordering assertion is not independently proven), early request not skipped, claim never released (`the run is over and the node still reads as in use`), no hold check in the close, a launcher ignoring the node. The step sidecar is written by the test (what the inner script does), not by a real `srun --jobid`: that half is H17. |
| H15 | Hold-script tests, one per release-table row, plus: no idle release while busy; a stale `busy` counts as not busy; *Never* disables only idle release; the app-gone-while-queued start-up exit | done | `tests/` | same file, one test per release-table row plus the traps above; 12 tests, ~30 s |
| H16 | README §10 subsection and the ARCHITECTURE remote section; reconcile paragraphs that say every run asks for its own node | done | `_Docs/Markdowns/` | README §10 gets *Keeping a node between runs* (after *Max queue wait*, which it extends) with the four-row release table, the settings and the billing note; three neighbouring sentences were reconciled ("A run is always one task on one node", "A node that fails while the run is going", *Max queue wait* now also bounds Request a node and the wait for a busy node). ARCHITECTURE gets `remote_hold.py`, `slurm_ids.py` and `granted_node_panel.py` and a sentence on the config row. `test_doc_version_sync` green. Checked by grep for "its own node" / "asks for": line 1794 and the changelog-style paragraph near the end of the README describe an ordinary run and stay true. |
| H17 | On the user's cluster: request an A10G node, run Test through it (GPU visible), fail a run, run again on the same job id, watch the idle release and the two-minute rule after quitting, measure clock skew | todo | the user's cluster | — |

## 12. Adversarial review of the pushed diff

A fresh reviewer read the diff for defects (CLAUDE.md §7.3). Each finding was reproduced or read against the tree before
being worked; the fixes are in the commit that adds this section, each with a test that fails without it (mutated on HEAD).

| # | Finding | State | Evidence / reason |
|---|---|---|---|
| R1 | `--output=~/…` single-quoted, so `~` never expanded and the hold's log path did not exist (every default host) | done | reproduced; `--output` and the path are now two words; `test_a_request_sends_sbatch_…` asserts an expanded absolute path and no `~` anywhere in the sbatch argv |
| R2 | A batched run longer than a node's limit discarded the user's node for a fresh one that could not hold it either | done | `check_fit` renews only when the run would fit a fresh node's limit; otherwise it warns and stays (`…longer_than_any_node…`) |
| R3 | An unanswered `scontrol` read as "ended": a second node could be requested beside a live one, and a run took its own | done | new `unknown` phase (live); `Invalid job id` is the only answer that means gone (`…did_not_answer…`, 3 tests) |
| R4 | `owned` outlived a release; release acted on whatever job the shared directory named; `check_fit` could cancel a found node | done | release is identity-checked (`AW_MOVED`); ownership ends with the node; a node this window did not request is never replaced (`…acts_only_on_the_job_it_asked_for`, `…did_not_request_is_not_replaced…`, `…released_its_node_does_not_cancel…`) |
| R5 | A handle kept the host it was made with after the host was edited | done | `node_for` refreshes host and paths when no node is live or owned. **No test:** the refresh is read, not driven; the risk is a stale setting, not a wrong cancel |
| R6 | A GPU asked for with `--gpus` / `--gpus-per-task` gave the step no `--gres`: the pipeline ran on the CPU silently | done | `_job_gres` reads `TresPerNode`, `TresPerJob`, `TresPerTask`, `Gres` (3 cases) |
| R7 | The idle time mixed the file server's clock with the node's (`_aw_now` is assigned by the heartbeat check) | done | renamed `_aw_tick`; `test_the_idle_clock_is_this_nodes_own…` is a read of the generated script — a skewed clock cannot be forced from outside |
| R8 | A heartbeat still in its ssh when a run ended could re-create the busy marker; a failing lease constructor left it | done | `stop(wait_s=10)` before the `rm`; constructor cleans up (`…beat_still_in_flight…`) |
| R9 | Two claimants that both find a stale marker can interleave rm/create and both win | declined | needs two windows to claim within the same instant a marker has been dead for ten minutes; the fix (a lock directory) adds a second marker whose own staleness would need the same handling. The window is recorded here, not closed |
| R10 | Time left was the claim-time reading, hours before staging ends | done | `check_fit` re-reads the state (`…time_left_is_read_again…`) |
| R11 | `request()` overwrote a running heartbeat; an idle-released node's heartbeat kept beating; the close notice showed for any handle | done | `request()` stops it, `state()` stops it on `ended`, the notice keys on `GrantedNode.active` (2 tests) |
| R12 | srun's `-u` (unbuffered) passed to sbatch means `--usage`; `--exclude=?` built for an unnamed node | done | both dropped (`…unbuffered_flag_is_not_passed…`; the `?` guard is read, not driven) |
| R13 | Docs claimed two minutes though a run that crashed mid-run counts as using the node for up to ten more | done | README table and the module docstring say so |

