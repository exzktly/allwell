"""End-to-end incremental pipeline test.

Drives the *real* ``process_microscopy`` over a tiny synthetic dataset using the
dependency-free ``classical_threshold`` segmenter, and asserts that an
``--incremental`` re-run after new FOV files are appended processes ONLY the new
FOVs and merges them into the existing per-well CSV + ``<well>_out.zip`` —
leaving the already-processed FOV's rows byte-identical.

Requires the imaging/segmentation stack (the pipeline's unconditional StarDist
dependency check runs even for the classical method), so it is skipped where
that stack isn't installed; CI runs it with the full ``requirements-remote`` env.
"""

import csv
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("skimage")
pytest.importorskip("tifffile")
pytest.importorskip("stardist")   # pipeline imports the dep check unconditionally
np = pytest.importorskip("numpy")
import tifffile

REPO = Path(__file__).resolve().parent.parent
SCHEMA = "experiment:channel:well:fov:timepoint"
SEP = "_"


def _tif_bytes(seed: int) -> bytes:
    """A 64×64 frame with three bright square blobs on a dim background — enough
    for Otsu + watershed (classical_threshold) to find a few objects."""
    rng = np.random.default_rng(seed)
    img = np.full((64, 64), 100, np.uint16)
    for cy, cx in [(16, 16), (16, 44), (44, 30)]:
        img[cy - 5:cy + 5, cx - 5:cx + 5] = 5000
    img = (img + rng.integers(0, 50, (64, 64)).astype(np.uint16)).astype(np.uint16)
    buf = io.BytesIO()
    tifffile.imwrite(buf, img)
    return buf.getvalue()


def _group(well: str, fov: str) -> dict[str, bytes]:
    return {
        f"exp_NIR_{well}_{fov}_T01.tif": _tif_bytes(abs(hash((well, fov, "n"))) % 99999),
        f"exp_GFP_{well}_{fov}_T01.tif": _tif_bytes(abs(hash((well, fov, "g"))) % 99999),
    }


def _run(in_dir: Path, out_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable, "process_microscopy.py",
        "--input_dir", str(in_dir), "--output_dir", str(out_dir),
        "--nuclear_token", "NIR", "--fluor_tokens", "GFP",
        "--segmentation_method", "classical_threshold",
        "--filename_schema", SCHEMA, "--filename_sep", SEP,
        "--no_auto_threshold", "--workers", "1", *extra,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO))


def _rows(out_dir: Path) -> list[dict]:
    with open(out_dir / "measurements_A01.csv", newline="") as fh:
        return list(csv.DictReader(fh))


def _zip_names(out_dir: Path) -> set[str]:
    return set(zipfile.ZipFile(out_dir / "A01_out.zip").namelist())


def test_incremental_merges_only_new_fov(tmp_path):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    with zipfile.ZipFile(in_dir / "A01.zip", "w") as z:
        for name, data in _group("A01", "1").items():
            z.writestr(name, data)

    # 1) Initial full run: FOV 1 only.
    r = _run(in_dir, out_dir)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    rows1 = _rows(out_dir)
    assert {row["fov"] for row in rows1} == {"1"}
    assert _zip_names(out_dir) == {
        "exp_GFP_A01_1_T01_tophat.tif",
        "exp_NIR_A01_1_T01_labels.tif",
        "exp_NIR_A01_1_T01_overlay.png",
        "exp_NIR_A01_1_T01_tophat.tif",
    }

    # 2) Append FOV 2 into the well zip (what loose-file append produces), then
    #    re-run incrementally.
    with zipfile.ZipFile(in_dir / "A01.zip", "a") as z:
        for name, data in _group("A01", "2").items():
            z.writestr(name, data)
    r = _run(in_dir, out_dir, "--incremental", "--incremental_prior_dir", str(out_dir))
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    combined = r.stdout + r.stderr
    assert "Well A01 — merging 1 new FOV(s)." in combined

    rows2 = _rows(out_dir)
    assert {row["fov"] for row in rows2} == {"1", "2"}
    # FOV-1 rows are untouched (only FOV 2 was reprocessed and merged in).
    fov1_before = [r for r in rows1 if r["fov"] == "1"]
    fov1_after = [r for r in rows2 if r["fov"] == "1"]
    assert fov1_before == fov1_after
    assert len(rows2) > len(rows1)
    # Output zip gained only the FOV-2 images; FOV-1 members preserved.
    names = _zip_names(out_dir)
    assert "exp_NIR_A01_2_T01_labels.tif" in names
    assert "exp_NIR_A01_1_T01_labels.tif" in names

    # 3) Re-run incrementally with nothing new → up-to-date no-op, no changes.
    r = _run(in_dir, out_dir, "--incremental", "--incremental_prior_dir", str(out_dir))
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert "Well A01 — up to date (no new FOVs)." in (r.stdout + r.stderr)
    assert _rows(out_dir) == rows2


def test_incremental_recovers_done_set_from_out_zip_when_csv_missing(tmp_path):
    """A processed well can have its ``<well>_out.zip`` without a sibling CSV
    (e.g. a remote run that materialised only the archive, or a lost CSV). The
    incremental diff must recover the already-done FOV-groups from the archive so
    it processes ONLY the genuinely-new FOV — never re-segmenting the originals.

    Regression for: "it literally reanalyzed previously processed images" when
    in/ has zips + loose TIFs and out/ has out-zips but no CSV.
    """
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    with zipfile.ZipFile(in_dir / "A01.zip", "w") as z:
        for name, data in _group("A01", "1").items():
            z.writestr(name, data)

    # Initial full run → A01_out.zip + measurements_A01.csv.
    r = _run(in_dir, out_dir)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    fov1_zip_members = _zip_names(out_dir)

    # Simulate the "out-zip but no CSV" state.
    (out_dir / "measurements_A01.csv").unlink()

    # Append FOV 2 and re-run incrementally.
    with zipfile.ZipFile(in_dir / "A01.zip", "a") as z:
        for name, data in _group("A01", "2").items():
            z.writestr(name, data)
    r = _run(in_dir, out_dir, "--incremental", "--incremental_prior_dir", str(out_dir))
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    combined = r.stdout + r.stderr
    # FOV 1 was recovered from the out-zip and skipped; only FOV 2 reprocessed.
    assert "recovered" in combined and "_out.zip" in combined
    assert "Well A01 — merging 1 new FOV(s)." in combined

    # The out-zip keeps every original FOV-1 member and gains the FOV-2 images.
    names = _zip_names(out_dir)
    assert fov1_zip_members <= names
    assert "exp_NIR_A01_2_T01_labels.tif" in names
