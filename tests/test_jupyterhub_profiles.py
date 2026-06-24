"""Tests for JupyterHub compute-profile discovery in services.remote_config.

Covers the pure kwargs→srun mapper, the JSON parser, the ssh query (subprocess
mocked), and an end-to-end run of the on-remote discovery snippet against a
realistic jupyterhub_config.py whose spawner imports are NOT installed (so the
stub-importer path is exercised).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from unittest.mock import patch

import pytest

from services.remote_config import (
    RemoteHost,
    _JHUB_PROFILES_SNIPPET,
    parse_jupyterhub_profiles_json,
    profile_kwargs_to_launcher,
    query_jupyterhub_profiles,
)


# ── profile_kwargs_to_launcher ──────────────────────────────────────────────


def test_kwargs_to_launcher_batchspawner_req_fields():
    out = profile_kwargs_to_launcher(
        {"req_partition": "gpu", "req_ngpus": 1, "req_runtime": "4:00:00",
         "req_memory": "64G", "req_nprocs": 16}
    )
    assert out == (
        "srun --partition=gpu --cpus-per-task=16 --mem=64G "
        "--time=4:00:00 --gres=gpu:1"
    )


def test_kwargs_to_launcher_bare_keys_and_options():
    out = profile_kwargs_to_launcher(
        {"partition": "cpu", "req_options": "--account=lab --qos=normal"}
    )
    assert "--partition=cpu" in out
    assert out.endswith("--account=lab --qos=normal")


def test_kwargs_to_launcher_ngpus_zero_is_omitted():
    out = profile_kwargs_to_launcher({"req_partition": "cpu", "req_ngpus": 0})
    assert "--gres" not in out


def test_kwargs_to_launcher_explicit_launcher_wins():
    out = profile_kwargs_to_launcher(
        {"launcher": "srun -p custom --time=1:00:00", "req_partition": "ignored"}
    )
    assert out == "srun -p custom --time=1:00:00"


def test_kwargs_to_launcher_nothing_recognised_is_empty():
    assert profile_kwargs_to_launcher({"image": "jupyter/scipy"}) == ""
    assert profile_kwargs_to_launcher({}) == ""
    assert profile_kwargs_to_launcher("not a dict") == ""  # type: ignore[arg-type]


# ── parse_jupyterhub_profiles_json ──────────────────────────────────────────


_DOC = {
    "ok": True,
    "source": "/etc/jupyterhub/jupyterhub_config.py",
    "profiles": [
        {"name": "GPU 1x (4h)", "slug": "gpu",
         "kwargs": {"req_partition": "gpu", "req_ngpus": 1, "req_runtime": "4:00:00"}},
        {"name": "CPU 16 (1d)", "slug": "cpu",
         "kwargs": {"req_partition": "cpu", "req_nprocs": 16, "req_runtime": "24:00:00"}},
        {"name": "Cloud image", "slug": "img", "kwargs": {"image": "foo"}},  # non-SLURM
    ],
}


def test_parse_ok_maps_each_profile_and_skips_non_slurm():
    ok, profiles, detail = parse_jupyterhub_profiles_json(json.dumps(_DOC))
    assert ok
    assert detail == ""
    assert [n for n, _ in profiles] == ["GPU 1x (4h)", "CPU 16 (1d)"]
    assert profiles[0][1] == "srun --partition=gpu --time=4:00:00 --gres=gpu:1"


def test_parse_tolerates_login_banner_noise():
    noisy = "Welcome to login01\nLast login: ...\n" + json.dumps(_DOC)
    ok, profiles, _ = parse_jupyterhub_profiles_json(noisy)
    assert ok and len(profiles) == 2


def test_parse_logical_failure_surfaces_error():
    doc = {"ok": False, "error": "jupyterhub_config.py not found (looked in: ...)"}
    ok, profiles, detail = parse_jupyterhub_profiles_json(json.dumps(doc))
    assert not ok and profiles == []
    assert "not found" in detail


def test_parse_all_profiles_non_slurm_is_failure():
    doc = {"ok": True, "profiles": [{"name": "x", "kwargs": {"image": "y"}}]}
    ok, profiles, detail = parse_jupyterhub_profiles_json(json.dumps(doc))
    assert not ok
    assert "no SLURM-shaped" in detail


def test_parse_empty_output_uses_stderr():
    ok, profiles, detail = parse_jupyterhub_profiles_json(
        "", stderr="ssh: connect: Connection refused", returncode=255
    )
    assert not ok
    assert "Connection refused" in detail


# ── query_jupyterhub_profiles (subprocess mocked) ───────────────────────────


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_query_runs_snippet_over_ssh_and_parses():
    host = RemoteHost(name="c", ssh_alias="lobby",
                      jupyterhub_config_path="/opt/jh/jupyterhub_config.py")
    with patch("services.remote_config.subprocess.run",
               return_value=_Result(0, json.dumps(_DOC))) as run:
        ok, profiles, _ = query_jupyterhub_profiles(host)
    assert ok and len(profiles) == 2
    # The remote command embeds the snippet and the configured path.
    sent = run.call_args.args[0][-1]
    assert "python3 -c" in sent
    assert "/opt/jh/jupyterhub_config.py" in sent


def test_query_timeout_is_actionable():
    host = RemoteHost(name="c", ssh_alias="lobby")
    with patch("services.remote_config.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=25)):
        ok, profiles, detail = query_jupyterhub_profiles(host, timeout=25)
    assert not ok and "imed out" in detail


# ── end-to-end: the real discovery snippet vs a sample config ───────────────


def test_snippet_extracts_profiles_with_uninstalled_spawner_imports(tmp_path):
    """The on-remote snippet must read ProfilesSpawner.profiles even when the
    config imports wrapspawner / batchspawner (not installed here) — the
    stub-importer keeps the `c.X = ...` assignments alive."""
    cfg = tmp_path / "jupyterhub_config.py"
    cfg.write_text(
        "from wrapspawner import ProfilesSpawner\n"
        "from batchspawner import SlurmSpawner\n"
        "c.JupyterHub.spawner_class = ProfilesSpawner\n"
        "c.ProfilesSpawner.profiles = [\n"
        "    ('GPU node', 'gpu', SlurmSpawner,\n"
        "     dict(req_partition='gpu', req_ngpus=1, req_runtime='4:00:00')),\n"
        "    ('CPU node', 'cpu', SlurmSpawner,\n"
        "     dict(req_partition='cpu', req_nprocs=16, req_runtime='24:00:00')),\n"
        "]\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-c", _JHUB_PROFILES_SNIPPET, str(cfg)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout.strip().splitlines()[-1])
    assert doc["ok"] is True
    assert doc["source"] == str(cfg)
    names = [p["name"] for p in doc["profiles"]]
    assert names == ["GPU node", "CPU node"]
    assert doc["profiles"][0]["kwargs"]["req_partition"] == "gpu"


def test_snippet_handles_profile_list_dict_form(tmp_path):
    """The profile_list (list-of-dicts) shape is also extracted."""
    cfg = tmp_path / "jupyterhub_config.py"
    cfg.write_text(
        "c.SlurmSpawner.profile_list = [\n"
        "    {'display_name': 'Big mem', 'slug': 'bigmem',\n"
        "     'options': {'req_partition': 'highmem', 'req_memory': '512G'}},\n"
        "]\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-c", _JHUB_PROFILES_SNIPPET, str(cfg)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout.strip().splitlines()[-1])
    assert doc["ok"] is True
    assert doc["profiles"][0]["name"] == "Big mem"
    assert doc["profiles"][0]["kwargs"]["req_partition"] == "highmem"


def test_snippet_missing_config_reports_not_found(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-c", _JHUB_PROFILES_SNIPPET, str(tmp_path / "nope.py")],
        capture_output=True, text=True, timeout=30,
    )
    doc = json.loads(proc.stdout.strip().splitlines()[-1])
    assert doc["ok"] is False
    assert "not found" in doc["error"]
