"""Changing what a run computes must not be answered "already done".

The pipeline has two reuse predicates. `pipeline.well_skip.output_exists_for_well`
asked only whether a well's CSV and archive both exist; the incremental done-set
asked only which `(fov, timepoint)` pairs were already covered. Neither knew
anything about *how* the run was configured, so re-running a plate with a
different segmenter, threshold or channel set was answered "up to date" and the
old numbers kept.

The half that makes it a data-integrity defect rather than a missed
recomputation: `write_pipeline_info` runs before any image work and rewrites the
sidecar to advertise the **new** configuration. Measured on the unfixed code, a
plate produced by `stardist_nuclei @ prob 0.2` and re-run as `cellpose,
min_nucleus_area_px=200, prob 0.9` logged "up to date (no new FOVs)" for every
well, kept run 1's CSV rows, and published a sidecar naming cellpose. Exit 0,
`run_check` complete — a plate describing a configuration that produced none of
the numbers in it.

`util.settings_digest.KEYED_ON` is the roster of what invalidates a prior
result, and `tests/test_reuse_is_keyed_completely.py` registers this decision
against it. Every key there is mutated alone below and must move the digest: a
key that does not is a key the reuse is not really keyed on.

**Grandfathering is deliberate.** A well with no recorded digest is accepted, so
introducing this does not force every user to reprocess every plate they touch.
The cost — a plate written before this record existed keeps the old behaviour
until its next forced run — is stated rather than left to be discovered.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from pipeline.settings_record import (
    SETTINGS_DIRNAME,
    record_well_settings,
    recorded_well_settings,
    well_settings_allow_reuse,
)
from pipeline.well_skip import output_exists_for_well
from util.settings_digest import KEYED_ON, settings_digest

BASE = {
    "segmentation_method": "stardist_nuclei",
    "nuclear_token": "NIR",
    "fluor_tokens": ["GFP"],
    "stardist_prob_thresh": 0.2,
    "filename_schema": "experiment:channel:well:fov:timepoint",
    "filename_sep": "_",
}

#: A value that differs from BASE's for each key, so "mutate one alone" is a
#: real change rather than a no-op the test cannot tell from a pass.
OTHER = {
    "segmentation_method": "cellpose",
    "nuclear_token": "DAPI",
    "cytoplasm_token": "CY5",
    "phase_token": "BF",
    "min_nucleus_area_px": 200,
    "stardist_prob_thresh": 0.9,
    "stardist_nms_thresh": 0.7,
    "cellpose_diameter": 30.0,
    "cellpose_flow_thresh": 0.8,
    "cellpose_cellprob": -1.0,
    "fluor_tokens": ["GFP", "RFP"],
    "smfish_tokens": ["GFP"],
    "shape_features": ["area", "circularity"],
    "qc_options": {"qc_min_area": 50},
    "background": "fft",
    "tophat_radius": 40,
    "fft_radius": 12,
    "flatfield": True,
    "empty_well": True,
    "background_well": "H12",
    "filename_schema": "experiment:well:fov:timepoint:channel",
    "filename_sep": "-",
}


def _well_on_disk(out: Path, well: str = "A01") -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"measurements_{well}.csv").write_text("well,fov\nA01,F001\n")
    with zipfile.ZipFile(out / f"{well}_out.zip", "w") as zf:
        zf.writestr("x_labels.tif", b"x")


def test_the_roster_covers_every_key_the_digest_reads():
    """One roster, not two. `KEYED_ON` is the definition; this is its liveness."""
    missing = sorted(set(KEYED_ON) - set(OTHER))
    assert not missing, (
        "these keys are in KEYED_ON but this test has no differing value for "
        f"them, so nothing here proves the digest reads them: {missing}")


@pytest.mark.parametrize("key", sorted(KEYED_ON))
def test_changing_one_key_alone_moves_the_digest(key):
    """Mutated individually, so a key carried by another cannot mask it."""
    baseline = settings_digest(BASE)
    changed = settings_digest({**BASE, key: OTHER[key]})

    assert changed != baseline, (
        f"{key!r} does not move the settings digest, so a run that changed it "
        f"would be answered 'already done' and keep the old numbers. It is in "
        f"KEYED_ON because: {KEYED_ON[key]}.")


def test_the_same_configuration_gives_the_same_digest():
    """Discriminating, not merely different every time — §7.7's other half.

    A digest that churned per call would satisfy every test above and make the
    check refuse everything, turning an integrity fix into a permanent full
    recompute.
    """
    assert settings_digest(BASE) == settings_digest(dict(BASE))
    assert settings_digest(BASE) == settings_digest({**BASE, "workers": 8}), (
        "an option outside KEYED_ON moved the digest; only what changes the "
        "output belongs in it")


def test_list_order_counts_but_dict_order_does_not():
    """`fluor_tokens` order decides the CSV columns; a dict's key order is noise."""
    a = settings_digest({**BASE, "fluor_tokens": ["GFP", "RFP"]})
    b = settings_digest({**BASE, "fluor_tokens": ["RFP", "GFP"]})
    assert a != b, "channel order changes the CSV columns and must be keyed"

    c = settings_digest({**BASE, "qc_options": {"a": 1, "b": 2}})
    d = settings_digest({**BASE, "qc_options": {"b": 2, "a": 1}})
    assert c == d, "a dict's key order is not a configuration difference"


def test_an_options_mapping_with_none_of_the_keys_is_unknown_not_a_match():
    assert settings_digest({}) == ""
    assert settings_digest({"workers": 4}) == ""


# ── the decision the predicates actually consult ────────────────────────────

def test_a_well_with_no_marker_is_grandfathered(tmp_path):
    """The decision recorded for this change: absent means accepted."""
    out = tmp_path / "out"
    _well_on_disk(out)

    assert output_exists_for_well(out, "A01", "measurements")
    assert recorded_well_settings(out, "A01") == ""
    assert well_settings_allow_reuse(out, "A01", settings_digest(BASE)), (
        "a plate written before this record existed was refused, which would "
        "force every user to reprocess every plate they touch")


def test_a_well_recorded_under_different_settings_is_not_reused(tmp_path):
    out = tmp_path / "out"
    _well_on_disk(out)
    record_well_settings(out, "A01", settings_digest(BASE))

    other = settings_digest({**BASE, "segmentation_method": "cellpose"})
    assert not well_settings_allow_reuse(out, "A01", other), (
        "the well's output was produced by stardist and a cellpose run reused "
        "it — the defect this whole change is about")


def test_a_well_recorded_under_the_same_settings_is_reused(tmp_path):
    out = tmp_path / "out"
    _well_on_disk(out)
    key = settings_digest(BASE)
    record_well_settings(out, "A01", key)

    assert well_settings_allow_reuse(out, "A01", key), (
        "an unchanged re-run recomputed a well it did not need to")


def test_the_marker_is_dot_prefixed_so_no_scan_mistakes_it_for_a_well(tmp_path):
    out = tmp_path / "out"
    _well_on_disk(out)
    record_well_settings(out, "A01", settings_digest(BASE))

    assert SETTINGS_DIRNAME.startswith("."), SETTINGS_DIRNAME
    assert (out / SETTINGS_DIRNAME / "A01.json").is_file()


def test_an_unreadable_marker_reads_as_unknown_rather_than_as_a_mismatch(tmp_path):
    """Best-effort, like every other record here: it costs answers, not runs."""
    out = tmp_path / "out"
    _well_on_disk(out)
    record_well_settings(out, "A01", settings_digest(BASE))
    (out / SETTINGS_DIRNAME / "A01.json").write_text("{not json")

    assert recorded_well_settings(out, "A01") == ""
    assert well_settings_allow_reuse(out, "A01", settings_digest(BASE))


# ── the wiring, not the helper ──────────────────────────────────────────────

def test_the_predicate_the_worker_pool_installs_consults_the_settings():
    """§7.7 #1: test the caller's sequence, not the unit's API.

    Every test above drives `well_settings_allow_reuse` directly, and all of
    them would pass against a `process_well_zips` that never called it — which
    is exactly how a correct helper ships behind an unreachable branch. This
    reads the predicate `process_well_zips` actually hands the pool.
    """
    import ast
    import inspect

    import process_microscopy as pm

    for fn in (pm.process_well_zips, pm.process_well_folders):
        src = inspect.getsource(fn)
        tree = ast.parse(inspect.cleandoc(src))
        installed = [
            ast.unparse(kw.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "output_exists"
        ]
        assert installed, f"{fn.__name__} installs no output_exists predicate"
        assert any("well_settings_allow_reuse" in expr for expr in installed), (
            f"{fn.__name__}'s skip predicate does not consult the settings, so "
            f"a re-run with a different segmenter is still answered 'already "
            f"done': {installed}")


def test_the_incremental_path_consults_the_settings_too():
    """The other predicate. Both had to be swept, or one holds the old rule."""
    import inspect

    import process_microscopy as pm

    for fn in (pm.process_well_zip_task, pm.process_well_folder_task):
        src = inspect.getsource(fn)
        assert "well_settings_allow_reuse" in src, (
            f"{fn.__name__}'s incremental branch does not consult the "
            f"settings, so changing the segmenter leaves every covered "
            f"(fov, timepoint) in the done-set and the well logs 'up to date'")
