"""Tests for scripts/_bootstrap_lib (v5.2.2 additions).

Coverage is intentionally narrow: the bootstrap module is a sequence
of shell-outs to ssh/rsync/conda whose real behaviour can only be
exercised against a live remote. The v5.2.2 change is purely about
argv construction — does ``_ssh_run`` pass ``-i <key>`` when an
``ssh_key_path`` is configured? — so the tests mock the subprocess
layer and assert the argv shape.

Earlier helpers (probe_host, install_*, measure_bandwidth) are not
re-tested here; the v5.2.2 change is uniform "thread ssh_key_path
through every call", and verifying the leaf ``_ssh_run`` covers that
contract.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock


# scripts/ isn't a package; the wrappers add the repo root to sys.path
# at runtime. Mirror that here so the import works under pytest.
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _bootstrap_lib as bl


# ── _ssh_run: -i flag handling (v5.2.2) ────────────────────────────────────


def test_ssh_run_omits_i_when_no_key(monkeypatch):
    """Default path (no key configured): no -i. Matches pre-v5.2.2
    behaviour so a first-time bootstrap on a host using ssh-agent /
    ~/.ssh/config keys keeps working unchanged."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bl.subprocess, "run", fake_run)
    bl._ssh_run("bigbox", ["echo", "ok"])
    assert "-i" not in captured["cmd"]
    # The alias is wherever ssh's -o options end — verify ordering
    # rather than a fixed index since v5.2.3 prepends accept-new +
    # ConnectTimeout to every invocation.
    assert "bigbox" in captured["cmd"]
    alias_idx = captured["cmd"].index("bigbox")
    # Options come before destination per ssh argv discipline.
    assert all(o in captured["cmd"][:alias_idx]
               for o in ("StrictHostKeyChecking=accept-new",))


def test_ssh_run_inserts_i_flag_when_key_set(monkeypatch, tmp_path):
    """v5.2.2 fix: ssh_key_path threads down to -i <expanded_path>
    so a host whose only accepted credential is the wizard-generated
    key bootstraps successfully on a second run."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bl.subprocess, "run", fake_run)
    key = tmp_path / "allwell_bigbox"
    bl._ssh_run("bigbox", ["echo", "ok"], ssh_key_path=str(key))

    # -i must be present, followed by the expanded key path, and both
    # must come BEFORE the alias (ssh strict argv ordering).
    assert "-i" in captured["cmd"]
    i_idx = captured["cmd"].index("-i")
    assert captured["cmd"][i_idx + 1] == str(key)
    alias_idx = captured["cmd"].index("bigbox")
    assert i_idx < alias_idx, "ssh requires options before the destination"


def test_ssh_run_expands_tilde_in_key_path(monkeypatch):
    """The wizard's default key path uses ~ ; ssh doesn't expand it
    in argv elements so we must expand client-side."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bl.subprocess, "run", fake_run)
    bl._ssh_run(
        "bigbox", ["echo", "ok"],
        ssh_key_path="~/.ssh/allwell_bigbox",
    )
    i_idx = captured["cmd"].index("-i")
    assert "~" not in captured["cmd"][i_idx + 1]
    assert captured["cmd"][i_idx + 1].endswith("allwell_bigbox")


# ── _load_existing_key (v5.2.2) ────────────────────────────────────────────


def test_ssh_run_passes_accept_new_for_first_contact(monkeypatch):
    """v5.2.3 fix: StrictHostKeyChecking=accept-new prevents the
    first-bootstrap exit-255-with-'Host key verification failed'
    failure mode. Without it, ssh would prompt the user for trust
    confirmation, but the bootstrap pipes a heredoc into stdin so
    there's no place to prompt — ssh exits before the probe script
    even runs."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bl.subprocess, "run", fake_run)
    bl._ssh_run("bigbox", ["echo", "ok"])
    # The option value travels as the argument *after* the -o, per
    # the GNU ssh argv convention.
    assert "-o" in captured["cmd"]
    assert "StrictHostKeyChecking=accept-new" in captured["cmd"]
    # And a connect timeout so a dead host fails fast.
    assert any(arg.startswith("ConnectTimeout=") for arg in captured["cmd"])


def test_ssh_run_options_precede_alias(tmp_path, monkeypatch):
    """ssh's argv parser requires options *before* the destination.
    Catch regressions where someone reorders the cmd list and
    accidentally puts an -o flag after the alias."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bl.subprocess, "run", fake_run)
    key = tmp_path / "k"
    bl._ssh_run("bigbox", ["echo", "ok"], ssh_key_path=str(key))
    alias_idx = captured["cmd"].index("bigbox")
    # Every -o, -i must appear before the alias position.
    for i, arg in enumerate(captured["cmd"]):
        if arg in ("-o", "-i"):
            assert i < alias_idx, f"option {arg!r} must precede alias 'bigbox'"


def test_load_existing_key_returns_empty_when_no_remotes(tmp_path, monkeypatch):
    """Typical first-bootstrap: no remotes.toml at all yet. Returns
    empty string so the rest of the bootstrap behaves as if no key
    is configured."""
    monkeypatch.setenv("ALLWELL_REMOTES", str(tmp_path / "nope.toml"))
    assert bl._load_existing_key("anything") == ""


def test_load_existing_key_returns_path_when_set(tmp_path, monkeypatch):
    """Wizard-then-bootstrap flow: the user ran the SSH Key wizard
    which wrote ssh_key_path into remotes.toml. The bootstrap must
    pick it up so it can authenticate."""
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "[remote.jupyterhub]\n"
        "ssh_alias = \"jupyterhub\"\n"
        "ssh_key_path = \"~/.ssh/allwell_jupyterhub\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALLWELL_REMOTES", str(cfg))
    assert bl._load_existing_key("jupyterhub") == "~/.ssh/allwell_jupyterhub"


def test_load_existing_key_empty_for_unknown_alias(tmp_path, monkeypatch):
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "[remote.knownhost]\nssh_alias = \"knownhost\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALLWELL_REMOTES", str(cfg))
    assert bl._load_existing_key("not-in-config") == ""


def test_load_existing_key_empty_for_entry_without_key(tmp_path, monkeypatch):
    """An entry exists but the user hasn't run the wizard — empty
    string, not 'None' or a missing-key error."""
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "[remote.keyless]\nssh_alias = \"keyless\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALLWELL_REMOTES", str(cfg))
    assert bl._load_existing_key("keyless") == ""


def test_locate_conda_sh_snippet_handles_both_paths():
    """v5.2.4: the conda.sh discovery snippet that's prepended to
    ensure_env_and_staging / install_pip_deps must handle both:

      * miniforge in $HOME (the bootstrap's preferred install target)
      * a system / pre-existing conda elsewhere on PATH

    Without this, a host whose probe reports has_conda=true via the
    PATH check (e.g. HPC module-loaded conda) would fail in
    ensure_env_and_staging with "source: no such file or directory"
    because the script hard-coded $HOME/miniforge3.
    """
    # Static check on the snippet: the two probe paths must be present
    # in the order the docstring promises.
    snippet = bl._LOCATE_CONDA_SH
    miniforge_idx = snippet.find('"$HOME/miniforge3/etc/profile.d/conda.sh"')
    info_base_idx = snippet.find("conda info --base")
    assert miniforge_idx != -1, "miniforge check must be present"
    assert info_base_idx != -1, "system-conda fallback must be present"
    assert miniforge_idx < info_base_idx, \
        "miniforge path must be checked first (the bootstrap installs there)"
    # Must exit non-zero with a diagnostic if neither works — silent
    # fallthrough would leave the next conda activate call running
    # against a stale shell env.
    assert "exit 1" in snippet
    assert "could not locate conda.sh" in snippet


def test_locate_conda_sh_snippet_is_used_by_setup_scripts():
    """Regression guard: the install / activate paths must use the
    shared discovery snippet, not hard-coded $HOME/miniforge3."""
    # Read the source of the relevant functions.
    import inspect
    setup_src = inspect.getsource(bl.ensure_env_and_staging)
    pip_src = inspect.getsource(bl.install_pip_deps)
    # Both should reference the discovery snippet.
    assert "_LOCATE_CONDA_SH" in setup_src
    assert "_LOCATE_CONDA_SH" in pip_src
    # And neither should still hard-code the source line (which used
    # to live in the heredoc body before v5.2.4).
    assert 'source "$HOME/miniforge3/etc/profile.d/conda.sh"' not in setup_src
    assert 'source "$HOME/miniforge3/etc/profile.d/conda.sh"' not in pip_src


def test_load_existing_key_is_silent_on_toml_parse_error(tmp_path, monkeypatch):
    """A corrupt remotes.toml shouldn't block bootstrap — fall back
    to no-key behaviour so the user can still bootstrap from scratch
    (and the bootstrap will rewrite the file at the end)."""
    cfg = tmp_path / "remotes.toml"
    cfg.write_text("this is not [[ valid toml = ", encoding="utf-8")
    monkeypatch.setenv("ALLWELL_REMOTES", str(cfg))
    # Must not raise.
    assert bl._load_existing_key("anything") == ""


def test_ssh_run_passes_batchmode_v528(monkeypatch):
    """v5.2.8: BatchMode=yes prevents the bootstrap from dropping the
    user into an interactive password prompt they can't satisfy. With
    no key configured AND the server requiring auth, ssh should exit
    with 'Permission denied' immediately so the wizard-then-bootstrap
    diagnostic path is obvious — not a hung prompt the user types
    blindly into."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bl.subprocess, "run", fake_run)
    bl._ssh_run("bigbox", ["echo", "ok"])
    assert "BatchMode=yes" in captured["cmd"]


# ── v5.2.9: section name vs ssh_alias ───────────────────────────────────────


def test_load_existing_key_finds_entry_when_section_differs_from_ssh_alias(tmp_path, monkeypatch):
    """User reported flow: [remote.j] with ssh_alias='jupyterhub'.
    The bootstrap looks up by section name 'j'; the ssh_alias is
    what actually gets passed to ssh. v5.2.9 separates the two."""
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "[remote.j]\n"
        "ssh_alias = \"jupyterhub\"\n"
        "ssh_key_path = \"~/.ssh/allwell_j\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALLWELL_REMOTES", str(cfg))
    # _load_existing_key takes the SECTION name as argv from the CLI
    # and returns the ssh_key_path. That part is unchanged from v5.2.2.
    assert bl._load_existing_key("j") == "~/.ssh/allwell_j"


def test_write_or_update_remotes_toml_preserves_user_ssh_alias(tmp_path, monkeypatch):
    """v5.2.9 fix: re-running bootstrap on `[remote.j]` with
    ssh_alias='jupyterhub' must keep ssh_alias='jupyterhub' in the
    rewritten block. Before v5.2.9, write_or_update_remotes_toml's
    `replace(base, ssh_alias=alias)` clobbered the user's mapping
    with the section name."""
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "[remote.j]\n"
        "ssh_alias = \"jupyterhub\"\n"
        "remote_project_dir = \"~/allwell\"\n"
        "remote_staging_dir = \"~/.allwell-staging\"\n"
        "remote_python = \"~/miniforge3/envs/allwell/bin/python\"\n"
        "cpu_only = false\n"
        "extra_args = []\n"
        "cache_budget_gb = 200\n"
        "measured_bandwidth_mbps = 0.00\n",
        encoding="utf-8",
    )
    probe = bl.HostProbe(
        has_conda=True, has_repo=True, has_env=True,
        tensorflow_version="2.13.0", stardist_ok=True,
        gpu_visible=True, python_version="3.10.14",
    )
    bl.write_or_update_remotes_toml("j", probe, mbps=42.0, cfg_path=cfg)

    text = cfg.read_text(encoding="utf-8")
    # ssh_alias must still be "jupyterhub" — NOT "j".
    assert 'ssh_alias = "jupyterhub"' in text, \
        f"ssh_alias was clobbered with section name; file is:\n{text}"
    # And measured_bandwidth_mbps did get updated, proving the rewrite
    # ran (this isn't a no-op test).
    assert "measured_bandwidth_mbps = 42.00" in text


# ── _ssh_run: Windows CRLF guard (v5.x.x) ──────────────────────────────────


def test_ssh_run_pipes_string_input_as_bytes(monkeypatch):
    """When the caller passes ``input=<script>`` the script must reach
    ``subprocess.run`` as bytes (not as a text-mode string) so Python
    does NOT translate ``\\n`` → ``\\r\\n`` when writing to ssh's stdin
    pipe on Windows. The remote bash otherwise bails on every line
    with ``bash: line 1: $'\\r': command not found`` and the bootstrap
    aborts before doing anything useful."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["input"] = kwargs.get("input")
        captured["text"] = kwargs.get("text")
        return MagicMock(returncode=0, stdout=b"ok\n", stderr=b"")

    monkeypatch.setattr(bl.subprocess, "run", fake_run)
    bl._ssh_run("bigbox", ["bash", "-s"], input="set -e\necho hi\n")

    assert isinstance(captured["input"], (bytes, bytearray)), (
        f"input must be bytes, got {type(captured['input']).__name__}; "
        "text-mode would re-translate \\n on Windows."
    )
    assert b"\r" not in captured["input"], (
        "CR leaked into the input; bash on the remote would bail "
        r"with `bash: line 1: $'\r': command not found`."
    )
    # text= must NOT be True — otherwise subprocess would re-translate
    # the bytes-or-str we pass back to a text stream on the pipe.
    assert captured["text"] is not True


def test_ssh_run_returns_text_stdout_when_subprocess_yields_bytes(monkeypatch):
    """The bytes-mode fix must still hand callers ``str``-typed
    ``.stdout`` / ``.stderr`` so the rest of the bootstrap (which
    parses probe output line-by-line as text) keeps working."""

    def fake_run(cmd, **kwargs):
        return MagicMock(
            returncode=0,
            stdout=b"has_conda=true\nhas_env=true\n",
            stderr=b"",
            args=cmd,
        )

    monkeypatch.setattr(bl.subprocess, "run", fake_run)
    result = bl._ssh_run("bigbox", ["bash", "-s"], input="echo")
    assert isinstance(result.stdout, str)
    assert "has_conda=true" in result.stdout
    assert result.returncode == 0
