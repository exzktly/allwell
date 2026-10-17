"""A well left part-way by an earlier build continues where it stopped; it is not started again.

The report: a batched remote run on 9.12.5 was to be stopped and resumed on the current build, and every well it
had part-processed would have started from its first field. Two things were in the way, both decidable here.

* **The settings key.** The pipeline names a well's checkpoint directory after its settings digest and refuses a
  checkpoint recorded under another. The digest's definition changed after 9.12.5 — the nanowell options stopped
  counting outside nanowell mode — so the same options hash differently and every 9.12.5 checkpoint read as
  another configuration's. ``util.settings_digest.settings_key_for`` now carries the digest an earlier build would
  have recorded beside the current one, and a checkpoint or well recorded under it is accepted.
* **The output directory** is the other half of that directory's name; the remote half is
  ``services.remote_batch_resume.reattach_output_dir``, tested in ``test_a_batched_restart_reuses_the_remote``.

The legacy digest is pinned to the value 9.12.5's own code computes for the same arguments (run against a worktree
of that release), so a change to a parser default that breaks the match fails here rather than on a user's plate.
"""

from __future__ import annotations

import json
from pathlib import Path

import process_microscopy as pm
from pipeline.cli_args import build_parser
from pipeline.fov_checkpoint import _output_stamp, _save_fov_checkpoint, _settings_stamp
from pipeline.io import scratch_base_for
from pipeline.field_quality_record import FOCUS_COLUMN
from pipeline.settings_record import SETTINGS_DIRNAME, record_well_settings, well_settings_allow_reuse
from tests.test_a_resumed_run_keeps_its_field_quality import (  # noqa: F401 - the fixture is used by name
    SCHEMA, _plate, _run_main, stubbed_segmenter,
)
from util.settings_digest import legacy_settings_digests, settings_digest, settings_key_for

#: What 9.12.5's own ``settings_digest`` returns for ``_ARGV_9125`` (computed against a worktree of that release).
_ARGV_9125 = ["--input_dir", "/in", "--output_dir", "/out", "--nuclear_token", "DAPI", "--fluor_tokens", "GFP",
              "--csv_prefix", "measurements", "--filename_schema", "experiment:well:fov:timepoint:channel",
              "--filename_sep", "_", "--workers", "6"]
_DIGEST_9125 = "9672fd59f699c9c7"


def _opts(argv):
    return vars(build_parser().parse_known_args(argv)[0])


def test_the_legacy_digest_is_the_one_9_12_5_recorded():
    opts = _opts(_ARGV_9125)
    assert settings_digest(opts) != _DIGEST_9125, "the fixture must reproduce the definition change"
    assert legacy_settings_digests(opts) == (_DIGEST_9125,)
    assert settings_key_for(opts).split("~") == [settings_digest(opts), _DIGEST_9125]


def test_a_well_recorded_by_the_earlier_build_is_not_rerun(tmp_path):
    opts = _opts(_ARGV_9125)
    record_well_settings(tmp_path, "C01", _DIGEST_9125)
    assert well_settings_allow_reuse(tmp_path, "C01", settings_key_for(opts))
    assert not well_settings_allow_reuse(tmp_path, "C01", settings_digest(opts)), (
        "characterising the defect: without the alias the earlier build's well reads as other settings")
    record_well_settings(tmp_path, "C02", settings_key_for(opts))
    assert json.loads((tmp_path / SETTINGS_DIRNAME / "C02.json").read_text())["digest"] == settings_digest(opts), (
        "only the current digest is ever written")


def test_a_field_the_earlier_build_checkpointed_is_not_computed_again(tmp_path, monkeypatch, stubbed_segmenter):
    plate = _plate(tmp_path)
    out = plate / "out"
    out.mkdir()
    argv = ["--input_dir", str(plate / "in"), "--output_dir", str(out), "--nuclear_token", "DAPI",
            "--fluor_tokens", "GFP", "--filename_schema", ":".join(SCHEMA), "--workers", "1",
            "--background", "none", "--flatfield", "none", "--no_auto_threshold", "--no_run_check"]
    (legacy,) = legacy_settings_digests(_opts(argv))
    # What a 9.12.5 run killed after C01's first field leaves on its scratch volume: a staging directory named for
    # its own digest, holding F001's records under that digest.
    staging = scratch_base_for(out) / f"_tmp_images_{_output_stamp(out)}_{_settings_stamp(legacy)}_C01"
    staging.mkdir(parents=True)
    # Shaped as the stub's real rows are (`stubbed_segmenter`), with a value no fresh computation produces.
    restored = {"well": "C01", "fov": "F001", "timepoint": "0h", "nucleus_id": 1, "area": 777.0, FOCUS_COLUMN: 0.3}
    _save_fov_checkpoint(staging, "exp_DAPI_C01_F001_0h", [restored], legacy, quality={})

    # Counted at the quantification stub, which `_run_main` leaves alone (it replaces `process_image_group`, so a
    # counter there would count nothing — the first draft of this test did exactly that and passed vacuously).
    processed: list[str] = []
    stub = pm._quantify_image_group

    def counting(**kwargs):
        processed.append(Path(kwargs["nuclear_path"]).name)
        return stub(**kwargs)

    monkeypatch.setattr(pm, "_quantify_image_group", counting)
    _run_main(monkeypatch, plate, incremental=False)
    assert len(processed) == 2, f"expected F002 and F003 to be computed, got {processed}"

    assert not any("F001" in name for name in processed), (
        f"the field the earlier build had finished was computed again: {processed}")
    from util.csv_gz import existing_csv, open_csv_text
    with open_csv_text(existing_csv(out / "measurements_C01.csv")) as fh:
        rows = fh.read()
    assert "777.0" in rows, "the restored field's rows are not in the well's CSV"
