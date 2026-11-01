"""A remote run's Batch size of 0 is one well per worker, counted before the run the way the node will count it.

The user asked for the first upload to be only as big, and as long, as the workers that need it: the whole plate up
front was slow and could need more disk than the remote has. ``services.remote_batch_auto`` resolves ``0`` from the
Workers field or the scheduler command's CPUs and GPUs through ``pipeline.parallelism.expected_workers``.

Wrong implementations these reject: an estimate that drifts from what the pipeline actually starts (held against
``plan_parallelism`` itself over a grid of nodes); a Cellpose run not capped at two workers a GPU; a parser that misses
a spelling of ``--cpus-per-task`` or of the GPU request; guessing a size for a node of unknown size instead of saying
so; writing the resolved size back over the user's 0; and a resolved size that never reaches the run.
"""

from __future__ import annotations

import json
import logging
import sys
from types import SimpleNamespace

import pytest

import pipeline.parallelism as par
import services.pipeline_runner as pr
from services.remote_batch_auto import RESOLVED_KEY, LauncherResources, launcher_resources, resolve_batch_size


@pytest.mark.parametrize("cpus", [1, 2, 3, 5, 8, 17, 32, 33, 64])
@pytest.mark.parametrize("gpus", [0, 1, 4])
@pytest.mark.parametrize("method", ["stardist_nuclei", "cellpose"])
@pytest.mark.parametrize("workers, tf_threads", [(0, 0), (10, 0), (0, 2), (3, 8)])
def test_the_estimate_is_what_the_pipeline_starts(monkeypatch, cpus, gpus, method, workers, tf_threads):
    monkeypatch.setattr(par, "_cpu_count", lambda: cpus)
    monkeypatch.setattr(par, "visible_gpu_count", lambda: gpus)
    monkeypatch.setattr(par, "node_specs_line", lambda: "")
    monkeypatch.setattr(sys, "platform", "linux")
    args = SimpleNamespace(workers=workers, tf_threads=tf_threads, cpu_only=False, segmentation_method=method)
    plan = par.plan_parallelism(args, logging.getLogger("t"))
    assert par.expected_workers(cpus, gpus, method, workers=workers, tf_threads=tf_threads) == plan.workers


@pytest.mark.parametrize("launcher, expected", [
    ("srun --partition=gpu --gres=gpu:4 --cpus-per-task=32 --time=4:00:00", LauncherResources(32, 4)),
    ("srun --cpus-per-task 16 --gres=gpu:l4:2", LauncherResources(16, 2)),
    ("srun -c 8 --gpus=4", LauncherResources(8, 4)),
    ("srun -c12 --gpus=l4:3", LauncherResources(12, 3)),
    ("srun -c 4 -G 2", LauncherResources(4, 2)),
    ("srun -c 4 --gpus-per-node=2", LauncherResources(4, 2)),
    ("srun -c 4 --gpus-per-task=1", LauncherResources(4, 1)),
    ("srun -c 4 --gres=mps:100,gpu:a10g:1", LauncherResources(4, 1)),
    ("srun -c 4 --gres=gpu", LauncherResources(4, 1)),
    ("srun --partition=cpu", LauncherResources(None, None)),
    ("srun --exclusive --gres=gpu:4", LauncherResources(None, 4, exclusive=True)),
])
def test_what_the_scheduler_command_asks_for(launcher, expected):
    assert launcher_resources(launcher) == expected


def _host(launcher="srun --gres=gpu:4 --cpus-per-task=32", exclusive=False):
    return SimpleNamespace(compute_launcher=launcher, exclusive_node=exclusive)


def test_a_cellpose_run_on_four_gpus_gets_one_well_per_worker():
    """32 CPUs keep one back and give 7 workers of 4 threads; 4 GPUs allow 8, so 7 it is."""
    n, said = resolve_batch_size({"remote_batch_size": "0", "segmentation_method": "cellpose"}, _host())
    assert n == 7
    assert said == ("[remote] batch size 0 → 7 well(s) a batch, one per worker (7 worker(s): 32 CPUs and 4 GPU(s) in "
                    "the scheduler command, Cellpose at up to 2 a GPU).\n")


def test_the_gpu_cap_binds_when_cpus_would_allow_more():
    n, _ = resolve_batch_size({"remote_batch_size": 0, "segmentation_method": "cellpose"},
                              _host("srun --gres=gpu:2 -c 64"))
    assert n == 4                                    # 63 // 4 = 15 by CPU, 2 GPUs x 2


def test_the_workers_field_wins_within_the_cpus():
    n, said = resolve_batch_size({"remote_batch_size": "0", "workers": "10", "segmentation_method": "cellpose"},
                                 _host())
    assert n == 10 and "the Workers field (10), within 32 CPUs" in said
    n, _ = resolve_batch_size({"remote_batch_size": "0", "workers": "50"}, _host())
    assert n == 31                                   # clamped to the core budget, as the pipeline clamps it


@pytest.mark.parametrize("method", ["cellpose", "cellpose_labelfree"])
def test_cpu_only_does_not_plan_a_cellpose_run_for_the_cpus(monkeypatch, method):
    """``cpu_only`` is StarDist's switch: the pipeline still puts Cellpose on the GPU and caps it at two workers a
    GPU (``test_cellpose_uses_the_gpu``), so the batch is sized to those workers, not to 32 // 4 = 8 CPU workers.
    ``cellpose_labelfree`` is what every nanowell run is pinned to. Held against ``plan_parallelism`` on the node the
    scheduler command describes, with ``cpu_only`` set on both sides."""
    n, said = resolve_batch_size({"remote_batch_size": "0", "segmentation_method": method, "cpu_only": True},
                                 _host("srun --gres=gpu:1 -c 33"))
    assert n == 2 and "1 GPU(s)" in said
    monkeypatch.setattr(par, "_cpu_count", lambda: 33)
    monkeypatch.setattr(par, "visible_gpu_count", lambda: 1)
    monkeypatch.setattr(par, "node_specs_line", lambda: "")
    monkeypatch.setattr(sys, "platform", "linux")
    args = SimpleNamespace(workers=0, tf_threads=0, cpu_only=True, segmentation_method=method)
    assert par.plan_parallelism(args, logging.getLogger("t")).workers == n


def test_cpu_only_changes_nothing_for_a_stardist_run():
    n, _ = resolve_batch_size({"remote_batch_size": "0", "segmentation_method": "stardist_nuclei", "cpu_only": True},
                              _host("srun --gres=gpu:1 -c 33"))
    assert n == 8                                    # 32 // 4: StarDist is never capped by the GPUs


def test_a_positive_batch_size_is_the_users():
    assert resolve_batch_size({"remote_batch_size": "5", "workers": "10"}, _host()) == (5, "")


@pytest.mark.parametrize("host, reason", [
    (_host(""), "this host has no scheduler command"),
    (_host("srun --partition=cpu"), "does not say how many CPUs the run gets"),
    (_host("srun --exclusive -c 8"), "takes the whole node (--exclusive)"),
    (_host("srun -c 8", exclusive=True), "takes the whole node (--exclusive)"),
])
def test_an_unknown_node_size_is_said_not_guessed(host, reason):
    n, said = resolve_batch_size({"remote_batch_size": "0", "segmentation_method": "cellpose"}, host)
    assert n == 0, "a node of unknown size must not get a guessed batch size"
    assert "the whole plate in one batch" in said and reason in said and "Set the Workers field" in said


def test_the_workers_field_alone_still_sizes_it():
    n, said = resolve_batch_size({"remote_batch_size": "", "workers": "6"}, _host("srun --partition=cpu"))
    assert n == 6 and "the pipeline may start fewer" in said


def test_the_real_start_hands_the_run_the_resolved_size_and_keeps_the_setting(monkeypatch, tmp_path):
    """Through `PipelineRunner.start`, the seam the Analyze tab calls: the run's options carry the resolved size,
    every reader of the batch size sees it, the caller's dict is untouched, and the dataset records the 0."""
    seen = []
    monkeypatch.setattr(pr.PipelineRunner, "_run_pipeline_thread",
                        lambda self, pipeline, opts, resolve_dirs: seen.append(opts))
    host = SimpleNamespace(name="gpu", compute_launcher="srun --gres=gpu:4 --cpus-per-task=32",
                           exclusive_node=False)
    monkeypatch.setattr(pr, "RemoteRunner", lambda h: SimpleNamespace(host=h))
    asked = {"remote_batch_size": "0", "segmentation_method": "cellpose"}
    runner = pr.PipelineRunner()
    runner.start(tmp_path / "process_microscopy.py", asked, resolve_dirs=lambda *a: None, remote=host)
    runner._thread.join(5)
    (opts,) = seen
    assert opts[RESOLVED_KEY] == 7 and pr._remote_batch_size(opts) == 7
    assert opts["remote_batch_size"] == "0" and RESOLVED_KEY not in asked
    assert runner._batch_note.startswith("[remote] batch size 0 → 7 well(s)")
    (tmp_path / "pipeline_info.json").write_text(json.dumps({"execution_options": {}}))
    assert pr.record_remote_batch_size(tmp_path, opts)
    recorded = json.loads((tmp_path / "pipeline_info.json").read_text())["execution_options"]["remote_batch_size"]
    assert recorded == 0, "the dataset must keep asking for one well per worker, not the size one node gave"


def test_a_local_run_resolves_nothing(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(pr.PipelineRunner, "_run_pipeline_thread",
                        lambda self, pipeline, opts, resolve_dirs: seen.append(opts))
    runner = pr.PipelineRunner()
    runner.start(tmp_path / "p.py", {"remote_batch_size": "0"}, resolve_dirs=lambda *a: None)
    runner._thread.join(5)
    assert RESOLVED_KEY not in seen[0] and runner._batch_note == ""
