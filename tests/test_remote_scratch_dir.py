"""The pipeline's scratch volume on the remote is a per-host setting.

``pipeline.io.scratch_base_for`` reads ``$ALLWELL_SCRATCH_DIR`` and otherwise
uses ``~/.allwell/scratch``. ``services/pipeline_runner`` sets that variable in
the *GUI's* environment, which reaches a local subprocess and nothing else — the
composed remote command exported no such thing, so a cluster run wrote its
transient working set (roughly six float32 intermediates per FOV-group, per
concurrent well) to the login node's home quota. That is the volume an HPC site
deliberately makes small.

The quoting is the part that has to be run rather than read. The values a
cluster user types are ``$TMPDIR``, ``$SLURM_TMPDIR`` and ``/scratch/$USER``,
and a string assertion cannot tell a single quote from a double one — so the
composed fragment is executed under a real bash with a fabricated environment
and the resulting variable is read back.
"""

from __future__ import annotations

import subprocess

import pytest

from services.remote_config import RemoteHost, _format_block, _parse_entry
from services.remote_commands import (
    _compose_remote_cmd,
    _remote_scratch_export,
)
from services.remote_commands import (
    _batch_driver_snippet,
    _compose_driver_cmd,
)

_ARGV = ["python", "process_microscopy.py", "--input_dir", "/r/in"]


def _value_under_bash(fragment: str, env: dict[str, str]) -> str:
    """Run *fragment* in a real bash and report what it set the variable to."""
    result = subprocess.run(
        ["bash", "-c", f'{fragment} printf "%s" "$ALLWELL_SCRATCH_DIR"'],
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **env},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


# ── the expansion, executed ──────────────────────────────────────────────────


@pytest.mark.parametrize("configured,env,expected", [
    # The three spellings a cluster user actually types.
    ("$TMPDIR", {"TMPDIR": "/local/scratch/job7"}, "/local/scratch/job7"),
    ("$TMPDIR/allwell", {"TMPDIR": "/local/scratch/job7"},
     "/local/scratch/job7/allwell"),
    ("/scratch/$USER", {"USER": "rosalind"}, "/scratch/rosalind"),
    ("$SLURM_TMPDIR", {"SLURM_TMPDIR": "/node/tmp"}, "/node/tmp"),
    # A plain absolute path is left exactly as written.
    ("/fast/scratch", {}, "/fast/scratch"),
    # Tilde: double quotes suppress ~ expansion, so it has to become $HOME
    # before it gets there — the same defect `_remote_quote` exists to avoid,
    # arrived at from the opposite direction.
    ("~/scratch", {"HOME": "/home/rosalind"}, "/home/rosalind/scratch"),
    ("~", {"HOME": "/home/rosalind"}, "/home/rosalind"),
    # A space is not shell syntax; it must survive.
    ("/fast/my scratch", {}, "/fast/my scratch"),
])
def test_the_configured_path_reaches_the_pipeline_expanded(configured, env, expected):
    got = _value_under_bash(_remote_scratch_export(configured), env)
    assert got == expected


def test_single_quoting_it_would_have_been_wrong():
    """Guards the *choice*, by measuring the alternative rather than asserting
    which quote character was used.

    `_remote_quote` is the helper every other remote path goes through, and it
    is the obvious thing to reach for here. Run it, and the pipeline is handed
    the literal string `$TMPDIR` — it would create a directory of that name in
    whatever the job's working directory happened to be, and the run would look
    like it had honoured the setting.
    """
    from services.remote_transport import _remote_quote

    env = {"TMPDIR": "/local/scratch/job7"}
    naive = f"export ALLWELL_SCRATCH_DIR={_remote_quote('$TMPDIR')}; "
    assert _value_under_bash(naive, env) == "$TMPDIR", (
        "the quoting this test exists to rule out no longer behaves that way")

    assert _value_under_bash(_remote_scratch_export("$TMPDIR"), env) == env["TMPDIR"]


def test_nothing_configured_exports_nothing():
    """Empty is the default and every pre-existing config, so it has to leave
    the command exactly as it was."""
    assert _remote_scratch_export("") == ""
    assert _remote_scratch_export("   ") == ""


@pytest.mark.parametrize("value", [
    "/scratch; rm -rf /",
    "/scratch && echo hi",
    "/scratch`whoami`",
    "/scratch$(whoami)",
    '/scratch"x',
    "/scratch\nmore",
    "/scratch | tee x",
])
def test_shell_syntax_is_refused_rather_than_composed(value):
    """A typo here would otherwise take the whole command down with a bash error
    that names nothing the user can trace back to a config field."""
    with pytest.raises(ValueError) as exc:
        _remote_scratch_export(value)
    assert "remote_scratch_dir" in str(exc.value)


# ── both composers carry it ──────────────────────────────────────────────────


def test_the_single_shot_command_exports_it():
    cmd = _compose_remote_cmd(
        "srun -N1", "/r/run.pid", _ARGV, cache_hit=False,
        scratch_dir="$TMPDIR/allwell")
    assert "ALLWELL_SCRATCH_DIR" in cmd
    # Before the pipeline, or the pipeline would not see it.
    assert cmd.index("ALLWELL_SCRATCH_DIR") < cmd.index("process_microscopy.py")


def test_the_batched_driver_exports_it_too():
    """Missing this one would put batched runs on the cluster home with nothing
    in the log saying why only some runs were affected."""
    script = _batch_driver_snippet("/r/run-x", 2, _ARGV)
    for launcher in ("srun -N1", ""):          # scheduled and direct alike
        cmd = _compose_driver_cmd(launcher, "/r/run.pid", script,
                                  scratch_dir="/scratch/$USER")
        assert "ALLWELL_SCRATCH_DIR" in cmd, launcher
        assert cmd.index("ALLWELL_SCRATCH_DIR") < cmd.index("process_microscopy.py")


def test_a_cache_hit_needs_no_scratch():
    """It execs `true` and writes nothing at all."""
    cmd = _compose_remote_cmd("srun -N1", "/r/run.pid", ["true"], cache_hit=True,
                              scratch_dir="/scratch/$USER")
    assert "ALLWELL_SCRATCH_DIR" not in cmd


def test_a_host_that_sets_none_composes_the_command_it_always_did():
    """The compatibility case: an existing config must produce a byte-identical
    command, because this field is the only thing that changed for it."""
    for launcher, cache_hit in (("srun -N1", False), ("", False), ("srun -N1", True)):
        with_field = _compose_remote_cmd(
            launcher, "/r/run.pid", _ARGV, cache_hit=cache_hit, scratch_dir="")
        without = _compose_remote_cmd(
            launcher, "/r/run.pid", _ARGV, cache_hit=cache_hit)
        assert with_field == without
        assert "ALLWELL_SCRATCH_DIR" not in with_field


# ── it survives the config file ──────────────────────────────────────────────


def test_the_field_round_trips_through_remotes_toml():
    host = RemoteHost(name="cluster", ssh_alias="hpc-login",
                      remote_scratch_dir="/scratch/$USER")
    block = _format_block(host)
    assert "remote_scratch_dir" in block

    import tomllib
    parsed = tomllib.loads(block)["remote"]["cluster"]
    back = _parse_entry("cluster", parsed)
    assert back.remote_scratch_dir == "/scratch/$USER"


def test_a_host_without_one_writes_no_line_for_it():
    """Same as compute_launcher and the jupyterhub path: the common case leaves
    the block tidy, and the loader defaults the field when it is absent."""
    block = _format_block(RemoteHost(name="box", ssh_alias="box"))
    assert "remote_scratch_dir" not in block

    import tomllib
    back = _parse_entry("box", tomllib.loads(block)["remote"]["box"])
    assert back.remote_scratch_dir == ""
