"""A remote command or script that arrives cut, or re-parsed, runs nothing -- and the app never asks a host to remove
anything outside its staging directory.

A user's remote home folder (Windows here, Linux there) was emptied by an ``rm -rf`` after a log line naming the home
folder itself as what ``rm`` could not remove. The command had 486 operands and 32 000 characters, and Git for Windows'
``ssh.exe`` re-parses long command lines (``remote_commands.pack_for_login_shell``'s docstring records it mangling
launch commands); cut after the ``"$HOME"`` that begins an operand, what runs is ``rm -rf "$HOME"``. Only the launch
commands were packed against that. Now every command line ``ssh_control`` and the other control paths send is packed,
every ``bash -s`` script is one ``{ … }`` group, and every ``rm`` through ``DatasetCache._ssh_run`` must name a path
strictly inside the staging directory (the rule that every call site packs or groups is the fast-lane
guard ``test_every_ssh_command_is_packed.py``).

The cut tests run each prefix for real under the shells a host may have, with a home folder holding data.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from services.remote_config import RemoteHost
from services.remote_transport import (
    RM_REFUSED_EXIT,
    guard_script,
    pack_for_login_shell,
    rm_refusal,
    ssh_control,
)

SHELLS = [s for s in ("bash", "sh", "dash") if shutil.which(s)]
#: A command that deletes the stand-in home's data, then leaves a mark that it ran to the end.
DESTRUCTIVE = 'rm -rf "$HOME"/data; touch "$HOME"/ran_whole'


def _home(tmp_path):
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    (home / "data" / "thesis.txt").write_text("keep", encoding="utf-8")
    return home


def _run(shell, command, home, stdin=None):
    argv = [shell, "-s"] if stdin is not None else [shell, "-c", command]
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=30,
                          env={**os.environ, "HOME": str(home)})


@pytest.mark.parametrize("shell", SHELLS)
def test_a_packed_command_cut_anywhere_runs_nothing(tmp_path, shell):
    packed = pack_for_login_shell(DESTRUCTIVE)
    step = max(1, len(packed) // 120)                     # every position near both ends, a sample between
    cuts = sorted(set(range(1, 40)) | set(range(1, len(packed), step)) | set(range(len(packed) - 40, len(packed))))
    for cut in cuts:
        home = _home(tmp_path / f"{shell}{cut}")
        _run(shell, packed[:cut], home)
        assert (home / "data" / "thesis.txt").exists() and not (home / "ran_whole").exists(), (shell, cut)
    home = _home(tmp_path / f"{shell}whole")
    _run(shell, packed, home)
    assert (home / "ran_whole").exists() and not (home / "data").exists()       # whole, it does run


@pytest.mark.parametrize("shell", SHELLS)
def test_a_packed_command_with_a_character_changed_runs_nothing_or_exactly_itself(tmp_path, shell):
    """A changed character either breaks the decoding -- and nothing runs -- or falls in the gzip header's timestamp,
    which the contents do not depend on, and the very same command runs. Never a different command."""
    import base64
    import gzip

    packed = pack_for_login_shell(DESTRUCTIVE)
    start = packed.index("printf %s ") + len("printf %s ")
    blob_end = packed.index(" |", start)
    broken = 0
    for k, at in enumerate(range(start, blob_end, max(1, (blob_end - start) // 25))):
        bad = packed[:at] + ("A" if packed[at] != "A" else "B") + packed[at + 1:]
        try:
            same = gzip.decompress(base64.b64decode(bad[start:blob_end])).decode() == DESTRUCTIVE
        except Exception:                                 # noqa: BLE001 -- any failure to decode is "not the same"
            same = False
        home = _home(tmp_path / f"{shell}{k}")
        _run(shell, bad, home)
        if same:
            assert (home / "ran_whole").exists(), (shell, at - start)
        else:
            broken += 1
            assert (home / "data" / "thesis.txt").exists() and not (home / "ran_whole").exists(), (shell, at - start)
    assert broken >= 20                                   # most positions are in the compressed data itself


@pytest.mark.parametrize("shell", [s for s in SHELLS if s == "bash"])
def test_a_grouped_script_cut_anywhere_runs_nothing(tmp_path, shell):
    script = guard_script("set -u\nmkdir -p \"$HOME\"/stage\n" + DESTRUCTIVE + "\necho done\n")
    for cut in range(1, len(script) - 1):
        if script[:cut].rstrip().endswith("}"):
            continue                                     # the group is whole: that is the script
        home = _home(tmp_path / f"cut{cut}")
        _run(shell, "", home, stdin=script[:cut])
        assert (home / "data" / "thesis.txt").exists() and not (home / "stage").exists(), cut


def test_the_old_ungrouped_script_cut_mid_way_ran_its_first_half(tmp_path):
    """Why the group: without it, a script cut after its first line ran that line."""
    script = DESTRUCTIVE.replace("; ", "\n") + "\n"
    home = _home(tmp_path)
    _run("bash", "", home, stdin=script[:script.index("\n") + 1])
    assert not (home / "data").exists()


def test_ssh_control_packs_a_command_and_groups_a_script(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append((list(cmd), kw.get("input")))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("services.remote_transport.run_bounded", fake_run)
    host = RemoteHost(name="h", ssh_alias="x")
    ssh_control(host, ["rm -f ~/.allwell-staging/runs/a/.aw_x"], check=False, timeout_s=5)
    ssh_control(host, ["bash", "-s"], check=False, timeout_s=5, stdin=b"echo hi\n")
    from services.remote_commands import unpack_login_shell_command
    (cmd1, _), (cmd2, stdin2) = seen
    assert unpack_login_shell_command(cmd1[-1]) == "rm -f ~/.allwell-staging/runs/a/.aw_x"
    assert cmd2[-2:] == ["bash", "-s"] and stdin2 == b"{\necho hi\n}\n"


HOST = RemoteHost(name="h", ssh_alias="x")          # remote_staging_dir: the default, ~/.allwell-staging


@pytest.mark.parametrize("operand", [
    "~", "~/", "/data/home/zakary.singer", "$HOME", "/", "~/.allwell-staging", "~/.allwell-staging/",
    "~/.allwell-staging/runs/../..", "~/.allwell-staging/./runs", "~/other", "/tmp/x", "~/.allwell-staging-other/x", "",
])
def test_an_rm_outside_the_staging_directory_is_refused(operand):
    assert rm_refusal(HOST, ["rm", "-rf", operand]).startswith("refused to remove")
    assert rm_refusal(HOST, ["rm", "-f", "~/.allwell-staging/runs/a/x", operand]).startswith("refused")


@pytest.mark.parametrize("operand", ["~/.allwell-staging/runs/abc/in", "~/.allwell-staging/runs/abc/.aw_batch_1_ready",
                                     "~/.allwell-staging//datasets/k/", "~/.allwell-staging/runs/abc/out"])
def test_an_rm_inside_the_staging_directory_is_allowed(operand):
    assert rm_refusal(HOST, ["rm", "-rf", operand]) == ""
    assert rm_refusal(HOST, ["mkdir", "-p", "~"]) == ""                       # not an rm: not this check's


def test_a_refused_rm_is_never_sent(monkeypatch):
    from services.remote_upload import DatasetCache

    sent = []
    monkeypatch.setattr("services.remote_upload.ssh_control", lambda *a, **k: sent.append(a) or None)
    cache = DatasetCache.__new__(DatasetCache)
    cache.host = HOST
    result = cache._ssh_run(["rm", "-rf", "~"], check=False)
    assert sent == [] and result.returncode == RM_REFUSED_EXIT and "refused" in result.stderr
    with pytest.raises(RuntimeError, match="refused"):
        cache._ssh_run(["rm", "-rf", "/data/home/zakary.singer"], check=True)
    assert sent == []
