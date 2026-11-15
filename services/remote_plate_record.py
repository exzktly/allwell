"""The plate-level half of a re-run, for a remote run that found every well already finished here.

A run's settings are of two kinds. Most change a well's own output, are in the settings key
(``util.settings_digest.KEYED_ON``) and send the well again when they change. A few change nothing any well wrote
and are only *recorded* for the plate, in ``pipeline_info.json``: the focus cut a run states (``--focus_min``, which
Review's Cell Gating applies) and whether the end-of-run cell-gating pass is on (``--no_auto_threshold``, which
``services.remote_finished_wells`` accepts either way for the same reason). A local re-run of a finished plate skips
every well and still rewrites the sidecar and runs that pass, so changing one of them costs seconds. A remote
re-run that finds nothing to send launches no pipeline, here or on the host, so nothing would write them — and the
run would end "Nothing to upload" over a sidecar still stating the settings of the run before.

:func:`record_plate_settings` writes those options into the sidecar from the run's own argv; the caller
(``PipelineRunner._nothing_left_to_upload``) then runs the cell-gating pass here over the whole plate when it is on
(``services.remote_process.run_local_auto_threshold``).

``PLATE_SETTINGS`` is the roster, and ``tests/test_a_finished_remote_rerun_updates_the_plate_record.py`` holds it
against the settings key: each entry, changed alone, must send no well again. An option that does send wells is
recorded by the host's own run and has no place here.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from util.json_write import try_write_json_atomic

#: Parser destination -> why a finished well is still this run's when it changes, and so why nothing but this
#: module records the change on a remote re-run.
PLATE_SETTINGS: dict[str, str] = {
    "focus_min": "recorded in the sidecar as the plate's stated cut; the run never acts on it",
    "no_auto_threshold": "switches only the end-of-run cell-gating pass, which reads the wells and changes none",
}

SIDECAR = "pipeline_info.json"


def plate_settings_from_argv(pipeline_argv: Sequence[str]) -> dict | None:
    """``PLATE_SETTINGS``' values as the pipeline would parse them from *pipeline_argv* (the local argv, launcher
    and all), or ``None`` when it does not parse."""
    from pipeline.cli_args import build_parser
    from services.remote_finished_wells import _flags

    try:
        args = build_parser().parse_known_args(_flags(pipeline_argv))[0]
    except SystemExit:
        return None
    return {"focus_min": float(args.focus_min or 0.0), "no_auto_threshold": bool(args.no_auto_threshold)}


def record_plate_settings(output_dir: Path, pipeline_argv: Sequence[str], log) -> dict[str, tuple]:
    """Write the run's plate-level settings into *output_dir*'s sidecar, as a local re-run's rewrite of it does.

    Returns ``{option: (recorded before, recorded now)}`` for each one that changed, and says each in one *log*
    line. Empty when nothing differed. A sidecar that is missing or unreadable, or an argv that does not parse,
    records nothing and says so: the plate then still states the earlier run's settings, which must not pass in
    silence."""
    info_path = Path(output_dir) / SIDECAR
    wanted = plate_settings_from_argv(pipeline_argv)
    try:
        info = json.loads(info_path.read_text(encoding="utf-8")) if wanted is not None else None
    except (OSError, ValueError) as exc:
        log(f"[remote] WARN this run's plate-level settings were not recorded: {info_path} could not be read "
            f"({exc}).\n")
        return {}
    if not isinstance(info, dict):
        log("[remote] WARN this run's plate-level settings were not recorded: "
            + ("its arguments could not be read.\n" if wanted is None else f"{info_path} is not a sidecar.\n"))
        return {}
    exec_opts = dict(info["execution_options"]) if isinstance(info.get("execution_options"), dict) else {}
    qc = dict(info["qc_options"]) if isinstance(info.get("qc_options"), dict) else {}
    # Absent reads as the parser's default, so a sidecar written before an option existed is not "changed" by it.
    before = {"focus_min": float(qc.get("focus_min") or 0.0),
              "no_auto_threshold": bool(exec_opts.get("no_auto_threshold"))}
    changed = {name: (before[name], wanted[name]) for name in PLATE_SETTINGS if before[name] != wanted[name]}
    if not changed:
        return {}
    qc["focus_min"] = wanted["focus_min"]
    exec_opts.update(wanted)
    info["qc_options"], info["execution_options"] = qc, exec_opts
    if not try_write_json_atomic(info_path, info):
        log(f"[remote] WARN this run's plate-level settings were not recorded: {info_path} could not be written.\n")
        return {}
    said = {"focus_min": lambda was, now: f"focus cut {was:g} -> {now:g}",
            "no_auto_threshold": lambda was, now: f"cell-gating pass {'off' if was else 'on'} -> "
                                                  f"{'off' if now else 'on'}"}
    log("[remote] plate-level settings recorded for the wells already here (no well is recomputed for them): "
        + "; ".join(said[name](*pair) for name, pair in changed.items()) + "\n")
    return changed
