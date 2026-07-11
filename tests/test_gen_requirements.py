"""The per-profile requirements generator drops exactly the profiled-out deps.

``scripts/gen_requirements`` uses the *same* ``Profile.includes`` predicate the
runtime filter + ``derive_profile`` use, so the pip install set, the stripped
tree, and the runtime subset always agree. Exercised against a synthetic catalog
+ temp requirements files so it runs without touching the real pins.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")  # profile loader lives in the well_viewer package

from scripts.gen_requirements import (
    _dist_name,
    _normalize,
    excluded_pip_names,
    generate,
)
from well_viewer.core.profile import FULL, Profile


def _catalog():
    return [
        {"name": "Multivariate", "deps": ["sklearn", "umap"],
         "pip": ["scikit-learn", "umap-learn"]},
        {"name": "Cellpose", "paths": ["pipeline/cellpose.py"],
         "pip": ["cellpose", "torch", "torchvision"]},
        # A pure-Python feature with no heavy pip deps drops nothing.
        {"name": "Spatial", "paths": ["well_viewer/features/spatial"]},
    ]


# ── name parsing ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line,expected", [
    ("torch==2.0.1", "torch"),
    ("torchvision==0.15.2        # comment", "torchvision"),
    ("scikit-learn==1.3.2", "scikit-learn"),
    ('pyobjc-framework-Cocoa==10.3.1 ; sys_platform == "darwin"',
     "pyobjc-framework-Cocoa"),
    ("pkg[extra]>=1.0", "pkg"),
    ("# a comment", None),
    ("-r requirements-remote.txt", None),
    ("", None),
    ("   ", None),
])
def test_dist_name(line, expected):
    assert _dist_name(line) == expected


def test_normalize_pep503():
    assert _normalize("scikit_learn") == "scikit-learn"
    assert _normalize("UMAP-Learn") == "umap-learn"
    assert _normalize("Torch") == "torch"


# ── exclusion set ────────────────────────────────────────────────────────────

def test_full_profile_drops_nothing():
    assert excluded_pip_names(_catalog(), FULL) == set()


def test_thin_like_profile_drops_excluded_feature_pip_names():
    # Admits neither Multivariate nor Cellpose → both dep sets drop.
    prof = Profile(name="thin", features=frozenset({"Plotting"}))
    drop = excluded_pip_names(_catalog(), prof)
    assert drop == {"scikit-learn", "umap-learn", "cellpose", "torch",
                    "torchvision"}


def test_admitting_a_feature_keeps_its_deps():
    prof = Profile(name="mv", features=frozenset({"Plotting", "Multivariate"}))
    drop = excluded_pip_names(_catalog(), prof)
    assert "scikit-learn" not in drop and "umap-learn" not in drop
    assert {"cellpose", "torch", "torchvision"} <= drop  # Cellpose still out


# ── end-to-end generate() with -r inlining ───────────────────────────────────

def _write_reqs(tmp_path: Path) -> Path:
    (tmp_path / "requirements-remote.txt").write_text(
        "tensorflow==2.13.0\n"
        "stardist==0.9.1\n"
        "torch==2.0.1  # cellpose backend\n"
        "cellpose==3.1.1.2\n"
        'torchvision==0.15.2 ; sys_platform != "win32"\n',
        encoding="utf-8")
    base = tmp_path / "requirements.txt"
    base.write_text(
        "-r requirements-remote.txt\n"
        "PySide6==6.7.3\n"
        "scikit-learn==1.3.2\n"
        "umap-learn==0.5.5\n",
        encoding="utf-8")
    return base


def test_generate_inlines_includes_and_comments_dropped(tmp_path):
    base = _write_reqs(tmp_path)
    prof = Profile(name="thin", features=frozenset({"Plotting"}))
    drop = excluded_pip_names(_catalog(), prof)
    out = generate(base, _catalog(), prof, drop)

    active_lines = [ln for ln in out.splitlines()
                    if ln.strip() and not ln.startswith("#")]
    # Kept, uncommented.
    assert "tensorflow==2.13.0" in active_lines
    assert "stardist==0.9.1" in active_lines
    assert "PySide6==6.7.3" in active_lines
    # Dropped → present only as a commented marker, never as an active line.
    for dist in ("torch", "cellpose", "torchvision", "scikit-learn", "umap-learn"):
        assert not any(_dist_name(ln) == dist for ln in active_lines), dist
    assert out.count("dropped —") == 5
    # The -r include was inlined (remote lines appear), not left as a bare -r.
    assert not any(ln.strip().startswith("-r ") for ln in active_lines)


def test_generate_full_profile_is_passthrough(tmp_path):
    base = _write_reqs(tmp_path)
    out = generate(base, _catalog(), FULL, set())
    active = [ln for ln in out.splitlines()
              if ln.strip() and not ln.startswith("#")]
    for dist in ("torch", "cellpose", "torchvision", "scikit-learn",
                 "umap-learn", "tensorflow", "stardist", "PySide6"):
        assert any(_dist_name(ln) == dist for ln in active), dist
    assert "dropped —" not in out
