"""The nanowell cell count takes Cellpose's flow and cell-probability thresholds, end to end.

Reported: brightfield nanowell plates of T cells and dendritic cells, where the T cells are counted well and many of
the larger, fainter DCs are not found at all. Lowering the cell-probability threshold is the remedy, and a label-free
run outside nanowell mode can now set it; a nanowell run could not, because its count is its own Cellpose call
(``nanowell_measure.cellpose_segmenter``) and always segmented at Cellpose's defaults. Asked: "Expose these options
for nanowell plate".

These tests follow a typed threshold the whole way, through the real Analyze tab: the field the user types in, the
argv the run is launched with, the pipeline's own parser and ``nanowell_options_from_args``, the worker's
``settings_from_kwargs``, the reuse key, and the keyword arguments Cellpose's segmenting function is finally called
with. Each half was easy to get right alone; a value that stops at any seam counts nothing differently.

A threshold left at Cellpose's own value, typed or blank, must reach none of the reuse key, the settings digest or
the segmenter's cache identity, so a plate counted before this existed is not re-segmented for it.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

from tests.test_nanowell_form import tab  # noqa: F401


def _argv(opts: dict) -> list[str]:
    from services.pipeline_service import build_pipeline_args

    return build_pipeline_args(Path("/x/pm.py"), Path("/in"), Path("/out"), opts)


def _on(tab):  # noqa: F811
    tab._set_fluor_rows(["PHASE", "GFP"], smfish=[], phase="PHASE")
    tab._nanowell_cb.setChecked(True)


def _parsed(argv: list[str]):
    """The argv as the pipeline parses it, through the nanowell options and the worker's settings."""
    from pipeline import nanowell_task
    from pipeline.cli_args import build_parser, nanowell_options_from_args

    parser = build_parser()
    args = parser.parse_args(argv[argv.index("/x/pm.py") + 1:])
    options = nanowell_options_from_args(args, parser.error)
    return args, options, nanowell_task.settings_from_kwargs({"_nanowell": options})


def _opts(tab, flow: str = "", cellprob: str = "") -> dict:  # noqa: F811
    _on(tab)
    tab._nanowell_flow_thr_edit.setText(flow)
    tab._nanowell_cellprob_thr_edit.setText(cellprob)
    return tab._collect_run_options()


def test_the_two_rows_are_shown_with_the_count_s_settings_in_nanowell_mode_only(tab):  # noqa: F811
    box = tab._nanowell_count_box
    for attr in ("_nanowell_flow_thr_edit", "_nanowell_cellprob_thr_edit"):
        assert box.isAncestorOf(getattr(tab, attr)), f"{attr} is not with the count's other settings"
        assert not getattr(tab, attr).isVisibleTo(tab), f"{attr} is offered outside nanowell mode"
    tab._nanowell_cb.setChecked(True)
    for attr in ("_nanowell_flow_thr_edit", "_nanowell_cellprob_thr_edit"):
        assert getattr(tab, attr).isVisibleTo(tab), f"{attr} is hidden in nanowell mode"


def test_typed_thresholds_reach_the_settings_the_reuse_key_and_the_segmenter(tab):  # noqa: F811
    argv = _argv(_opts(tab, flow="0.9", cellprob="-1.5"))
    assert argv[argv.index("--nanowell_flow_threshold") + 1] == "0.9"
    assert argv[argv.index("--nanowell_cellprob_threshold") + 1] == "-1.5"
    _args, options, settings = _parsed(argv)
    assert options["flow_threshold"] == 0.9 and options["cellprob_threshold"] == -1.5
    assert (settings.flow_threshold, settings.cellprob_threshold) == (0.9, -1.5)
    key = settings.key()
    assert key["flow_threshold"] == 0.9 and key["cellprob_threshold"] == -1.5
    assert settings.segmenter_options() == {"model_type": settings.cellpose_model, "flow_threshold": 0.9,
                                            "cellprob_threshold": -1.5}


def test_the_segmenter_calls_cellpose_with_the_thresholds(monkeypatch, tab):  # noqa: F811
    """At the boundary: the keyword arguments ``pipeline.cellpose`` receives, through the segmenter the run builds
    from the settings (``segmenter_options``), for one crop and for a batch."""
    from pipeline import nanowell_measure as nm

    calls = []
    fake = types.ModuleType("pipeline.cellpose")
    fake._segment_cellpose = lambda image, **kw: calls.append(("one", kw)) or "labels"
    fake._segment_cellpose_many = lambda images, **kw: calls.append(("many", kw)) or ["labels"]
    monkeypatch.setitem(sys.modules, "pipeline.cellpose", fake)
    import pipeline

    monkeypatch.setattr(pipeline, "cellpose", fake, raising=False)

    _args, _options, settings = _parsed(_argv(_opts(tab, cellprob="-2")))
    segment = nm.cellpose_segmenter(12.0, **settings.segmenter_options())
    segment("image")
    segment.segment_many(["image"], (0.0, 1.0))
    for kind, kw in calls:
        assert kw["cellprob_threshold"] == -2.0, f"the {kind} call was not given the threshold: {kw}"
        assert "flow_threshold" not in kw, f"the {kind} call was given a flow threshold nobody set: {kw}"
        assert kw["diameter"] == 12.0 and kw["model_type"] == settings.cellpose_model
    assert [kind for kind, _ in calls] == ["one", "many"]


def test_blank_or_cellpose_s_own_values_change_nothing_a_finished_plate_was_keyed_on(tab):  # noqa: F811
    """A blank field sends no flag; a typed 0.4 or 0 is Cellpose's own default and is dropped from the arguments,
    the options and the key, so the settings digest, the reuse key and the kept masks stay those of every earlier
    run at the defaults."""
    from pipeline.cli_args import settings_key_from_argv

    blank = _argv(_opts(tab))
    assert "--nanowell_flow_threshold" not in blank and "--nanowell_cellprob_threshold" not in blank
    typed_default = _argv(_opts(tab, flow="0.4", cellprob="0"))
    changed = _argv(_opts(tab, cellprob="-1"))

    def tail(argv):
        return argv[argv.index("/x/pm.py") + 1:]

    assert settings_key_from_argv(tail(typed_default)) == settings_key_from_argv(tail(blank))
    assert settings_key_from_argv(tail(changed)) != settings_key_from_argv(tail(blank))
    for argv in (blank, typed_default):
        args, options, settings = _parsed(argv)
        assert args.nanowell_flow_threshold is None and args.nanowell_cellprob_threshold is None
        assert "flow_threshold" not in options and "cellprob_threshold" not in options
        assert "flow_threshold" not in settings.key() and "cellprob_threshold" not in settings.key()
        assert settings.segmenter_options() == {"model_type": settings.cellpose_model}


def test_a_mistyped_or_out_of_range_threshold_blocks_the_run(tab):  # noqa: F811
    from analyze.domain.nanowell_fields import nanowell_option_errors

    for flow, cellprob, said in (("abc", "", "Flow threshold must be a number"),
                                 ("-0.1", "", "Flow threshold must be 0 or more"),
                                 ("", "-7", "Cell-prob threshold must be -6 to 6"),
                                 ("", "6.5", "Cell-prob threshold must be -6 to 6")):
        errors = nanowell_option_errors(_opts(tab, flow=flow, cellprob=cellprob))
        assert any(said in e for e in errors), (flow, cellprob, errors)
    assert not [e for e in nanowell_option_errors(_opts(tab, flow="0.8", cellprob="-2")) if "threshold" in e]


def test_test_detection_counts_at_the_typed_thresholds(tab):  # noqa: F811
    """Test detection builds its settings from the form (``settings_from_opts``), not from argv: it must count at
    the same thresholds the run would."""
    from analyze.nanowell_section import settings_from_opts

    settings = settings_from_opts(_opts(tab, flow="1.0", cellprob="-1"))
    assert (settings.flow_threshold, settings.cellprob_threshold) == (1.0, -1.0)
    assert settings_from_opts(_opts(tab)).segmenter_options() == {"model_type": settings.cellpose_model}


def test_a_dataset_s_thresholds_come_back_into_the_form(tab):  # noqa: F811
    """A restored run (a dataset's recorded options, or a queued job's) shows the thresholds it ran at, and a run
    that recorded none shows blank: Cellpose's defaults."""
    _on(tab)
    tab._apply_nanowell_settings({"nanowell_format": "100um", "nanowell_cellprob_threshold": -1.5})
    assert tab._nanowell_cellprob_thr_edit.text() == "-1.5" and tab._nanowell_flow_thr_edit.text() == ""
    tab._apply_nanowell_settings({"nanowell_format": "100um"})
    assert tab._nanowell_cellprob_thr_edit.text() == ""

