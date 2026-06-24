"""Tests for the SLURM partition helpers in services.remote_config.

Covers the pure launcher-string editors (``launcher_get_partition`` /
``launcher_set_partition`` — used by the GUI's partition dropdown) and the
``sinfo`` query's output parsing (subprocess mocked; no real ssh).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from services.remote_config import (
    RemoteHost,
    launcher_get_partition,
    launcher_set_partition,
    query_slurm_partitions,
)


# ── launcher_get_partition ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "launcher, expected",
    [
        ("srun --partition=gpu --time=4:00:00", "gpu"),
        ("srun --partition cpu-large --mem=64G", "cpu-large"),
        ("srun -p debug", "debug"),
        ("srun -pdebug", "debug"),
        ("srun --cpus-per-task=16", ""),          # no partition
        ("", ""),                                  # empty launcher
    ],
)
def test_launcher_get_partition(launcher, expected):
    assert launcher_get_partition(launcher) == expected


# ── launcher_set_partition ──────────────────────────────────────────────────


def test_set_partition_inserts_after_program_when_absent():
    out = launcher_set_partition("srun --time=4:00:00", "gpu")
    assert out == "srun --partition=gpu --time=4:00:00"


def test_set_partition_replaces_equals_form():
    out = launcher_set_partition("srun --partition=cpu --mem=64G", "gpu")
    assert launcher_get_partition(out) == "gpu"
    assert "cpu" not in out


def test_set_partition_replaces_space_and_short_forms():
    assert launcher_get_partition(
        launcher_set_partition("srun --partition cpu --mem=8G", "gpu")
    ) == "gpu"
    assert launcher_get_partition(
        launcher_set_partition("srun -p cpu", "gpu")
    ) == "gpu"
    assert launcher_get_partition(
        launcher_set_partition("srun -pcpu", "gpu")
    ) == "gpu"


def test_set_partition_empty_removes_flag():
    out = launcher_set_partition("srun --partition=gpu --mem=64G", "")
    assert "--partition" not in out
    assert out == "srun --mem=64G"


def test_set_partition_is_idempotent():
    once = launcher_set_partition("srun --partition=gpu", "cpu")
    twice = launcher_set_partition(once, "cpu")
    assert once == twice == "srun --partition=cpu"


# ── query_slurm_partitions (sinfo output parsing) ───────────────────────────


def _host() -> RemoteHost:
    return RemoteHost(name="cluster", ssh_alias="lobby")


class _Result:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_query_partitions_parses_dedups_strips_default_marker():
    # %P emits the default partition with a trailing '*' and can repeat a
    # partition across node-state rows — both must collapse.
    out = "gpu*\ncpu\ncpu\ndebug\n"
    with patch("services.remote_config.subprocess.run", return_value=_Result(0, out)):
        ok, parts, detail = query_slurm_partitions(_host())
    assert ok
    assert parts == ["cpu", "debug", "gpu"]   # sorted, deduped, no '*'
    assert detail == ""


def test_query_partitions_sinfo_missing_is_actionable():
    err = "bash: sinfo: command not found"
    with patch("services.remote_config.subprocess.run", return_value=_Result(127, "", err)):
        ok, parts, detail = query_slurm_partitions(_host())
    assert not ok
    assert parts == []
    assert "SLURM" in detail


def test_query_partitions_empty_output():
    with patch("services.remote_config.subprocess.run", return_value=_Result(0, "\n  \n")):
        ok, parts, detail = query_slurm_partitions(_host())
    assert not ok
    assert "no partitions" in detail.lower()


def test_query_partitions_timeout():
    import subprocess as _sp
    with patch(
        "services.remote_config.subprocess.run",
        side_effect=_sp.TimeoutExpired(cmd="ssh", timeout=20),
    ):
        ok, parts, detail = query_slurm_partitions(_host(), timeout=20)
    assert not ok
    assert "imed out" in detail
