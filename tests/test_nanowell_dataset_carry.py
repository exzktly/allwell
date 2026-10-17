"""Subset and Merge Plates carry a dataset's nanowell tables (tracker P10b, P10c), driven through each tool's
own plan and execution, on datasets whose images, measurements and tables agree."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from datasets.merge_plates.apply import execute_plan as merge_execute
from datasets.merge_plates.merge_plates_compute import MergePlatesError, build_plate_merge_plan
from datasets.subset.apply import execute_plan as subset_execute
from datasets.subset.subset_compute import SubsetFilter, build_subset_plan
from tests.fixtures.dataset_builder import build_dataset

SETTINGS = {"plate_format": "100um", "cell_diameter_um": 15.0}


def _dataset(root: Path, wells=("A01",), timepoints=("0h", "24h"), *, nanowells=True, settings=SETTINGS,
             spelling=None, cells=1, info_blocks=None):
    """The repo's realistic dataset (two fields, OS debris and a stray file in every archive), plus, with
    *nanowells*, each well's tables naming every timepoint and the sidecar's nanowell block. *spelling* maps a
    well to the spelling its nanowell files use on disk (``A1`` for ``A01``)."""
    info_extra = None
    if nanowells:
        info_extra = {"nanowell": {"scheme_version": 1, "settings": dict(settings)},
                      "acquisition": {tp: {"pixel_size_um": {"x": 4.3, "y": 4.3}} for tp in timepoints},
                      **(info_blocks or {})}
    build_dataset(root, wells=list(wells), timepoints=list(timepoints), fovs=("F001", "F002"),
                  info_extra=info_extra, junk=True, stray=True)
    if nanowells:
        folder = root / "out" / "nanowells"
        folder.mkdir()
        for well in wells:
            name = (spelling or {}).get(well, well)
            (folder / f"{name}_grid.json").write_text(json.dumps({
                "scheme_version": 1, "well": name, "timepoints": {tp: {"status": "ok"} for tp in timepoints}}))
            body = "".join(f"{tp},{q},0,measured,{cells * (q + 1)}\n" for tp in timepoints for q in range(3))
            (folder / f"{name}.csv").write_text("timepoint,nw_q,nw_r,state,n_cells\n" + body)
            _template(folder / f"{name}_template.npz", timepoints[0])
    return root


def _template(path: Path, timepoint: str) -> None:
    import numpy as np

    from pipeline.nanowell_measure import Template, save_template

    save_template(path, Template(np.ones((5, 5)), 2, "own", 3, 9, 0.1, (0, 1, 2)), timepoint)


def _tables(root: Path, well: str):
    folder = root / "out" / "nanowells"
    grid = json.loads((folder / f"{well}_grid.json").read_text())
    with (folder / f"{well}.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    return grid, rows


# ── P10b: Subset ─────────────────────────────────────────────────────────────
def test_subset_carries_the_tables_of_the_wells_and_timepoints_it_keeps(tmp_path):
    src = _dataset(tmp_path / "src", wells=("A01", "B02"))
    plan = build_subset_plan(src, tmp_path / "dest", SubsetFilter(wells=frozenset({"B02"}),
                                                                   timepoint_labels=frozenset({"24h"})))
    summary = subset_execute(plan)
    assert summary.startswith("Subset written")
    grid, rows = _tables(tmp_path / "dest", "B02")
    assert list(grid["timepoints"]) == ["24h"] and grid["well"] == "B02"
    assert [(r["timepoint"], r["nw_q"], r["n_cells"]) for r in rows] == [("24h", "0", "1"), ("24h", "1", "2"),
                                                                         ("24h", "2", "3")]
    # The template was built at 0h, which this subset leaves out: it is not carried, never left naming a
    # timepoint the destination does not have.
    assert sorted(p.name for p in (tmp_path / "dest" / "out" / "nanowells").iterdir()) == ["B02.csv", "B02_grid.json"]
    info = json.loads((tmp_path / "dest" / "out" / "pipeline_info.json").read_text())
    assert info["nanowell"]["settings"] == SETTINGS                     # Review reads the scheme from here


def test_subset_finds_tables_written_under_an_unpadded_well(tmp_path):
    src = _dataset(tmp_path / "src", wells=("A01",), spelling={"A01": "A1"})
    subset_execute(build_subset_plan(src, tmp_path / "dest", SubsetFilter()))
    grid, rows = _tables(tmp_path / "dest", "A01")
    assert grid["well"] == "A01" and len(rows) == 6


def test_a_timepoint_filter_that_keeps_none_of_a_well_s_table_writes_no_table(tmp_path):
    src = _dataset(tmp_path / "src", wells=("A01",), timepoints=("0h", "24h"))
    folder = src / "out" / "nanowells"
    grid = json.loads((folder / "A01_grid.json").read_text())
    grid["timepoints"].pop("24h")                                      # the analysis covered 0h only
    (folder / "A01_grid.json").write_text(json.dumps(grid))
    (folder / "A01.csv").write_text("timepoint,nw_q,nw_r,state,n_cells\n0h,0,0,measured,1\n")
    subset_execute(build_subset_plan(src, tmp_path / "dest", SubsetFilter(timepoint_labels=frozenset({"24h"}))))
    assert not (tmp_path / "dest" / "out" / "nanowells").exists()


# ── P10c: Merge Plates ───────────────────────────────────────────────────────
def test_merge_plates_carries_both_plates_tables_with_dataset_2_s_wells_remapped(tmp_path):
    """Dataset 2's counts are ten times dataset 1's, so a carry that read the wrong plate shows."""
    one = _dataset(tmp_path / "one", wells=("A01",))
    two = _dataset(tmp_path / "two", wells=("A01",), cells=10)
    plan = build_plate_merge_plan(one, two, tmp_path / "dest", well_map={"A01": "C03"})
    merge_execute(plan)
    first, first_rows = _tables(tmp_path / "dest", "A01")
    moved, moved_rows = _tables(tmp_path / "dest", "C03")
    assert (first["well"], moved["well"]) == ("A01", "C03")
    assert [r["n_cells"] for r in first_rows] == ["1", "2", "3"] * 2
    assert [r["n_cells"] for r in moved_rows] == ["10", "20", "30"] * 2
    # Each well remembers its plate, so the end-of-run re-anchoring fits each plate's motion on its own.
    assert (first["plate"], moved["plate"]) == ("dataset 1: one", "dataset 2: two")
    from pipeline.nanowell_measure import load_template

    carried = load_template(tmp_path / "dest" / "out" / "nanowells" / "C03_template.npz")
    assert carried is not None and carried[1] == "0h"
    assert plan.blocks_warning is None


def test_merge_plates_refuses_two_analyses_under_different_settings(tmp_path):
    one = _dataset(tmp_path / "one", wells=("A01",))
    two = _dataset(tmp_path / "two", wells=("B01",), settings=dict(SETTINGS, plate_format="50um"))
    with pytest.raises(MergePlatesError, match="computed under different nanowell settings"):
        build_plate_merge_plan(one, two, tmp_path / "dest")


def test_merge_plates_says_when_only_one_plate_has_nanowell_results(tmp_path):
    one = _dataset(tmp_path / "one", wells=("A01",), nanowells=False)
    two = _dataset(tmp_path / "two", wells=("B01",))
    plan = build_plate_merge_plan(one, two, tmp_path / "dest")
    assert plan.blocks_warning == ("Only two has nanowell results; one's wells will have none, and the Nanowells "
                                   "page names them as wells without a table.")
    assert plan.merged_info["nanowell"]["settings"] == SETTINGS     # dataset 2's block, though dataset 1 led
    merge_execute(plan)
    assert _tables(tmp_path / "dest", "B01")[0]["well"] == "B01"


def _border_default(dest: Path) -> bool:
    """The border switch a merged dataset opens with, read the way Review reads it."""
    from review.domain.qc_params import QCParams
    from review.domain.viewer_state import read_pipeline_info

    return QCParams.from_pipeline_info(read_pipeline_info(dest / "out")[3]).exclude_border_cells


@pytest.mark.parametrize("first_is_nanowell", [False, True], ids=["ordinary-first", "nanowell-first"])
def test_a_nanowell_plate_merged_with_an_ordinary_one_keeps_border_exclusion_on(tmp_path, first_is_nanowell):
    """The merged sidecar carries the nanowell block, so Review read the whole
    merged plate as a nanowell run and opened it with the border switch off —
    keeping the ordinary plate's partial cells, whose area and totals describe
    a fragment, in every number. The safer of the two defaults for a plate that
    is part nanowell is on: what it drops is counted on the panel and the
    funnel, while a partial cell kept is a wrong row nothing marks."""
    one = _dataset(tmp_path / "one", wells=("A01",), nanowells=first_is_nanowell)
    two = _dataset(tmp_path / "two", wells=("B01",), nanowells=not first_is_nanowell)
    merge_execute(build_plate_merge_plan(one, two, tmp_path / "dest"))
    assert _border_default(tmp_path / "dest") is True

    # Merged again with a pure nanowell plate, on either side, the result is
    # still part ordinary.
    three = _dataset(tmp_path / "three", wells=("C01",))
    pair = (three, tmp_path / "dest") if first_is_nanowell else (tmp_path / "dest", three)
    merge_execute(build_plate_merge_plan(*pair, tmp_path / "dest2"))
    assert _border_default(tmp_path / "dest2") is True


def test_two_nanowell_plates_merged_keep_the_nanowell_default(tmp_path):
    """The other half: a rule that turned the switch on for every merge would
    pass the test above."""
    one = _dataset(tmp_path / "one", wells=("A01",))
    two = _dataset(tmp_path / "two", wells=("B01",))
    merge_execute(build_plate_merge_plan(one, two, tmp_path / "dest"))
    assert _border_default(tmp_path / "dest") is False


def test_a_merged_sidecar_drops_the_re_anchoring_record_and_refuses_two_calibrations(tmp_path):
    one = _dataset(tmp_path / "one", wells=("A01",), nanowells=False)
    two = _dataset(tmp_path / "two", wells=("A01",))
    info = json.loads((two / "out" / "pipeline_info.json").read_text())
    info["nanowell"]["reanchor"] = {"anchored": ["A01/24h"], "remaining": {}}
    (two / "out" / "pipeline_info.json").write_text(json.dumps(info))
    plan = build_plate_merge_plan(one, two, tmp_path / "dest", well_map={"A01": "C03"})
    assert plan.merged_info["nanowell"] == {"scheme_version": 1, "settings": SETTINGS,   # it named A01, now plate 1's
                                            # one is an ordinary plate: the border default stays on
                                            "mixed_with_ordinary_plate": True}
    three = _dataset(tmp_path / "three", wells=("B01",))
    info = json.loads((three / "out" / "pipeline_info.json").read_text())
    info["acquisition"]["0h"]["pixel_size_um"] = {"x": 0.65, "y": 0.65}
    (three / "out" / "pipeline_info.json").write_text(json.dumps(info))
    with pytest.raises(MergePlatesError, match="calibrations of timepoint '0h' differ"):
        build_plate_merge_plan(two, three, tmp_path / "dest2")


@pytest.mark.parametrize("damage, message", [
    (lambda f: (f / "A01.csv").unlink(), "A01 has a nanowell grid record and no sites table"),
    # Roll-up review X15: a gzipped sites table is not one Review can read, so it is not carried as one.
    (lambda f: ((f / "A01.csv.gz").write_bytes(__import__("gzip").compress((f / "A01.csv").read_bytes())),
                (f / "A01.csv").unlink()), "A01 has a nanowell grid record and no sites table"),
    (lambda f: (f / "A01_grid.json").write_text("{not json"), "A01_grid.json is unreadable"),
    (lambda f: ((f / "A1_grid.json").write_text((f / "A01_grid.json").read_text()),
                (f / "A1.csv").write_text((f / "A01.csv").read_text())), "A01, A1 are one well spelled 2 ways"),
])
def test_tables_that_cannot_be_carried_are_refused_before_anything_is_written(tmp_path, damage, message):
    from datasets.subset.subset_compute import SubsetError

    src = _dataset(tmp_path / "src", wells=("A01", "B02"))
    damage(src / "out" / "nanowells")
    with pytest.raises(SubsetError, match=message):
        build_subset_plan(src, tmp_path / "dest", SubsetFilter())
    with pytest.raises(MergePlatesError, match=message):
        build_plate_merge_plan(src, _dataset(tmp_path / "other", wells=("C01",)), tmp_path / "dest2")
    assert not (tmp_path / "dest").exists() and not (tmp_path / "dest2").exists()


def test_the_end_of_run_re_anchoring_fits_each_plate_of_a_merged_dataset_on_its_own(tmp_path, monkeypatch):
    from pipeline import nanowell_task as nt

    folder = tmp_path / "out" / "nanowells"
    folder.mkdir(parents=True)
    settings = {"k": 1}
    for well, plate in (("A01", "dataset 1: one"), ("A02", "dataset 1: one"), ("C03", "dataset 2: two"),
                        ("D04", None)):
        grid = {"scheme_version": 1, "well": well, "settings": settings, "timepoints": {}}
        if plate:
            grid["plate"] = plate
        (folder / f"{well}_grid.json").write_text(json.dumps(grid))
    seen = []
    monkeypatch.setattr(nt, "plan_reanchor", lambda grids: (seen.append(sorted(grids)), ({}, {}))[1])
    monkeypatch.setattr(nt, "settings_from_kwargs", lambda _k: type("S", (), {"key": lambda self: settings})())
    nt.reanchor_plate(tmp_path / "out", [], {}, "PHASE", [], "m", ["experiment"], "_", "key",
                      grouper=None, parse=None)
    assert sorted(seen) == [["A01", "A02"], ["C03"], ["D04"]]


def test_a_re_run_of_a_merged_well_keeps_its_plate(tmp_path):
    from pipeline import nanowell_run as nr
    from pipeline import nanowell_task as nt

    path = tmp_path / "C03_grid.json"
    path.write_text(json.dumps({"scheme_version": 1, "well": "C03", "plate": "dataset 2: two", "timepoints": {}}))
    nt.write_grid_json(path, "C03", nr.WellOutcome("C03"), nr.NanowellSettings("100um"))
    assert json.loads(path.read_text())["plate"] == "dataset 2: two"


def _set_rule(root: Path, well: str, array_radius: dict) -> None:
    path = root / "out" / "nanowells" / f"{well}_grid.json"
    grid = json.loads(path.read_text())
    grid["array_radius"] = array_radius
    path.write_text(json.dumps(grid))


_DISK = {"radius_um": 1200.0, "status": "shared"}
_EVIDENCE = {"radius_um": 1200.0, "status": "shared", "roster": [[0, 0]], "roster_rule": "evidence"}


def test_merge_plates_names_wells_whose_rosters_two_versions_decided(tmp_path):
    """Tracker X35a-R4: an old-rule plate merged with an evidence-rule one counts its rim differently. Each well's rows
    are right under its own rule, so the merge goes on, and its note names the wells of each rule."""
    one = _dataset(tmp_path / "one", wells=("A01",))
    two = _dataset(tmp_path / "two", wells=("B01",))
    _set_rule(one, "A01", _DISK)
    _set_rule(two, "B01", _EVIDENCE)
    plan = build_plate_merge_plan(one, two, tmp_path / "dest")
    assert "one/A01 keep an earlier version's nanowell roster" in (plan.blocks_warning or "")
    assert "two/B01 the current one" in plan.blocks_warning
    _set_rule(one, "A01", _EVIDENCE)
    assert build_plate_merge_plan(one, two, tmp_path / "dest2").blocks_warning is None


def test_validate_names_a_dataset_whose_wells_mix_roster_rules(tmp_path):
    from datasets.core.dataset_paths import DatasetPaths
    from datasets.core.nanowell_tables import nanowell_findings

    root = _dataset(tmp_path / "mixed", wells=("A01", "B01"))
    _set_rule(root, "A01", _DISK)
    _set_rule(root, "B01", _EVIDENCE)
    paths = DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out")
    findings = nanowell_findings(paths, {}, ["experiment", "channel", "well", "fov", "timepoint"], "_")
    assert [f[1] for f in findings if f[0] == "warning"] == ["nanowell_roster_rules_mixed"]
    assert "A01 keep an earlier version's" in next(f[2] for f in findings if f[1] == "nanowell_roster_rules_mixed")


def test_validate_says_which_wells_were_analysed_again_over_every_timepoint(tmp_path):
    """Tracker X35a-R8: re-analysis rewrites a well's earlier rows, which Validate names."""
    from datasets.core.dataset_paths import DatasetPaths
    from datasets.core.nanowell_tables import nanowell_findings

    root = _dataset(tmp_path / "again", wells=("A01",))
    _set_rule(root, "A01", {**_EVIDENCE, "reanalysed_because": "a new timepoint changed the well's roster"})
    paths = DatasetPaths(root=root, in_dir=root / "in", out_dir=root / "out")
    found = [f for f in nanowell_findings(paths, {}, ["experiment", "channel", "well", "fov", "timepoint"], "_")
             if f[1] == "nanowell_reanalysed"]
    assert found == [("info", "nanowell_reanalysed", "A01: a new timepoint changed the well's roster")]
