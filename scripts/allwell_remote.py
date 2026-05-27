#!/usr/bin/env python3
"""``allwell-remote`` — run process_microscopy.py on a remote machine.

Wraps the same argparse surface as ``process_microscopy.py`` and adds
the v5.0.0 remote-execution flags:

    --remote NAME           name of a host in ~/.config/allwell/remotes.toml
    --bring-back {full,csv-only}
                            output filter for what to materialize locally
                            (default: full — local output_dir is byte-
                            equivalent to a local run)
    --cleanup-remote {run,all,none}
                            what to delete on the remote after a verified
                            successful run (default: run — drop the per-
                            run output dir but keep the dataset cache)

Equivalence guarantee: with ``--bring-back full --cleanup-remote run``,
the local ``output_dir`` after a remote run is indistinguishable from
the result of invoking ``python process_microscopy.py …`` locally.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from process_microscopy import build_parser  # noqa: E402
from services.pipeline_service import find_pipeline_script  # noqa: E402
from services.remote_config import get_remote, load_remotes  # noqa: E402
from services.remote_runner import (  # noqa: E402
    OutputFilter,
    RemoteRunner,
    stage_local_zips,
)


def _build_argv_parser() -> argparse.ArgumentParser:
    """Augment process_microscopy's parser with the remote-execution flags."""
    parser = build_parser()
    parser.description = (
        "Run process_microscopy on a remote machine via SSH. "
        "Stages per-well zips, runs the pipeline remotely, fetches "
        "outputs back so the local output_dir is indistinguishable from "
        "a local run."
    )
    g = parser.add_argument_group("remote execution")
    g.add_argument(
        "--remote",
        metavar="NAME",
        help="Remote host name (a key under [remote.<NAME>] in remotes.toml). "
             "If omitted, allwell-remote prints the configured hosts and exits.",
    )
    g.add_argument(
        "--bring-back",
        choices=[f.value for f in OutputFilter],
        default=OutputFilter.FULL.value,
        help="What to bring back from the remote. 'full' (default) gives "
             "a local output_dir byte-equivalent to a local run; "
             "'csv-only' skips QC TIFs and adds --no_save_tophat on the remote.",
    )
    g.add_argument(
        "--cleanup-remote",
        choices=["run", "all", "none"],
        default="none",
        # v5.2.11: default changed from 'run' to 'none' so a
        # shared-machine remote never has files removed without the
        # user explicitly asking. Pass --cleanup-remote=run to restore
        # pre-v5.2.11 behaviour.
        help="After a verified successful run: 'none' (default) leaves "
             "everything in place — safe for shared remotes. 'run' removes "
             "the per-run output dir but keeps the dataset cache so re-"
             "runs are fast; 'all' also removes the dataset cache; 'none' "
             "leaves everything (for debugging).",
    )
    g.add_argument(
        "--selective-channels",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Restrict each well zip to the requested fluor/nuclear "
             "channels before upload, cutting upload by the unused-channel "
             "fraction (Phase-3 lever). On by default; pass "
             "--no-selective-channels to upload every channel image.",
    )
    g.add_argument(
        "--dry-run-cleanup",
        action="store_true",
        default=False,
        help="v5.2.23: with --cleanup-remote=run|all, log what WOULD be "
             "deleted at end-of-run (path, file count, total bytes) without "
             "actually deleting anything. Use to confirm what auto-delete "
             "would do on a shared remote before turning it on for real. "
             "Ignored when --cleanup-remote=none.",
    )
    return parser


def _list_remotes() -> int:
    remotes = load_remotes()
    if not remotes:
        print(
            "No remote hosts configured. Run scripts/bootstrap_remote.sh "
            "<ssh-alias> to set one up.",
            file=sys.stderr,
        )
        return 2
    for name, host in remotes.items():
        bw = host.measured_bandwidth_mbps or 0
        bw_str = f"{bw:.1f} Mbps" if bw > 0 else "bandwidth unknown"
        print(
            f"  {name:<20} ssh:{host.ssh_alias:<24} "
            f"python:{host.remote_python}  ({bw_str})"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_argv_parser()
    args = parser.parse_args(argv)

    if not args.remote:
        return _list_remotes()

    host = get_remote(args.remote)
    if host is None:
        print(
            f"Remote '{args.remote}' not in remotes.toml. Available:",
            file=sys.stderr,
        )
        _list_remotes()
        return 2

    pipeline_script = find_pipeline_script()
    if pipeline_script is None:
        print("process_microscopy.py not found alongside allwell-remote.", file=sys.stderr)
        return 2

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not input_dir.is_dir():
        print(f"--input_dir not a directory: {input_dir}", file=sys.stderr)
        return 2

    output_filter = OutputFilter(args.bring_back)

    def _log(msg: str) -> None:
        sys.stdout.write(msg)
        sys.stdout.flush()

    _log(f"[remote] target: {args.remote} ({host.ssh_alias})\n")

    selective = args.selective_channels
    zipped = stage_local_zips(
        input_dir,
        filename_schema=args.filename_schema,
        filename_sep=args.filename_sep,
        fluor_tokens=args.fluor_tokens if selective else None,
        nuclear_token=args.nuclear_token if selective else None,
        log_fn=_log,
    )
    _log(
        f"[remote] zipped input: {len(zipped.well_zips)} well-zips ({zipped.mode.value} mode)\n"
    )

    try:
        runner = RemoteRunner(host)
        est = runner.estimate(zipped)
        _log(f"[remote] estimate: {est.format()}\n")

        # Reconstruct the pipeline argv the way pipeline_service would
        # (already on argparse's defaults) — we feed it as-is and let
        # RemoteRunner rewrite the path args.
        from services.pipeline_service import build_pipeline_args
        opts = _args_to_opts(args)
        pipeline_argv = build_pipeline_args(pipeline_script, input_dir, output_dir, opts)

        proc = runner.spawn(
            zipped,
            output_dir,
            pipeline_argv,
            output_filter=output_filter,
            log_fn=_log,
        )
        try:
            for line in proc.stdout:
                _log(line)
            rc = proc.wait()
        except KeyboardInterrupt:
            _log("\n[remote] interrupted — cancelling.\n")
            runner.cancel(proc._ssh)  # noqa: SLF001 — explicit teardown
            proc.terminate()
            rc = 130

        runner.verify_and_cleanup(
            ok=(rc == 0),
            cleanup_mode=args.cleanup_remote,
            dry_run=args.dry_run_cleanup,
            log_fn=_log,
        )
        return rc
    finally:
        if zipped.cleanup is not None:
            zipped.cleanup()


def _args_to_opts(args: argparse.Namespace) -> dict:
    """Reshape argparse Namespace into the opts dict that
    ``build_pipeline_args`` expects (mirrors AnalyzeTab._collect_run_options)."""
    return {
        "nuclear_token": args.nuclear_token,
        "fluor_tokens": args.fluor_tokens,
        "csv_prefix": args.csv_prefix,
        "tophat_radius_nir": args.tophat_radius_nir,
        "tophat_radius_fluor": args.tophat_radius_fluor,
        "no_tophat_nir": args.no_tophat_nir,
        "no_tophat_fluor": args.no_tophat_fluor,
        "compress_input_well_folders": args.compress_input_well_folders,
        "compress_output_well_folders": args.compress_output_well_folders,
        "force": args.force,
        "cpu_only": args.cpu_only,
        "tf_threads": str(args.tf_threads),
        "workers": str(args.workers),
        "filename_schema": args.filename_schema,
        "filename_sep": args.filename_sep,
        "smfish_tokens": args.smfish_tokens,
        "segmentation_method": args.segmentation_method,
        "cytoplasm_token": args.cytoplasm_token,
        "min_nucleus_area_px": args.min_nucleus_area_px,
    }


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
