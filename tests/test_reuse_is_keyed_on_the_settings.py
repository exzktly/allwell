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
from util.settings_digest import KEYED_ON, NOT_KEYED_ON, settings_digest

BASE = {
    "segmentation_method": "stardist_nuclei",
    "nuclear_token": "NIR",
    "fluor_tokens": ["GFP"],
    "stardist_prob_thresh": 0.2,
    "filename_schema": "experiment:channel:well:fov:timepoint",
    "filename_sep": "_",
}

#: A value that differs from the parser's default for each key, so "mutate one
#: alone" is a real change rather than a no-op the test cannot tell from a pass.
#: Built against `build_parser()`'s own destinations — the first version of this
#: file invented six key names that no option uses, so every one of them passed
#: here while contributing nothing to a real run's digest.
OTHER = {
    "segmentation_method": "cellpose",
    "nuclear_token": "DAPI",
    "cytoplasm_token": "CY5",
    "phase_token": "BF",
    "min_nucleus_area_px": 200,
    "stardist_prob_thresh": 0.9,
    "stardist_nms_thresh": 0.7,
    "stardist_tile_px": 512,
    "cellpose_model": "nuclei",
    "cellpose_diameter": 30.0,
    "cellpose_flow_threshold": 0.8,
    "cellpose_cellprob_threshold": -1.0,
    "cellpose_nuclei_channel": "DAPI",
    "fluor_tokens": ["GFP", "RFP"],
    "smfish_tokens": ["GFP"],
    "shape_features": ["area", "circularity"],
    "qc_remove_debris": True,
    "qc_min_area": 50.0,
    "qc_max_area": 5000.0,
    "qc_min_solidity": 0.8,
    "background": "fft",
    "tophat_radius_nir": 40,
    "tophat_radius_fluor": 40,
    "no_tophat_nir": True,
    "no_tophat_fluor": True,
    "fft_small_nir": 12,
    "fft_small_fluor": 12,
    "mesh_ratio": 6.0,
    "mesh_sigma": 12.0,
    "flatfield": "per_channel",
    "background_dir": "/data/blank",
    "background_well": "H12",
    "background_well_map": "H12=A01",
    "no_save_tophat": True,
    "no_auto_threshold": True,
    "pos_control_well": "A01",
    "neg_control_well": "H12",
    "filename_schema": "experiment:well:fov:timepoint:channel",
    "filename_sep": "-",
}


def _well_on_disk(out: Path, well: str = "A01") -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"measurements_{well}.csv").write_text("well,fov\nA01,F001\n")
    with zipfile.ZipFile(out / f"{well}_out.zip", "w") as zf:
        zf.writestr("x_labels.tif", b"x")


def _parser_dests() -> set[str]:
    import process_microscopy as pm
    return set(vars(pm.build_parser().parse_args(["--input_dir", "/tmp/in"])))


def test_every_keyed_option_is_an_option_the_pipeline_actually_has():
    """The guard that was missing, and the defect it would have caught.

    The one caller is `settings_digest(vars(args))` on the pipeline's argparse
    namespace, so a key spelled anything other than a parser *destination*
    contributes nothing at all — silently. The first roster named six that do
    not exist (`cellpose_flow_thresh`, `cellpose_cellprob`, `qc_options`,
    `tophat_radius`, `fft_radius`, `empty_well`), so changing the Cellpose flow
    threshold on a plate left the digest unmoved, every well answered "up to
    date", and the old numbers were kept under a sidecar advertising the new
    value — the exact defect this whole record exists to prevent.
    """
    unknown = sorted(set(KEYED_ON) - _parser_dests())
    assert not unknown, (
        "these KEYED_ON entries are not parser destinations, so they never "
        f"reach the digest and the options they name are not keyed: {unknown}")


def test_no_pipeline_option_is_left_unclassified():
    """Adding an option must be a decision, not an omission.

    `KEYED_ON` and `NOT_KEYED_ON` have to partition the parser exactly. A new
    option that changes the numbers and is simply forgotten reintroduces the
    defect one option at a time, and nothing else in the suite can see it.
    """
    dests = _parser_dests()
    unclassified = sorted(dests - set(KEYED_ON) - set(NOT_KEYED_ON))
    assert not unclassified, (
        "these pipeline options are in neither roster, so nobody has said "
        "whether changing them invalidates a prior result. Put each in "
        f"KEYED_ON or NOT_KEYED_ON with its reason: {unclassified}")

    stale = sorted((set(KEYED_ON) | set(NOT_KEYED_ON)) - dests)
    assert not stale, (
        f"these roster entries name options the parser no longer has: {stale}")

    both = sorted(set(KEYED_ON) & set(NOT_KEYED_ON))
    assert not both, f"claimed as keyed and not keyed at once: {both}"


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


def test_a_short_circuited_well_is_not_stamped_with_this_runs_settings():
    """The marker has to mean "these settings produced what is on disk".

    Two paths return from a worker task without computing anything: the
    incremental branch that logs "up to date (no new FOVs)", and a well whose
    input holds no image groups. Written from the parent's `as_completed` loop,
    the marker landed on both — so a grandfathered well skipped under new
    settings acquired a record asserting those settings produced its rows. That
    spends the grandfathering escape hatch on a false claim rather than an
    honest unknown, and a later run under the *original* settings then
    recomputes a well that was already correct.

    Read off the source rather than driven, because the write has to sit after
    both artefacts are published and the pool spawns processes: what matters is
    which side of the early returns it is on.
    """
    import inspect

    import process_microscopy as pm

    for fn in (pm.process_well_zip_task, pm.process_well_folder_task):
        src = inspect.getsource(fn)
        assert "record_well_settings(output_dir, well_label, settings_key)" in src, (
            f"{fn.__name__} does not record the settings its own output was "
            f"produced under, so nothing on disk says which run wrote it")
        stamp = src.index("record_well_settings(")
        for early in ("up to date (no new FOVs)", "no image groups found"):
            if early in src:
                assert src.index(early) < stamp, (
                    f"{fn.__name__} stamps the settings marker before the "
                    f"{early!r} short circuit, so a well that computed nothing "
                    f"is recorded as having been produced by this run")
        assert src.index("Well %s — done.") > stamp, (
            f"{fn.__name__} records the settings after it declares the well "
            f"done; the marker must be written while both artefacts are known "
            f"to be on disk")


def test_a_remote_run_brings_the_settings_marker_back_with_the_well():
    """Otherwise the record is inert for every remote user.

    The pipeline writes `.aw_settings/<WELL>.json` in *its* output directory,
    which for a remote run is on the worker host. The fetch pulls
    `<well>_out.zip` and the per-well CSV and nothing else, so a remotely
    computed plate landed with no marker at all — permanently grandfathered,
    and every future settings change on it silently answered "already done".

    Asserted on the outbound pull rather than on a local write (§7.4 #1): the
    marker has to come from the worker, because the worker is what computed the
    digest, and a client that recomputed it would be a second definition that
    can drift.
    """
    import inspect

    from services.remote_runner import Fetcher

    src = inspect.getsource(Fetcher._fetch_one)
    assert "_pull_settings_marker" in src, (
        "a fetched well is never asked for the record of which configuration "
        "produced it, so the settings check cannot fire on a remote plate")
    assert src.index("materialized") < src.index("_pull_settings_marker"), (
        "the marker is pulled before the well is known to have materialised, "
        "so a failed fetch would credit local output that is not there")

    pull = inspect.getsource(Fetcher._pull_settings_marker)
    assert SETTINGS_DIRNAME in pull and "pull_small_file" in pull, pull
