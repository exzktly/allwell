"""The Cellpose model the nanowell count segments with (nanowell tracker L2).

The count used Cellpose 3's ``cyto3`` with no way to choose another; ``livecell_cp3``, the Cellpose 3 model trained on
LIVECell's phase-contrast images, is now a choice on the form and the command line. These follow the choice from the
form's combo to the argv, from the argv to the run's settings and its reuse key, and back from a sidecar. That the
run hands the chosen model to Cellpose is pinned in ``test_nanowell_preview.py``, where a real timepoint is measured.
"""

from __future__ import annotations

import pytest

from tests.test_nanowell_form import _argv, _flag, tab  # noqa: F401


def test_the_form_s_models_are_the_pipeline_s_and_the_parser_s():
    from analyze.domain.nanowell_fields import CELLPOSE_MODEL_CHOICES
    from pipeline.cli_args import build_parser
    from pipeline.nanowell_run import CELLPOSE_MODELS

    assert tuple(value for value, _ in CELLPOSE_MODEL_CHOICES) == CELLPOSE_MODELS == ("cyto3", "livecell_cp3")
    action = next(a for a in build_parser()._actions if a.dest == "nanowell_cellpose_model")
    assert tuple(action.choices) == CELLPOSE_MODELS and action.default is None


def _options(*extra):
    from pipeline.cli_args import build_parser, nanowell_options_from_args

    parser = build_parser()
    args = parser.parse_args(["--input_dir", "in", "--output_dir", "out", "--nanowell_format", "100um",
                              "--phase_token", "PHASE", *extra])
    return nanowell_options_from_args(args, parser.error)


def test_the_command_line_choice_reaches_the_run_s_settings_and_its_reuse_key():
    from pipeline.nanowell_task import settings_from_kwargs

    chosen = settings_from_kwargs({"_nanowell": _options("--nanowell_cellpose_model", "livecell_cp3")})
    assert chosen.cellpose_model == "livecell_cp3" and chosen.key()["cellpose_model"] == "livecell_cp3"
    for default in (_options(), _options("--nanowell_cellpose_model", "cyto3")):
        assert "cellpose_model" not in default                         # an earlier run's options, unchanged
        settings = settings_from_kwargs({"_nanowell": default})
        assert settings.cellpose_model == "cyto3" and "cellpose_model" not in settings.key()


def test_an_unknown_model_is_refused_by_the_settings_and_the_form():
    from analyze.domain.nanowell_fields import nanowell_option_errors
    from pipeline.nanowell_run import NanowellSettings

    with pytest.raises(ValueError, match="Cellpose model"):
        NanowellSettings("100um", cellpose_model="cyto2")
    errors = nanowell_option_errors({"nanowell_format": "100um", "nanowell_cellpose_model": "cyto2",
                                     "phase_token": "PHASE"})
    assert any("Cellpose model 'cyto2'" in e for e in errors)


def test_the_form_sends_the_model_only_when_it_is_not_the_default_and_a_sidecar_restores_it(tab):  # noqa: F811
    from analyze.nanowell_section import settings_from_opts

    tab._nanowell_cb.setChecked(True)
    assert tab._nanowell_model_cb.currentData() == "cyto3" and tab._nanowell_model_cb.isEnabled()
    assert "--nanowell_cellpose_model" not in _argv(tab)                # the default keeps the run's key
    tab._nanowell_model_cb.setCurrentIndex(tab._nanowell_model_cb.findData("livecell_cp3"))
    assert _flag(_argv(tab), "--nanowell_cellpose_model") == "livecell_cp3"
    assert settings_from_opts(tab._collect_run_spec().opts).cellpose_model == "livecell_cp3"   # Test detection's
    tab._apply_nanowell_settings({"nanowell_format": "100um", "nanowell_cellpose_model": None})
    assert tab._nanowell_model_cb.currentData() == "cyto3"              # a run that did not choose
    tab._apply_nanowell_settings({"nanowell_format": "100um", "nanowell_cellpose_model": "livecell_cp3"})
    assert tab._nanowell_model_cb.currentData() == "livecell_cp3"
    tab._nanowell_cb.setChecked(False)
    assert not tab._nanowell_model_cb.isEnabled()
