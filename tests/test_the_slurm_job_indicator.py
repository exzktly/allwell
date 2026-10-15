"""The Analyze pane's Slurm-job indicator: lit only while a job is open.

The chain has four links, and each is driven here through the real code:

1. the launch command a scheduler-backed host runs prints, from inside the
   allocation, what the node is (``services.slurm_node_specs``) — run in
   ``bash`` with ``env`` standing in for ``srun``, so the line asserted is the
   one the real composed command produces;
2. ``PipelineRunner`` prints the job-closed line once the remote process that
   held the job has exited, and only on a host that has a launcher;
3. ``ProgressTracker`` turns those lines into ``slurm_job`` events;
4. a real ``AnalyzeTab``, fed through the queue its runner fills, shows the
   icon dimmed while queued, lit with the node's specs while open, and hides it
   once the job closes or the run settles.
"""

from __future__ import annotations

import os
import subprocess
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from analyze.domain import slurm_job as sj
from services.pipeline_runner import ProgressTracker
from services.slurm_node_specs import (
    JOB_CLOSED_LINE,
    NODE_SPECS_PREFIX,
    node_specs_snippet,
    parse_node_specs,
)

_SPECS_LINE = (
    f"{NODE_SPECS_PREFIX}node=gpu07 | partition=gpu | cpus_allocated=8 | "
    "cpus_node=64 | cpu_model=AMD EPYC 7763 64-Core Processor | "
    "mem_allocated_mb=32768 | mem_per_cpu_mb=? | mem_node_mb=515703 | "
    "gpus=NVIDIA A100-SXM4-80GB (81920 MiB) | time_limit=4:00:00\n"
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ── 1. the line, from inside the allocation ──────────────────────────────────

def _fake_tools(tmp_path: Path) -> Path:
    """An ``nvidia-smi`` and a ``squeue`` that answer the way a GPU node does."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "nvidia-smi").write_text(
        "#!/bin/sh\necho 'NVIDIA A100-SXM4-80GB, 81920 MiB'\n"
        "echo 'NVIDIA A100-SXM4-80GB, 81920 MiB'\n")
    (bin_dir / "squeue").write_text("#!/bin/sh\necho '4:00:00'\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    return bin_dir


def test_the_real_launch_command_prints_the_node_s_specs(tmp_path):
    from services.remote_commands import _compose_remote_cmd

    home = tmp_path / "home"
    (home / "miniforge3/etc/profile.d").mkdir(parents=True)
    (home / "miniforge3/etc/profile.d/conda.sh").write_text("")
    env_dir = tmp_path / "env"
    (env_dir / "conda-meta").mkdir(parents=True)
    (env_dir / "conda-meta/history").write_text("")
    # `env` stands in for `srun`: it runs the inner command with the variables
    # Slurm would export inside the allocation.
    launcher = ("env SLURM_JOB_ID=42 SLURMD_NODENAME=gpu07 SLURM_JOB_PARTITION=gpu "
                "SLURM_CPUS_ON_NODE=8 SLURM_MEM_PER_NODE=32768")
    cmd = _compose_remote_cmd(launcher, str(tmp_path / "pid"), ["echo", "PIPELINE"],
                              cache_hit=False, env_selector=("prefix", str(env_dir)))
    env = {**os.environ, "HOME": str(home),
           "PATH": f"{_fake_tools(tmp_path)}:{os.environ['PATH']}"}
    out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                         timeout=30, env=env)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert lines[0] == "[remote] scheduler: allocation granted — job 42 on gpu07 (partition gpu)"
    specs = parse_node_specs(lines[1])
    # The node's own CPU count, model and memory come from this machine, so
    # they are checked for presence; everything Slurm exports, exactly.
    assert {k: specs[k] for k in ("node", "partition", "cpus_allocated",
                                  "mem_allocated_mb", "gpus", "time_limit")} == {
        "node": "gpu07", "partition": "gpu", "cpus_allocated": "8",
        "mem_allocated_mb": "32768",
        "gpus": "NVIDIA A100-SXM4-80GB (81920 MiB) + NVIDIA A100-SXM4-80GB (81920 MiB)",
        "time_limit": "4:00:00",
    }
    assert specs["cpus_node"].isdigit() and int(specs["mem_node_mb"]) > 0
    assert "mem_per_cpu_mb" not in specs, "unset in the job, so not shown"
    assert lines[2] == "PIPELINE"


def test_the_batched_driver_launch_prints_it_too():
    from services.remote_commands import _compose_driver_cmd

    cmd = _compose_driver_cmd("srun -p gpu", "/r/pid", "true")
    # The inner command is shell-quoted, so look for the prefix, which has no
    # quote characters to be escaped.
    assert NODE_SPECS_PREFIX in cmd


def test_a_node_with_none_of_the_tools_still_prints_the_line(tmp_path):
    out = subprocess.run(["bash", "-c", node_specs_snippet()], capture_output=True,
                         text=True, timeout=10,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    specs = parse_node_specs(out.stdout)
    assert specs is not None
    assert specs["gpus"] == "none"
    assert "cpus_allocated" not in specs and "partition" not in specs


def test_any_other_line_is_not_a_specs_line():
    assert parse_node_specs("[remote] scheduler: allocation granted — job 1") is None


# ── 2. the runner says when the job is over ──────────────────────────────────

def _run_remote(monkeypatch, tmp_path, *, launcher: str):
    import services.pipeline_runner as pr
    from services.pipeline_service import build_pipeline_args
    from services.remote_runner import LocalInputMode, ZippedInput

    zipped = ZippedInput(zip_dir=tmp_path, mode=LocalInputMode.ZIP,
                         well_zips=(tmp_path / "A01.zip",), up_to_date_wells=())
    monkeypatch.setattr(pr, "stage_local_zips", lambda *a, **k: zipped)
    order: list[str] = []

    class _Proc:
        pid = 1
        returncode = 0
        stdout = iter([
            "[remote] scheduler: allocation granted — job 42 on gpu07 (partition gpu)\n",
            _SPECS_LINE, "Well A01 — done.\n"])
        _ssh = None

        def poll(self):
            return 0

        def wait(self, timeout=None):
            order.append("wait")
            return 0

    remote = types.SimpleNamespace(
        host=types.SimpleNamespace(compute_launcher=launcher),
        estimate=lambda z: types.SimpleNamespace(format=lambda: "est"),
        spawn=lambda *a, **k: _Proc(), verify_and_cleanup=lambda ok, **k: True,
        close_remote_job=lambda **k: None, cancel=lambda proc: None,
        can_request_suspend=False)
    r = pr.PipelineRunner()
    r._remote = types.SimpleNamespace(name="h")
    r._remote_runner = remote
    opts = {"filename_schema": "experiment:channel:well:fov:timepoint",
            "filename_sep": "_", "fluor_tokens": ["GFP"], "nuclear_token": "DAPI",
            "csv_prefix": "measurements"}
    args = build_pipeline_args(Path("process_microscopy.py"), tmp_path / "in",
                               tmp_path / "out", opts)
    r._run_pipeline_remote(tmp_path / "in", tmp_path / "out", args, opts)
    lines = []
    while not r.log_q.empty():
        kind, payload = r.log_q.get_nowait()
        if kind == "line":
            lines.append(payload)
    return lines


def test_the_job_is_reported_closed_after_its_process_exits(monkeypatch, tmp_path):
    lines = _run_remote(monkeypatch, tmp_path, launcher="srun -p gpu")
    assert lines.count(JOB_CLOSED_LINE) == 1
    # After everything the job printed, not before it.
    assert lines.index(JOB_CLOSED_LINE) > lines.index("Well A01 — done.\n")


def test_a_host_without_a_scheduler_says_nothing_about_a_job(monkeypatch, tmp_path):
    lines = _run_remote(monkeypatch, tmp_path, launcher="")
    assert JOB_CLOSED_LINE not in lines


# ── 3. the tracker ───────────────────────────────────────────────────────────

def _slurm_events(line):
    return [p for k, p in ProgressTracker().parse(line) if k == "slurm_job"]


def test_the_tracker_turns_the_lines_into_events():
    assert _slurm_events(_SPECS_LINE) == [("specs", parse_node_specs(_SPECS_LINE))]
    assert _slurm_events(JOB_CLOSED_LINE) == [("closed", {})]
    assert _slurm_events("[remote] releasing the allocation\n") == [("closed", {})]
    assert _slurm_events("[remote] scheduler: allocation granted — job 9\n") == []


# ── the state and the tooltip ────────────────────────────────────────────────

def test_the_fold_distinguishes_open_from_queued_and_closed():
    job = sj.on_sched(sj.SlurmJob(), "waiting", "")
    assert job.state == sj.QUEUED
    job = sj.on_sched(job, "queued", "42")
    assert (job.state, job.job_id) == (sj.QUEUED, "42")
    job = sj.on_sched(job, "granted", "")
    assert (job.state, job.job_id) == (sj.OPEN, "42"), "keeps the id it was queued under"
    job = sj.on_specs(job, {"node": "gpu07"})
    assert job.specs == {"node": "gpu07"}
    assert sj.on_closed(job) == sj.SlurmJob()
    # A new request is a new job: the previous node's specs must not survive.
    assert sj.on_sched(job, "waiting", "") == sj.SlurmJob(state=sj.QUEUED)


def test_the_open_tooltip_names_the_node():
    job = sj.on_specs(sj.SlurmJob(state=sj.OPEN, job_id="42"), parse_node_specs(_SPECS_LINE))
    assert sj.tooltip(job) == (
        "Slurm job 42 is open.\n"
        "\n"
        "Node: gpu07\n"
        "Partition: gpu\n"
        "CPUs: 8 allocated of 64 on the node\n"
        "CPU model: AMD EPYC 7763 64-Core Processor\n"
        "Memory: 32.0 GB allocated of 503.6 GB on the node\n"
        "GPUs: NVIDIA A100-SXM4-80GB (81920 MiB)\n"
        "Time limit: 4:00:00"
    )


def test_memory_asked_per_cpu_is_labelled_per_cpu():
    job = sj.SlurmJob(state=sj.OPEN, specs={"mem_per_cpu_mb": "4096"})
    assert "Memory: 4.0 GB per CPU allocated" in sj.tooltip(job)


def test_the_other_tooltips():
    assert sj.tooltip(sj.SlurmJob(state=sj.QUEUED, job_id="7")) == (
        "Slurm job 7 is queued — waiting for the scheduler to grant a node.")
    assert sj.tooltip(sj.SlurmJob()) == "No Slurm job is open."
    assert sj.tooltip(sj.SlurmJob(state=sj.OPEN)) == (
        "Slurm job is open.\n\nThe node did not report its specifications.")


# ── 4. the real Analyze tab ──────────────────────────────────────────────────

def _feed(tab, *lines):
    for line in lines:
        tab._log_q.put(("line", line))
    tab._poll_log()


def test_the_analyze_tab_lights_the_icon_only_while_the_job_is_open(qapp):
    from analyze.tab import AnalyzeTab
    from ui import theme

    tab = AnalyzeTab()
    icon = tab._slurm_indicator
    assert icon.isHidden(), "no job yet"

    _feed(tab, "[remote] scheduler: requesting allocation via: srun -p gpu\n",
          "srun: job 42 queued and waiting for resources\n")
    assert not icon.isHidden()
    assert icon.toolTip() == ("Slurm job 42 is queued — waiting for the scheduler "
                              "to grant a node.")
    queued_px = icon.pixmap().toImage()

    _feed(tab, "[remote] scheduler: allocation granted — job 42 on gpu07 (partition gpu)\n",
          _SPECS_LINE)
    assert not icon.isHidden()
    assert icon.toolTip().startswith("Slurm job 42 is open.\n\nNode: gpu07\n")
    # Lit: drawn in the success colour, not the muted one it was queued in.
    lit_px = icon.pixmap().toImage()
    assert lit_px != queued_px
    from widgets.icons import make_pixmap
    assert lit_px == make_pixmap("server", theme.Colors.success, 16,
                                 icon.devicePixelRatioF()).toImage()

    _feed(tab, JOB_CLOSED_LINE)
    assert icon.isHidden()
    assert icon.toolTip() == "No Slurm job is open."


def test_a_settled_run_puts_the_icon_out(qapp):
    from analyze.tab import AnalyzeTab

    tab = AnalyzeTab()
    _feed(tab, "[remote] scheduler: allocation granted — job 42 on gpu07 (partition gpu)\n")
    assert not tab._slurm_indicator.isHidden()
    tab._on_run_settled()
    assert tab._slurm_indicator.isHidden()
