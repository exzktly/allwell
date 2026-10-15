"""The two image-storage options: referencing the phase input, and storing a
corrected frame at its input's bit depth (``pipeline/output_images.py``).

Both exist to shrink ``<well>_out.zip`` and both promise not to change a
measured number. So the tests here pin three things for each: what lands in the
output directory (by name and by dtype, read back off disk), that the
measurements of a real FOV-group are identical under every combination, and
that every surface which carries the option — the CLI, the argv builder, the
sidecar, the remote threshold re-run — carries the value the user chose.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("skimage")
tifffile = pytest.importorskip("tifffile")

import numpy as np  # noqa: E402

from pipeline.output_images import storage_array, write_processed_images  # noqa: E402
from pipeline.output_policy import (  # noqa: E402
    DEFAULT_PHASE_OUTPUT,
    DEFAULT_TOPHAT_DTYPE,
    PHASE_OUTPUT_COPY,
    PHASE_OUTPUT_REFERENCE,
    TOPHAT_DTYPE_FLOAT32,
    TOPHAT_DTYPE_SOURCE,
)

U16 = float(np.iinfo(np.uint16).max)
U8 = float(np.iinfo(np.uint8).max)


# ── storage_array: the rule that decides a file's dtype ─────────────────────

def test_a_uint16_frame_is_stored_as_uint16_rounded_to_the_nearest_count():
    arr = np.array([[0.0, 1.4, 1.6], [2.5, 3.49, 65535.0]], dtype=np.float32)
    out, reason = storage_array(arr, U16, mode=TOPHAT_DTYPE_SOURCE)
    assert reason == ""
    assert out.dtype == np.uint16
    # rint is round-half-to-even: 2.5 -> 2. Asserted as values, not as a range.
    assert out.tolist() == [[0, 1, 2], [2, 3, 65535]]


def test_a_uint8_frame_is_stored_as_uint8():
    out, reason = storage_array(np.array([[0.4, 254.6]], np.float32), U8,
                                mode=TOPHAT_DTYPE_SOURCE)
    assert (out.dtype, out.tolist(), reason) == (np.uint8, [[0, 255]], "")


@pytest.mark.parametrize("ceiling", [None, 1.0, float(np.iinfo(np.uint32).max)])
def test_an_input_that_is_not_uint8_or_uint16_stays_float32(ceiling):
    """A float input (ceiling None) may be a 0–1 normalised channel, where
    rounding to counts would leave nothing; bool and uint32 are not types
    float32 round-trips through as a pure rounding."""
    arr = np.array([[0.25, 0.75]], np.float32)
    out, reason = storage_array(arr, ceiling, mode=TOPHAT_DTYPE_SOURCE)
    assert out.dtype == np.float32
    assert out.tolist() == [[0.25, 0.75]]
    assert reason == "input is not uint8/uint16"


def test_a_frame_lifted_above_the_ceiling_keeps_its_top_rather_than_clipping():
    """A flat-field division can push a corrected pixel past the camera's
    maximum. Storing it as uint16 would clip it there, inventing a saturation
    the data does not have — so the whole frame stays float32 and says why."""
    arr = np.array([[10.0, 70000.25]], np.float32)
    out, reason = storage_array(arr, U16, mode=TOPHAT_DTYPE_SOURCE)
    assert out.dtype == np.float32
    assert out.tolist() == [[10.0, 70000.25]]
    assert "exceeds" in reason and "65535" in reason


def test_the_ceiling_itself_after_rounding_still_fits():
    out, _ = storage_array(np.array([[65534.6]], np.float32), U16, mode=TOPHAT_DTYPE_SOURCE)
    assert (out.dtype, out.tolist()) == (np.uint16, [[65535]])


def test_rounding_past_the_ceiling_is_refused():
    out, reason = storage_array(np.array([[65535.5]], np.float32), U16,
                                mode=TOPHAT_DTYPE_SOURCE)
    # rint(65535.5) is 65536 (half to even): one count over, so float32.
    assert out.dtype == np.float32 and "exceeds" in reason


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_a_non_finite_frame_stays_float32(bad):
    out, reason = storage_array(np.array([[1.0, bad]], np.float32), U16,
                                mode=TOPHAT_DTYPE_SOURCE)
    assert out.dtype == np.float32 and reason == "frame holds non-finite values"


def test_a_negative_frame_stays_float32():
    """The writer clips at zero before calling, but the rule must not wrap a
    negative into a huge unsigned value if a caller does not."""
    out, reason = storage_array(np.array([[-3.0, 4.0]], np.float32), U16,
                                mode=TOPHAT_DTYPE_SOURCE)
    assert out.dtype == np.float32 and out.tolist() == [[-3.0, 4.0]]
    assert reason == "frame holds negative values"


def test_float32_mode_never_converts():
    out, reason = storage_array(np.array([[1.4]], np.float32), U16, mode=TOPHAT_DTYPE_FLOAT32)
    assert out.dtype == np.float32 and out.tolist()[0][0] == pytest.approx(1.4)
    assert reason == "float32 requested"


def test_an_empty_frame_takes_the_integer_type():
    out, reason = storage_array(np.zeros((0, 0), np.float32), U16, mode=TOPHAT_DTYPE_SOURCE)
    assert (out.dtype, out.shape, reason) == (np.uint16, (0, 0), "")


# ── write_processed_images: what lands on disk ──────────────────────────────

_H = 24


def _frame(seed: int, *, top: float = 3000.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.random((_H, _H)) * top).astype(np.float32)


def _labels() -> np.ndarray:
    lab = np.zeros((_H, _H), np.int32)
    lab[3:9, 3:9] = 1
    lab[12:20, 12:20] = 2
    return lab


def _write(tmp_path, *, nuclear_token, fluor_tokens, phase_token, phase_output,
           tophat_dtype, ceilings=None, smfish=(), nir_corr=None, fluor_tophat=None):
    """Drive the writer as process_image_group does, returning the output dir."""
    out = tmp_path / "out"
    out.mkdir()
    nuclear_path = Path(f"exp_{nuclear_token}_A01_f01_T01.tif")
    fluor_paths = [Path(f"exp_{t}_A01_f01_T01.tif") for t in fluor_tokens]
    nir = nir_corr if nir_corr is not None else _frame(1)
    fluors = fluor_tophat if fluor_tophat is not None else [
        _frame(10 + i) for i in range(len(fluor_tokens))]
    if ceilings is None:
        ceilings = [U16] * (1 + len(fluor_tokens))
    write_processed_images(
        save_tophat_images=True, nuclear_path=nuclear_path, fluor_paths=fluor_paths,
        fluor_tokens=list(fluor_tokens), output_dir=out, nir_raw=nir, nir_corr=nir,
        fluor_tophat=fluors, fluor_corr=list(fluors), smfish_set=set(smfish),
        segmentation_method="stardist_nuclei", nuclear_points=None,
        cytoplasm_tophat=None, cytoplasm_binary_mask=None, labels=_labels(),
        background_method="sliding_disk", nuclear_token=nuclear_token,
        phase_token=phase_token, phase_output=phase_output, tophat_dtype=tophat_dtype,
        nuclear_ceiling=ceilings[0], fluor_ceilings=ceilings[1:],
    )
    return out


def _names(out: Path) -> set[str]:
    return {p.name for p in out.iterdir()}


def _dtype(out: Path, name: str):
    return tifffile.imread(out / name).dtype


def test_label_free_reference_writes_no_tophat_for_the_phase_nuclear_slot(tmp_path):
    """Label-free segments the phase channel, which therefore sits in the
    nuclear slot: that is the one ``_tophat`` the reference skips."""
    out = _write(tmp_path, nuclear_token="BF", fluor_tokens=["BF", "GFP"],
                 phase_token="bf", phase_output=PHASE_OUTPUT_REFERENCE,
                 tophat_dtype=TOPHAT_DTYPE_SOURCE)
    assert _names(out) == {
        "exp_GFP_A01_f01_T01_tophat.tif",
        "exp_BF_A01_f01_T01_labels.tif",
        "exp_BF_A01_f01_T01_overlay.png",
    }


def test_phase_as_a_fluor_channel_reference_skips_only_that_channel(tmp_path):
    out = _write(tmp_path, nuclear_token="DAPI", fluor_tokens=["GFP", "BF"],
                 phase_token="bf", phase_output=PHASE_OUTPUT_REFERENCE,
                 tophat_dtype=TOPHAT_DTYPE_SOURCE)
    tophats = sorted(n for n in _names(out) if n.endswith("_tophat.tif"))
    assert tophats == ["exp_DAPI_A01_f01_T01_tophat.tif", "exp_GFP_A01_f01_T01_tophat.tif"]


def test_the_phase_token_is_matched_case_insensitively(tmp_path):
    """The run records the token lower-cased while filenames keep the user's
    case — the same comparison ``_correct_image_group`` uses to pass it through."""
    out = _write(tmp_path, nuclear_token="DAPI", fluor_tokens=["Phase"],
                 phase_token="PHASE", phase_output=PHASE_OUTPUT_REFERENCE,
                 tophat_dtype=TOPHAT_DTYPE_SOURCE)
    assert "exp_Phase_A01_f01_T01_tophat.tif" not in _names(out)


def test_copy_writes_the_phase_tophat_as_the_unchanged_input(tmp_path):
    bf = np.rint(_frame(7)).astype(np.float32)        # what a uint16 input loads as
    out = _write(tmp_path, nuclear_token="DAPI", fluor_tokens=["BF"],
                 phase_token="bf", phase_output=PHASE_OUTPUT_COPY,
                 tophat_dtype=TOPHAT_DTYPE_SOURCE, fluor_tophat=[bf])
    written = tifffile.imread(out / "exp_BF_A01_f01_T01_tophat.tif")
    assert written.dtype == np.uint16
    assert np.array_equal(written, bf.astype(np.uint16))


def test_no_phase_token_means_every_channel_is_written(tmp_path):
    out = _write(tmp_path, nuclear_token="DAPI", fluor_tokens=["GFP", "BF"],
                 phase_token="", phase_output=PHASE_OUTPUT_REFERENCE,
                 tophat_dtype=TOPHAT_DTYPE_SOURCE)
    assert {"exp_DAPI_A01_f01_T01_tophat.tif", "exp_GFP_A01_f01_T01_tophat.tif",
            "exp_BF_A01_f01_T01_tophat.tif"} <= _names(out)


def test_source_dtype_writes_uint16_and_float32_mode_writes_float32(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    src = _write(tmp_path / "a", nuclear_token="DAPI", fluor_tokens=["GFP"],
                 phase_token="", phase_output=PHASE_OUTPUT_REFERENCE,
                 tophat_dtype=TOPHAT_DTYPE_SOURCE)
    flt = _write(tmp_path / "b", nuclear_token="DAPI", fluor_tokens=["GFP"],
                 phase_token="", phase_output=PHASE_OUTPUT_REFERENCE,
                 tophat_dtype=TOPHAT_DTYPE_FLOAT32)
    for name in ("exp_DAPI_A01_f01_T01_tophat.tif", "exp_GFP_A01_f01_T01_tophat.tif"):
        assert _dtype(src, name) == np.uint16
        assert _dtype(flt, name) == np.float32
    # Same pixels up to half a count: the storage type is the only difference.
    a = tifffile.imread(src / "exp_GFP_A01_f01_T01_tophat.tif").astype(np.float64)
    b = tifffile.imread(flt / "exp_GFP_A01_f01_T01_tophat.tif").astype(np.float64)
    assert np.max(np.abs(a - b)) <= 0.5


def test_each_channel_is_judged_by_its_own_input_type(tmp_path):
    """A float input beside a uint16 one: only the uint16 channel converts."""
    out = _write(tmp_path, nuclear_token="DAPI", fluor_tokens=["GFP", "NORM"],
                 phase_token="", phase_output=PHASE_OUTPUT_REFERENCE,
                 tophat_dtype=TOPHAT_DTYPE_SOURCE, ceilings=[U16, U16, None])
    assert _dtype(out, "exp_GFP_A01_f01_T01_tophat.tif") == np.uint16
    assert _dtype(out, "exp_NORM_A01_f01_T01_tophat.tif") == np.float32


def test_an_overflowing_channel_stays_float32_while_its_neighbours_convert(tmp_path):
    lifted = _frame(3)
    lifted[0, 0] = 80000.0
    out = _write(tmp_path, nuclear_token="DAPI", fluor_tokens=["GFP", "RFP"],
                 phase_token="", phase_output=PHASE_OUTPUT_REFERENCE,
                 tophat_dtype=TOPHAT_DTYPE_SOURCE, fluor_tophat=[_frame(4), lifted])
    assert _dtype(out, "exp_GFP_A01_f01_T01_tophat.tif") == np.uint16
    rfp = tifffile.imread(out / "exp_RFP_A01_f01_T01_tophat.tif")
    assert rfp.dtype == np.float32 and float(rfp[0, 0]) == 80000.0


def test_smfish_stays_float32_under_source_dtype(tmp_path):
    """A LoG response is small and fractional: rounding it erases the spots."""
    spots = _frame(5, top=0.8)
    out = tmp_path / "out"
    out.mkdir()
    write_processed_images(
        save_tophat_images=True, nuclear_path=Path("exp_DAPI_A01_f01_T01.tif"),
        fluor_paths=[Path("exp_FISH_A01_f01_T01.tif")], fluor_tokens=["FISH"],
        output_dir=out, nir_raw=_frame(1), nir_corr=_frame(1),
        fluor_tophat=[_frame(2)], fluor_corr=[spots], smfish_set={"FISH"},
        segmentation_method="stardist_nuclei", nuclear_points=None,
        cytoplasm_tophat=None, cytoplasm_binary_mask=None, labels=_labels(),
        background_method="sliding_disk", nuclear_token="DAPI",
        tophat_dtype=TOPHAT_DTYPE_SOURCE, nuclear_ceiling=U16, fluor_ceilings=[U16],
    )
    sm = tifffile.imread(out / "exp_FISH_A01_f01_T01_smfish.tif")
    assert sm.dtype == np.float32 and np.allclose(sm, spots)
    assert _dtype(out, "exp_FISH_A01_f01_T01_tophat.tif") == np.uint16


def test_a_caller_that_passes_no_ceilings_gets_float32(tmp_path):
    """The safe direction: not knowing the input type never rounds anything."""
    out = tmp_path / "out"
    out.mkdir()
    write_processed_images(
        save_tophat_images=True, nuclear_path=Path("exp_DAPI_A01_f01_T01.tif"),
        fluor_paths=[Path("exp_GFP_A01_f01_T01.tif")], fluor_tokens=["GFP"],
        output_dir=out, nir_raw=_frame(1), nir_corr=_frame(1),
        fluor_tophat=[_frame(2)], fluor_corr=[_frame(2)], smfish_set=set(),
        segmentation_method="stardist_nuclei", nuclear_points=None,
        cytoplasm_tophat=None, cytoplasm_binary_mask=None, labels=_labels(),
        background_method="sliding_disk",
    )
    assert _dtype(out, "exp_DAPI_A01_f01_T01_tophat.tif") == np.float32
    assert _dtype(out, "exp_GFP_A01_f01_T01_tophat.tif") == np.float32


# ── process_image_group: real loader, real correction, real quantification ──

_SCHEMA = "experiment:channel:well:fov:timepoint"


def _write_inputs(folder: Path) -> tuple[Path, list[Path]]:
    """A FOV-group of real uint16 TIFFs: DAPI nuclear, GFP readout, BF phase."""
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    yy, xx = np.mgrid[0:48, 0:48]
    blob = ((yy - 16) ** 2 + (xx - 16) ** 2 < 36) | ((yy - 32) ** 2 + (xx - 34) ** 2 < 49)
    frames = {
        "DAPI": 200 + 1500 * blob + rng.integers(0, 40, (48, 48)),
        "GFP": 300 + 800 * blob + 10 * xx + rng.integers(0, 60, (48, 48)),
        "BF": 2000 + 300 * np.sin(xx / 4.0) + rng.integers(0, 80, (48, 48)),
    }
    paths = {}
    for tok, arr in frames.items():
        p = folder / f"exp_{tok}_A01_f01_T01.tif"
        tifffile.imwrite(p, np.asarray(arr).astype(np.uint16))
        paths[tok] = p
    return paths["DAPI"], [paths["GFP"], paths["BF"]]


def _run_group(monkeypatch, tmp_path, *, phase_output, tophat_dtype):
    import process_microscopy as pm
    from util.schema import parse_schema

    nuclear, fluors = _write_inputs(tmp_path / "in")
    out = tmp_path / f"out_{phase_output}_{tophat_dtype}"
    out.mkdir()
    lab = np.zeros((48, 48), np.int32)
    yy, xx = np.mgrid[0:48, 0:48]
    lab[(yy - 16) ** 2 + (xx - 16) ** 2 < 36] = 1
    lab[(yy - 32) ** 2 + (xx - 34) ** 2 < 49] = 2
    # Segmentation is the one stage stubbed: StarDist is not in the test stack,
    # and the claim under test is about what happens around it.
    monkeypatch.setattr(pm, "_segment_image_group", lambda **k: (lab, None, None, None))
    records, _quality = pm.process_image_group(
        nuclear, fluors, ["GFP", "BF"], out, nuclear_token="DAPI",
        tophat_nir=True, tophat_radius_nir=5, tophat_fluor=[True, True],
        tophat_radius_fluor=[5, 5], schema=parse_schema(_SCHEMA), sep="_",
        measure_focus=False, phase_token="bf",
        phase_output=phase_output, tophat_dtype=tophat_dtype,
    )
    return records, out


def test_measurements_are_identical_under_every_storage_combination(monkeypatch, tmp_path):
    runs = {
        (po, td): _run_group(monkeypatch, tmp_path, phase_output=po, tophat_dtype=td)
        for po in (PHASE_OUTPUT_REFERENCE, PHASE_OUTPUT_COPY)
        for td in (TOPHAT_DTYPE_SOURCE, TOPHAT_DTYPE_FLOAT32)
    }
    baseline = runs[(PHASE_OUTPUT_COPY, TOPHAT_DTYPE_FLOAT32)][0]
    assert baseline, "the fixture produced no cells to compare"
    assert any(k.startswith("bf_") for k in baseline[0]), "phase channel not quantified"
    for key, (records, _out) in runs.items():
        assert records == baseline, f"measurements changed under {key}"


def test_the_real_group_writes_what_each_combination_promises(monkeypatch, tmp_path):
    _rec, ref_src = _run_group(monkeypatch, tmp_path, phase_output=PHASE_OUTPUT_REFERENCE,
                               tophat_dtype=TOPHAT_DTYPE_SOURCE)
    _rec, copy_flt = _run_group(monkeypatch, tmp_path, phase_output=PHASE_OUTPUT_COPY,
                                tophat_dtype=TOPHAT_DTYPE_FLOAT32)
    assert not (ref_src / "exp_BF_A01_f01_T01_tophat.tif").exists()
    assert _dtype(ref_src, "exp_GFP_A01_f01_T01_tophat.tif") == np.uint16
    assert _dtype(ref_src, "exp_DAPI_A01_f01_T01_tophat.tif") == np.uint16
    # The copy-mode phase file is the input frame, pixel for pixel.
    bf_in = tifffile.imread(tmp_path / "in" / "exp_BF_A01_f01_T01.tif")
    bf_out = tifffile.imread(copy_flt / "exp_BF_A01_f01_T01_tophat.tif")
    assert bf_out.dtype == np.float32 and np.array_equal(bf_out, bf_in.astype(np.float32))
    assert _dtype(copy_flt, "exp_GFP_A01_f01_T01_tophat.tif") == np.float32


# ── the CLI, the argv builder, the sidecar ─────────────────────────────────

def _parse(*extra):
    from pipeline.cli_args import build_parser
    return build_parser().parse_args(
        ["--input_dir", "i", "--output_dir", "o", *extra])


def test_the_cli_defaults_are_reference_and_source():
    args = _parse()
    assert (args.phase_output, args.tophat_dtype) == ("reference", "source")
    assert (DEFAULT_PHASE_OUTPUT, DEFAULT_TOPHAT_DTYPE) == ("reference", "source")


def test_the_cli_accepts_copy_and_float32_and_refuses_anything_else():
    args = _parse("--phase_output", "copy", "--tophat_dtype", "float32")
    assert (args.phase_output, args.tophat_dtype) == ("copy", "float32")
    with pytest.raises(SystemExit):
        _parse("--phase_output", "link")


def _argv(opts):
    from services.pipeline_service import build_pipeline_args
    base = dict(nuclear_token="DAPI", fluor_tokens=["GFP"], csv_prefix="m",
                filename_schema=_SCHEMA, filename_sep="_")
    return build_pipeline_args(Path("pm.py"), Path("in"), Path("out"), {**base, **opts})


def test_the_defaults_add_nothing_to_the_argv():
    """The remote run id and argv digest are computed from these bytes, so a
    run at the defaults must send what it sent before the options existed."""
    for opts in ({}, {"phase_output": "reference", "tophat_dtype": "source"}):
        argv = _argv(opts)
        assert "--phase_output" not in argv and "--tophat_dtype" not in argv


def test_a_non_default_choice_reaches_the_argv():
    argv = _argv({"phase_output": "copy", "tophat_dtype": "float32"})
    i, j = argv.index("--phase_output"), argv.index("--tophat_dtype")
    assert (argv[i + 1], argv[j + 1]) == ("copy", "float32")


def test_the_built_argv_parses_back_to_the_chosen_values():
    """The two ends agree: what the form sends is what the pipeline reads."""
    from pipeline.cli_args import build_parser
    for po, td in (("copy", "float32"), ("reference", "source"), ("copy", "source")):
        argv = _argv({"phase_output": po, "tophat_dtype": td})
        args = build_parser().parse_args(argv[2:])
        assert (args.phase_output, args.tophat_dtype) == (po, td)


def test_the_sidecar_records_both_and_an_old_caller_records_the_legacy_values(tmp_path):
    import json
    import process_microscopy as pm

    def _info(**kw):
        out = tmp_path / f"o{len(list(tmp_path.iterdir()))}"
        pm.write_pipeline_info(
            out, input_dir=tmp_path, filename_schema=_SCHEMA, filename_sep="_",
            nuclear_token="DAPI", fluor_tokens=["GFP"], smfish_tokens=[],
            segmentation_method="stardist_nuclei", cytoplasm_token="",
            min_nucleus_area_px=50, **kw)
        return json.loads((out / "pipeline_info.json").read_text())

    new = _info(phase_output="reference", tophat_dtype="source")
    assert (new["phase_output"], new["tophat_dtype"]) == ("reference", "source")
    legacy = _info()
    assert (legacy["phase_output"], legacy["tophat_dtype"]) == ("copy", "float32")


def test_the_remote_threshold_rerun_reads_the_input_dir_and_the_phase_choice():
    from services.remote_process import _auto_threshold_args_from_argv
    from pipeline.auto_threshold_pass import _referenced_channels

    at_default = _auto_threshold_args_from_argv(
        _argv({}) + ["--phase_token", "BF"])
    assert at_default.input_dir == "in"
    assert _referenced_channels(at_default) == ["bf"]
    copied = _auto_threshold_args_from_argv(
        _argv({"phase_output": "copy"}) + ["--phase_token", "BF"])
    assert _referenced_channels(copied) == []


def test_no_phase_token_references_nothing():
    from pipeline.auto_threshold_pass import _referenced_channels
    assert _referenced_channels(SimpleNamespace(phase_token="", phase_output="reference")) == []


# ── main(): the run a user starts, end to end ───────────────────────────────

def _plate(tmp_path) -> Path:
    import zipfile
    plate = tmp_path / "plate"
    (plate / "in").mkdir(parents=True)
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:48, 0:48]
    blob = (yy - 20) ** 2 + (xx - 20) ** 2 < 64
    with zipfile.ZipFile(plate / "in" / "B02.zip", "w") as zf:
        for fov in ("F001", "F002"):
            for ch, arr in (("DAPI", 300 + 1200 * blob), ("GFP", 200 + 900 * blob),
                            ("BF", 2500 + 40 * np.sin(xx / 3.0))):
                frame = (arr + rng.integers(0, 50, (48, 48))).astype(np.uint16)
                zf.writestr(f"exp_{ch}_B02_{fov}_0h.tif", _tif_bytes(frame))
    return plate


def _tif_bytes(arr) -> bytes:
    import io
    buf = io.BytesIO()
    tifffile.imwrite(buf, arr)
    return buf.getvalue()


def _run_main(monkeypatch, plate, *extra):
    """``main`` for real — pool run serially in-process, segmenter stubbed (the
    harness ``test_only_wells_run`` uses) — with the real writer and the real
    quantifier, so the archive is exactly what a user's run would produce."""
    import json
    import sys
    import process_microscopy as pm
    import pipeline.suspend as _suspend
    from pipeline import well_skip

    lab = np.zeros((48, 48), np.int32)
    yy, xx = np.mgrid[0:48, 0:48]
    lab[(yy - 20) ** 2 + (xx - 20) ** 2 < 64] = 1
    monkeypatch.setattr(pm, "_segment_image_group", lambda **k: (lab, None, None, None))

    def _serial(*, well_zips, output_dir, shared_kwargs_template, nuclear_token,
                fluor_tokens, csv_prefix, schema, sep, incremental,
                incremental_prior_dir, settings_key, force, force_wells=None, **_):
        for label, zip_path in well_zips:
            pm.process_well_zip_task(
                label, zip_path, output_dir, shared_kwargs_template, nuclear_token,
                fluor_tokens, csv_prefix, schema, sep,
                well_skip.incremental_for_well(incremental, label, set()),
                incremental_prior_dir, settings_key, force)
        return []

    monkeypatch.setattr(pm, "process_well_zips", _serial)
    monkeypatch.setattr(_suspend, "install_handlers", lambda **k: None)
    monkeypatch.setattr(_suspend, "watch_parent", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", [
        "process_microscopy.py", "--input_dir", str(plate / "in"),
        "--output_dir", str(plate / "out"), "--nuclear_token", "DAPI",
        "--fluor_tokens", "GFP", "BF", "--phase_token", "BF",
        "--filename_schema", "experiment:channel:well:fov:timepoint",
        "--workers", "1", "--background", "none", "--flatfield", "none",
        "--no_focus_metric", *extra])
    try:
        pm.main()
    except SystemExit as exc:
        assert not exc.code, f"the pipeline exited {exc.code}"
    return json.loads((plate / "out" / pm.PIPELINE_INFO_FILENAME).read_text())


def _archive(plate) -> dict[str, np.dtype]:
    import io
    import zipfile
    with zipfile.ZipFile(plate / "out" / "B02_out.zip") as zf:
        return {n: tifffile.imread(io.BytesIO(zf.read(n))).dtype
                for n in zf.namelist() if n.endswith("_tophat.tif")}


def test_a_default_run_references_phase_and_stores_uint16(monkeypatch, tmp_path):
    plate = _plate(tmp_path)
    info = _run_main(monkeypatch, plate)
    assert _archive(plate) == {
        f"exp_{ch}_B02_{fov}_0h_tophat.tif": np.dtype(np.uint16)
        for ch in ("DAPI", "GFP") for fov in ("F001", "F002")}
    assert (info["phase_output"], info["tophat_dtype"]) == ("reference", "source")
    # The end-of-run threshold pass read the phase channel from in/B02.zip.
    assert "bf" in info["cell_gating"]["thresh_frac_on"]


def test_a_copy_float32_run_archives_what_it_did_before(monkeypatch, tmp_path):
    plate = _plate(tmp_path)
    info = _run_main(monkeypatch, plate, "--phase_output", "copy", "--tophat_dtype", "float32")
    assert _archive(plate) == {
        f"exp_{ch}_B02_{fov}_0h_tophat.tif": np.dtype(np.float32)
        for ch in ("DAPI", "GFP", "BF") for fov in ("F001", "F002")}
    assert (info["phase_output"], info["tophat_dtype"]) == ("copy", "float32")
    assert "bf" in info["cell_gating"]["thresh_frac_on"]


def test_the_csv_is_identical_either_way(monkeypatch, tmp_path):
    import gzip
    a, b = _plate(tmp_path / "a"), _plate(tmp_path / "b")
    _run_main(monkeypatch, a)
    _run_main(monkeypatch, b, "--phase_output", "copy", "--tophat_dtype", "float32")

    def _csv(plate):
        (path,) = sorted((plate / "out").glob("measurements_B02.csv*"))
        raw = path.read_bytes()
        return gzip.decompress(raw) if path.suffix == ".gz" else raw
    assert _csv(a) and _csv(a) == _csv(b)
