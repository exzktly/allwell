"""The bootstrap creates, probes and installs the ``allwell-cellpose`` env beside ``allwell``.

Asked for: Cellpose-SAM in bfloat16 on the remote, which needs a Torch that ``allwell`` cannot hold beside TensorFlow
2.13. The second env's roster and pins are held against ``requirements-remote-cellpose.txt`` as ``allwell``'s are
against its file (``tests/test_bootstrap_probes_every_requirement.py``), and its install follows its own probe.
"""

from __future__ import annotations

import re
import types
from pathlib import Path

from services import remote_bootstrap as rb

_REQ = Path(__file__).resolve().parents[1] / rb.CELLPOSE_REQUIREMENTS


def _requirements() -> tuple[set[str], dict[str, str]]:
    names, pins = set(), {}
    for raw in _REQ.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[\s=<>!~;\[]", line, maxsplit=1)[0].lower()
        names.add(name)
        if "==" in line and ";" not in line:
            pins[name] = line.split("==", 1)[1].strip()
    return names, pins


def test_every_requirement_of_the_cellpose_env_is_probed_and_its_pins_are_the_file_s():
    names, pins = _requirements()
    covered = set(rb.CELLPOSE_REQUIRED_IMPORTS) | set(rb.CELLPOSE_NOT_PROBED)
    assert names == covered, (sorted(names - covered), sorted(covered - names))
    assert rb.CELLPOSE_VERSION_PINS and all(pins.get(d) == v for d, v in rb.CELLPOSE_VERSION_PINS.items())
    # The reason the env exists: a Torch that runs Cellpose-SAM in bfloat16 (pipeline.cellpose.BF16_MIN_TORCH).
    from pipeline.cellpose import BF16_MIN_TORCH, _torch_at_least

    assert _torch_at_least(rb.CELLPOSE_VERSION_PINS["torch"], BF16_MIN_TORCH)
    assert "tensorflow" not in names and "stardist" not in names


def test_the_probe_script_carries_the_cellpose_env_s_roster_and_pins():
    script = rb._PROBE_SCRIPT
    assert not re.search(r"__[A-Z][A-Z_]*__", script)                       # every placeholder filled
    assert f'grep -qx "{rb.CELLPOSE_ENV_NAME}"' in script
    assert " ".join(rb.CELLPOSE_REQUIRED_IMPORTS.values()) in script
    assert " ".join(f"{d}=={v}" for d, v in rb.CELLPOSE_VERSION_PINS.items()) in script


def test_the_probe_parses_the_cellpose_env(monkeypatch):
    stdout = ("has_conda=true\nhas_env=true\ntensorflow_version=2.13.0\ngpu_visible=true\nmissing_modules=\n"
              "off_pin=\ncellpose_env=true\ncellpose_missing_modules=cellpose\ncellpose_off_pin=torch==2.4.1\n"
              "cellpose_cuda=False\ncellpose_pip_in_progress=true\n")
    monkeypatch.setattr(rb, "_ssh_run", lambda *a, **k: types.SimpleNamespace(stdout=stdout))
    probe = rb.probe_host("h")
    assert probe.cellpose_env and probe.cellpose_missing_modules == ("cellpose",)
    assert probe.cellpose_off_pin == ("torch==2.4.1",) and probe.cellpose_cuda == "False"
    assert probe.cellpose_pip_in_progress


def _probe(**kw) -> rb.HostProbe:
    base = dict(has_conda=True, has_repo=True, has_env=True, tensorflow_version="2.13.0", stardist_ok=True,
                gpu_visible=True, python_version="3.10.14")
    base.update(kw)
    return rb.HostProbe(**base)


def _install(monkeypatch, probe, returncode=0):
    calls, said = [], []
    monkeypatch.setattr(rb, "_ssh_run", lambda *a, **k: calls.append((a, k))
                        or types.SimpleNamespace(stdout="", returncode=returncode))
    monkeypatch.setattr(rb, "say", said.append)
    rb.install_cellpose_deps("h", probe)
    return calls, said


def test_a_complete_cellpose_env_is_left_alone(monkeypatch):
    calls, said = _install(monkeypatch, _probe(cellpose_env=True))
    assert calls == [] and any("skipping its pip install" in s for s in said)


def test_a_missing_or_incomplete_cellpose_env_is_installed_into_with_its_own_markers(monkeypatch):
    for probe in (_probe(), _probe(cellpose_env=True, cellpose_off_pin=("torch==2.4.1",))):
        calls, _said = _install(monkeypatch, probe)
        (args, kwargs), = calls
        script = kwargs["input"]
        assert "conda activate allwell-cellpose\n" in script and rb.CELLPOSE_REQUIREMENTS in script
        assert ".pip_cellpose_running" in script and ".pip_running" not in script


def test_a_failed_cellpose_install_is_said_and_does_not_stop_the_bootstrap(monkeypatch):
    _calls, said = _install(monkeypatch, _probe(), returncode=1)
    assert any("failed" in s and "float32" in s for s in said)


def test_a_cellpose_env_whose_torch_cannot_see_the_gpu_is_said():
    assert rb.cellpose_cuda_note(_probe(cellpose_env=True, cellpose_cuda="True")) == ""
    assert rb.cellpose_cuda_note(_probe(cellpose_env=False)) == ""
    assert rb.cellpose_cuda_note(_probe(cellpose_env=True, cellpose_cuda="False", gpu_visible=False)) == ""
    note = rb.cellpose_cuda_note(_probe(cellpose_env=True, cellpose_cuda="False"))
    assert "cannot see the GPU" in note and "525" in note and "conda env remove -n allwell-cellpose" in note


def test_both_envs_are_created_and_force_removes_both():
    assert "ENV_NAME=allwell-cellpose\n" in rb.env_setup_script(rb.CELLPOSE_ENV_NAME)
    assert "ENV_NAME=allwell\n" in rb.env_setup_script()
    assert f"for ENV_NAME in {rb.ENV_NAME} {rb.CELLPOSE_ENV_NAME}; do" in rb.force_clean_script()


def test_the_requirements_file_is_sent_to_the_remote():
    from services.remote_manifest import _REQUIRED_REMOTE_FILES

    assert rb.CELLPOSE_REQUIREMENTS in _REQUIRED_REMOTE_FILES


def test_a_bootstrap_installs_the_cellpose_env_after_allwell_s_and_says_when_its_torch_cannot_see_the_gpu(
        monkeypatch, capsys):
    """Through ``main``, every remote step stubbed: the order the steps run in, and the probe asked again after the
    install, whose answer is the one the GPU note reads."""
    steps = []
    first = _probe()
    after = _probe(cellpose_env=True, cellpose_cuda="False")
    probes = iter([first, after])

    def step(name, result=None):
        def run(*a, **k):
            steps.append(name)
            return result
        return run
    monkeypatch.setattr(rb, "_open_log_file", lambda: (None, None))
    monkeypatch.setattr(rb, "_load_existing_key", lambda alias: "")
    monkeypatch.setattr(rb, "probe_host", lambda *a, **k: steps.append("probe") or next(probes))
    for name in ("install_miniforge_if_missing", "ensure_env_and_staging", "sync_required_files",
                 "install_pip_deps", "install_cellpose_deps"):
        monkeypatch.setattr(rb, name, step(name))
    monkeypatch.setattr(rb, "measure_bandwidth", step("measure_bandwidth", 100.0))
    monkeypatch.setattr(rb, "write_or_update_remotes_toml", step("write", ("/x/remotes.toml", "[remote.h]", True)))
    import services.remote_config as rc
    monkeypatch.setattr(rc, "get_remote", lambda alias: None, raising=False)
    assert rb.main(["h"]) == 0
    assert steps == ["probe", "install_miniforge_if_missing", "ensure_env_and_staging", "sync_required_files",
                     "install_pip_deps", "install_cellpose_deps", "probe", "measure_bandwidth", "write"]
    assert "cannot see the GPU" in capsys.readouterr().out


def test_the_cellpose_env_holds_numpy_below_2():
    """Nothing in the Cellpose env caps numpy as TensorFlow does in ``allwell``, and every recent
    opencv-python-headless (a Cellpose requirement with no bound) needs numpy 2: unpinned, pip upgraded numpy under
    conda's 1.24 builds and the pinned imagecodecs failed to import ("numpy.dtype size changed")."""
    from pathlib import Path

    lines = [ln.split("#")[0].strip() for ln in
             (Path(__file__).resolve().parents[1] / "requirements-remote-cellpose.txt").read_text().splitlines()]
    assert "numpy>=1.24,<1.25" in lines
    assert "opencv-python-headless<4.11" in lines
