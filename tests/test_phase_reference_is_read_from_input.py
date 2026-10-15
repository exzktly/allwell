"""A referenced phase channel is read back from the input archive — everywhere.

Under ``--phase_output reference`` (``pipeline/output_images.py``) a run archives
no ``_tophat`` for its phase / brightfield channel, because the pipeline never
corrects that channel and its input frame already holds those pixels. Every
reader that wanted the corrected frame therefore has to find it in
``in/<WELL>.zip`` instead, and each was written to read ``out/`` only:

* Review's frame loader, which already falls back from a missing ``_tophat`` to
  the raw frame — but counts each such cell as *uncorrected* and badges it, which
  for the phase channel would be false on every cell;
* the Image Table's placeholder, which would say "(no image)" for a dataset whose
  ``in/`` simply did not come with it;
* the pipeline's end-of-run Otsu estimate and Review's Cell Gating
  auto-threshold, which sample ``_tophat`` members only and would quietly lose
  the phase channel's threshold.

Each test builds a real on-disk in/out dataset in the shape the pipeline writes
and asserts the value a reader produced, with fixtures built so that reading
the wrong source gives a different number.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")
tifffile = pytest.importorskip("tifffile")
pytest.importorskip("skimage")

import numpy as np  # noqa: E402

from review.data import frame_load as fl  # noqa: E402

_SCHEMA = "experiment:channel:well:fov:timepoint"
LOG = logging.getLogger("test_phase_reference")


def _tif(arr) -> bytes:
    buf = io.BytesIO()
    tifffile.imwrite(buf, np.asarray(arr))
    return buf.getvalue()


def _info(phase_output="reference") -> dict:
    return {
        "schema": _SCHEMA, "separator": "_",
        "schema_fields": _SCHEMA.split(":"), "well_index": 2, "channel_index": 1,
        "fov_index": 3, "tp_index": 4, "nuclear_token": "DAPI",
        "fluor_tokens": ["dapi", "gfp", "bf"], "phase_token": "bf",
        **({"phase_output": phase_output} if phase_output else {}),
    }


def _labels():
    lab = np.zeros((32, 32), np.int32)
    lab[4:12, 4:12] = 1
    lab[18:28, 18:28] = 2
    return lab


def _dataset(root: Path, *, bf_in=7, bf_out=None, with_in=True) -> Path:
    """``in/A01.zip`` with raw DAPI/GFP/BF, ``out/A01_out.zip`` with labels and
    DAPI's tophat only — GFP has no tophat on purpose, as the control for "a
    real fallback is still counted". *bf_out* adds a BF tophat, as a copy-mode
    run would have written."""
    (root / "out").mkdir(parents=True)
    if with_in:
        (root / "in").mkdir()
        with zipfile.ZipFile(root / "in" / "A01.zip", "w") as zf:
            for ch, v in (("BF", bf_in), ("GFP", 3), ("DAPI", 5)):
                zf.writestr(f"exp_{ch}_A01_f01_T01.tif", _tif(np.full((32, 32), v, np.uint16)))
    with zipfile.ZipFile(root / "out" / "A01_out.zip", "w") as zf:
        zf.writestr("exp_DAPI_A01_f01_T01_labels.tif", _tif(_labels()))
        zf.writestr("exp_DAPI_A01_f01_T01_tophat.tif",
                    _tif(np.full((32, 32), 50, np.uint16)))
        if bf_out is not None:
            zf.writestr("exp_BF_A01_f01_T01_tophat.tif",
                        _tif(np.full((32, 32), bf_out, np.float32)))
    return root


def _ctx(root: Path, info: dict):
    ds = SimpleNamespace(data_dir=root / "out",
                         in_dir=(root / "in") if (root / "in").is_dir() else None,
                         pipeline_info=info, fov_tp_extractor=None)
    return SimpleNamespace(state=SimpleNamespace(dataset=ds))


def _load(ctx, chan):
    fallbacks: set = set()
    arr = fl.load_frame_array(ctx, {}, "A01", chan, "T01", "f01", use_tophat=True,
                              raw_fallbacks=fallbacks)
    return arr, fallbacks


# ── Review's frame loader ────────────────────────────────────────────────────

def test_the_phase_channel_is_served_its_input_frame_and_not_counted_uncorrected(tmp_path):
    ctx = _ctx(_dataset(tmp_path), _info())
    arr, fallbacks = _load(ctx, "BF")
    assert arr is not None and float(arr.mean()) == 7.0      # the input frame's value
    assert fallbacks == set(), "the phase frame was badged as an uncorrected fallback"


def test_a_real_fallback_on_another_channel_is_still_counted(tmp_path):
    """The control: GFP has no tophat either, and IS uncorrected."""
    ctx = _ctx(_dataset(tmp_path), _info())
    arr, fallbacks = _load(ctx, "GFP")
    assert float(arr.mean()) == 3.0
    assert fallbacks == {("A01", "gfp", "T01", "f01")}


def test_a_copy_mode_phase_tophat_still_wins_over_the_input(tmp_path):
    ctx = _ctx(_dataset(tmp_path, bf_out=99.0), _info("copy"))
    arr, fallbacks = _load(ctx, "BF")
    assert float(arr.mean()) == 99.0 and fallbacks == set()


def test_the_phase_frame_is_gone_when_in_is_missing(tmp_path):
    ctx = _ctx(_dataset(tmp_path, with_in=False), _info())
    arr, _ = _load(ctx, "BF")
    assert arr is None


# ── the placeholder that names what is missing ──────────────────────────────

@pytest.mark.parametrize("chan, info, expected", [
    ("BF", _info(), "(phase input not found — needs in/)"),
    ("bf (raw)", _info(), "(phase input not found — needs in/)"),
    ("GFP", _info(), "(no image)"),
    # Keyed on the phase channel, not on the sidecar's phase_output: a sidecar
    # saying "copy" can sit over wells an earlier reference run wrote.
    ("BF", _info("copy"), "(phase input not found — needs in/)"),
    ("BF", _info(None), "(phase input not found — needs in/)"),
    ("BF", {**_info(), "phase_token": ""}, "(no image)"),
])
def test_missing_frame_note(chan, info, expected):
    ctx = SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(pipeline_info=info)))
    assert fl.missing_frame_note(ctx, chan) == expected


def test_the_image_table_cell_shows_the_phase_note(monkeypatch):
    """The outbound value: what ``_render_cell`` hands the grid as placeholder."""
    pytest.importorskip("PySide6")
    from review.features.image_browser.image_table import controller as C

    monkeypatch.setattr(C, "_load_array", lambda *a, **k: None)
    st = SimpleNamespace(raw_fallbacks=set(), scan_misses=set(), channel_opts={},
                         auto_lut_by_channel={}, cells=[], image_cache={})
    ctx = SimpleNamespace(
        state=SimpleNamespace(
            image_table=st, channel=SimpleNamespace(ratio_metrics=[]),
            dataset=SimpleNamespace(pipeline_info=_info(), well_labels={})),
        runtime=SimpleNamespace(image_table=st))
    cell = {"well": "A01", "chan": "BF", "tp": "T01", "fov": "f01"}
    _pix, _arr, ok, note = C._render_cell(ctx, {}, cell, 0, None, 64, [], None)
    assert (ok, note) == (False, "(phase input not found — needs in/)")
    cell["chan"] = "GFP"
    assert C._render_cell(ctx, {}, cell, 0, None, 64, [], None)[3] == "(no image)"


# ── the pipeline's end-of-run Otsu estimate ─────────────────────────────────

def _graded(labels, scale):
    """Per-cell means that differ (1×, 2× scale) against a zero background."""
    return (labels.astype(np.float32) * scale).astype(np.uint16)


def _otsu_dataset(root: Path, *, bf_in_scale=400, bf_out_scale=None, nest=False):
    (root / "out").mkdir(parents=True)
    (root / "in").mkdir()
    lab = _labels()
    for well in ("A01", "A02"):
        with zipfile.ZipFile(root / "out" / f"{well}_out.zip", "w") as zf:
            zf.writestr(f"exp_DAPI_{well}_f01_T01_labels.tif", _tif(lab))
            zf.writestr(f"exp_GFP_{well}_f01_T01_tophat.tif", _tif(_graded(lab, 300)))
            if bf_out_scale is not None:
                zf.writestr(f"exp_BF_{well}_f01_T01_tophat.tif",
                            _tif(_graded(lab, bf_out_scale)))
        prefix = "plate/" if nest else ""
        with zipfile.ZipFile(root / "in" / f"{well}.zip", "w") as zf:
            zf.writestr(f"{prefix}exp_BF_{well}_f01_T01.tif", _tif(_graded(lab, bf_in_scale)))
            zf.writestr(f"{prefix}exp_GFP_{well}_f01_T01.tif", _tif(_graded(lab, 1)))
            # What a Mac leaves inside an archive: must not be read as a frame.
            zf.writestr(f"__MACOSX/{prefix}._exp_BF_{well}_f01_T01.tif", b"junk")
    return root


def _estimate(root, **kw):
    from pipeline.auto_threshold import estimate_thresholds_from_outputs
    return estimate_thresholds_from_outputs(
        root / "out", fluor_channels=["gfp", "bf"], filename_schema=_SCHEMA,
        filename_sep="_", log=LOG, rng_seed=0, **kw)


def test_a_referenced_phase_channel_is_thresholded_from_the_input_archive(tmp_path):
    root = _otsu_dataset(tmp_path)
    without = _estimate(root)
    with_ref = _estimate(root, input_dir=root / "in", referenced_channels=["BF"])
    assert "bf" not in without, "the fixture already had a BF tophat"
    assert set(with_ref) == {"gfp", "bf"}
    # Its cells sit at 400 and 800 counts against a zero background, so its
    # threshold lands in that range — not in GFP's (300/600), which is what
    # reading the wrong member would give.
    assert 0 < with_ref["bf"] <= 800 and with_ref["bf"] != with_ref["gfp"]
    assert with_ref["gfp"] == without["gfp"], "GFP's estimate must not move"


def test_nested_input_members_are_found_and_macos_junk_is_not(tmp_path):
    root = _otsu_dataset(tmp_path, nest=True)
    flat = _otsu_dataset(tmp_path / "flat")
    nested = _estimate(root, input_dir=root / "in", referenced_channels=["bf"])
    assert nested["bf"] == _estimate(flat, input_dir=flat / "in",
                                     referenced_channels=["bf"])["bf"]


def test_an_archived_phase_tophat_wins_over_the_input(tmp_path):
    """A well an earlier copy-mode run wrote is read from its own archive.
    The two sources are scaled differently, so reading the input instead gives
    a different number."""
    both = _otsu_dataset(tmp_path / "a", bf_in_scale=400, bf_out_scale=2000)
    only_out = _otsu_dataset(tmp_path / "b", bf_in_scale=400, bf_out_scale=2000)
    got = _estimate(both, input_dir=both / "in", referenced_channels=["bf"])
    assert got["bf"] == _estimate(only_out)["bf"]
    assert got["bf"] != _estimate(_otsu_dataset(tmp_path / "c"), input_dir=tmp_path / "c" / "in",
                                  referenced_channels=["bf"])["bf"]


def test_a_missing_input_archive_is_said_and_costs_only_that_channel(tmp_path, caplog):
    root = _otsu_dataset(tmp_path)
    for z in (root / "in").glob("*.zip"):
        z.unlink()
    with caplog.at_level(logging.INFO, logger=LOG.name):
        got = _estimate(root, input_dir=root / "in", referenced_channels=["bf"])
    assert set(got) == {"gfp"}
    assert any("no input archive" in r.getMessage() for r in caplog.records)


def test_the_end_of_run_pass_reads_the_phase_from_input_at_the_default(tmp_path):
    """Through the dispatcher a run actually calls, with an args object that
    does not mention phase_output — the pipeline's default is what ran."""
    from pipeline.auto_threshold_pass import run_auto_threshold_pass

    root = _otsu_dataset(tmp_path)
    (root / "out" / "pipeline_info.json").write_text(json.dumps({"schema_version": 2}))
    args = SimpleNamespace(
        no_auto_threshold=False, nuclear_token="DAPI", cytoplasm_token="",
        segmentation_method="stardist_nuclei", csv_prefix="m",
        filename_schema=_SCHEMA, filename_sep="_", pos_control_well="",
        neg_control_well="", phase_token="BF", input_dir=str(root / "in"))
    written = run_auto_threshold_pass(args, root / "out", ["DAPI", "GFP", "BF"], LOG)
    assert "bf" in written and "gfp" in written
    # A copy-mode run whose wells were all up to date archived nothing new: the
    # archives still hold no BF tophat, and the phase must still be found.
    args.phase_output = "copy"
    (root / "out" / "pipeline_info.json").write_text(json.dumps({"schema_version": 2}))
    assert "bf" in run_auto_threshold_pass(args, root / "out", ["DAPI", "GFP", "BF"], LOG)


# ── Review's Cell Gating auto-threshold ─────────────────────────────────────

def _review_otsu(root, info, *, well_labels):
    from review.data.auto_threshold import compute_auto_thresholds
    (root / "out" / "pipeline_info.json").write_text(json.dumps(info))
    return compute_auto_thresholds(root / "out", fluor_channels=["gfp", "bf"],
                                   rng_seed=0, well_labels=well_labels,
                                   in_dir=root / "in")


@pytest.mark.parametrize("well_labels", [["A01", "A02"], None])
def test_review_auto_threshold_reads_a_referenced_phase_from_the_input(tmp_path, well_labels):
    """Both of its walks: by named wells, and over every output archive."""
    root = _otsu_dataset(tmp_path)
    got = _review_otsu(root, _info(), well_labels=well_labels)
    assert "bf" in got and "gfp" in got
    # The sidecar's phase_output is not what decides it: a later copy-mode run
    # over up-to-date wells, or a merge with a copy-mode plate, records "copy"
    # (or nothing) over archives that hold no BF tophat.
    for recorded in ("copy", None):
        other = _otsu_dataset(tmp_path / f"rec_{recorded}")
        assert _review_otsu(other, _info(recorded), well_labels=well_labels)["bf"] == got["bf"]


def test_review_and_pipeline_estimates_agree_for_the_phase_channel(tmp_path):
    root = _otsu_dataset(tmp_path)
    review = _review_otsu(root, _info(), well_labels=None)
    pipeline = _estimate(root, input_dir=root / "in", referenced_channels=["bf"])
    assert review["bf"] == pipeline["bf"]


# ── the reviewer's reproduced cases ─────────────────────────────────────────

def _respell_inputs(root: Path, spelling: dict[str, str]) -> None:
    for canonical, typed in spelling.items():
        (root / "in" / f"{canonical}.zip").rename(root / "in" / f"{typed}.zip")


def test_an_input_archive_spelled_without_the_leading_zero_is_found(tmp_path):
    """The pipeline accepts ``A1.zip`` and writes ``A01_out.zip``; the phase
    frames must still be found beside it, by both estimators."""
    ref = _otsu_dataset(tmp_path / "ref")
    expected = _estimate(ref, input_dir=ref / "in", referenced_channels=["bf"])["bf"]
    root = _otsu_dataset(tmp_path / "typed")
    _respell_inputs(root, {"A01": "A1", "A02": "a2"})
    assert _estimate(root, input_dir=root / "in", referenced_channels=["bf"])["bf"] == expected
    for well_labels in (None, ["A01", "A02"]):
        assert _review_otsu(root, _info(), well_labels=well_labels)["bf"] == expected


def test_a_multi_page_input_frame_is_sampled_at_its_first_plane(tmp_path):
    """The pipeline measures page 0 of a stack; the estimator must sample the
    same plane, not a (Z, H) slice that matches no label image."""
    flat = _otsu_dataset(tmp_path / "flat")
    expected = _estimate(flat, input_dir=flat / "in", referenced_channels=["bf"])["bf"]
    stack = _otsu_dataset(tmp_path / "stack")
    lab = _labels()
    for well in ("A01", "A02"):
        z = stack / "in" / f"{well}.zip"
        z.unlink()
        with zipfile.ZipFile(z, "w") as zf:
            planes = np.stack([_graded(lab, 400), _graded(lab, 9000), _graded(lab, 9000)])
            zf.writestr(f"exp_BF_{well}_f01_T01.tif", _tif(planes))
    assert _estimate(stack, input_dir=stack / "in", referenced_channels=["bf"])["bf"] == expected


def test_a_role_suffixed_member_in_an_input_archive_is_not_a_raw_frame(tmp_path):
    """An input archive that carries a stray ``_tophat`` (a re-imported output)
    must not be read as the phase frame: only the raw member is."""
    clean = _otsu_dataset(tmp_path / "clean")
    expected = _estimate(clean, input_dir=clean / "in", referenced_channels=["bf"])["bf"]
    dirty = _otsu_dataset(tmp_path / "dirty")
    lab = _labels()
    for well in ("A01", "A02"):
        z = dirty / "in" / f"{well}.zip"
        members = {}
        with zipfile.ZipFile(z) as zf:
            members = {n: zf.read(n) for n in zf.namelist()}
        z.unlink()
        with zipfile.ZipFile(z, "w") as zf:
            # Sorted first, so a reader taking the first match would take it.
            zf.writestr(f"aaa/exp_BF_{well}_f01_T01_tophat.tif", _tif(_graded(lab, 9000)))
            for n, b in members.items():
                zf.writestr(n, b)
    assert _estimate(dirty, input_dir=dirty / "in", referenced_channels=["bf"])["bf"] == expected


def test_the_remote_run_id_ignores_the_storage_options_as_the_local_key_does():
    from services.remote_run_id import pipeline_argv_digest
    from util.settings_digest import settings_digest

    base = ["pm.py", "--nuclear_token", "DAPI", "--phase_token", "BF"]
    changed = base + ["--phase_output", "copy", "--tophat_dtype", "float32"]
    assert pipeline_argv_digest(base) == pipeline_argv_digest(changed)
    common = {"nuclear_token": "DAPI", "phase_token": "BF"}
    assert settings_digest({**common, "phase_output": "reference", "tophat_dtype": "source"}) \
        == settings_digest({**common, "phase_output": "copy", "tophat_dtype": "float32"})
