"""The Remotes dialog's scheduler test runs through the real launch path.

The probe is only worth having if a launch that is broken for a run is broken
for it too, so these tests do not stub the composition: they take the exact
command :class:`services.scheduler_probe.ProbeRun` would hand ``ssh``, run it
under ``sh`` as the login shell would, and let it go through conda activation,
the environment guard, the packed-command unpacking and the ``srun`` retry
wrapper. Only the cluster is stood in for — an ``srun`` that runs the job here
with the Slurm variables a real step gets, a ``sinfo``, and a ``conda`` whose
``run`` executes this machine's Python — and only the probe script is run in
place of the pipeline, as in the app.
"""

from __future__ import annotations

import shlex
import threading
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from services.remote_config import RemoteHost
from services.scheduler_probe import ProbeRun, parse_probe, summarize


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def cluster(tmp_path):
    """A login node whose srun grants a job with the CPUs it was asked for."""
    home, bindir, env = tmp_path / "home", tmp_path / "bin", tmp_path / "env"
    for d in (home / "miniforge3/etc/profile.d", bindir, env / "conda-meta"):
        d.mkdir(parents=True)
    (home / "miniforge3/etc/profile.d/conda.sh").write_text("")
    (env / "conda-meta/history").write_text("")
    _exe(bindir / "conda", (
        'case "$1" in\n'
        f'  env) echo "allwell {env}";;\n'
        f'  info) echo "{home}/miniforge3";;\n'
        # conda run --no-capture-output -n allwell python -c SCRIPT
        f'  run) shift 4; shift; exec {shlex.quote(sys.executable)} "$@";;\n'
        'esac\n'))
    _exe(bindir / "sinfo", "printf 'n1 idle\\n'\n")
    _exe(bindir / "srun", (
        'case " $* " in *" --test-only "*) exit 0;; esac\n'
        'cpt=""\n'
        'for a in "$@"; do case "$a" in --cpus-per-task=*) cpt="${a#*=}";; esac; done\n'
        'while [ "$1" != "bash" ]; do shift; done\n'
        'echo "srun: job 4242 has been allocated resources" >&2\n'
        'SLURM_JOB_ID=4242 SLURM_CPUS_PER_TASK="$cpt" exec "$@"\n'))
    return {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(home)}


def _run_as_login_shell(probe: ProbeRun, env: dict) -> tuple[list[str], int]:
    out = subprocess.run(["sh", "-c", probe.argv[-1]], capture_output=True,
                         text=True, env=env, timeout=120)
    return (out.stdout + out.stderr).splitlines(keepends=True), out.returncode


def test_a_probe_launch_runs_the_script_on_the_granted_node(cluster):
    host = RemoteHost(name="aws", ssh_alias="aws.example",
                      compute_launcher="srun --partition=gpu --cpus-per-task=16")
    probe = ProbeRun(host)
    assert probe.argv[:2] == ["ssh", "-tt"] and probe.argv[-2] == "aws.example"
    lines, rc = _run_as_login_shell(probe, cluster)
    result = parse_probe(lines, rc)

    assert result.completed, "".join(lines)
    assert result.facts["slurm_job_id"] == "4242"
    assert result.facts["slurm_cpus_per_task"] == "16"
    passed, sentences = summarize(result)
    assert sentences[0].endswith("as Slurm job 4242.")
    assert any(s.startswith("CPUs: ") for s in sentences)
    # Torch is not in the test stack: the probe says so rather than failing.
    assert any("Torch not importable" in s for s in sentences)
    assert passed is False


def test_a_launcher_with_a_loose_word_is_refused_before_anything_is_sent():
    host = RemoteHost(name="aws", ssh_alias="aws.example",
                      compute_launcher="srun --mem=60G g4dn.12xlarge")
    with pytest.raises(ValueError, match="g4dn.12xlarge"):
        ProbeRun(host)


def _facts(**over) -> list[str]:
    facts = {"node": "gpu-1", "cpus": "16", "node_cpus": "16",
             "slurm_job_id": "7", "slurm_cpus_per_task": "16",
             "cuda_visible_devices": "0", "torch": "2.3.1", "torch_cuda": "True",
             "torch_devices": "1", "cellpose": "3.1.1.2", "done": "1", **over}
    return [f"[probe] {k}={v}\n" for k, v in facts.items()]


def test_a_healthy_gpu_job_passes_and_says_what_cellpose_will_use():
    lines = _facts() + ["[probe] gpu=GPU 0: NVIDIA L4 (UUID: GPU-1)\n"]
    passed, sentences = summarize(parse_probe(lines, 0))
    assert passed
    assert "Cellpose can use 1 GPU(s): up to 2 Cellpose worker(s) by default." in sentences


def test_one_core_of_a_big_node_is_named_with_the_fix():
    lines = _facts(cpus="2", node_cpus="16", slurm_cpus_per_task="")
    passed, sentences = summarize(parse_probe(lines, 0))
    assert not passed
    assert any("--cpus-per-task=<number of cores>" in s for s in sentences)


def test_a_gpu_node_with_a_cpu_only_torch_is_flagged():
    lines = _facts(torch_cuda="False", torch_devices="0") + [
        "[probe] gpu=GPU 0: NVIDIA L4 (UUID: GPU-1)\n"]
    passed, sentences = summarize(parse_probe(lines, 0))
    assert not passed
    assert any("CPU-only build" in s for s in sentences)


def test_a_job_that_never_finished_shows_its_last_lines():
    lines = ["srun: error: Unable to allocate resources: Invalid partition name\n"]
    passed, sentences = summarize(parse_probe(lines, 1))
    assert not passed
    assert sentences[0].startswith("✗ The test job did not finish (exit code 1)")
    assert sentences[-1] == lines[0].rstrip()


# ── the button in the real Remotes form ─────────────────────────────────────

class _StubProbe:
    """Stands in for the ssh launch only; the summary is the real one."""

    made: list = []

    def __init__(self, host):
        self.host = host
        self.cancelled = threading.Event()
        _StubProbe.made.append(self)

    def run(self, on_line):
        lines = _facts() + ["[probe] gpu=GPU 0: NVIDIA L4 (UUID: GPU-1)\n"]
        on_line("[remote] scheduler: allocation granted — job 7 on gpu-1\n")
        for line in lines:
            on_line(line)
        return parse_probe(lines, 0)

    def cancel(self):
        self.cancelled.set()


@pytest.fixture
def form(qtbot, monkeypatch):
    import analyze.remote_dialog as ard

    _StubProbe.made.clear()
    monkeypatch.setattr(ard, "ProbeRun", _StubProbe)
    dlg = ard.RemoteFormDialog(None, host=None)
    qtbot.addWidget(dlg)
    dlg._name_edit.setText("aws")
    dlg._ssh_alias_edit.setText("aws.example")
    dlg._compute_loc_combo.setCurrentIndex(1)
    return dlg


@pytest.mark.qt
def test_the_test_button_runs_the_form_s_command_and_reports_the_node(form, qtbot):
    form._compute_launcher_edit.setText("srun -p gpu --cpus-per-task=16 --gres=gpu:1")
    form._test_sched_btn.click()
    (probe,) = _StubProbe.made
    assert probe.host.compute_launcher == "srun -p gpu --cpus-per-task=16 --gres=gpu:1", (
        "the test must run what the form says now, not what was saved")
    window = form._scheduler_test
    qtbot.waitUntil(lambda: bool(window._summary_lbl.text()), timeout=5000)
    assert window._status_lbl.text() == "Test passed"
    assert "✓ Ran on gpu-1 as Slurm job 7." in window._summary_lbl.text()
    assert "up to 2 Cellpose worker(s)" in window._summary_lbl.text()
    window.close()
    assert not probe.cancelled.is_set(), "a finished test has nothing to cancel"


@pytest.mark.qt
def test_a_loose_word_is_refused_without_sending_anything(form):
    form._compute_launcher_edit.setText("srun --mem=60G g4dn.12xlarge")
    form._test_sched_btn.click()
    assert _StubProbe.made == []
    assert "g4dn.12xlarge" in form._test_result_lbl.text()
