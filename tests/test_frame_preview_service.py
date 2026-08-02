"""Finding and processing one frame the way the pipeline would.

``services.frame_preview`` is what the Analyze pane's Preview page and the Review
pane's Playground both call, so it is the single place where "what the preview
shows" is decided. Two properties matter:

* it finds frames in **every input layout Analyze accepts** — loose TIFs, loose
  TIFs under ``in/``, and per-well zips — because a preview that silently finds
  nothing in a valid folder reads as "this feature is broken";
* it never reimplements the pipeline. Correction goes through
  ``correct_single_frame`` and segmentation through ``segment_nuclei``, so a
  preview cannot disagree with the run it is previewing.

Qt-free and TF-free — no app fixture, no model.
"""

from __future__ import annotations

import io
import zipfile

import numpy as np
import pytest

import services.frame_preview as FP
from util.schema import DEFAULT_SCHEMA, parse_schema

tifffile = pytest.importorskip("tifffile")

SCHEMA = parse_schema(DEFAULT_SCHEMA)


def _img(seed: int = 0, shape=(32, 32)):
    return (np.random.default_rng(seed).random(shape) * 900).astype(np.uint16)


@pytest.fixture
def loose_dir(tmp_path):
    d = tmp_path / "loose"
    d.mkdir()
    for well in ("A01", "A02"):
        for chan in ("NIR", "GFP"):
            tifffile.imwrite(d / f"exp_{chan}_{well}_1_0.tif", _img())
    return d


@pytest.fixture
def zip_dir(tmp_path):
    d = tmp_path / "zips"
    d.mkdir()
    for well in ("C04", "C05"):
        with zipfile.ZipFile(d / f"{well}.zip", "w") as zf:
            for chan in ("NIR", "GFP"):
                buf = io.BytesIO()
                tifffile.imwrite(buf, _img())
                zf.writestr(f"exp_{chan}_{well}_1_0.tif", buf.getvalue())
            # Real archives are not tidy. A member that is not an image must be
            # skipped, not turned into an unreadable frame.
            zf.writestr(".DS_Store", b"junk")
            zf.writestr("notes/", b"")
    return d


# ── discovery ────────────────────────────────────────────────────────────────

def test_finds_loose_tifs_and_parses_the_schema(loose_dir):
    refs = FP.sample_input_frames(loose_dir, schema=SCHEMA, sep="_")
    assert len(refs) == 4
    assert {r.well for r in refs} == {"A01", "A02"}
    assert {r.channel for r in refs} == {"NIR", "GFP"}
    assert all(r.fov == "1" and r.timepoint == "0" for r in refs)


def test_finds_tifs_under_an_in_subfolder(tmp_path):
    """The dataset layout Analyze is usually pointed at."""
    (tmp_path / "in").mkdir()
    tifffile.imwrite(tmp_path / "in" / "exp_NIR_B03_2_5.tif", _img())
    refs = FP.sample_input_frames(tmp_path, schema=SCHEMA, sep="_")
    assert len(refs) == 1
    assert (refs[0].well, refs[0].fov, refs[0].timepoint) == ("B03", "2", "5")


def test_finds_members_inside_per_well_zips(zip_dir):
    refs = FP.sample_input_frames(zip_dir, schema=SCHEMA, sep="_")
    assert {r.well for r in refs} == {"C04", "C05"}
    # Non-image members never become frames.
    assert all(not r.stem.startswith(".DS") for r in refs)
    assert all(isinstance(r.source, tuple) for r in refs)


def test_the_sample_spans_wells_rather_than_exhausting_the_first(tmp_path):
    d = tmp_path / "many"
    d.mkdir()
    for well in ("A01", "A02", "A03"):
        with zipfile.ZipFile(d / f"{well}.zip", "w") as zf:
            for i in range(20):
                buf = io.BytesIO()
                tifffile.imwrite(buf, _img())
                zf.writestr(f"exp_NIR_{well}_{i}_0.tif", buf.getvalue())
    refs = FP.sample_input_frames(d, schema=SCHEMA, sep="_", limit=12)
    assert len({r.well for r in refs}) > 1, (
        "a limit that lands entirely inside one archive would preview a single "
        "well and look like the rest of the plate is missing")


def test_an_empty_folder_yields_no_frames(tmp_path):
    assert FP.sample_input_frames(tmp_path, schema=SCHEMA, sep="_") == []


def test_a_name_the_schema_cannot_parse_still_previews(tmp_path):
    """A folder with an unexpected naming scheme is previewable — it just cannot
    be labelled by well. Dropping it would look like the folder was empty."""
    tifffile.imwrite(tmp_path / "whatever.tif", _img())
    refs = FP.sample_input_frames(tmp_path, schema=SCHEMA, sep="_")
    assert len(refs) == 1
    assert refs[0].label() == "whatever"


# ── loading ──────────────────────────────────────────────────────────────────

def test_loads_a_loose_frame(loose_dir):
    ref = FP.sample_input_frames(loose_dir, schema=SCHEMA, sep="_")[0]
    arr = FP.load_frame(ref)
    assert arr.shape == (32, 32) and arr.dtype == np.float32


def test_loads_a_frame_out_of_a_zip(zip_dir):
    ref = FP.sample_input_frames(zip_dir, schema=SCHEMA, sep="_")[0]
    arr = FP.load_frame(ref)
    assert arr.shape == (32, 32) and arr.dtype == np.float32


def test_an_unreadable_source_is_none_not_a_crash(tmp_path):
    bad = tmp_path / "bad.tif"
    bad.write_text("not an image")
    assert FP.load_frame(FP.FrameRef(source=bad, stem="bad")) is None


# ── correction: the pipeline's, minus what one frame cannot do ───────────────

def test_correction_runs_the_pipelines_own_function(monkeypatch):
    import process_microscopy as pm
    seen = {}

    def _spy(arr, **kwargs):
        seen.update(kwargs)
        return arr
    monkeypatch.setattr(pm, "correct_single_frame", _spy)

    FP.correct_frame(np.zeros((8, 8), np.float32),
                     flatfield_mode="gaussian", background_method="sliding_disk",
                     radius=17, mesh_ratio=4.5)
    assert seen["flatfield_mode"] == "gaussian"
    assert seen["background_method"] == "sliding_disk"
    assert seen["radius"] == 17 and seen["mesh_ratio"] == 4.5


@pytest.mark.parametrize(("ff", "bg", "expected"), [
    ("empty_well", "empty_well", ["flat-field empty_well", "background empty_well"]),
    ("basic", "sliding_disk", ["flat-field basic"]),
    ("none", "empty_well", ["background empty_well"]),
    ("gaussian", "robust_mesh", []),
])
def test_reference_well_modes_are_named_not_silently_skipped(ff, bg, expected):
    """One frame has no reference well to sample. The caller has to be able to
    SAY which correction did not run — a preview that quietly showed an
    uncorrected frame while the form claimed a correction is the exact
    substitution this codebase bans."""
    assert FP.correction_needs_a_plate(ff, bg) == expected


def test_a_reference_mode_is_dropped_from_the_actual_correction(monkeypatch):
    import process_microscopy as pm
    seen = {}
    monkeypatch.setattr(pm, "correct_single_frame",
                        lambda arr, **kw: seen.update(kw) or arr)
    FP.correct_frame(np.zeros((8, 8), np.float32),
                     flatfield_mode="empty_well", background_method="empty_well")
    assert seen["flatfield_mode"] == "none"
    assert seen["background_method"] == "none"


# ── segmentation ─────────────────────────────────────────────────────────────

def test_a_missing_install_is_named(monkeypatch):
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert FP.missing_stardist_deps() == ["stardist", "csbdeep", "tensorflow"]
    with pytest.raises(RuntimeError, match="not installed"):
        FP.segment_frame(np.zeros((8, 8), np.float32))


def test_segmentation_calls_the_pipelines_own_inference(monkeypatch):
    """Not a lookalike: the preview must run ``segment_nuclei``, so the input
    scrubbing and the near-constant-field refusal that make the uncatchable
    ClipperLib abort unreachable apply in the GUI too."""
    import pipeline.stardist as sd

    monkeypatch.setattr(FP, "missing_stardist_deps", lambda: [])
    monkeypatch.setattr(FP, "_GUI_STARDIST_MODEL", object())
    seen = {}

    def _spy(arr, *, model, params):
        seen["params"] = params
        return np.zeros(arr.shape, dtype=np.int32)
    monkeypatch.setattr(sd, "segment_nuclei", _spy)

    FP.segment_frame(np.zeros((8, 8), np.float32),
                     prob_thresh=0.25, nms_thresh=0.5, tile_px=512)
    assert seen["params"] == sd.StarDistParams(prob_thresh=0.25, nms_thresh=0.5,
                                               tile_px=512)


def test_thresholds_default_to_the_models_own(monkeypatch):
    import pipeline.stardist as sd

    monkeypatch.setattr(FP, "missing_stardist_deps", lambda: [])
    monkeypatch.setattr(FP, "_GUI_STARDIST_MODEL", object())
    seen = {}
    monkeypatch.setattr(sd, "segment_nuclei",
                        lambda arr, *, model, params: seen.update(params=params)
                        or np.zeros(arr.shape, dtype=np.int32))
    FP.segment_frame(np.zeros((8, 8), np.float32))
    assert seen["params"] == sd.DEFAULT_STARDIST_PARAMS
