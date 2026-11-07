"""The remote delete layer (``services.remote_delete``): ``aw_rm`` run for real under bash and dash against a stand-in
home folder, and the Python-side refusal.

What it must never do is what happened: remove a home folder, or anything outside the directory a delete is meant for,
however the target was spelled or what it resolves to on the host.

Every target here lies inside the test's own temporary folder, including the ones that must be refused: if the layer
were broken, what it wrongly removed would be the stand-in's files, never a path of the machine running the tests.
(A case once named ``/tmp`` as a target under the root ``/``; with the root check broken it would have removed this
machine's real ``/tmp``.)
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from services.remote_delete import AW_RM, REFUSED_EXIT, Refused, command, exec_sh, refusal, remote_delete

SHELLS = [s for s in ("bash", "dash", "sh") if shutil.which(s)]


def _home(tmp_path):
    home = tmp_path / "home"
    stage = home / ".allwell-staging" / "runs" / "run-a"
    (stage / "in").mkdir(parents=True)
    (stage / "in" / "A01.zip").write_bytes(b"x")
    (stage / "out").mkdir()
    for name in ("x_A01.csv", "x_A02.csv"):
        (stage / "out" / name).write_text("1", encoding="utf-8")
    (home / "thesis").mkdir()
    (home / "thesis" / "chapter1.tex").write_text("keep", encoding="utf-8")
    return home


def _sh(shell, script, home):
    return subprocess.run([shell, "-c", script], capture_output=True, text=True, timeout=30, cwd=str(home),
                          env={**os.environ, "HOME": str(home)})


def _intact(home):
    return (home / "thesis" / "chapter1.tex").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("shell", SHELLS)
def test_it_deletes_inside_the_root(tmp_path, shell):
    home = _home(tmp_path)
    run = home / ".allwell-staging/runs/run-a"
    out = _sh(shell, command("~/.allwell-staging/runs", ["~/.allwell-staging/runs/run-a/in",
                                                         "~/.allwell-staging/runs/run-a/out/*_A01.csv"]), home)
    assert out.returncode == 0, out.stderr
    assert not (run / "in").exists() and not (run / "out" / "x_A01.csv").exists()
    assert (run / "out" / "x_A02.csv").exists() and _intact(home)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("root, target", [
    ('"$HOME"', '"$HOME"/thesis'),                                         # a root that is the home folder
    ("/", '"$HOME"/thesis'),                                               # a root that is /
    ('"$HOME"/.allwell-staging', '"$HOME"'),                               # the home folder as a target
    ('"$HOME"/.allwell-staging', '"$HOME"/.allwell-staging'),              # the root itself
    ('"$HOME"/.allwell-staging', '"$HOME"/.allwell-staging/runs/..'),      # .. back to the root
    ('"$HOME"/.allwell-staging', '"$HOME"/.allwell-staging/runs/../../thesis'),
    ('"$HOME"/.allwell-staging', '"$HOME"/.allwell-staging/escape/thesis'),  # through a symlink out of the root
])
def test_aw_rm_refuses_what_resolves_outside_its_root(tmp_path, shell, root, target):
    home = _home(tmp_path)
    (home / ".allwell-staging" / "escape").symlink_to(home)
    out = _sh(shell, AW_RM + f"aw_rm {root} {target}", home)
    assert out.returncode == REFUSED_EXIT, (out.returncode, out.stderr)
    assert _intact(home) and (home / ".allwell-staging" / "runs" / "run-a" / "in" / "A01.zip").exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_a_symlink_inside_the_root_is_removed_as_a_link_not_followed(tmp_path, shell):
    home = _home(tmp_path)
    link = home / ".allwell-staging" / "runs" / "to_home"
    link.symlink_to(home)
    out = _sh(shell, AW_RM + 'aw_rm "$HOME"/.allwell-staging/runs "$HOME"/.allwell-staging/runs/to_home/', home)
    assert out.returncode == 0, out.stderr
    assert not link.exists() and not link.is_symlink() and _intact(home)


@pytest.mark.parametrize("target", ["~", "~/", "/data/home/someone", "~/.allwell-staging", "~/.allwell-staging/",
                                    "~/.allwell-staging/runs/../x", "~/.allwell-staging/./x", "~/elsewhere/x",
                                    "~/.allwell-staging/runs/a b", "~/.allwell-staging/runs/$(id)",
                                    "~/.allwell-staging/runs/;rm", ""])
def test_python_refuses_before_anything_is_built(target):
    assert refusal("~/.allwell-staging", target)
    with pytest.raises(ValueError):
        command("~/.allwell-staging", [target])


@pytest.mark.parametrize("root", ["", "/", "~", "~/", "$HOME"])
def test_no_root_that_is_home_or_slash_is_accepted(root):
    assert refusal(root, f"{root.rstrip('/')}/x")


def test_a_refused_delete_is_never_sent():
    class Cache:
        sent = []

        def _ssh_script(self, script, **kw):
            self.sent.append(script)

    cache = Cache()
    result = remote_delete(cache, "~/.allwell-staging", ["~"])
    assert isinstance(result, Refused) and result.returncode == REFUSED_EXIT and cache.sent == []


@pytest.mark.parametrize("shell", SHELLS)
def test_find_exec_through_the_layer(tmp_path, shell):
    home = _home(tmp_path)
    runs = home / ".allwell-staging" / "runs"
    argv = ["find", str(runs), "-mindepth", "1", "-maxdepth", "1", *exec_sh(str(runs))]
    out = subprocess.run(argv, capture_output=True, text=True, timeout=30, cwd=str(home),
                         env={**os.environ, "HOME": str(home)})
    assert out.returncode == 0, out.stderr
    assert list(runs.iterdir()) == [] and _intact(home)


@pytest.mark.parametrize("shell", SHELLS)
def test_under_a_root_that_does_not_exist_yet_there_is_nothing_to_remove(tmp_path, shell):
    """As for ``rm -rf`` of a path that is not there: a fresh host has no staging directory yet."""
    home = _home(tmp_path)
    out = _sh(shell, command("~/not-yet", ["~/not-yet/runs/x"]), home)
    assert out.returncode == 0 and out.stderr == "" and _intact(home)


def _cache(home):
    """A real ``DatasetCache`` whose scripts run under bash against the stand-in home instead of over ssh."""
    from services.remote_config import RemoteHost
    from services.remote_upload import DatasetCache

    cache = DatasetCache.__new__(DatasetCache)
    cache.host = RemoteHost(name="h", ssh_alias="h", remote_staging_dir="~/.allwell-staging")
    cache._ssh_script = lambda script, *, check, **kw: _sh("bash", script, home)
    return cache


def test_every_rm_the_cache_sends_goes_through_aw_rm(tmp_path):
    """``DatasetCache._ssh_run(["rm", ...])`` is how most remote deletes are spelled; it must reach the host as
    ``aw_rm``, so the host-side check (what the path resolves to) runs and not only the check on the string."""
    home = _home(tmp_path)
    run = home / ".allwell-staging" / "runs" / "run-a"
    (run / "escape").symlink_to(home)                 # inside the staging directory as written; the home folder resolved
    cache = _cache(home)
    assert cache._ssh_run(["rm", "-rf", "~/.allwell-staging/runs/run-a/in"], check=True).returncode == 0
    assert not (run / "in").exists() and (run / "out").exists()
    refused = cache._ssh_run(["rm", "-rf", "~/.allwell-staging/runs/run-a/escape/thesis"], check=False)
    assert refused.returncode == REFUSED_EXIT and "not inside" in refused.stderr
    assert _intact(home)
