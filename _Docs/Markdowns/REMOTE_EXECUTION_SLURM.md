# Remote execution on an HPC cluster (JupyterHub / SLURM lobby node)

This guide covers running the AllWell pipeline on an HPC cluster whose entry
point is a **lobby / login node** — the machine you SSH into (often the same
node a JupyterHub spawner authenticates against). On these clusters you are
**not allowed to run heavy compute on the login node**; you acquire a *compute
node* from the batch scheduler (SLURM here; the same idea applies to PBS/SGE/LSF)
and run work there.

AllWell models this with **one optional per-host setting**, `compute_launcher`.
Everything else about remote execution is unchanged.

> If your remote is an ordinary single box you SSH into and run compute on
> directly, you do **not** need any of this — leave `compute_launcher` empty
> (the default) and AllWell runs the pipeline on the SSH host as before. See
> `REMOTE_EXECUTION_WINDOWS.md` for the client-side ssh/rsync setup.

---

## The model

```
        your laptop                     lobby / login node          scheduled compute node
   ┌──────────────────┐   ssh + rsync   ┌──────────────────┐  srun  ┌──────────────────────┐
   │ AllWell GUI/CLI  │ ───────────────▶│ shared filesystem │ ─────▶ │ python                │
   │ remote_runner    │◀─────────────── │ (home / scratch)  │        │ process_microscopy.py │
   └──────────────────┘   per-well zips └──────────────────┘        └──────────────────────┘
                          + CSV results        ▲   shared FS visible from both   ▲
                                               └─────────────────────────────────┘
```

- **All the cheap control-plane work stays on the login node** — rsync staging,
  the pre-flight cache check, per-well result fetch, and cleanup. These are
  lightweight filesystem operations on the **shared filesystem** (NFS / Lustre /
  GPFS), which login nodes are explicitly meant to do.
- **Only the one heavy command** — the pipeline invocation — is launched into a
  scheduler allocation via `compute_launcher`, so it runs on a compute node the
  cluster schedules, not on the login node.
- Because home/scratch is **shared**, data rsync'd to the login node's view of
  the filesystem is visible to the compute node with no second copy. (This guide
  assumes a shared filesystem; if your compute nodes have private local storage
  only, this model does not apply.)
- No JupyterHub web/REST API is involved. AllWell drives the **same scheduler**
  JupyterHub's BatchSpawner uses, directly over SSH.

---

## Configuration

Add a `compute_launcher` line to the host's block in `remotes.toml`
(`~/.config/allwell/remotes.toml`, or `%APPDATA%\allwell\remotes.toml` on
Windows), or use the GUI: **Analyze → Manage… → Add/Edit**, set **Run compute**
to *"On a node it schedules (SLURM)"*, and fill in the **Scheduler command**.

```toml
# CPU partition
[remote.cluster]
ssh_alias = "hpc-login"              # an ~/.ssh/config Host entry for the lobby
remote_project_dir = "~/allwell"
remote_staging_dir = "~/scratch/.allwell-staging"   # put staging on shared scratch
remote_python = "~/miniforge3/envs/allwell/bin/python"
cpu_only = true
compute_launcher = "srun --partition=cpu --cpus-per-task=16 --mem=64G --time=4:00:00"

# GPU partition (same login node, different node request)
[remote.cluster-gpu]
ssh_alias = "hpc-login"
remote_project_dir = "~/allwell"
remote_staging_dir = "~/scratch/.allwell-staging"
remote_python = "~/miniforge3/envs/allwell/bin/python"
cpu_only = false
compute_launcher = "srun --partition=gpu --gres=gpu:1 --cpus-per-task=16 --mem=64G --time=4:00:00"
```

A matching `~/.ssh/config` entry for the lobby (recommended over inline
`user@host` so key/jump settings live in one place):

```
Host hpc-login
    HostName login.hpc.example.edu
    User myuser
    # ProxyJump bastion        # only if the login node sits behind a bastion
```

When `compute_launcher` is empty (every pre-existing config), behaviour is
byte-identical to legacy direct-on-host execution.

---

## Selecting which *type* of node the job runs on

There is **no separate node-type field**. Node selection is expressed entirely
through the scheduler flags inside `compute_launcher`, which AllWell passes
**verbatim** to the scheduler — SLURM, not AllWell, interprets them. To target a
particular node type, use the scheduler's own selectors:

| You want… | Add to `compute_launcher` |
|---|---|
| A specific partition / queue | `--partition=gpu` · `--partition=cpu-large` |
| A GPU (and a specific GPU model) | `--gres=gpu:1` · `--gres=gpu:a100:2` |
| A CPU/feature class | `--constraint=cascadelake` · `--constraint="skylake|icelake"` |
| A QOS / account | `--qos=high` · `--account=mylab` |
| Cores / memory / walltime | `--cpus-per-task=16 --mem=64G --time=4:00:00` |

Two ways to use multiple node types:

1. **One remote per node type** (recommended): define `[remote.cluster-gpu]`,
   `[remote.cluster-cpu-big]`, … all sharing the same `ssh_alias` (the lobby)
   but with different `compute_launcher` lines, then pick the right one from the
   Analyze tab's **Run on:** dropdown per run. This keeps node choice a
   first-class, repeatable selection.
2. **Edit the one launcher string** ad hoc in the form / TOML when you only
   occasionally switch.

Note that `cpu_only` (and the pipeline's `--cpu_only` flag) is a **pipeline
device** setting — it tells the analysis code not to use a GPU — and is
*independent* of the **node request** in `compute_launcher`. For a GPU run set
`cpu_only = false` **and** request a GPU node (`--gres=gpu:…`); for a CPU run set
`cpu_only = true` and omit `--gres`. They must agree, but neither implies the
other.

---

## How it runs (under the hood)

For a scheduled host the runner composes (see
`services/remote_runner.py::_compose_remote_cmd`):

```bash
echo $$ > <pidfile>;                       # login-side PID, for the cancel path
exec srun <your flags> bash -lc '          # acquire + enter the compute node
    <activate conda>;                      # env lives on the shared FS
    echo "$SLURM_JOB_ID" > <pidfile>.slurm;# job id, for scancel on Stop
    exec python process_microscopy.py …    # the actual work, on the compute node
'
```

- `srun` streams the job's stdout/stderr back through the login-side SSH in real
  time, so the GUI's per-well progress (`… — done.`) and the run dashboard update
  live exactly as on a direct host.
- **Cancel / Stop** kills the login-side `srun` client (which tears the
  allocation down) and additionally `scancel`s the recorded `$SLURM_JOB_ID` as a
  belt-and-braces second channel.

---

## Things to expect

- **Queue wait.** If the requested partition is busy, `srun` blocks while it
  waits for the allocation. The SSH connection is held open
  (`ServerAliveInterval=30`), and the dashboard shows no progress until the node
  is granted — this is normal, not a hang. Keep `--time=` realistic for your
  cluster's scheduling policy.
- **Per-run allocation.** Each pipeline run (and, in batched mode, each batch)
  acquires a fresh allocation that exits when the work finishes — no long-lived
  session to manage or leak. A full cache hit skips the pipeline entirely and
  does **not** consume an allocation.
- **Bootstrap.** `Manage… → Bootstrap` installs the conda env into your shared
  `$HOME` on the login node; because the filesystem is shared, the compute node
  sees the same env with no extra step. Set `compute_launcher` after bootstrap
  (or leave it and add it in the form afterwards).
- **Module-provided conda.** If your cluster exposes conda via `module load`
  rather than a `~/miniforge3` install, the activation snippet falls back to
  `conda info --base`; ensure the relevant `module load` is in your shell login
  files (`bash -lc` sources them) or the env is otherwise on `PATH`.
