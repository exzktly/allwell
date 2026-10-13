"""Fetch turns a JupyterHub profile into a launcher srun can run.

A profile holds values (``req_partition``, ``req_options`` …), and the cluster's
job template (``batch_script``) decides which ``#SBATCH`` option each becomes.
Fetch mapped the values by name and appended ``req_options`` to the end of the
launcher as it stood, so on a cluster whose template reads
``#SBATCH --constraint={{options}}`` a GPU profile with
``req_options='g4dn.12xlarge'`` came back as
``srun --partition=gpu-small-queue --cpus-per-task=16 --mem=60G g4dn.12xlarge``
— the launcher a user ran, whose job failed with
``execve(): g4dn.12xlarge: No such file`` once a node had started.

The end-to-end tests run the real discovery snippet against a config file and
feed its output through the real parser; the last one presses Fetch in the real
Remotes form.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from services.remote_config import (
    _JHUB_PROFILES_SNIPPET,
    parse_jupyterhub_profiles_json,
    profile_launcher,
)
from services.remote_srun_retry import stray_launcher_words
from services.slurm_batch_script import directive_flags, launcher_from_batch_script

# The shape of the reporting cluster's config: a site template that places
# ``options`` after ``--constraint=``, with batchspawner's usual job-only lines.
_SITE_CONFIG = '''
from wrapspawner import ProfilesSpawner
from batchspawner import SlurmSpawner
c.JupyterHub.spawner_class = ProfilesSpawner
c.SlurmSpawner.req_runtime = "8:00:00"
c.SlurmSpawner.batch_script = """#!/bin/bash
#SBATCH --output={{homedir}}/jupyterhub_slurmspawner_%j.log
#SBATCH --job-name=spawner-jupyterhub
#SBATCH --chdir={{homedir}}
#SBATCH --export={{keepvars}}
#SBATCH --get-user-env=L
{% if partition %}#SBATCH --partition={{partition}}
{% endif %}{% if nprocs %}#SBATCH --cpus-per-task={{nprocs}}
{% endif %}{% if memory %}#SBATCH --mem={{memory}}
{% endif %}{% if runtime %}#SBATCH --time={{runtime}}
{% endif %}{% if options %}#SBATCH --constraint={{options}}
{% endif %}
srun {{cmd}}
"""
c.ProfilesSpawner.profiles = [
    ("GPU g4dn.12xlarge", "gpu", SlurmSpawner,
     dict(req_partition="gpu-small-queue", req_nprocs="16", req_memory="60G",
          req_options="g4dn.12xlarge")),
    ("CPU short", "cpu", SlurmSpawner,
     dict(req_partition="cpu-queue", req_nprocs="4", req_runtime="1:00:00")),
]
'''


def _discover(tmp_path, config: str) -> str:
    cfg = tmp_path / "jupyterhub_config.py"
    cfg.write_text(config, encoding="utf-8")
    proc = subprocess.run([sys.executable, "-c", _JHUB_PROFILES_SNIPPET, str(cfg)],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_the_reported_profile_becomes_a_constraint(tmp_path):
    ok, profiles, detail = parse_jupyterhub_profiles_json(_discover(tmp_path, _SITE_CONFIG))
    assert ok, detail
    launchers = dict(profiles)
    gpu = launchers["GPU g4dn.12xlarge"]
    assert gpu == ("srun --partition=gpu-small-queue --cpus-per-task=16 --mem=60G "
                   "--time=8:00:00 --constraint=g4dn.12xlarge")
    assert stray_launcher_words(gpu) == []
    # The spawner-wide runtime is a default the profile overrides.
    assert launchers["CPU short"] == (
        "srun --partition=cpu-queue --cpus-per-task=4 --time=1:00:00")
    assert detail == "", "nothing was lost, so nothing may be reported"


def test_a_bare_word_with_no_template_is_left_out_and_named():
    """Without a site template, batchspawner writes ``req_options`` as a bare
    ``#SBATCH`` line, where a lone word is no option at all. It must not reach
    the launcher, and the person must be told it was left out."""
    doc = {"ok": True, "profiles": [{"name": "GPU", "kwargs": {
        "req_partition": "gpu-small-queue", "req_options": "g4dn.12xlarge --qos=high"}}]}
    ok, profiles, detail = parse_jupyterhub_profiles_json(json.dumps(doc))
    assert ok
    assert profiles == [("GPU", "srun --partition=gpu-small-queue --qos=high")]
    assert detail == "“GPU” left out “g4dn.12xlarge” (not attached to any option)."


def test_only_resource_options_reach_the_launcher():
    """--output would take the stream the app reads, --export would strip the
    job's environment, and --ntasks 4 would run the pipeline four times."""
    made = launcher_from_batch_script(
        "#SBATCH --output=/tmp/x.log\n#SBATCH --export=NONE\n#SBATCH --ntasks 4\n"
        "#SBATCH -p {partition} -c 8 --gres gpu:{ngpus} --exclusive\n",
        {"req_partition": "gpu", "req_ngpus": 2})
    assert made.launcher == ("srun --partition=gpu --cpus-per-task=8 "
                             "--gres=gpu:2 --exclusive")
    assert made.dropped == []


def test_a_resource_line_the_profile_cannot_fill_is_reported():
    made = launcher_from_batch_script(
        "#SBATCH --chdir={{homedir}}\n#SBATCH --partition={{partition}}\n"
        "#SBATCH --gres=gpu:{{ngpus}}\n", {"req_partition": "gpu"})
    assert made.launcher == "srun --partition=gpu"
    # --chdir needs homedir too, but losing it does not change the node.
    assert made.dropped == ["#SBATCH --gres=gpu:{{ngpus}} (the profile sets no ngpus)"]


@pytest.mark.parametrize("tokens, kept, stray", [
    (["-p", "gpu", "--mem", "4G", "-c8"],
     ["--partition=gpu", "--mem=4G", "--cpus-per-task=8"], []),
    (["--constraint=g4dn.12xlarge"], ["--constraint=g4dn.12xlarge"], []),
    (["g4dn.12xlarge"], [], ["g4dn.12xlarge"]),
    (["--job-name", "x", "--get-user-env=L", "loose"], [], ["loose"]),
])
def test_directive_words_sort_into_flags_and_strays(tokens, kept, stray):
    assert directive_flags(tokens) == (kept, stray)


def test_an_explicit_launcher_on_a_profile_still_wins():
    assert profile_launcher({"launcher": "srun -p x", "req_partition": "y"},
                            batch_script="#SBATCH --partition={{partition}}"
                            ).launcher == "srun -p x"


# ── the real Remotes form ───────────────────────────────────────────────────

class _MemorySettings:
    def __init__(self):
        self._d: dict = {}

    def value(self, key, default=None):
        return self._d.get(key, default)

    def setValue(self, key, value):
        self._d[key] = value


@pytest.mark.qt
def test_fetch_then_pick_saves_a_launcher_srun_can_run(qtbot, monkeypatch, tmp_path):
    """Press Fetch, pick the GPU profile, press OK: what is saved is the
    launcher, with the instance type as a constraint."""
    import analyze.remote_dialog as ard
    import services.remote_config as rc

    stdout = _discover(tmp_path, _SITE_CONFIG)
    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, stdout, ""))
    monkeypatch.setattr(ard.RemoteFormDialog, "_settings",
                        lambda self, _s=_MemorySettings(): _s)
    written: list = []
    monkeypatch.setattr(ard, "write_remote", lambda host: written.append(host))

    dlg = ard.RemoteFormDialog(None, host=None)
    qtbot.addWidget(dlg)
    dlg._name_edit.setText("aws")
    dlg._ssh_alias_edit.setText("aws.example")
    dlg._compute_loc_combo.setCurrentIndex(1)

    dlg._fetch_prof_btn.click()
    deadline = time.monotonic() + 5
    while dlg._profile_combo.count() < 3 and time.monotonic() < deadline:
        qtbot.wait(20)
    idx = dlg._profile_combo.findText("GPU g4dn.12xlarge")
    assert idx > 0, [dlg._profile_combo.itemText(i)
                     for i in range(dlg._profile_combo.count())]
    dlg._profile_combo.setCurrentIndex(idx)
    dlg._profile_combo.activated.emit(idx)

    assert dlg.form_error() is None, dlg.form_error()
    dlg._on_ok()
    assert written, "the fetched profile's launcher was refused"
    assert written[-1].compute_launcher.endswith("--constraint=g4dn.12xlarge")
    assert stray_launcher_words(written[-1].compute_launcher) == []
