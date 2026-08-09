"""Everything the Home page displays, computed against a fake ctx.

No Qt, no app fixture, no dataset on disk — which is the point of putting every
number in ``collect.py``. The page is otherwise the hardest surface in Review to
test, and a landing page whose figures are wrong is worse than no landing page.

The rule most of these tests exist for: **a figure the page could not obtain is
``Missing``, never zero.** "Not computed" and "computed as zero" are different
facts, and conflating them is the bug class the completeness record was built to
end — it would be perverse for the page reporting that record to reintroduce it.
"""

from __future__ import annotations

import types

import pytest

from well_viewer.features.home import collect as hc

WELLS = ("A01", "A02", "A03", "A04", "A05")


def _well(health="complete", *, intent=8, covered_any=8, covered_csv=8,
          failed=(), csv_state="ok"):
    return {
        "intent": intent, "covered_any": covered_any, "covered_csv": covered_csv,
        "missing_count": max(0, intent - covered_any), "missing": [],
        "csv_state": csv_state, "has_in": True,
        "has_out_zip": covered_any > 0, "has_out_folder": False,
        "failed": list(failed), "health": health,
    }


def _block(wells=None, **kw):
    wells = wells if wells is not None else {"A01": _well()}
    complete = sum(1 for w in wells.values() if w["health"] == "complete")
    partial = sum(1 for w in wells.values() if w["health"] == "partial")
    block = {
        "version": 1, "skipped": False, "partial_scan": False, "suspended": False,
        "finished_at": "2026-08-09T12:00:00Z", "duration_s": 3720.0,
        "host": "hpc-login-3", "wells": wells,
        "totals": {
            "wells": len(wells), "complete": complete, "partial": partial,
            "missing_csv": 0, "no_output": 0,
            "intent": sum(w["intent"] for w in wells.values()),
            "covered_any": sum(w["covered_any"] for w in wells.values()),
            "covered_csv": sum(w["covered_csv"] for w in wells.values()),
        },
        "errors": {"files": 0, "by_type": {}}, "findings": [],
    }
    block.update(kw)
    return block


def _ctx(run_check=None, *, info=None, wells=WELLS, data_dir="/data/plate7",
         selections=(), plot_groups=(), well_labels=None, notes=""):
    pipeline_info = dict(info or {})
    if run_check is not None:
        pipeline_info["run_check"] = run_check
    from pathlib import Path
    return types.SimpleNamespace(state=types.SimpleNamespace(
        dataset=types.SimpleNamespace(
            data_dir=Path(data_dir) if data_dir else None,
            pipeline_info=pipeline_info,
            well_paths=dict.fromkeys(wells, "x"),
            well_labels=dict(well_labels or {}),
        ),
        selection=types.SimpleNamespace(
            selections=list(selections), plot_groups=list(plot_groups),
            notes_text=notes),
    ))


# ── the Missing sentinel ────────────────────────────────────────────────────

def test_missing_is_falsey_and_carries_its_reason():
    m = hc.Missing("no completeness record for this dataset")
    assert not m
    assert "no completeness record" in m.reason


def test_missing_is_not_a_number():
    assert hc.Missing("x") != 0
    assert not isinstance(hc.Missing("x"), int)


# ── record states ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(("block", "expected"), [
    (None, "none"),
    ({}, "none"),
    ({"skipped": True}, "skipped"),
    ({"partial_scan": True}, "partial"),
    ({"wells": {}}, "ok"),
])
def test_the_four_record_states_are_distinguishable(block, expected):
    assert hc.record_state(_ctx(block)) == expected


def test_each_unavailable_state_gives_its_own_reason():
    reasons = {hc.record_reason(_ctx(b))
               for b in (None, {"skipped": True}, {"partial_scan": True})}
    assert len(reasons) == 3, "three different problems, three different sentences"
    assert hc.record_reason(_ctx(_block())) == ""


# ── the em-dash rule ────────────────────────────────────────────────────────

@pytest.mark.parametrize("block", [None, {"skipped": True}])
def test_without_a_record_the_record_backed_tiles_are_missing_not_zero(block):
    tiles = {t.key: t for t in hc.stat_tiles(_ctx(block))}
    for key in ("wells", "coverage"):
        assert isinstance(tiles[key].value, hc.Missing), (
            f"{key} must not report a number it could not compute")
        assert tiles[key].value.reason


def test_the_reason_is_on_screen_not_only_in_the_value():
    tiles = {t.key: t for t in hc.stat_tiles(_ctx(None))}
    assert tiles["coverage"].sub, "the reason belongs beside the dash"


def test_with_a_record_the_tiles_carry_real_numbers():
    ctx = _ctx(_block({w: _well() for w in WELLS}))
    tiles = {t.key: t for t in hc.stat_tiles(ctx)}
    assert tiles["wells"].value == 5
    assert tiles["coverage"].value == 100
    assert all(t.available for t in tiles.values()
               if t.key in ("wells", "coverage"))


def test_coverage_always_shows_its_denominator():
    """A percentage whose denominator is hidden is the kind of number that has
    been quietly wrong here before."""
    wells = {"A01": _well(intent=10, covered_any=9, covered_csv=9)}
    ctx = _ctx(_block(wells))
    coverage = next(t for t in hc.stat_tiles(ctx) if t.key == "coverage")
    assert coverage.value == 90
    assert "9 of 10" in coverage.sub


def test_a_dataset_with_nothing_to_compare_against_does_not_claim_full_coverage():
    wells = {"A01": _well(intent=0, covered_any=0, covered_csv=0)}
    coverage = next(t for t in hc.stat_tiles(_ctx(_block(wells)))
                    if t.key == "coverage")
    assert isinstance(coverage.value, hc.Missing)


# ── the plate ───────────────────────────────────────────────────────────────

def test_every_recorded_health_state_reaches_the_plate():
    wells = {
        "A01": _well("complete"),
        "A02": _well("partial", covered_any=4, covered_csv=4),
        "A03": _well("partial", covered_any=0, covered_csv=0),
        "A04": _well("missing", csv_state="empty"),
        "A05": _well("complete", failed=["F003_24h"]),
    }
    cells = hc.plate_cells(_ctx(_block(wells)))
    assert cells == {
        "A01": hc.CELL_COMPLETE,
        "A02": hc.CELL_PARTIAL,
        "A03": hc.CELL_INPUT_ONLY,
        "A04": hc.CELL_NO_MEASUREMENTS,
        "A05": hc.CELL_FAILED,
    }


def test_a_staged_well_is_not_painted_as_half_finished():
    """Both are `partial` in the record — the shared classifier's answer, and
    correctly so. The plate separates them on a number already recorded, not by
    re-deciding whether the well is complete."""
    wells = {"A01": _well("partial", covered_any=0),
             "A02": _well("partial", covered_any=4)}
    cells = hc.plate_cells(_ctx(_block(wells)))
    assert cells["A01"] == hc.CELL_INPUT_ONLY
    assert cells["A02"] == hc.CELL_PARTIAL


def test_without_a_record_every_well_is_unchecked():
    """Not "fine" and not "broken" — neither is known, and the plate has to be
    able to say so."""
    cells = hc.plate_cells(_ctx(None))
    assert set(cells) == set(WELLS)
    assert set(cells.values()) == {hc.CELL_UNCHECKED}


def test_only_part_done_wells_get_a_coverage_arc():
    wells = {"A01": _well("complete"),
             "A02": _well("partial", covered_any=6),
             "A03": _well("partial", covered_any=0)}
    fractions = hc.plate_fractions(_ctx(_block(wells)))
    assert set(fractions) == {"A02"}
    assert fractions["A02"] == pytest.approx(0.75)


def test_the_legend_counts_what_the_plate_paints():
    wells = {"A01": _well("complete"), "A02": _well("complete"),
             "A03": _well("partial", covered_any=4)}
    counts = {state: n for state, _label, n in hc.plate_legend(_ctx(_block(wells)))}
    assert counts[hc.CELL_COMPLETE] == 2
    assert counts[hc.CELL_PARTIAL] == 1
    assert counts[hc.CELL_FAILED] == 0, "an empty state still gets a legend row"


# ── health ──────────────────────────────────────────────────────────────────

def test_a_clean_plate_says_so_rather_than_listing_zeros():
    rows = hc.health_rows(_ctx(_block({w: _well() for w in WELLS})))
    assert len(rows) == 1
    assert rows[0]["severity"] == "ok"


def test_problems_are_named_with_their_wells():
    wells = {"A01": _well("partial", covered_any=4),
             "A02": _well("complete", failed=["F003_24h"])}
    rows = {r["label"]: r for r in hc.health_rows(_ctx(_block(wells)))}
    assert rows["Partial wells"]["wells"] == ["A01"]
    assert rows["Wells with failures"]["wells"] == ["A02"]
    assert rows["Wells with failures"]["severity"] == "danger"


def test_error_files_are_named_by_exception_type():
    block = _block({"A01": _well()},
                   errors={"files": 3, "by_type": {"MemoryError": 2, "OSError": 1}})
    rows = {r["label"]: r for r in hc.health_rows(_ctx(block))}
    assert "MemoryError · 2" in rows["Errors"]["value"]


def test_a_degraded_scan_is_rendered_not_merely_stored():
    """The check swallows a per-well failure and continues. If the page did not
    surface that, a truncated record would read as a clean plate."""
    rows = hc.health_rows(_ctx(_block({"A01": _well()}, partial_scan=True)))
    assert any("did not finish" in r["value"] for r in rows)


def test_a_suspended_run_is_explained_rather_than_blamed():
    rows = hc.health_rows(_ctx(_block({"A01": _well()}, suspended=True)))
    assert any("suspended" in r["label"].lower() for r in rows)


def test_findings_from_the_record_reach_the_health_card():
    block = _block({"A01": _well()}, findings=[
        {"severity": "warning", "code": "mixed_csv_prefix", "message": "two prefixes"}])
    assert any("two prefixes" == r["value"] for r in hc.health_rows(_ctx(block)))


def test_no_record_reports_that_and_stops():
    rows = hc.health_rows(_ctx(None))
    assert len(rows) == 1
    assert "no completeness record" in rows[0]["value"]


# ── run parameters ──────────────────────────────────────────────────────────

def test_a_sentinel_threshold_reads_as_the_models_default_not_as_a_number():
    """The pipeline stores a negative sentinel for "use the pretrained weights'
    own threshold". Rendering that as -1 would be nonsense, and rendering it as
    a chosen value would be a lie."""
    ctx = _ctx(_block(), info={
        "segmentation_method": "stardist_nuclei",
        "execution_options": {"stardist_prob_thresh": -1.0,
                              "stardist_nms_thresh": 0.25}})
    rows = {r["label"]: r for r in hc.run_parameters(ctx)}
    assert rows["Object probability"]["is_default"] is True
    assert "default" in rows["Object probability"]["value"]
    assert rows["Overlap (NMS)"]["is_default"] is False
    assert rows["Overlap (NMS)"]["value"] == "0.25"


def test_the_background_method_is_named_in_full():
    """top-hat and sliding-disk have been mistaken for each other; the method
    name is worth the row."""
    ctx = _ctx(_block(), info={"qc_options": {"background": "sliding_disk"}})
    rows = {r["label"]: r["value"] for r in hc.run_parameters(ctx)}
    assert rows["Background subtraction"] == "sliding disk"


def test_the_duration_is_human_readable():
    rows = {r["label"]: r["value"] for r in hc.run_parameters(_ctx(_block()))}
    assert rows["Duration"] == "1 h 02 m"


# ── your work ───────────────────────────────────────────────────────────────

def test_an_untouched_dataset_reports_real_zeros():
    """These are computed facts — the user has defined nothing — so they are
    zeros, not em-dashes. That distinction is why Missing is its own type."""
    work = {r["key"]: r for r in hc.your_work(_ctx(_block()))}
    assert work["replicates"]["count"] == 0
    assert not any(isinstance(r["count"], hc.Missing) for r in work.values())


def test_work_is_counted_from_state():
    ctx = _ctx(_block(), selections=[{"id": 1}, {"id": 2}], plot_groups=[{"n": "g"}],
               well_labels={"A01": "treated", "A02": ""}, notes="a note")
    work = {r["key"]: r for r in hc.your_work(ctx)}
    assert work["replicates"]["count"] == 2
    assert work["plot_groups"]["count"] == 1
    assert work["labels"]["count"] == 1 and work["labels"]["total"] == 2
    assert work["notes"]["count"] == 1


# ── next step ───────────────────────────────────────────────────────────────

def test_no_dataset_offers_to_open_one():
    assert hc.next_step(_ctx(None, wells=(), data_dir=None))["action"] == "open"


def test_a_folder_that_opened_but_holds_no_wells_still_offers_to_open_one():
    """``data_dir`` is set when a load *begins*, and for a folder that turns out
    to hold nothing readable it stays set. The page is gated on the wells — the
    same thing the shared empty state covering it is gated on — so the two
    cannot describe the same moment differently."""
    step = hc.next_step(_ctx(None, wells=(), data_dir="/data/plate7"))
    assert step["action"] == "open"
    assert not hc.has_dataset(_ctx(None, wells=(), data_dir="/data/plate7"))


def test_the_ladder_puts_failures_above_everything_else():
    wells = {"A01": _well("complete", failed=["x"]),
             "A02": _well("partial", covered_any=4)}
    step = hc.next_step(_ctx(_block(wells)))
    assert step["action"] == "errors"
    assert step["severity"] == "danger"


def test_a_missing_record_is_itself_the_next_step():
    assert hc.next_step(_ctx(None))["action"] == "validate"


def test_a_healthy_unlabelled_plate_is_pointed_at_sample_definitions():
    ctx = _ctx(_block({w: _well() for w in WELLS}))
    assert hc.next_step(ctx)["action"] == "samples"


def test_a_healthy_labelled_plate_is_pointed_at_plotting():
    ctx = _ctx(_block({w: _well() for w in WELLS}),
               selections=[{"id": 1}], well_labels={"A01": "treated"})
    step = hc.next_step(ctx)
    assert step["action"] == "plot"
    assert step["severity"] == "ok"


def test_the_next_step_row_is_never_absent():
    """A block that appears and disappears between datasets moves everything
    below it."""
    for block in (None, {}, {"skipped": True}, _block()):
        assert hc.next_step(_ctx(block))["text"]


# ── channels ────────────────────────────────────────────────────────────────

def test_the_segmentation_channel_is_flagged():
    ctx = _ctx(_block(), info={
        "fluor_tokens": ["DAPI", "W2Cy3", "W3Cy5", "TL"],
        "nuclear_token": "DAPI", "cytoplasm_token": "W2Cy3",
        "smfish_tokens": ["W3Cy5"], "phase_token": "TL"})
    chips = {c["token"]: c for c in hc.channel_chips(ctx)}
    assert chips["DAPI"]["segmentation"] is True
    assert chips["W2Cy3"]["role"] == "cyto"
    assert chips["W3Cy5"]["role"] == "smfish"
    assert chips["TL"]["role"] == "phase"
    assert sum(1 for c in chips.values() if c["segmentation"]) == 1


# ── identity ────────────────────────────────────────────────────────────────

def test_the_masthead_carries_when_the_record_was_taken():
    """The record is a snapshot of run-end; a dataset repaired since still
    reads as it did then, so the page must not imply it is live."""
    ident = hc.identity(_ctx(_block()))
    assert ident["finished_at"] == "2026-08-09T12:00:00Z"
    assert ident["name"] == "plate7"


def test_identity_survives_a_dataset_with_no_record():
    ident = hc.identity(_ctx(None))
    assert ident["name"] == "plate7"
    assert ident["finished_at"] == ""


# ── nothing here reads the disk ─────────────────────────────────────────────

def test_the_page_opens_no_files(monkeypatch):
    """The premise of the whole design: Home reports a record, it does not
    measure. If any of this starts reading the dataset, a landing page pays a
    zip read and a CSV parse per well on every open."""
    import builtins
    import zipfile
    from pathlib import Path

    def _no(*a, **kw):
        raise AssertionError("Home must not read the dataset")

    monkeypatch.setattr(builtins, "open", _no)
    monkeypatch.setattr(zipfile, "ZipFile", _no)
    monkeypatch.setattr(Path, "open", _no)
    monkeypatch.setattr(Path, "iterdir", _no)
    monkeypatch.setattr(Path, "glob", _no)

    ctx = _ctx(_block({w: _well() for w in WELLS}))
    for fn in (hc.identity, hc.plate_cells, hc.plate_fractions, hc.plate_legend,
               hc.stat_tiles, hc.channel_chips, hc.health_rows,
               hc.run_parameters, hc.your_work, hc.next_step):
        fn(ctx)


# ── a dataset produced before the check existed ─────────────────────────────

def _old_sidecar():
    """A sidecar exactly as a pre-feature run left it: no run_check key."""
    return {
        "schema": "exp:channel:well:fov:timepoint", "separator": "_",
        "fluor_tokens": ["DAPI", "W3Cy5"], "nuclear_token": "DAPI",
        "smfish_tokens": ["W3Cy5"],
        "available_timepoints": ["0", "24h", "48h"],
        "available_fovs": ["F001", "F002"],
        "shape_features": ["area", "solidity"],
        "segmentation_method": "stardist_nuclei",
        "qc_options": {"background": "top_hat", "flatfield": "basic"},
    }


def test_an_old_dataset_still_fills_in_everything_the_sidecar_knows():
    """Opening a plate produced before this feature must not give a blank page.

    Most of Home comes from keys the sidecar has always carried, so timepoints,
    fields, shape features, channels and the whole run receipt render normally.
    Only the two figures that depend on the completeness record go unknown.
    """
    ctx = _ctx(None, info=_old_sidecar())
    tiles = {t.key: t for t in hc.stat_tiles(ctx)}

    assert tiles["timepoints"].value == 3
    assert tiles["fovs"].value == 2
    assert tiles["shape_features"].value == 2
    assert [c["token"] for c in hc.channel_chips(ctx)] == ["DAPI", "W3Cy5"]
    assert any(c["segmentation"] for c in hc.channel_chips(ctx))
    receipt = {r["label"]: r["value"] for r in hc.run_parameters(ctx)}
    assert receipt["Background subtraction"] == "top hat"
    assert hc.identity(ctx)["schema"] == "exp:channel:well:fov:timepoint"


def test_an_old_dataset_marks_only_the_record_backed_figures_unknown():
    ctx = _ctx(None, info=_old_sidecar())
    tiles = {t.key: t for t in hc.stat_tiles(ctx)}
    unavailable = {k for k, t in tiles.items() if not t.available}
    assert unavailable == {"wells", "coverage"}, (
        "only the figures that need the completeness record may go unknown")


def test_an_old_datasets_plate_is_neutral_rather_than_green_or_red():
    """Not "fine" and not "broken" — the run left nothing that says either, and
    painting a guess would be the conflation the record exists to end."""
    ctx = _ctx(None, info=_old_sidecar())
    cells = hc.plate_cells(ctx)
    assert set(cells) == set(WELLS)
    assert set(cells.values()) == {hc.CELL_UNCHECKED}
    assert hc.CELL_COMPLETE not in cells.values()


def test_an_old_dataset_still_reports_the_users_own_work():
    """None of this comes from the record, so it is as complete as ever."""
    ctx = _ctx(None, info=_old_sidecar(), selections=[{"id": 1}],
               well_labels={"A01": "treated"}, notes="an old note")
    work = {r["key"]: r["count"] for r in hc.your_work(ctx)}
    assert work["replicates"] == 1
    assert work["labels"] == 1
    assert work["notes"] == 1
