"""The one list of files a remote run needs on the worker.

Lifted out of ``services/remote_runner`` because it is a *data* declaration
with no behaviour, and because the two guards over it were in direct tension:
``tests/test_remote_manifest`` requires an entry for every new ``pipeline/`` or
``util/`` module, while ``tests/test_monolith_budgets`` caps
``remote_runner.py``'s length — so adding a required entry could fail the
budget, and the budget could only be met by not adding it. Here, a new entry
costs the runner nothing.

``remote_runner`` re-exports ``_REQUIRED_REMOTE_FILES``, so every existing
importer — ``services/remote_bootstrap``, the PyInstaller spec, the manifest tests
and the several ``util/`` and ``pipeline/`` docstrings that cite it by that
path — keeps working unchanged. A re-export is a binding, though: a test that
patches the name on ``remote_runner`` will not be seen by a reader that
imports it from here (§6.4).
"""

from __future__ import annotations

# Files this framework needs on the remote in order to run the pipeline.
# Kept minimal on purpose: process_microscopy.py plus its local-module
# imports, plus the requirements list (so the bootstrap script knows what
# pip-installs to do). Paths are relative to the repo root.
#
# Synced before every run by :meth:`RemoteRunner.spawn` so a user who
# edits any of these files locally doesn't end up running stale code on
# the remote — the per-run cost is a few hundred KB and a quick rsync
# stat-pass when nothing changed.
_REQUIRED_REMOTE_FILES: tuple[str, ...] = (
    "process_microscopy.py",
    # General Qt-free utilities (well-token parsing, logged-swallow) imported by
    # process_microscopy at module top level, so the remote needs the package.
    "util/__init__.py",
    "util/aw_safe.py",
    # One `os.scandir` in place of a `stat` per directory entry. Client-side
    # only — its importers are the Analyze input scan and the remote runner's
    # own staging, both of which run here — but the manifest must list every
    # ``util/`` file so the completeness guard (test_remote_manifest) passes.
    "util/dir_scan.py",
    # The retry-with-backoff shield behind every pipeline write that lands on
    # the output volume (pipeline.io imports it at module scope).
    # Rebinds sys.stdout / sys.stderr when a frozen windowed build nulls them;
    # imported by process_microscopy at module load (and by well_plate_zipper). A
    # remote run never hits the frozen-windowed case, but the manifest must list
    # every ``util/`` file so the completeness guard (test_remote_manifest) passes.
    # Reduces a metadata label to a filename token; all three converters name
    # their images through it.
    "util/filename_token.py",
    "util/frozen_io.py",
    "util/retry_io.py",
    # The one atomic JSON write. ``pipeline/run_report`` and
    # ``process_microscopy`` both replace ``pipeline_info.json`` through it on
    # the remote, so the remote genuinely needs this one.
    "util/json_write.py",
    # Serpentine plate-position labelling for packed OME-TIFF input. The
    # de-interleave runs client-side (before any remote sync), so a remote run
    # never imports this — but the manifest must list every ``util/`` file so
    # the completeness guard (test_remote_manifest) passes.
    "util/plate_grid.py",
    "util/schema.py",
    # FOV-token numeric ordering, imported by process_microscopy
    # (``_collect_available_fovs``) so the remote run needs it.
    "util/fov.py",
    # Channel-name → role heuristic (converter + Analyze). Client-side only, but
    # the manifest mirrors every ``util/`` file.
    "util/channel_roles.py",
    # Filename-schema inference for the Analyze input scan; client-side only.
    "util/schema_infer.py",
    "util/timepoint.py",
    # The one string naming the configuration a well's output was produced
    # under, so a re-run with different settings is not answered "already
    # done". Both the worker host and the client compute it, and they have to
    # agree, so the module has to be the same one on both sides.
    "util/settings_digest.py",
    "util/well_token.py",
    # Free-space primitives + the single wording for a volume that filled. The
    # retry shield below rewrites an ENOSPC through it, so a remote run that
    # runs its output volume dry says which volume and how much is left.
    "util/disk_space.py",
    # "Would the pipeline group this file as input?" — one set, read by
    # process_microscopy's scanners and by this module's upload planner.
    "util/image_exts.py",
    # Atomic + verified publication of the per-well archives, imported by
    # process_microscopy and pipeline.io — without it the remote run aborts
    # before it compresses its first well.
    "util/zip_write.py",
    # The Qt-free ``pipeline`` package: analysis-domain modules (background math,
    # auto-threshold, flat-field, shape-feature catalog) + the Cellpose/StarDist
    # segmenters and atomic writers. process_microscopy re-imports it at module
    # top level (Torch/TensorFlow stay lazy), so the remote needs the whole
    # package or the run aborts with ModuleNotFoundError.
    "pipeline/__init__.py",
    "pipeline/auto_threshold.py",
    # The post-run pass over that sampler: which estimator this run's
    # arguments call for, and the merge into pipeline_info.json.
    # process_microscopy re-exports it and calls it at the end of every
    # run, remote included.
    "pipeline/auto_threshold_pass.py",
    "pipeline/background.py",
    "pipeline/capacity.py",
    # The pipeline's argparse declaration. The remote runs process_microscopy,
    # which imports it at module scope, so a missing file here aborts the run
    # with ModuleNotFoundError before it reads a single image.
    "pipeline/cli_args.py",
    "pipeline/cellpose.py",
    # The one definition of "is this well complete", shared by the end-of-run
    # check and the Datasets Validate tool.
    "pipeline/completeness.py",
    # The blank-well sampler and the two model builders behind
    # --flatfield/--background empty_well. The remote genuinely runs these:
    # a batched run reads its blank out of --background_dir on the worker.
    "pipeline/empty_well.py",
    # Per-field image-quality metrics, computed inside the quantification
    # pass, so the remote worker genuinely needs it.
    "pipeline/field_quality.py",
    # Its stdlib-only half: the per-well record, the collected block and
    # the merge the remote fetch performs. Imported by the remote client
    # itself, which is why it may not reach the imaging stack.
    "pipeline/field_quality_record.py",
    # The pretrained-weights cache and its cross-process download lock.
    # process_microscopy re-exports these; the remote worker loads the
    # model through them on every run.
    "pipeline/stardist_cache.py",
    "pipeline/flatfield.py",
    # Per-FOV resume checkpoints, keyed on the output directory and the run's
    # settings so one plate cannot resume another's intermediates.
    "pipeline/fov_checkpoint.py",
    # Which of the three input layouts a plate is in; process_microscopy
    # imports it at module scope.
    "pipeline/input_layout.py",
    "pipeline/incremental_keys.py",
    "pipeline/well_skip.py",
    "pipeline/io.py",
    # Packed OME-TIFF de-interleave. Runs client-side before remote sync (the
    # remote receives ordinary loose TIFs), but listed so the manifest stays a
    # complete mirror of ``pipeline/`` per test_remote_manifest.
    # The Zeiss CZI reader and its per-well explode. Like ``ome_explode``
    # both run client-side — the remote receives per-well archives that are
    # already converted — but the manifest mirrors every ``pipeline/`` file,
    # so they are listed rather than made exceptions nobody can see.
    "pipeline/czi_read.py",
    "pipeline/czi_set.py",
    "pipeline/czi_explode.py",
    # How a plate's files are read at once — across worker processes, because
    # reading one is a Python parser rather than a wait. ``czi_set`` imports
    # it at module scope, so it travels with it whether or not a remote run
    # ever scans a plate.
    "pipeline/scan_pool.py",
    # The Nikon ND2 adapter. Unlike the CZI reader this is a thin layer over
    # the `nd2` package rather than a parser; like the other two it runs
    # client-side, and is listed because the manifest mirrors every
    # ``pipeline/`` file rather than carrying exceptions nobody can see.
    "pipeline/nd2_read.py",
    "pipeline/nd2_explode.py",
    # What the exploders share: the member-name rule, the resume check, the
    # worker split, the timepoint plan and the in-memory TIFF encode.
    "pipeline/explode_common.py",
    # The elapsed-hour naming, which ``explode_common`` re-exports. Its own
    # module because it is the only part of that one a header scan needs, and
    # ``explode_common`` imports numpy and tifffile — which a spawned scan
    # worker would then pay for once per process.
    "pipeline/timepoints.py",
    # Where a plate's parsed headers are remembered between sessions, so a
    # folder reopened costs no parsing. Client-side like the converters it
    # serves, and listed because the manifest mirrors every ``pipeline/`` file
    # rather than carrying exceptions nobody can see.
    "pipeline/scan_cache.py",
    "pipeline/ome_explode.py",
    # The loose-TIF grouping pre-pass, spawned as a subprocess. Like
    # ``ome_explode`` it runs client-side — the remote is sent per-well archives
    # that are already grouped — but the manifest mirrors every ``pipeline/``
    # file, so it is listed rather than made an exception nobody can see.
    "pipeline/well_plate_zipper.py",
    "pipeline/run_report.py",
    # Where the settings digest is recorded beside each well's output, and
    # the reuse decision that reads it back — consulted by both skip
    # predicates, so a remote run needs it as much as a local one.
    "pipeline/settings_record.py",
    # The sidecar blocks the user owns (cell_gating / sample_definitions /
    # ratios / notes), shared by write_pipeline_info and the end-of-run fetch.
    "pipeline/sidecar.py",
    "pipeline/shape_features.py",
    "pipeline/stardist.py",
    # Cooperative wind-down: the remote pipeline catches SIGTERM and finishes
    # the FOV-groups in flight instead of dying, so a suspended remote run
    # leaves the same resumable output a local one does.
    "pipeline/suspend.py",
    "pipeline/threshold_optimize.py",
    # v5.2.1: headless-only requirements (no PySide6/pyobjc/pyinstaller
    # — the remote doesn't run the GUI). See requirements.txt for
    # the GUI superset.
    "requirements-remote.txt",
)
