"""Review's one door into the Analyze pane: open Analyze on the loaded dataset, scoped to named wells.

Home's *Finish this run*, QC triage's *Re-run wells* and the Nanowells page's Array view (*Re-run anchored
wells*) all go through :func:`open_in_analyze`, so more than one feature uses it and it lives here rather than in
any one of them. :func:`dataset_root` is the folder Datasets tools and Analyze want, one level above the ``out/``
folder Review loads.
"""

from __future__ import annotations

from pathlib import Path


def open_in_analyze(ctx, *, force_wells, only_wells, incremental: bool = True) -> bool:
    """Open Analyze on this dataset, incremental, scoped to the named wells.

    The one Review-side door into Analyze. Home's *Finish this run* goes
    through it with the record's own answer to which wells need work; QC
    triage's *Re-run wells* goes through it with the wells the user picked,
    both forced and limited, so nothing else on the plate is touched.
    *incremental* off is for a caller whose wells are all forced anyway — QC triage's *Re-run wells* and the
    Nanowells Array view's *Re-run anchored wells* — so a remote nanowell run, which refuses Incremental, is not refused for a setting it does not need.
    Returns whether the hand-off happened — ``False`` in a build that mounts no
    Analyze pane, or for a folder that is not a dataset Analyze can open.
    """
    shell = ctx.window.window()
    handoff = getattr(shell, "_open_dataset_in_analyze", None)
    if not callable(handoff):
        return False
    root = dataset_root(ctx)
    if not root:
        return False
    handoff(
        Path(root),
        dict(getattr(ctx.state.dataset, "pipeline_info", None) or {}),
        incremental=incremental,
        force_wells=list(force_wells),
        only_wells=list(only_wells),
    )
    return True


def dataset_root(ctx) -> str:
    """The folder a Datasets tool wants, which is not the one Review loaded.

    Review's ``data_dir`` is where the measurements CSVs are, and for the layout
    the pipeline writes that is the ``out/`` subfolder — Analyze's own loader
    descends into it (``load_controller.load_path``). Every Datasets tool wants
    the folder *above* it, the one holding both ``in/`` and ``out/``, because
    that is what a dataset is to them (``datasets.core.dataset_paths``).
    Handing them ``out/`` fails their check and leaves the user re-picking a
    folder the app already had open.

    ``in_dir`` is the honest signal: the loader sets it precisely when it
    detected that layout, and leaves it ``None`` for a flat folder of CSVs,
    which has no parent to prefer and is not a dataset those tools can read
    anyway.
    """
    dataset = getattr(ctx.state, "dataset", None)
    in_dir = getattr(dataset, "in_dir", None)
    if in_dir is not None:
        return str(Path(in_dir).parent)
    data_dir = getattr(dataset, "data_dir", None)
    return str(data_dir) if data_dir else ""
