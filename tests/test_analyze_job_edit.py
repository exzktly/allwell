"""``AnalyzeTab.hydrate_job`` is a true inverse of ``collect_spec``.

The Job Queue's edit flow is collect → store → hydrate → collect: a queued
Analyze job's spec is written into the form, the user changes what they came
to change, and the form is collected straight back over the stored spec. Any
option the hydrator cannot restore is therefore not merely "not shown" — it is
silently replaced by whatever the live form happened to hold, in a spec the
user believes they only edited one field of.

The round trip below is the guard for that. It is deliberately built from a
spec whose every value differs from the form's default, because a round trip
over defaults passes without restoring anything at all.
"""

from __future__ import annotations

import pytest


# A fully-populated, entirely non-default run. Every value here differs from
# what a freshly-built AnalyzeTab collects — `test_the_fixture_is_all_non_default`
# proves it, so this file cannot rot into a vacuous pass.
_OPTS = {
    "raw": "/data/plate7",
    "scratch_dir": "/scratch/allwell",
    "nuclear_token": "DAPI",
    "fluor_tokens": ["GFP", "RFP", "BF"],
    "csv_prefix": "quantified",
    "tophat_radius_nir": "77",
    "tophat_radius_fluor": "88",
    "fft_small_nir": "5",
    "fft_small_fluor": "6",
    "mesh_ratio": "4.5",
    "mesh_sigma": "2.5",
    "no_tophat_nir": True,
    "no_tophat_fluor": True,
    # Always-on constants in _collect_run_options — carried so the comparison
    # is over the whole dict, not a subset of it.
    "compress_input_well_folders": True,
    "compress_output_well_folders": True,
    "move_source_tifs": True,
    "remote_batch_size": "12",
    "force": True,
    "incremental": True,
    "force_wells": ["A1", "B2"],
    "cpu_only": True,
    "allow_low_disk": True,
    "tf_threads": "4",
    "workers": "3",
    "stardist_tile_px": "512",
    "stardist_prob_thresh": "0.45",
    "stardist_nms_thresh": "0.3",
    "filename_schema": "channel:well:fov:timepoint:experiment",
    "filename_sep": "-",
    "smfish_tokens": ["RFP"],
    "shape_features": ["area", "solidity"],
    "flatfield": "gaussian",
    "background": "top_hat",
    "background_well": "H12",
    # The written form the editor produces — an explicit well list, never a
    # range, so what is stored round-trips to itself.
    "background_well_map": "H12=A01,A02",
    "auto_threshold": False,
    "run_check": False,
    "auto_threshold_method": "controls",
    "pos_control_well": "A1",
    "neg_control_well": "A2",
    "qc_remove_debris": True,
    "qc_min_area": "10.0",
    "qc_max_area": "900.0",
    "qc_min_solidity": "0.5",
    # Both non-default, so a hydration that drops either shows: the checkbox
    # ships on, and the cut ships blank.
    "measure_focus": False,
    "focus_min": "0.083",
    "phase_token": "BF",
    "segmentation_method": "stardist_seeded_watershed_cell",
    "cytoplasm_token": "GFP",
    "min_nucleus_area_px": "75",
    "cellpose_model": "nuclei",
    "cellpose_diameter": "30.0",
    "cellpose_flow_threshold": "0.6",
    "cellpose_cellprob_threshold": "0.1",
    "cellpose_nuclei_channel": "1",
}

# ``cleanup_remote`` / ``dry_run_cleanup`` are remote-only settings: nothing
# reads either on a local run, because ``PipelineRunner`` passes them only down
# the remote branch. This local spec therefore carries the form's own defaults,
# and the variants that matter are the ones naming a remote — ``_REMOTE_SPEC``
# below. (The form no longer rewrites the two boxes when "Run on:" returns to
# Local; it only hides them. Doing so silently dropped a dry-run tick and
# re-armed an auto-delete the user had turned off.)
_SPEC = {
    "opts": dict(_OPTS),
    "remote_name": None,
    "cleanup_remote": "all",
    "dry_run_cleanup": False,
    "output_filter": "full",
    "selective_channels": True,
    # Non-default, so the round trip proves the flag is restored rather than
    # merely left at the form's own default.
    "auto_detect": True,
}

# Both remote variants below are combinations the form can actually reach.
# ``_refresh_remote_controls`` *enables* dry-run only while auto-delete is
# checked, so "auto-delete off, dry-run on" is a state the user cannot get to
# by clicking; only a hand-edited job_queue.json could hold one.
_REMOTE_SPEC = {
    **_SPEC,
    "remote_name": "bench-box",
    "cleanup_remote": "all",
    "dry_run_cleanup": True,
}

_REMOTE_NO_CLEANUP_SPEC = {
    **_SPEC,
    "remote_name": "bench-box",
    "cleanup_remote": "none",
    "dry_run_cleanup": False,
}


# Otsu *with* both control wells filled. Reachable and dormant — the wells are
# ignored by an Otsu run (see ``_auto_threshold_validation_error``: "Otsu needs
# no wells; any text is dormant") — and it is the one shape that catches a
# hydrator inferring the method from the wells instead of reading it. A spec
# whose method and wells agree round-trips even when the method is never
# restored at all.
_OTSU_WITH_WELLS_SPEC = {
    **_SPEC,
    "opts": {**_OPTS, "auto_threshold": True, "auto_threshold_method": "otsu"},
}

# Blank means "use the model's own default" for both StarDist thresholds. The
# spec has to be hydrated *over* a form already holding real values for this to
# prove anything — which is exactly the edit-a-queued-job situation.
_BLANK_THRESHOLDS_SPEC = {
    **_SPEC,
    "opts": {**_OPTS, "stardist_prob_thresh": "", "stardist_nms_thresh": ""},
}


@pytest.fixture
def tab(qtbot):
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    # A remote the form can select without a remotes.toml on disk, added the
    # same way AnalyzeTab._refresh_remotes adds the real ones.
    t._remote_cb.addItem("bench-box", userData="bench-box")
    return t


def test_the_fixture_is_all_non_default(tab):
    """Guards the guard: a round trip over default values proves nothing.

    Every key in ``_OPTS`` that a hydrator could plausibly get wrong must
    differ from what a fresh form collects. The three always-on constants are
    exempt — they have no widget to restore them into and cannot vary.
    """
    constants = {"compress_input_well_folders", "compress_output_well_folders",
                 "move_source_tifs"}
    default = tab.collect_spec()["opts"]
    same = [k for k, v in _OPTS.items() if k not in constants and default.get(k) == v]
    assert not same, f"these fixture values match the form default, so they test nothing: {same}"


def test_hydrate_then_collect_reproduces_the_spec(tab):
    """The round trip. A key that survives here is a key the edit flow keeps."""
    tab.hydrate_job(_SPEC)
    assert tab.collect_spec() == _SPEC


@pytest.mark.parametrize("spec", [_REMOTE_SPEC, _REMOTE_NO_CLEANUP_SPEC],
                         ids=["dry-run", "no-cleanup"])
def test_hydrate_then_collect_reproduces_a_remote_spec(tab, spec):
    """The remote-only settings round-trip too.

    Separate from the local round trip because ``cleanup_remote`` and
    ``dry_run_cleanup`` are inert for a local run — the form resets them —
    so only a spec naming a remote can show they are carried at all. Both
    variants are needed: between them they vary each flag away from what the
    local spec pins it to.
    """
    tab.hydrate_job(spec)
    assert tab.collect_spec() == spec


def test_the_auto_threshold_method_is_read_not_inferred(tab):
    """Otsu stays Otsu even when both control wells happen to be filled.

    ``_apply_pipeline_info`` infers the method from whether both wells are
    named, which is right for the sidecar it was written for and wrong for a
    job spec that states the method outright.
    """
    tab.hydrate_job(_OTSU_WITH_WELLS_SPEC)
    assert tab.collect_spec() == _OTSU_WITH_WELLS_SPEC


def test_blank_stardist_thresholds_overwrite_populated_fields(tab):
    """A blank threshold is a value, and must replace one already on the form.

    ``_restore_stardist_thresholds`` skips anything it cannot read as a float,
    so a blank would otherwise leave the previous job's number in place — the
    next run then gets a threshold nobody asked for.
    """
    tab.hydrate_job(_SPEC)                      # real numbers in both fields
    assert tab.collect_spec()["opts"]["stardist_prob_thresh"] == "0.45"
    tab.hydrate_job(_BLANK_THRESHOLDS_SPEC)     # now blank them
    assert tab.collect_spec() == _BLANK_THRESHOLDS_SPEC


def test_hydrate_covers_every_option_collect_produces(tab):
    """No option may be added to the form without a hydration counterpart.

    ``_OPTS`` is compared key-for-key against a live collection rather than
    spot-checked, so a new entry in ``_collect_run_options`` fails this test
    until it is given a value here — at which point the round trip above is
    what proves it actually restores.
    """
    assert set(tab.collect_spec()["opts"]) == set(_OPTS)


def test_hydrate_leaves_the_preview_path_alone(tab):
    """``show_job`` stays the shallow preview it is documented to be.

    Hydration is the edit path; preview deliberately touches only the input
    field, so that clicking a queued row never rewrites the form under a user
    who was only looking.
    """
    tab.show_job({"opts": {"raw": "/data/other"}})
    collected = tab.collect_spec()["opts"]
    assert collected["raw"] == "/data/other"
    # Everything else is still the form's own state, not the preview spec's.
    assert collected["nuclear_token"] != _OPTS["nuclear_token"]


# ── hydrating one job over another ───────────────────────────────────────────
#
# The round trip above always hydrates a *fresh* form, which is the one case a
# set-only restore cannot fail. The real edit flow hydrates over whatever the
# last job left behind, and that is where a falsy value has to overwrite.

@pytest.mark.parametrize("key,attr,populated", [
    ("scratch_dir", "_scratch_edit", "/scratch/allwell"),
    ("raw", "_input_edit", "/data/plate7"),
])
def test_a_blank_value_overwrites_the_previous_job_s(tab, key, attr, populated):
    """Regression: `_apply_pipeline_info` is set-only for both of these
    (`if scratch and ...`, `if in_dir:`), so hydrating a job that has no
    scratch directory over a form that does left the previous job's path in
    the field — and collecting it back wrote that path into this job."""
    tab.hydrate_job(_SPEC)
    assert getattr(tab, attr).text() == populated

    tab.hydrate_job({**_SPEC, "opts": {**_OPTS, key: None}})
    assert getattr(tab, attr).text() == ""
    assert tab.collect_spec()["opts"][key] in (None, "", ".")


def test_a_missing_remote_is_reported_rather_than_silently_swapped(tab):
    """A job naming a remote this install no longer has cannot be restored.

    Saving the edit anyway would rewrite the job to run on whichever machine
    happened to be selected — a different computer, silently. hydrate_job
    returns the problem so the caller can say so.
    """
    problems = tab.hydrate_job({**_SPEC, "remote_name": "gone-box"})
    assert problems, "a vanished remote must be reported"
    assert "gone-box" in problems[0]


def test_a_remote_that_still_exists_reports_nothing(tab):
    assert tab.hydrate_job(_REMOTE_SPEC) == []
    assert tab.collect_spec()["remote_name"] == "bench-box"
