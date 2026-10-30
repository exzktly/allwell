"""A Cellpose worker's "ready" line does not report StarDist's device as its own.

Reported: on a GPU node, a Cellpose run's workers each logged ``Worker N ready (CPU-only (--cpu_only); label-free
'cellpose_labelfree' — StarDist not loaded)``, which reads as Cellpose running on the CPU. The device in that line was
TensorFlow's policy for StarDist, from ``--cpu_only`` — a remote host writes that from its login node's TensorFlow —
while Cellpose picks its own device and logs it when its model loads (``pipeline.cellpose._cellpose_device``).
"""

from __future__ import annotations

import logging
import os

import pytest

import process_microscopy as pm


@pytest.fixture
def worker_env(monkeypatch):
    """``_worker_init`` sets the thread variables for the process it runs in; keep them out of the rest of the run."""
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                 "NUMEXPR_NUM_THREADS", "TF_NUM_INTRAOP_THREADS", "TF_NUM_INTEROP_THREADS"):
        monkeypatch.setenv(name, os.environ.get(name, "")) if name in os.environ else monkeypatch.delenv(
            name, raising=False)
    monkeypatch.setattr(pm, "_ensure_pipeline_io_ready", lambda: None)


def _ready_line(caplog, method: str, force_cpu: bool) -> str:
    with caplog.at_level(logging.INFO, logger="process_microscopy"):
        pm._worker_init(force_cpu=force_cpu, threads_per_worker=0, segmentation_method=method)
    return next(r.getMessage() for r in caplog.records if " ready (" in r.getMessage())


@pytest.mark.parametrize("method", ["cellpose_labelfree", "cellpose"])
@pytest.mark.parametrize("force_cpu", [True, False])
def test_a_cellpose_worker_says_cellpose_names_its_own_device(worker_env, caplog, method, force_cpu):
    line = _ready_line(caplog, method, force_cpu)
    assert "CPU-only" not in line and "Metal" not in line
    assert f"label-free '{method}'" in line and "logs the device it runs on" in line
    assert "--cpu_only does not apply" in line


def test_another_label_free_method_names_no_device_either(worker_env, caplog):
    line = _ready_line(caplog, "classical_threshold", True)
    assert "CPU-only" not in line and "no segmentation model to load" in line
