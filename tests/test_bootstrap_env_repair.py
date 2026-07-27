"""Bootstrap can rebuild an environment whose creation failed part-way.

The trap this closes: ``conda create -n allwell`` that dies mid-way (on a
cluster, most often a full home-directory quota) leaves a directory at the env
prefix with no ``conda-meta/history``. conda then refuses to *run* in it —
every run fails with ``EnvironmentLocationNotFound: not a conda environment:
<prefix>`` — and refuses to *create over* it, so a re-bootstrap fails behind it
with "prefix already exists". Nothing the user could do from the app fixed it:

* the bootstrap's create-or-skip test asked "is the name in ``conda env list``",
  which is a different question from "is this an environment", and
* ``--force`` removed ``~/miniforge3`` only, while on a host with a
  pre-existing system conda the env lives in ``~/.conda/envs/allwell``.

Both scripts are exercised here against a stub ``conda`` and a scratch ``HOME``,
because the decision they encode is the whole point of them and an ssh call
hides it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from _bootstrap_lib import env_setup_script, force_clean_script  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="needs a POSIX shell to run the script")


def _fake_conda(bin_dir: Path, *, envs: dict[str, str], create_log: Path) -> None:
    """A stub ``conda`` answering ``info --base`` / ``env list`` / ``create``.

    ``conda create`` records its call and materialises a valid env (a prefix
    with ``conda-meta/history``) so the script's own success condition is real
    rather than asserted — and, like the real thing, **refuses** when a
    directory already sits at the prefix. That refusal is the second half of
    the trap: without it these tests would pass against the broken script too.
    """
    rows = "\n".join(f'echo "{name}   {prefix}"' for name, prefix in envs.items())
    (bin_dir / "conda").write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        '  "info --base") echo "$AW_FAKE_BASE" ;;\n'
        '  "env list") echo "# conda environments:"\n'
        f"    {rows or 'true'} ;;\n"
        '  "create "*)\n'
        f'    echo "$@" >> {create_log}\n'
        '    for _p in "$HOME/.conda/envs/allwell" "$AW_FAKE_BASE/envs/allwell"; do\n'
        '      if [ -d "$_p" ]; then\n'
        '        echo "CondaValueError: prefix already exists: $_p" >&2\n'
        "        exit 1\n"
        "      fi\n"
        "    done\n"
        '    mkdir -p "$AW_FAKE_BASE/envs/allwell/conda-meta"\n'
        '    touch "$AW_FAKE_BASE/envs/allwell/conda-meta/history" ;;\n'
        "esac\n"
    )
    (bin_dir / "conda").chmod(0o755)


@pytest.fixture
def remote(tmp_path):
    """A scratch stand-in for the remote host: HOME, a conda base, a stub conda."""
    home = tmp_path / "home"
    base = tmp_path / "opt" / "conda"
    bin_dir = tmp_path / "bin"
    for d in (home, base, bin_dir):
        d.mkdir(parents=True)
    # The bootstrap sources conda.sh before anything else; a no-op file is
    # enough for the parts under test.
    (base / "etc" / "profile.d").mkdir(parents=True)
    (base / "etc" / "profile.d" / "conda.sh").write_text("")
    return home, base, bin_dir


def _run(script: str, remote, *, envs: dict[str, str]) -> tuple[
    subprocess.CompletedProcess, Path]:
    home, base, bin_dir = remote
    create_log = home.parent / "create.log"
    _fake_conda(bin_dir, envs=envs, create_log=create_log)
    r = subprocess.run(
        ["bash", "-s"], input=script, capture_output=True, text=True,
        env={
            "HOME": str(home),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "AW_FAKE_BASE": str(base),
        },
    )
    return r, create_log


def _make_env(prefix: Path, *, valid: bool) -> None:
    prefix.mkdir(parents=True, exist_ok=True)
    if valid:
        (prefix / "conda-meta").mkdir()
        (prefix / "conda-meta" / "history").touch()
    else:
        # What a creation that died part-way leaves behind: some content, no
        # conda-meta/history.
        (prefix / "lib").mkdir()


# ── create-or-repair ────────────────────────────────────────────────────────

def test_a_valid_env_is_left_alone(remote):
    home, base, _ = remote
    prefix = base / "envs" / "allwell"
    _make_env(prefix, valid=True)

    r, create_log = _run(
        env_setup_script(), remote, envs={"allwell": str(prefix)})

    assert r.returncode == 0, r.stderr
    assert "already present" in r.stderr
    assert not create_log.exists(), "a healthy env must not be recreated"
    assert (prefix / "conda-meta" / "history").is_file()


def test_a_half_created_env_is_removed_and_rebuilt(remote):
    """The user's case: `<home>/.conda/envs/allwell` exists but is not an env.

    conda does not list it (no ``conda-meta/history``), so the old check saw
    "absent" and ran ``conda create``, which then failed on "prefix already
    exists" — the state with no way out from inside the app.
    """
    home, base, _ = remote
    stale = home / ".conda" / "envs" / "allwell"
    _make_env(stale, valid=False)

    r, create_log = _run(env_setup_script(), remote, envs={})

    assert r.returncode == 0, r.stderr
    assert "is not a conda environment" in r.stderr
    assert "Removing it" in r.stderr
    assert not (stale / "lib").exists(), "the broken prefix must be gone"
    assert create_log.read_text().startswith("create -y -n allwell")


def test_a_missing_env_is_created_without_removing_anything(remote):
    r, create_log = _run(env_setup_script(), remote, envs={})

    assert r.returncode == 0, r.stderr
    assert "is not a conda environment" not in r.stderr
    assert create_log.read_text().startswith("create -y -n allwell")


def test_the_staging_dirs_are_made_either_way(remote):
    home, _, _ = remote
    r, _ = _run(env_setup_script(), remote, envs={})
    assert r.returncode == 0, r.stderr
    assert (home / ".allwell-staging" / "datasets").is_dir()
    assert (home / ".allwell-staging" / "runs").is_dir()
    assert (home / "allwell" / "_Docs").is_dir()


# ── --force ─────────────────────────────────────────────────────────────────

def test_force_removes_the_env_where_it_actually_lives(remote):
    """``--force`` promises to remove a suspect env. On a system-conda host that
    env is under ``~/.conda/envs``, which the old ``rm -rf ~/miniforge3`` never
    touched — so the one documented repair path did nothing."""
    home, _, _ = remote
    prefix = home / ".conda" / "envs" / "allwell"
    _make_env(prefix, valid=True)
    (home / "miniforge3").mkdir()

    r, _ = _run(force_clean_script(), remote, envs={"allwell": str(prefix)})

    assert r.returncode == 0, r.stderr
    assert not prefix.exists()
    assert not (home / "miniforge3").exists()


def test_force_also_clears_a_half_created_env(remote):
    home, _, _ = remote
    stale = home / ".conda" / "envs" / "allwell"
    _make_env(stale, valid=False)

    r, _ = _run(force_clean_script(), remote, envs={})

    assert r.returncode == 0, r.stderr
    assert not stale.exists()


def test_force_refuses_a_prefix_that_is_not_the_envs_directory(remote):
    """A conda that reports something unexpected must not turn --force into an
    arbitrary ``rm -rf`` on the user's machine."""
    home, _, _ = remote
    odd = home / "important-data"
    odd.mkdir()

    r, _ = _run(force_clean_script(), remote, envs={"allwell": str(odd)})

    assert "refusing to remove unexpected" in r.stderr
    assert odd.exists()
