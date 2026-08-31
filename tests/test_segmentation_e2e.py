"""End-to-end segmentation runs driven through the real Analyze tab.

One test per segmentation method the app ships (the five entries of the Analyze
"Segmentation" dropdown). Each builds a synthetic plate of schema-named TIFFs on
disk, fills in the Analyze form the way an operator would, presses **Run**, and
then pumps the Qt event loop until the real ``process_microscopy.py`` subprocess
the tab spawned has finished. The assertions are on what actually landed on
disk — the per-well CSV, the ``pipeline_info.json`` sidecar, and the label image
inside the per-well output zip — plus the tab's own completion contract (the
``on_pipeline_complete`` callback and the "Processing Complete" log banner).

Nothing here is mocked: the grouping (zipper) phase, the subprocess launch, the
log/progress plumbing, segmentation, quantification, CSV assembly and output
zipping all run for real. That is the point — the arg-mapping and form-state
tests (``test_segmentation_methods.py``, ``test_labelfree_phase.py``) already
cover the wiring in isolation; these cover the whole path.

Cost + skips. Every test spawns a pipeline subprocess that loads a segmentation
model, so the module is marked ``slow`` and stays out of the fast lane. The
StarDist methods need TensorFlow + StarDist and the Cellpose methods need Torch +
Cellpose; where those are absent (the lean CI) the corresponding tests skip and
only ``classical_threshold`` — which needs nothing beyond scipy/skimage — runs.
Availability is probed with ``importlib.util.find_spec`` rather than an import,
deliberately: TensorFlow and Torch cannot always share one process, and importing
both into the *test* process to decide whether to skip would risk exactly the
abort the pipeline avoids by keeping each run in its own subprocess.

Run them one file at a time, as with the other Qt-heavy tests:

    scripts/test.sh tests/test_segmentation_e2e.py -m ""
"""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import time
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

from PySide6.QtWidgets import QApplication

from tests.fixtures.synthetic_pipeline import (
    DEFAULT_CENTERS,
    DEFAULT_RADIUS,
    write_synthetic_channels,
)

pytestmark = [pytest.mark.slow, pytest.mark.qt]

# A pipeline run is a model load plus inference; generous, but bounded so a hung
# subprocess fails the test instead of the session.
RUN_TIMEOUT_S = 900.0


def _have(*modules: str) -> bool:
    """True when every named module is importable *without importing it*."""
    return all(importlib.util.find_spec(m) is not None for m in modules)


requires_stardist = pytest.mark.skipif(
    not _have("tensorflow", "stardist"),
    reason="StarDist segmentation needs the TensorFlow + StarDist stack",
)


def _cellpose_weights_cached(*models: str) -> bool:
    """True when every named Cellpose model is already on disk.

    Cellpose fetches its weights from cellpose.org on first use. A machine that
    can't reach it (offline CI, a locked-down egress policy) has the package
    installed but no model to run, which is a skip rather than a failure. The
    lookup mirrors ``cellpose.models``' own cache location without importing
    Cellpose — and therefore without pulling Torch into the test process.
    """
    root = Path(os.environ.get("CELLPOSE_LOCAL_MODELS_PATH")
                or Path.home() / ".cellpose" / "models")
    return all((root / m).exists() for m in models)


def requires_cellpose(*models: str):
    """Skip marker for a Cellpose method that needs *models* available."""
    if not _have("torch", "cellpose"):
        return pytest.mark.skipif(True, reason="needs the Torch + Cellpose stack")
    return pytest.mark.skipif(
        not _cellpose_weights_cached(*models),
        reason=f"Cellpose weights not cached locally: {', '.join(models)}",
    )


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ── dataset + form helpers ───────────────────────────────────────────────────

WELLS = ("A01", "A02")


def _make_plate(root: Path, channels: dict[str, str], *, wells=WELLS, n_fovs: int = 1) -> Path:
    """Write loose schema-named TIFFs under ``root/raw`` and return that folder.

    Loose TIFs at the top level is the layout a user drops on the app most
    often, so the run also exercises the grouping (zipper) phase that turns them
    into ``raw/in/<well>.zip`` before the pipeline starts. Two wells: the tab
    refuses a folder holding three or fewer TIFs (it can't tell that layout
    apart from a stray drop), and a second well proves the per-well partitioning
    of the CSVs.
    """
    raw = Path(root) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for well in wells:
        for fov in range(1, n_fovs + 1):
            write_synthetic_channels(raw, channels, well=well, fov=str(fov))
    return raw


def _configure_form(
    tab,
    *,
    input_dir: Path,
    nuclear_token: str,
    fluor_tokens: list[str],
    method: str,
    phase_token: str = "",
    cytoplasm_token: str = "",
) -> None:
    """Fill the Analyze form for a run, as the operator's clicks would."""
    idx = tab._seg_method_cb.findData(method)
    assert idx >= 0, f"method {method!r} not offered by the dropdown"
    tab._seg_method_cb.setCurrentIndex(idx)

    tab._input_edit.setText(str(input_dir))
    tab._nuclear_token_edit.setText(nuclear_token)
    tab._set_fluor_rows(fluor_tokens, smfish=[], phase=phase_token)
    tab._cytoplasm_edit.setText(cytoplasm_token)
    tab._csv_prefix_edit.setText("measurements")
    tab._min_nucleus_area_edit.setText("20")

    # Isolate segmentation: no flat-field, no background subtraction, no debris
    # filter. Each of those has its own tests; here they would only add ways for
    # an unrelated stage to change the object count.
    tab._flatfield_cb.setCurrentText("none")
    tab._background_cb.setCurrentText("none")
    tab._qc_remove_debris_cb.setChecked(False)

    # One worker, CPU only — deterministic, and small enough for a CI box.
    tab._workers_edit.setText("1")
    tab._cpu_only_cb.setChecked(True)


def _configure_cellpose(tab, *, model: str, diameter: str) -> None:
    """Set the general-Cellpose parameter page (model + expected object size)."""
    tab._cellpose_model_cb.setCurrentText(model)
    tab._cellpose_diameter_edit.setText(diameter)


def _run_and_wait(tab, qapp, *, timeout: float = RUN_TIMEOUT_S) -> None:
    """Press Run and pump the event loop until the pipeline subprocess ends."""
    tab._run()
    assert tab._running, f"the run never started — the tab rejected the form: {tab.dialogs}"
    deadline = time.monotonic() + timeout
    while tab._running and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    # Drain whatever the poller queued on the last tick (the "done" banner and
    # the completion callback ride the same queue as "finished").
    for _ in range(20):
        qapp.processEvents()
        time.sleep(0.01)
    assert not tab._running, f"pipeline did not finish within {timeout:.0f}s"


def _assert_clean_run(tab, completed: list[Path], out_dir: Path) -> None:
    """The tab's own completion contract: banner, callback, output directory."""
    log = tab._log.toPlainText()
    assert "Processing Complete" in log, f"run did not report success:\n{log[-4000:]}"
    assert "Traceback" not in log, f"pipeline raised:\n{log[-4000:]}"
    assert "FAILED well" not in log, f"a well failed:\n{log[-4000:]}"
    assert completed == [out_dir], f"on_pipeline_complete got {completed!r}, want [{out_dir!r}]"


def _read_rows(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def _assert_outputs(
    out_dir: Path,
    *,
    method: str,
    fluor_token: str,
    min_objects: int = 1,
) -> list[dict]:
    """Assert the artefacts a completed run leaves behind; return the A01 rows."""
    info = json.loads((out_dir / "pipeline_info.json").read_text())
    assert info["segmentation_method"] == method

    per_well: dict[str, list[dict]] = {}
    for well in WELLS:
        csv_path = out_dir / f"measurements_{well}.csv"
        assert csv_path.exists(), (
            f"no CSV for {well} in {sorted(p.name for p in out_dir.iterdir())}"
        )
        rows = _read_rows(csv_path)
        assert len(rows) >= min_objects, (
            f"{well}: {len(rows)} object(s) segmented, want >= {min_objects}"
        )
        assert len({r["nucleus_id"] for r in rows}) == len(rows), f"{well}: duplicate object ids"
        for row in rows:
            assert row["well"] == well
            assert float(row["area_px"]) > 0.0
            # Quantification ran on the segmented regions: the fluor channel is
            # bright inside the objects and dim outside.
            assert float(row[f"{fluor_token.lower()}_mean_intensity"]) > 0.0
        per_well[well] = rows

        out_zip = out_dir / f"{well}_out.zip"
        assert out_zip.exists(), f"{well}: per-well output zip missing"
        with zipfile.ZipFile(out_zip) as zf:
            names = zf.namelist()
        assert any(n.endswith("_labels.tif") for n in names), f"{well}: no label image in {names}"
    return per_well["A01"]


@pytest.fixture
def analyze_tab(qapp, monkeypatch):
    """A real AnalyzeTab whose modal error dialogs are captured, not shown.

    ``_run`` reports every rejection through ``QMessageBox.critical``, which
    would block forever under the offscreen platform. Capturing the calls turns
    a rejected run into a readable assertion instead of a hang.
    """
    import analyze.tab as analyze_tab_mod

    dialogs: list[tuple[str, str]] = []
    monkeypatch.setattr(
        analyze_tab_mod.QMessageBox,
        "critical",
        staticmethod(lambda _parent, title, text, *a, **k: dialogs.append((title, text))),
    )
    completed: list[Path] = []
    tab = analyze_tab_mod.AnalyzeTab(on_pipeline_complete=completed.append)
    tab.completed = completed          # test-side handles, not tab API
    tab.dialogs = dialogs
    try:
        yield tab
    finally:
        tab._alive = False             # stop the self-rescheduling log poller
        if tab._runner.is_running:
            tab._runner.stop()


def _drive(tab, qapp, tmp_path, *, channels, method, **form) -> Path:
    """Build a plate, run it through the tab, and assert the shared invariants."""
    raw = _make_plate(tmp_path, channels)
    _configure_form(tab, input_dir=raw, method=method, **form)
    _run_and_wait(tab, qapp)
    assert not tab.dialogs, f"the tab refused the run: {tab.dialogs}"

    out_dir = raw / "out"
    _assert_clean_run(tab, tab.completed, out_dir)
    assert tab._last_output_dir == out_dir
    return out_dir


# ── one test per shipped segmentation method ─────────────────────────────────

@requires_stardist
def test_e2e_stardist_nuclei(analyze_tab, qapp, tmp_path):
    """The default method: StarDist on the nuclear channel."""
    out_dir = _drive(
        analyze_tab, qapp, tmp_path,
        channels={"DAPI": "nuclear", "GFP": "fluor"},
        method="stardist_nuclei",
        nuclear_token="DAPI", fluor_tokens=["GFP"],
    )
    rows = _assert_outputs(out_dir, method="stardist_nuclei", fluor_token="GFP",
                           min_objects=len(DEFAULT_CENTERS) - 1)
    # Synthetic nuclei are radius-12 disks (area ≈ 452); StarDist recovers their
    # geometry closely, so a loose band still catches a mis-scaled mask.
    for row in rows:
        assert 250 <= float(row["area_px"]) <= 800
        assert float(row["gfp_mean_intensity"]) > 500.0


@requires_stardist
def test_e2e_stardist_seeded_watershed_cell(analyze_tab, qapp, tmp_path):
    """StarDist nuclei as seeds, grown into the cytoplasm channel by watershed."""
    out_dir = _drive(
        analyze_tab, qapp, tmp_path,
        channels={"DAPI": "nuclear", "GFP": "fluor", "CYTO": "cytoplasm"},
        method="stardist_seeded_watershed_cell",
        nuclear_token="DAPI", fluor_tokens=["GFP", "CYTO"],
        cytoplasm_token="CYTO",
    )
    rows = _assert_outputs(out_dir, method="stardist_seeded_watershed_cell",
                           fluor_token="GFP", min_objects=len(DEFAULT_CENTERS) - 1)
    # Cells are the radius-22 cytoplasm disks (area ≈ 1520), materially larger
    # than the radius-12 nuclei the seeds came from — the watershed grew them.
    for row in rows:
        assert float(row["area_px"]) > 600
    # The watershed method writes its intermediates alongside the labels.
    with zipfile.ZipFile(out_dir / "A01_out.zip") as zf:
        names = zf.namelist()
    assert any(n.endswith("_nuclear_points.tif") for n in names), names
    assert any(n.endswith("_cytoplasm_tophat.tif") for n in names), names


@requires_cellpose("nuclei")
def test_e2e_cellpose(analyze_tab, qapp, tmp_path):
    """General Cellpose on the chosen segmentation channel, with tuned params."""
    tab = analyze_tab
    _configure_cellpose(tab, model="nuclei", diameter=str(2 * DEFAULT_RADIUS))
    out_dir = _drive(
        tab, qapp, tmp_path,
        channels={"DAPI": "nuclear", "GFP": "fluor"},
        method="cellpose",
        nuclear_token="DAPI", fluor_tokens=["GFP"],
    )
    rows = _assert_outputs(out_dir, method="cellpose", fluor_token="GFP")
    for row in rows:
        assert float(row["gfp_mean_intensity"]) > 500.0


@requires_cellpose("cyto3")
def test_e2e_cellpose_labelfree(analyze_tab, qapp, tmp_path):
    """The label-free preset: Cellpose cyto3 on the channel marked Phase.

    No fluorescent stain drives segmentation here — the nuclear slot is remapped
    onto the phase channel by the pipeline — but the fluor channel is still
    quantified inside the resulting cell masks.
    """
    out_dir = _drive(
        analyze_tab, qapp, tmp_path,
        channels={"DAPI": "nuclear", "GFP": "fluor", "BF": "phase"},
        method="cellpose_labelfree",
        nuclear_token="DAPI", fluor_tokens=["GFP", "BF"],
        phase_token="BF",
    )
    _assert_outputs(out_dir, method="cellpose_labelfree", fluor_token="GFP")


def test_e2e_classical_threshold(analyze_tab, qapp, tmp_path):
    """Otsu + watershed — the dependency-free method, always exercised."""
    out_dir = _drive(
        analyze_tab, qapp, tmp_path,
        channels={"DAPI": "nuclear", "GFP": "fluor"},
        method="classical_threshold",
        nuclear_token="DAPI", fluor_tokens=["GFP"],
    )
    rows = _assert_outputs(out_dir, method="classical_threshold", fluor_token="GFP",
                           min_objects=len(DEFAULT_CENTERS))
    # Otsu splits the bimodal synthetic field cleanly, so the objects are the
    # radius-12 disks themselves.
    for row in rows:
        assert 250 <= float(row["area_px"]) <= 800
        assert float(row["gfp_mean_intensity"]) > 500.0
