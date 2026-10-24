"""What the nanowell count's segmenter is given: the tile as imaged, or the rim-divided tile (``segment_on``).

Asked for: "if cpsam was trained on non-ratioed images, shouldn't we have a way to give it what it was trained on".
The count segmented only the rim-suppressed tile — each nanowell divided by the well's empty-nanowell template and
everything between them set to exactly 1.0 — a ratio image no Cellpose model was trained on, whose near-flat empty
nanowells Cellpose's own 1st-99th percentile scaling stretches. ``raw`` gives it the tile as imaged; ``rim`` keeps the
old input. A run's options that name none mean ``rim``, so earlier runs keep their settings and reuse keys.

Held at the boundary: what the segmenter is actually called with, in the run's own measurement and in Test
detection's crops, and the choice from the form's combo through the argv to the run's settings.
"""

from __future__ import annotations

import numpy as np
import pytest

import process_microscopy as pm
from pipeline import nanowell_run as nr
from pipeline.nanowell_preview import preview_timepoint
from tests.test_nanowell_form import _argv, _flag, tab  # noqa: F401
from tests.test_nanowell_preview import SCHEMA, WELL, plate  # noqa: F401


def _segmented_with(plate, tmp_path, segment_on):  # noqa: F811
    """Every image the segmenter is called with while Test detection measures one timepoint, and the result."""
    calls = []

    def recording(image):
        calls.append(np.array(image))
        return np.zeros(np.shape(image), dtype=np.int32)

    zip_path, _ = plate
    scratch = tmp_path / f"scratch_{segment_on}"
    scratch.mkdir()
    result = preview_timepoint(zip_path, WELL, "0h", nr.NanowellSettings("100um", segment_on=segment_on),
                               grouper=pm.find_image_groups_in_zip, parse=pm.parse_filename, schema=SCHEMA, sep="_",
                               phase_token="PHASE", scratch=scratch, segmenter=recording, n_crops=2)
    return calls, result


def _flat_share(image) -> float:
    """The share of a tile that is exactly 1.0: what rim suppression sets everything between nanowells to."""
    return float(np.mean(np.asarray(image) == 1.0))


def test_raw_gives_the_segmenter_the_tile_as_imaged_and_rim_the_rim_divided_one(plate, tmp_path):  # noqa: F811
    raw_calls, raw = _segmented_with(plate, tmp_path, "raw")
    rim_calls, rim = _segmented_with(plate, tmp_path, "rim")
    assert raw_calls and rim_calls and len(raw_calls) == len(rim_calls)
    # Rim-divided: the lattice between nanowells is flat 1.0, most of every tile. As imaged, nothing is.
    assert min(_flat_share(c) for c in rim_calls) > 0.3
    assert max(_flat_share(c) for c in raw_calls) < 0.01
    # The same tiles, so the same shapes, and the crops say which image they show.
    assert [c.shape for c in raw_calls] == [c.shape for c in rim_calls]
    assert raw.crops and {c["input"] for c in raw.crops} == {"raw"}
    assert rim.crops and {c["input"] for c in rim.crops} == {"rim"}
    # A crop is a window of the image the segmenter was given, not of the rim-divided one.
    for crop in raw.crops:
        h, w = crop["rim"].shape
        assert _flat_share(crop["rim"]) < 0.01 and crop["rim"].shape == (h, w)


def test_the_run_s_settings_name_the_input_and_an_earlier_run_s_mean_rim():
    from pipeline.cli_args import build_parser, nanowell_options_from_args
    from pipeline.nanowell_task import settings_from_kwargs

    parser = build_parser()

    def options(*extra):
        args = parser.parse_args(["--input_dir", "in", "--output_dir", "out", "--nanowell_format", "100um",
                                  "--phase_token", "PHASE", *extra])
        return nanowell_options_from_args(args, parser.error)

    new = settings_from_kwargs({"_nanowell": options()})
    assert new.segment_on == "raw" and new.key()["segment_on"] == "raw"
    for legacy in (options("--nanowell_segment_on", "rim"),
                   {k: v for k, v in options().items() if k != "segment_on"}):
        settings = settings_from_kwargs({"_nanowell": legacy})
        assert settings.segment_on == "rim" and "segment_on" not in settings.key()
    with pytest.raises(ValueError, match="segmenter input"):
        nr.NanowellSettings("100um", segment_on="ratio")


def test_the_form_sends_the_input_it_shows_and_a_sidecar_restores_it(tab):  # noqa: F811
    from analyze.nanowell_section import settings_from_opts

    tab._nanowell_cb.setChecked(True)
    combo = tab._nanowell_segment_on_cb
    assert combo.currentData() == "raw" and combo.isEnabled()
    assert _flag(_argv(tab), "--nanowell_segment_on") == "raw"
    combo.setCurrentIndex(combo.findData("rim"))
    assert _flag(_argv(tab), "--nanowell_segment_on") == "rim"          # sent: left out, the default would run
    assert settings_from_opts(tab._collect_run_spec().opts).segment_on == "rim"     # Test detection's settings
    tab._apply_nanowell_settings({"nanowell_format": "100um", "nanowell_segment_on": None})
    assert combo.currentData() == "rim"                                  # a run that did not choose
    tab._apply_nanowell_settings({"nanowell_format": "100um", "nanowell_segment_on": "raw"})
    assert combo.currentData() == "raw"
    tab._nanowell_cb.setChecked(False)
    assert not combo.isEnabled()


def test_an_earlier_nanowell_run_on_the_rim_divided_image_keeps_its_digest():
    """The option is new, so its parser default joins every nanowell run's digest; a run on ``rim`` (with cyto3) is
    the configuration every earlier run recorded, and is recognised as it. ``raw`` is not."""
    from pipeline.cli_args import build_parser
    from tests.test_a_half_finished_well_resumes_across_builds import _ARGV_9125, _DIGEST_9145
    from util.settings_digest import settings_key_aliases, settings_key_for

    nanowell = [*_ARGV_9125, "--nanowell_format", "100um", "--phase_token", "PHASE", "--nanowell_cellpose_model",
                "cyto3"]

    def aliases(*extra):
        return settings_key_aliases(settings_key_for(vars(build_parser().parse_known_args([*nanowell, *extra])[0])))

    assert _DIGEST_9145["nanowell"] in aliases("--nanowell_segment_on", "rim", "--nanowell_segment_scope", "tile")
    assert _DIGEST_9145["nanowell"] not in aliases("--nanowell_segment_on", "raw", "--nanowell_segment_scope", "tile")


def test_the_form_s_segment_choice_reaches_the_run_and_test_detection_and_a_sidecar_restores_it(tab):  # noqa: F811
    """Asked for: a control for how much of each tile the count segments (``segment_scope``), which a run took only
    from the command line's default. Each nanowell is the default; a run that named none segmented whole tiles."""
    from analyze.nanowell_section import settings_from_opts

    tab._nanowell_cb.setChecked(True)
    combo = tab._nanowell_segment_scope_cb
    assert combo.currentData() == "nanowells" and combo.isEnabled()
    assert _flag(_argv(tab), "--nanowell_segment_scope") == "nanowells"
    assert settings_from_opts(tab._collect_run_spec().opts).segment_scope == "nanowells"
    combo.setCurrentIndex(combo.findData("tile"))
    assert _flag(_argv(tab), "--nanowell_segment_scope") == "tile"
    assert settings_from_opts(tab._collect_run_spec().opts).segment_scope == "tile"   # Test detection's settings
    tab._apply_nanowell_settings({"nanowell_format": "100um", "nanowell_segment_scope": "nanowells"})
    assert combo.currentData() == "nanowells"
    tab._apply_nanowell_settings({"nanowell_format": "100um", "nanowell_segment_scope": None})
    assert combo.currentData() == "tile"                                 # a run that did not choose
    tab._nanowell_cb.setChecked(False)
    assert not combo.isEnabled()
