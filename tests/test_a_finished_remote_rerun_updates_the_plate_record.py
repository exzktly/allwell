"""A remote re-run that finds every well finished here still records the run's plate-level settings.

Review tracker H2. Two settings change nothing any well wrote, so a well made under another value of them is still
this run's and is not sent again: the focus cut a run states (``--focus_min``) and whether the end-of-run
cell-gating pass is on (``--no_auto_threshold``). A local re-run of a finished plate skips every well and then
rewrites the sidecar and runs that pass. The remote path ended at "Nothing to upload" having launched no pipeline
anywhere, so the sidecar went on stating the previous run's cut and held no thresholds.

Measured on a real two-well plate before the fix: the local re-run recorded ``focus_min`` 0.3 and thresholds
``{gfp: 48.1, nir: 0.0}``; the same re-run through ``PipelineRunner._run_pipeline_remote`` left ``focus_min`` 0.0,
``no_auto_threshold`` True and no thresholds, and reported the plate complete.

These drive the real ``_run_pipeline_remote`` with the real staging (the harness of
``test_a_remote_rerun_skips_finished_wells``); the host is a stub, and no test here reaches it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_a_remote_rerun_skips_finished_wells import _opts, _plate, _record, _run

pytest.importorskip("PySide6")

#: The run that made the plate: no focus cut stated, the cell-gating pass switched off.
MADE_UNDER = dict(auto_threshold=False, measure_focus=True)


def _finished_plate(tmp_path: Path, **sidecar) -> tuple[Path, Path]:
    """Both wells finished under ``MADE_UNDER``, beside the sidecar that run left (plus *sidecar*'s blocks)."""
    inp, out = _plate(tmp_path)
    for well in ("A01", "B02"):
        _record(out, well, _opts(**MADE_UNDER))
    info = {"qc_options": {"focus_min": 0.0, "measure_focus": True},
            "execution_options": {"focus_min": 0.0, "no_auto_threshold": True, "workers": 3},
            "notes": "kept", **sidecar}
    (out / "pipeline_info.json").write_text(json.dumps(info), encoding="utf-8")
    return inp, out


def _info(out: Path) -> dict:
    return json.loads((out / "pipeline_info.json").read_text(encoding="utf-8"))


def test_a_cut_stated_and_the_pass_switched_on_are_recorded_though_nothing_is_sent(tmp_path):
    inp, out = _finished_plate(tmp_path)
    sent, lines = _run(inp, out, _opts(auto_threshold=True, measure_focus=True, focus_min="0.3"))
    assert sent is None                                               # neither setting sends a well again
    assert any("Nothing to upload" in ln for ln in lines)
    info = _info(out)
    assert info["qc_options"]["focus_min"] == 0.3
    assert (info["execution_options"]["focus_min"], info["execution_options"]["no_auto_threshold"]) == (0.3, False)
    # The pass ran here over the plate: the segmentation channel's unconditional zero is what it always writes.
    assert info["cell_gating"]["thresh_frac_on"]["dapi"] == 0.0
    # What the record did not own is as it was.
    assert info["notes"] == "kept" and info["execution_options"]["workers"] == 3
    assert info["qc_options"]["measure_focus"] is True
    said = [ln for ln in lines if "plate-level settings recorded" in ln]
    assert len(said) == 1 and "focus cut 0 -> 0.3" in said[0] and "cell-gating pass off -> on" in said[0]


def test_the_pass_switched_off_is_recorded_and_not_run_and_stored_thresholds_stay(tmp_path, monkeypatch):
    import services.pipeline_runner as pr

    ran: list = []
    monkeypatch.setattr(pr, "run_local_auto_threshold", lambda out, argv, log: ran.append(list(argv)))
    inp, out = _finished_plate(
        tmp_path, qc_options={"focus_min": 0.5},
        execution_options={"focus_min": 0.5, "no_auto_threshold": False},
        cell_gating={"thresh_frac_on": {"gfp": 12.0}})
    sent, lines = _run(inp, out, _opts(**MADE_UNDER))
    assert sent is None and ran == []                                 # the user's choice stands
    info = _info(out)
    assert info["qc_options"]["focus_min"] == 0.0                     # a cut withdrawn is recorded as none
    assert info["execution_options"]["no_auto_threshold"] is True
    assert info["cell_gating"] == {"thresh_frac_on": {"gfp": 12.0}}
    assert any("focus cut 0.5 -> 0" in ln and "cell-gating pass on -> off" in ln for ln in lines)


def test_an_unchanged_re_run_rewrites_nothing_and_still_gates_the_plate(tmp_path, monkeypatch):
    import services.pipeline_runner as pr

    ran: list = []
    monkeypatch.setattr(pr, "run_local_auto_threshold", lambda out, argv, log: ran.append((out, list(argv))))
    inp, out = _finished_plate(tmp_path, execution_options={"focus_min": 0.0, "no_auto_threshold": False})
    before = (out / "pipeline_info.json").read_bytes()
    sent, lines = _run(inp, out, _opts(auto_threshold=True, measure_focus=True))
    assert sent is None
    assert (out / "pipeline_info.json").read_bytes() == before
    assert not any("plate-level settings recorded" in ln for ln in lines)
    # As a local re-run does on every run, with this run's own argv and never a host's suppression of the pass.
    assert len(ran) == 1 and ran[0][0] == out and "--no_auto_threshold" not in ran[0][1]


def test_a_plate_with_no_sidecar_says_the_settings_were_not_recorded(tmp_path, monkeypatch):
    import services.pipeline_runner as pr

    monkeypatch.setattr(pr, "run_local_auto_threshold", lambda out, argv, log: None)
    inp, out = _finished_plate(tmp_path)
    (out / "pipeline_info.json").unlink()
    sent, lines = _run(inp, out, _opts(auto_threshold=True, measure_focus=True, focus_min="0.3"))
    assert sent is None
    assert any("plate-level settings were not recorded" in ln and "pipeline_info.json" in ln for ln in lines)
    assert not (out / "pipeline_info.json").exists()


@pytest.mark.parametrize("option, changed", [
    ("focus_min", dict(MADE_UNDER, focus_min="0.3")),
    ("no_auto_threshold", dict(MADE_UNDER, auto_threshold=True)),
])
def test_each_plate_setting_changed_alone_leaves_every_well_finished(tmp_path, monkeypatch, option, changed):
    """The roster against the thing it is a roster of: an option belongs in ``PLATE_SETTINGS`` only while changing
    it sends no well again. One that does send them is recorded by the host's own run instead."""
    import services.pipeline_runner as pr
    from services.pipeline_service import build_pipeline_args
    from services.remote_plate_record import PLATE_SETTINGS, plate_settings_from_argv

    assert sorted(PLATE_SETTINGS) == ["focus_min", "no_auto_threshold"]
    monkeypatch.setattr(pr, "run_local_auto_threshold", lambda out, argv, log: None)
    inp, out = _finished_plate(tmp_path)
    script = Path("process_microscopy.py")
    was = plate_settings_from_argv(build_pipeline_args(script, inp, out, _opts(**MADE_UNDER)))
    now = plate_settings_from_argv(build_pipeline_args(script, inp, out, _opts(**changed)))
    assert [k for k in PLATE_SETTINGS if was[k] != now[k]] == [option]   # the fixture changes this one and no other
    sent, _lines = _run(inp, out, _opts(**changed))
    assert sent is None
