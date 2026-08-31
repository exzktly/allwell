"""The Analyze form ships 0.2 as its Nucleus probability starting value.

Blank in that field means "use the threshold baked into the pretrained weights"
(~0.479 for ``2D_versatile_fluo``), which is conservative for these plates. The
form now starts with a number in the box instead, so an untouched run detects
more nuclei — dim, out-of-focus and marginal ones included — than the pretrained
default would. That is a change to what a run *does*, not to how the panel looks,
which is why the shipped value is pinned here rather than left to a widget
literal nobody checks.

The Overlap (NMS) field is untouched and still starts blank; the asymmetry is
deliberate and asserted below so a later "tidy-up" cannot quietly even them out.

Pure constant / string / pure-function assertions — no app fixture, fast lane.
"""

from __future__ import annotations

from pathlib import Path

from analyze.seg_params import DEFAULT_PROB_THRESH_TEXT
from pipeline.stardist import NMS_THRESH_HELP, PROB_THRESH_HELP, parse_threshold
from services.pipeline_service import build_pipeline_args


def test_the_shipped_starting_value_is_0_2():
    assert DEFAULT_PROB_THRESH_TEXT == "0.2"


def test_the_pipeline_accepts_the_value_the_form_starts_with():
    """The failure mode a typo here produces: a form that ships a value the
    pipeline rejects, so every fresh run dies at argparse — or worse, falls back
    to the model default and segments with a threshold nobody chose."""
    assert parse_threshold(DEFAULT_PROB_THRESH_TEXT) == 0.2


def test_an_untouched_form_emits_the_flag():
    """0.2 is a real threshold, so it goes on the command line — and therefore
    into the run's ``pipeline_info.json``. A GUI run stays fully described by
    what it was launched with, even though the CLI's own default differs."""
    opts = dict(
        nuclear_token="NIR", fluor_tokens=["GFP"], csv_prefix="m",
        filename_schema="experiment:channel:well:fov:timepoint",
        filename_sep="_", segmentation_method="stardist_nuclei",
        min_nucleus_area_px=50, smfish_tokens=[], shape_features=[],
        tf_threads="0", workers="0", flatfield="none", background_well="",
        qc_remove_debris=False, qc_min_area=0.0, qc_max_area=0.0,
        qc_min_solidity=0.0,
        stardist_prob_thresh=DEFAULT_PROB_THRESH_TEXT,
    )
    args = build_pipeline_args(Path("/pipe.py"), Path("/in"), Path("/out"), opts)
    assert float(args[args.index("--stardist_prob_thresh") + 1]) == 0.2
    # Overlap still starts blank, so it emits nothing and the model's own NMS
    # applies. The two fields are deliberately not symmetric.
    assert "--stardist_nms_thresh" not in args


def test_clearing_the_field_still_means_the_models_own_threshold():
    """A starting value, not a floor. Every spelling of "auto" is unchanged."""
    for blank in ("", "   ", "auto", None):
        assert parse_threshold(blank) is None


def test_the_help_text_states_the_starting_value_and_how_to_leave_it():
    """Both halves, or the box says one thing and the tooltip another."""
    assert "0.2" in PROB_THRESH_HELP
    assert "0.479" in PROB_THRESH_HELP          # what clearing it returns to
    assert "Clear the field" in PROB_THRESH_HELP
    # The overlap threshold gained no such starting value, and must not claim one.
    assert "0.2" not in NMS_THRESH_HELP


def test_a_restored_sentinel_still_leaves_the_field_blank():
    """The one thing this change must not break.

    Loading a prior run's sidecar has to show **that run's** threshold. A run
    recorded with the pipeline's ``-1.0`` "model default" sentinel must restore
    as blank — overriding it with the fresh-form 0.2 would report the run as
    having used a threshold it did not.
    """
    from analyze.seg_params import AnalyzeSegParamsMixin

    class _Form(AnalyzeSegParamsMixin):
        def __init__(self):
            self._stardist_prob_thresh_edit = _Edit(DEFAULT_PROB_THRESH_TEXT)
            self._stardist_nms_thresh_edit = _Edit("")

    form = _Form()
    form._restore_stardist_thresholds({"stardist_prob_thresh": -1.0,
                                       "stardist_nms_thresh": -1.0})
    assert form._stardist_prob_thresh_edit.text() == ""

    # ...and a run that recorded a real threshold restores that number.
    form._restore_stardist_thresholds({"stardist_prob_thresh": 0.35})
    assert form._stardist_prob_thresh_edit.text() == "0.35"


class _Edit:
    """The two QLineEdit methods ``_restore_stardist_thresholds`` uses."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    def text(self) -> str:
        return self._text

    def setText(self, value: str) -> None:
        self._text = value
