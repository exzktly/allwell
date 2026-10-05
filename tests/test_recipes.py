"""Copying analysis from one dataset to another (a *recipe*).

``review/domain/recipe_compute.py`` (roster, fit), ``review/persistence/recipe.py``
(read, apply), the copied saved plot's marker, and the dialog's checklist. The
one test through the real app is ``tests/test_recipes_in_a_real_app.py``.

The fixtures are built so the right and wrong answers differ (CLAUDE.md §7.8
rule 2): the target lacks one channel the source gates on, names wells the
source does not, and already holds an auto-threshold value of its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


from review.core.dataset_state import DatasetState
from review.domain import recipe_compute as rc
from review.persistence import recipe as rp
from review.persistence import reset_scopes
from util import provenance_log as P

SRC_GATING = {"fluor_gates": {"gfp": 120.0, "cy5": 40.0},
              "thresh_frac_on": {"gfp": 55.0},
              "scatter_gates": [{"x_col": "cy5_mean_intensity", "y_col": "gfp_mean_intensity"}]}
SRC_DEFS = {"schema_version": 2,
            "selections": [{"name": "Treated", "wells": ["A01", "A02", "H12"]}],
            "well_labels": {"A01": "DMSO"}}
SRC_RATIOS = [{"name": "gfp/cy5", "numerator_channel": "gfp",
               "numerator_metric": "mean_intensity", "denominator_channel": "cy5",
               "denominator_metric": "mean_intensity", "epsilon": 1.0}]
SRC_PLOT = {"id": "p1", "created_at": "2026-01-01T00:00:00", "tab": "Bar Plots",
            "title": "GFP", "png_filename": "p1.png", "svg_filename": "p1.svg",
            "params": {"channel": "gfp"}, "thresholds": {}, "summary": "s"}


def _write_dataset(root: Path, *, info: dict, doc: dict | None = None,
                   layout: str = "in_out") -> Path:
    data = root / "out" if layout == "in_out" else root
    data.mkdir(parents=True)
    if layout == "in_out":
        (root / "in").mkdir()
    (data / "pipeline_info.json").write_text(json.dumps(info))
    if doc is not None:
        (data / "persistence.json").write_text(json.dumps({"schema_version": 1, **doc}))
    return data


def _source(tmp_path) -> Path:
    root = tmp_path / "plateA"
    _write_dataset(root, info={
        "nuclear_token": "dapi", "fluor_tokens": ["gfp", "cy5"],
        "cell_gating": SRC_GATING, "sample_definitions": SRC_DEFS},
        doc={"ratios": SRC_RATIOS, "saved_plots": [SRC_PLOT],
             "qc_params": {"min_area": 30}})
    return root


def _target(tmp_path):
    """A plate imaged without cy5, with wells A01..A03 only, whose pipeline
    already wrote its own auto-threshold."""
    data = _write_dataset(tmp_path / "plateB", info={
        "nuclear_token": "dapi", "fluor_tokens": ["gfp"], "run_check": {"x": 1},
        "cell_gating": {"thresh_frac_on": {"gfp": 30.0}}},
        doc={"saved_plots": [dict(SRC_PLOT, id="own", title="Own plot")]})
    ds = DatasetState()
    ds.begin_load(data)
    for w in ("A01", "A02", "A03"):
        ds.well_paths[w] = data / f"m_{w}.csv"
    calls = []
    ctx = SimpleNamespace(state=SimpleNamespace(dataset=ds),
                          persistence=SimpleNamespace(flush_all=lambda: calls.append("flush")))
    return ctx, data, calls


# ── the roster ──────────────────────────────────────────────────────────────

def _roster_problems(analysis: set[str], copied: set[str], declined: set[str]) -> list[str]:
    return ([f"unaccounted: {k}" for k in sorted(analysis - copied - declined)]
            + [f"both copied and declined: {k}" for k in sorted(copied & declined)]
            + [f"declines a scope that does not exist: {k}" for k in sorted(declined - analysis)])


def test_every_analysis_scope_is_copied_or_declined_with_a_reason():
    analysis = {s.key for s in reset_scopes.ANALYSIS_SCOPES}
    copied = {s.key for s in rc.RECIPE_SCOPES}
    problems = _roster_problems(analysis, copied, set(rc.DECLINED))
    assert not problems, problems
    assert all(len(why) > 40 for why in rc.DECLINED.values())


def test_the_roster_guard_bites():
    analysis = {s.key for s in reset_scopes.ANALYSIS_SCOPES} | {"new_scope"}
    assert "unaccounted: new_scope" in _roster_problems(
        analysis, {s.key for s in rc.RECIPE_SCOPES}, set(rc.DECLINED))


# ── reading ─────────────────────────────────────────────────────────────────

def test_a_dataset_root_is_read_from_its_out_folder(tmp_path):
    src = rp.read_dataset_blocks(_source(tmp_path))
    assert src.blocks[(rc.PIPELINE_INFO, "cell_gating")] == SRC_GATING
    assert src.blocks[(rc.PERSISTENCE, "ratios")] == SRC_RATIOS
    assert src.blocks[(rc.PERSISTENCE, "export_groups")] is None
    assert src.channels == {"dapi", "gfp", "cy5"}
    assert src.label == "plateA" and not src.refused


def test_an_unreadable_file_is_refused_not_read_as_empty(tmp_path):
    root = _source(tmp_path)
    (root / "out" / "pipeline_info.json").write_text("{not json")
    src = rp.read_dataset_blocks(root)
    assert "pipeline_info.json" in src.refused
    fits = {f.key: f for f in rc.check_fit(src, rp.read_dataset_blocks(root))}
    assert fits["gating"].status == "refused"
    assert "could not be read" in fits["gating"].describe()
    # A scope wholly in the readable file is unaffected.
    assert fits["ratios"].status != "refused"


def test_a_foreign_persistence_document_is_refused(tmp_path):
    root = _source(tmp_path)
    doc = root / "out" / "persistence.json"
    doc.write_text(json.dumps({"schema_version": 99, "ratios": SRC_RATIOS}))
    src = rp.read_dataset_blocks(root)
    assert "schema_version 99" in src.refused["persistence.json"]
    assert src.blocks[(rc.PERSISTENCE, "ratios")] is None


# ── fit ─────────────────────────────────────────────────────────────────────

def test_the_fit_names_what_the_target_lacks(tmp_path):
    ctx, _data, _calls = _target(tmp_path)
    fits = {f.key: f for f in rc.check_fit(rp.read_dataset_blocks(_source(tmp_path)),
                                           rp.read_open_dataset(ctx))}
    assert fits["gating"].missing_channels == ("cy5",)
    assert fits["gating"].replaces is True        # the target's own auto-threshold
    assert fits["ratios"].missing_channels == ("cy5",)
    assert fits["ratios"].replaces is False       # target has none
    assert fits["sample_definitions"].missing_wells == ("H12",)
    assert fits["sample_definitions"].missing_channels == ()
    assert fits["sample_variables"].status == "absent"
    assert fits["export_groups"].status == "absent"
    assert fits["saved_plots"].replaces is False  # added beside, never instead
    assert "arrive undrawn" in fits["saved_plots"].describe()


def test_a_clean_scope_says_so(tmp_path):
    ctx, _data, _calls = _target(tmp_path)
    src = rp.read_dataset_blocks(_source(tmp_path))
    src.blocks[(rc.PERSISTENCE, "qc_params")] = None
    src.blocks[(rc.PIPELINE_INFO, "cell_gating")] = {"thresh_frac_on": {"gfp": 30.0}}
    (fit,) = rc.check_fit(src, rp.read_open_dataset(ctx), keys=["gating"])
    assert fit.status == "clean" and fit.describe() == "Copies cleanly."


def test_column_names_count_as_their_channel():
    assert rc.channels_named({"x_col": "CY5_mean_intensity"}, {"cy5"}) == {"cy5"}
    assert rc.channels_named({"x_col": "cy55_area"}, {"cy5"}) == set()


# ── applying ────────────────────────────────────────────────────────────────

def test_apply_flushes_first_then_writes_through_the_recorded_paths(tmp_path):
    ctx, data, calls = _target(tmp_path)
    src = rp.read_dataset_blocks(_source(tmp_path))
    result = rp.apply_recipe(ctx, src, ["gating", "ratios", "sample_definitions",
                                        "saved_plots", "export_groups"])
    assert calls == ["flush"]
    assert result.applied == ["gating", "ratios", "sample_definitions",
                              "saved_plots", "export_groups"]
    info = json.loads((data / "pipeline_info.json").read_text())
    assert info["cell_gating"] == SRC_GATING
    assert info["sample_definitions"] == SRC_DEFS
    assert info["run_check"] == {"x": 1}          # the pipeline's own keys kept
    doc = json.loads((data / "persistence.json").read_text())
    assert doc["ratios"] == SRC_RATIOS and doc["qc_params"] == {"min_area": 30}
    assert "export_groups" not in doc             # absent in the source: untouched

    events = P.read_events(data).events
    changed = sorted(e["detail"]["domain"] for e in events if e["event"] == "analysis_changed")
    assert changed == ["cell_gating", "qc_params", "ratios", "sample_definitions"]
    (applied,) = [e for e in events if e["event"] == "recipe_applied"]
    assert applied["detail"]["source"] == "plateA"
    assert applied["detail"]["plots_added"] == 1


def test_copied_plots_are_added_undrawn_under_fresh_ids(tmp_path):
    ctx, data, _calls = _target(tmp_path)
    rp.apply_recipe(ctx, rp.read_dataset_blocks(_source(tmp_path)), ["saved_plots"])
    plots = json.loads((data / "persistence.json").read_text())["saved_plots"]
    own, copied = plots
    assert own["id"] == "own" and own["png_filename"] == "p1.png"
    assert copied["id"] not in ("own", "p1")
    assert (copied["png_filename"], copied["svg_filename"], copied["frames_filename"]) == ("", "", "")
    assert copied["copied_from"] == "plateA"
    assert copied["params"] == SRC_PLOT["params"]


def test_a_failing_scope_does_not_stop_the_others(tmp_path):
    ctx, data, _calls = _target(tmp_path)
    (data / "pipeline_info.json").unlink()       # nothing to merge gating into
    result = rp.apply_recipe(ctx, rp.read_dataset_blocks(_source(tmp_path)),
                             ["gating", "ratios"])
    assert result.applied == ["ratios"]
    assert "is missing" in result.failed["gating"]
    # Gating failed at its first block, before its persistence.json half.
    doc = json.loads((data / "persistence.json").read_text())
    assert "qc_params" not in doc and doc["ratios"] == SRC_RATIOS


def test_a_foreign_target_document_is_reported_not_silently_skipped(tmp_path):
    ctx, data, _calls = _target(tmp_path)
    (data / "persistence.json").write_text(json.dumps({"schema_version": 99}))
    result = rp.apply_recipe(ctx, rp.read_dataset_blocks(_source(tmp_path)), ["ratios"])
    assert result.applied == [] and "different build" in result.failed["ratios"]


# ── the copied plot reads as copied everywhere ──────────────────────────────

def test_a_copied_record_has_no_image_path_and_says_why(tmp_path):
    from review.features.saved_plots.state import SavedPlot, SavedPlotsStore, undrawn_note
    rec = SavedPlot.from_dict(dict(SRC_PLOT, png_filename="", copied_from="plateA"))
    ctx = SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(data_dir=tmp_path)))
    assert SavedPlotsStore().image_path(ctx, rec) is None
    assert undrawn_note(rec).startswith("Copied from plateA and not yet drawn")
    own = SavedPlot.from_dict(SRC_PLOT)
    assert undrawn_note(own) == "" and own.copied_from == ""


def test_the_pdf_report_pages_a_copied_plot_with_its_note(tmp_path):
    from review.features.saved_plots import _report
    from review.features.saved_plots.state import SavedPlot
    rec = SavedPlot.from_dict(dict(SRC_PLOT, png_filename="", copied_from="plateA"))
    texts = []

    class _Pdf:
        def savefig(self, fig):
            for ax in fig.axes:
                texts.extend(t.get_text() for t in ax.texts)

    _report._plot_page(_Pdf(), rec, image_path=None, summary="")
    assert any(t.startswith("Copied from plateA") for t in texts), texts


# ── the dialog ──────────────────────────────────────────────────────────────

def test_the_checklist_disables_what_cannot_be_copied(qtbot, tmp_path):
    from review.core.recipe_view import CopyAnalysisDialog
    ctx, _data, _calls = _target(tmp_path)
    fits = rc.check_fit(rp.read_dataset_blocks(_source(tmp_path)), rp.read_open_dataset(ctx))
    dialog = CopyAnalysisDialog(fits, "plateA")
    qtbot.addWidget(dialog)
    assert dialog.selected_keys() == ["sample_definitions", "gating", "ratios", "saved_plots"]
    assert not dialog._boxes["export_groups"].isEnabled()
    dialog._boxes["saved_plots"].setChecked(False)
    assert "saved_plots" not in dialog.selected_keys()


# ── found by review ────────────────────────────────────────────────────────

def test_copied_plots_are_in_the_gallery_after_the_reload(tmp_path):
    """The store was keyed on the folder alone, so the reload after a copy —
    same folder — kept the pre-copy records, and the next save erased the
    copies from disk."""
    from review.features.saved_plots.state import SavedPlotsStore
    ctx, data, _calls = _target(tmp_path)
    store = SavedPlotsStore()
    store.ensure_loaded(ctx)
    assert [r.id for r in store.records] == ["own"]
    rp.apply_recipe(ctx, rp.read_dataset_blocks(_source(tmp_path)), ["saved_plots"])
    ctx.state.dataset.begin_load(data)            # what the reload does
    store.ensure_loaded(ctx)
    assert len(store.records) == 2 and store.records[1].copied_from == "plateA"
    store.persist(ctx)
    plots = json.loads((data / "persistence.json").read_text())["saved_plots"]
    assert len(plots) == 2


def test_a_write_that_did_not_land_is_failed_not_applied(tmp_path, monkeypatch):
    from review.persistence import _doc

    def _full_disk(*a, **k):
        raise OSError(28, "No space left on device")

    ctx, data, _calls = _target(tmp_path)
    monkeypatch.setattr(_doc, "atomic_write_json", _full_disk)
    result = rp.apply_recipe(ctx, rp.read_dataset_blocks(_source(tmp_path)), ["ratios"])
    assert result.applied == [] and "could not be written" in result.failed["ratios"]
    applied = [e for e in P.read_events(data).events if e["event"] == "recipe_applied"]
    assert applied[0]["detail"]["scopes"] == []


def test_a_scope_that_fails_on_its_second_block_says_what_landed(tmp_path, monkeypatch):
    from review.persistence import _doc

    def _full_disk(*a, **k):
        raise OSError(28, "No space left on device")

    ctx, data, _calls = _target(tmp_path)
    monkeypatch.setattr(_doc, "atomic_write_json", _full_disk)
    result = rp.apply_recipe(ctx, rp.read_dataset_blocks(_source(tmp_path)), ["gating"])
    assert "already copied: cell_gating" in result.failed["gating"]


# ── recipe files ───────────────────────────────────────────────────────────

def test_a_recipe_file_is_the_folder_it_was_saved_from(tmp_path):
    """Read back, a recipe file gives the fit check exactly what its folder did.

    The fixture's blocks name a channel the target lacks (cy5) and a well it
    lacks (H12), so a file that dropped the channel list or a block would
    produce a different fit, not an equal one.
    """
    ctx, _data, _calls = _target(tmp_path)
    folder = rp.read_dataset_blocks(_source(tmp_path))
    path = rp.write_recipe_file(folder, tmp_path / f"plateA{rc.RECIPE_FILE_SUFFIX}")
    from_file = rp.read_recipe_file(path)
    target = rp.read_open_dataset(ctx)
    assert rc.check_fit(from_file, target) == rc.check_fit(folder, target)
    assert from_file.label == "plateA" and from_file.root == str(path)


def test_a_recipe_file_applies_as_its_folder_does(tmp_path):
    keys = ["gating", "ratios", "sample_definitions", "saved_plots"]
    source = rp.read_dataset_blocks(_source(tmp_path))
    via_folder, data_a, _ = _target(tmp_path / "a")
    rp.apply_recipe(via_folder, source, keys)
    via_file, data_b, _ = _target(tmp_path / "b")
    path = rp.write_recipe_file(source, tmp_path / "r.json")
    rp.apply_recipe(via_file, rp.read_recipe_file(path), keys)

    def _landed(data):
        info = json.loads((data / "pipeline_info.json").read_text())
        doc = json.loads((data / "persistence.json").read_text())
        plots = [{k: v for k, v in p.items() if k != "id"} for p in doc["saved_plots"]]
        return info["cell_gating"], info["sample_definitions"], doc["ratios"], plots
    assert _landed(data_a) == _landed(data_b)


def test_the_document_holds_only_what_the_source_holds(tmp_path):
    doc = rc.recipe_document(rp.read_dataset_blocks(_source(tmp_path)), app_version="x")
    assert (doc["kind"], doc["schema_version"], doc["app_version"]) == (
        rc.RECIPE_FILE_KIND, rc.RECIPE_FILE_SCHEMA, "x")
    assert doc["channels"] == ["cy5", "dapi", "gfp"]
    assert sorted(b["key"] for b in doc["blocks"]) == [
        "cell_gating", "qc_params", "ratios", "sample_definitions", "saved_plots"]


@pytest.mark.parametrize("doc, says", [
    ({"kind": "something-else", "schema_version": 1}, "not an All-Well recipe file"),
    ({"kind": rc.RECIPE_FILE_KIND, "schema_version": rc.RECIPE_FILE_SCHEMA + 1},
     "does not read"),
    ({"kind": rc.RECIPE_FILE_KIND, "schema_version": 1,
      "blocks": [{"file": "pipeline_info.json", "key": "run_check", "value": {}}]},
     "no recipe scope owns: pipeline_info.json::run_check"),
    ({"kind": rc.RECIPE_FILE_KIND, "schema_version": 1, "blocks": ["x"]},
     "not an object"),
], ids=["kind", "newer-schema", "unknown-block", "malformed-entry"])
def test_a_file_that_is_not_a_readable_recipe_is_refused_by_name(tmp_path, doc, says):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match=says):
        rp.read_recipe_file(path)


def test_a_source_that_could_not_be_read_is_not_saved_as_a_recipe(tmp_path):
    root = _source(tmp_path)
    (rp.data_dir_of(root) / "persistence.json").write_text("{not json")
    with pytest.raises(ValueError, match="persistence.json could not be read"):
        rp.write_recipe_file(rp.read_dataset_blocks(root), tmp_path / "r.json")
    assert not (tmp_path / "r.json").exists()


def test_saving_the_open_dataset_flushes_first_and_says_where(tmp_path):
    from review.core import recipe_view
    ctx, data, calls = _target(tmp_path)
    said = []
    ctx.notify = SimpleNamespace(set_status=said.append)
    ctx.window = None
    path = recipe_view.prompt_export_recipe(ctx, path=tmp_path / "out.json")
    assert calls == ["flush"]
    assert said == ["Saved this dataset's analysis to out.json."]
    assert rp.read_recipe_file(path).blocks[(rc.PIPELINE_INFO, "cell_gating")] == {
        "thresh_frac_on": {"gfp": 30.0}}


def test_each_menu_action_reaches_its_prompt(qtbot, tmp_path, monkeypatch):
    """The copy button's menu: three actions, each wired to what it names."""
    from PySide6.QtWidgets import QWidget
    from review.core import recipe_view
    seen = []
    monkeypatch.setattr(recipe_view, "prompt_copy_analysis",
                        lambda ctx, **kw: seen.append(("copy", kw.get("recipe_file"))))
    monkeypatch.setattr(recipe_view, "prompt_export_recipe",
                        lambda ctx, **kw: seen.append(("export", None)))
    parent = QWidget()
    qtbot.addWidget(parent)
    menu = recipe_view.copy_analysis_menu(SimpleNamespace(), parent)
    actions = [a for a in menu.actions() if not a.isSeparator()]
    assert [a.text() for a in actions] == [
        "Copy analysis from another dataset…", "Copy analysis from a recipe file…",
        "Save this dataset's analysis as a recipe file…"]
    for a in actions:
        a.trigger()
    assert seen == [("copy", None), ("copy", ""), ("export", None)]
