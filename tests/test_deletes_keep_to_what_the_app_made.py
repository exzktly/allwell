"""Each delete the audit found able to remove data that is not the app's, pinned to what it may remove now.

Every target in these tests lies inside the test's own temporary folder, including the ones that must be refused: a
broken check removes the stand-in's files, never a path of the machine running the tests.

F1/F2 bootstrap's ``~/miniforge3`` · F3 the earlier-attempt cleanup · F4 the per-well delete list · F5 the CSV gzip
loop · F6 Zip Loose Folder's originals · F7 the Datasets resume clear · R5 the reservation sweep.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.remote_delete import AW_RM

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def _sh(script: str, home: Path, shell: str = "bash") -> subprocess.CompletedProcess:
    return subprocess.run([shell, "-c", script], capture_output=True, text=True, timeout=60, cwd=str(home),
                          env={"PATH": "/usr/bin:/bin", "HOME": str(home)})


def _personal(home: Path) -> Path:
    keep = home / "thesis" / "chapter1.tex"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("keep", encoding="utf-8")
    return keep


# ── F1 / F2: bootstrap removes only a miniforge it installed ───────────────────────────────────────────────────────

def _miniforge(home: Path, *, ours: bool) -> Path:
    from services.remote_bootstrap import MINIFORGE_MARKER

    mf = home / "miniforge3"
    (mf / "envs" / "my-own-env").mkdir(parents=True)
    (mf / "envs" / "my-own-env" / "lib.py").write_text("mine", encoding="utf-8")
    if ours:
        (mf / MINIFORGE_MARKER).write_text("", encoding="utf-8")
    return mf


def test_f1_a_miniforge_the_app_did_not_install_is_never_removed_and_the_install_stops(tmp_path):
    from services.remote_bootstrap import miniforge_removal_shell

    home = tmp_path / "home"
    mf = _miniforge(home, ours=False)
    out = _sh('set -euo pipefail\nMINIFORGE_DIR="$HOME/miniforge3"\n' + miniforge_removal_shell(on_foreign="exit 3"),
              home)
    assert out.returncode == 3 and "NOT removing" in out.stderr
    assert (mf / "envs" / "my-own-env" / "lib.py").read_text(encoding="utf-8") == "mine"


def test_f1_one_the_app_installed_is_removed_for_a_clean_reinstall(tmp_path):
    from services.remote_bootstrap import miniforge_removal_shell

    home = tmp_path / "home"
    mf = _miniforge(home, ours=True)
    keep = _personal(home)
    out = _sh('set -euo pipefail\nMINIFORGE_DIR="$HOME/miniforge3"\n' + miniforge_removal_shell(on_foreign="exit 3"),
              home)
    assert out.returncode == 0, out.stderr
    assert not mf.exists() and keep.exists()


def _install_script(monkeypatch) -> str:
    """The script ``install_miniforge_if_missing`` sends, captured instead of sent."""
    from services import remote_bootstrap as rb

    sent = {}
    monkeypatch.setattr(rb, "_ssh_run", lambda alias, argv, *, input, **kw: sent.setdefault("script", input))
    rb.install_miniforge_if_missing("h", SimpleNamespace(has_conda=False), force=True)
    return sent["script"]


def test_f1_the_install_marks_what_it_installed(monkeypatch):
    from services.remote_bootstrap import MINIFORGE_MARKER

    script = _install_script(monkeypatch)
    lines = script.splitlines()
    assert f'touch "$MINIFORGE_DIR/{MINIFORGE_MARKER}"' in lines
    assert lines.index(f'touch "$MINIFORGE_DIR/{MINIFORGE_MARKER}"') > lines.index('bash "$INSTALLER" -b -p "$MINIFORGE_DIR"')


def test_f1_the_real_install_stops_at_a_personal_miniforge_before_downloading_anything(tmp_path, monkeypatch):
    home = tmp_path / "home"
    mf = _miniforge(home, ours=False)
    out = _sh(_install_script(monkeypatch), home)
    assert out.returncode == 3 and "NOT removing" in out.stderr
    assert (mf / "envs" / "my-own-env" / "lib.py").read_text(encoding="utf-8") == "mine"


def test_f2_force_keeps_a_personal_miniforge_and_everything_in_allwell_but_the_code(tmp_path):
    from services.remote_bootstrap import force_clean_script

    home = tmp_path / "home"
    mf = _miniforge(home, ours=False)
    (home / "allwell" / "code" / "v1").mkdir(parents=True)
    (home / "allwell" / "my-notes.txt").write_text("mine", encoding="utf-8")
    keep = _personal(home)
    out = _sh(force_clean_script(), home)
    assert out.returncode == 0, out.stderr
    assert mf.exists() and (mf / "envs" / "my-own-env" / "lib.py").exists()
    assert (home / "allwell" / "my-notes.txt").exists() and not (home / "allwell" / "code").exists()
    assert keep.exists()


# ── F3: the earlier-attempt cleanup removes run directories and nothing else ───────────────────────────────────────

RUNS = "~/.allwell-staging/runs"


@pytest.mark.parametrize("named", ["~", "~/", "~/in/..", "~/.allwell-staging", RUNS, f"{RUNS}/../runs",
                                   "~/.allwell-staging/datasets/k", "/data/home/someone", f"{RUNS}/notarun",
                                   f"{RUNS}/run-a/nested"])
def test_f3_a_journal_entry_that_is_not_a_run_directory_is_never_a_candidate(named):
    from services.remote_reuse import confine_to_runs

    assert confine_to_runs({named: "", f"{RUNS}/run-0123-abcd": ""}, RUNS) == {f"{RUNS}/run-0123-abcd": ""}


def test_f3_on_the_host_a_run_directory_that_resolves_outside_runs_is_refused(tmp_path):
    """The Python check reads strings; the host check reads what they resolve to. A run directory that is a link to
    the home folder is removed as a link, never followed."""
    from services.remote_reuse import remove_if_idle_script

    home = tmp_path / "home"
    runs = home / ".allwell-staging" / "runs"
    runs.mkdir(parents=True)
    (runs / "run-link").symlink_to(home)
    (runs / "run-real").mkdir()
    keep = _personal(home)
    out = _sh(remove_if_idle_script(str(runs), stale=120) + f'_aw_clean "{runs}/run-real"\n_aw_clean "{runs}/run-link"\n',
              home)
    assert not (runs / "run-real").exists() and not (runs / "run-link").is_symlink(), out.stderr
    assert keep.exists()


# ── F4 / F5: a space or a * in a directory does not change what is removed ─────────────────────────────────────────

def test_f4_the_per_well_delete_list_removes_only_the_well_s_files_whatever_the_directory_is_called(tmp_path,
                                                                                                    monkeypatch):
    from services import remote_transport as rt

    home = tmp_path / "home"
    stage = home / "my stage"                      # a space: unquoted, `~/my` and `stage/…` were two operands
    out_dir = stage / "runs" / "run-a" / "out"
    out_dir.mkdir(parents=True)
    for name in ("x_A01.csv", "x_A02.csv"):
        (out_dir / name).write_text("1", encoding="utf-8")
    (home / "my").mkdir()
    (home / "my" / "x_A01.csv").write_text("mine", encoding="utf-8")       # what the unquoted form would reach
    sent = []

    def fake_control(host, remote, *, check, timeout_s, stdin=None, what=""):
        sent.append(stdin.decode())
        r = subprocess.run([BASH, "-s"], input=stdin, capture_output=True, timeout=30, cwd=str(home),
                           env={"PATH": "/usr/bin:/bin", "HOME": str(home)})
        return subprocess.CompletedProcess(remote, r.returncode, r.stdout.decode(), r.stderr.decode())

    monkeypatch.setattr(rt, "ssh_control", fake_control)
    host = SimpleNamespace(remote_staging_dir="~/my stage")
    assert rt.delete_remote_paths(host, ["~/my stage/runs/run-a/out/*_A01.csv"], what="A01", log_fn=lambda _m: None)
    assert not (out_dir / "x_A01.csv").exists() and (out_dir / "x_A02.csv").exists()
    assert (home / "my" / "x_A01.csv").read_text(encoding="utf-8") == "mine"


def test_f5_the_csv_gzip_loop_reaches_only_the_output_directory(tmp_path, monkeypatch):
    from services import remote_csv

    home = tmp_path / "home"
    out_dir = home / "a b" / "out"
    out_dir.mkdir(parents=True)
    (out_dir / "x_A01.csv").write_text("1,2\n", encoding="utf-8")
    (home / "a").mkdir()
    (home / "a" / "x_A01.csv").write_text("mine", encoding="utf-8")
    captured = {}

    def fake_run(argv, *, input, **kw):
        captured["out"] = subprocess.run([BASH, "-s"], input=input, capture_output=True, timeout=30, cwd=str(home),
                                         env={"PATH": "/usr/bin:/bin", "HOME": str(home)})
        return captured["out"]

    monkeypatch.setattr(remote_csv, "run_bounded", fake_run)
    host = SimpleNamespace(ssh_argv=lambda allocate_tty=False: ["ssh", "h"])
    remote_csv.compress_remote_csvs(host, "~/a b/out", "A01", lambda _m: None)
    assert (out_dir / "x_A01.csv.gz").exists() and not (out_dir / "x_A01.csv").exists()
    assert (home / "a" / "x_A01.csv").read_text(encoding="utf-8") == "mine"


# ── F6: Zip Loose Folder keeps the originals unless the archive reads back whole ───────────────────────────────────

def test_f6_originals_are_kept_when_the_archive_falls_short(tmp_path, monkeypatch):
    from datasets.import_loose import apply

    well = tmp_path / "A01"
    well.mkdir()
    tifs = []
    for i in range(3):
        t = well / f"x_GFP_A01_F00{i}.tif"
        t.write_bytes(os.urandom(2048))
        tifs.append(t)
    real = apply.atomic_zip

    class Short:
        """An atomic_zip that silently drops the last member: an archive that falls short of what was written."""
        def __init__(self, target):
            self._cm = real(target)

        def __enter__(self):
            zf = self._cm.__enter__()
            write = zf.write
            zf.write = lambda path, arcname=None: None if str(path).endswith("F002.tif") else write(path, arcname)
            return zf

        def __exit__(self, *exc):
            return self._cm.__exit__(*exc)

    monkeypatch.setattr(apply, "atomic_zip", Short)
    with pytest.raises(ValueError, match="original TIF"):
        apply._zip_well_folders(tmp_path)
    assert all(t.exists() for t in tifs)


def test_f6_a_whole_archive_lets_the_originals_go(tmp_path):
    from datasets.import_loose import apply

    well = tmp_path / "A01"
    well.mkdir()
    for i in range(3):
        (well / f"x_GFP_A01_F00{i}.tif").write_bytes(os.urandom(2048))
    assert apply._zip_well_folders(tmp_path) == 1
    with zipfile.ZipFile(tmp_path / "A01.zip") as zf:
        assert len(zf.namelist()) == 3
    assert not any(well.glob("*.tif")) if well.exists() else True


# ── F7: the Datasets resume clear never reaches a source ──────────────────────────────────────────────────────────

def _paths(root: Path):
    from datasets.core.dataset_paths import DatasetPaths

    return DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out")


def _dataset(root: Path) -> Path:
    (root / "in").mkdir(parents=True)
    (root / "in" / "A01.zip").write_bytes(b"x")
    (root / "out").mkdir()
    return root


@pytest.mark.parametrize("where", ["same", "dest_inside_source", "source_inside_dest"])
def test_f7_a_destination_overlapping_a_source_is_never_cleared(tmp_path, where):
    from datasets.core.containers import clear_destination
    from util.safe_delete import DeleteRefused

    src = _dataset(tmp_path / "plate")
    dest = {"same": src, "dest_inside_source": src / "sub", "source_inside_dest": tmp_path}[where]
    with pytest.raises(DeleteRefused):
        clear_destination(_paths(dest), sources=(_paths(src),))
    assert (src / "in" / "A01.zip").exists()


def test_f7_a_destination_s_in_that_is_a_link_is_not_followed(tmp_path):
    from datasets.core.containers import clear_destination
    from util.safe_delete import DeleteRefused

    precious = _dataset(tmp_path / "precious")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "in").symlink_to(precious / "in")
    with pytest.raises(DeleteRefused):
        clear_destination(_paths(dest), sources=())
    assert (precious / "in" / "A01.zip").exists()


def test_f7_a_destination_apart_from_its_sources_is_cleared(tmp_path):
    from datasets.core.containers import clear_destination

    src, dest = _dataset(tmp_path / "plate"), _dataset(tmp_path / "dest")
    clear_destination(_paths(dest), sources=(_paths(src),))
    assert not (dest / "in").exists() and not (dest / "out").exists() and (src / "in" / "A01.zip").exists()


# ── R5: the reservation sweep removes a link as a link ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("shell", [s for s in ("sh", "dash", "bash") if shutil.which(s)])
def test_r5_a_stale_reservation_that_is_a_link_is_removed_as_a_link(tmp_path, shell):
    from services.remote_reservation import _stale_reservations_script

    home = tmp_path / "home"
    root = home / ".allwell-staging" / "reservations"
    root.mkdir(parents=True)
    keep = _personal(home)
    (root / "tok-link").symlink_to(home)
    os.utime(root / "tok-link", (0, 0), follow_symlinks=False)
    out = _sh(_stale_reservations_script(str(root)), home, shell)
    assert keep.exists(), out.stderr
    assert AW_RM.splitlines()[0] in _stale_reservations_script(str(root))


# ── D2: the pipeline's directory removal takes only the pipeline's own temporary folders ──────────────────────────

def test_d2_remove_directory_refuses_a_folder_the_pipeline_did_not_name(tmp_path):
    from pipeline.io import remove_directory

    out = tmp_path / "output"
    mine = out / "_tmp_extract_ab12_A01"
    (mine / "x").mkdir(parents=True)
    theirs = out / "A01"
    (theirs / "img.tif").parent.mkdir(parents=True)
    (theirs / "img.tif").write_bytes(b"theirs")
    link = out / "_tmp_images_link"
    link.symlink_to(theirs)
    remove_directory(theirs)
    remove_directory(link)
    remove_directory(mine)
    assert (theirs / "img.tif").read_bytes() == b"theirs" and link.is_symlink()
    assert not mine.exists()


# ── L2: the local layer ────────────────────────────────────────────────────────────────────────────────────────────

def test_l2_safe_rmtree_removes_a_folder_strictly_inside_its_root(tmp_path):
    from util.safe_delete import safe_rmtree

    root = tmp_path / "dest"
    (root / "in" / "A01").mkdir(parents=True)
    keep = root / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    safe_rmtree(root / "in", inside=root)
    safe_rmtree(root / "not-there", inside=root)          # nothing to remove is not an error
    assert not (root / "in").exists() and keep.exists()


def test_l2_safe_rmtree_refuses_a_link_and_leaves_what_it_points_at(tmp_path):
    from util.safe_delete import DeleteRefused, safe_rmtree

    root = tmp_path / "dest"
    root.mkdir()
    theirs = tmp_path / "theirs"
    (theirs / "data.txt").parent.mkdir()
    (theirs / "data.txt").write_text("theirs", encoding="utf-8")
    (root / "in").symlink_to(theirs)
    with pytest.raises(DeleteRefused, match="link"):
        safe_rmtree(root / "in", inside=root)
    assert (theirs / "data.txt").read_text(encoding="utf-8") == "theirs"


def test_l2_safe_rmtree_refuses_what_resolves_outside_its_root(tmp_path):
    from util.safe_delete import DeleteRefused, safe_rmtree

    root = tmp_path / "dest"
    (root / "sub").mkdir(parents=True)
    theirs = tmp_path / "theirs"
    theirs.mkdir()
    (theirs / "data.txt").write_text("theirs", encoding="utf-8")
    (root / "sub" / "hop").symlink_to(tmp_path)           # a link in the parent, not the target itself
    for target in (root / "sub" / ".." / ".." / "theirs", root / "sub" / "hop" / "theirs", root):
        with pytest.raises(DeleteRefused, match="not inside"):
            safe_rmtree(target, inside=root)
    assert (theirs / "data.txt").read_text(encoding="utf-8") == "theirs"


def test_l2_no_root_that_is_home_or_a_filesystem_root_is_accepted(tmp_path, monkeypatch):
    from util.safe_delete import refusal

    home = tmp_path / "home"
    (home / "x").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert "home folder" in refusal(home / "x", home)
    anchor = Path(tmp_path.anchor)
    assert "filesystem root" in refusal(tmp_path, anchor)           # the string check only; nothing is removed
