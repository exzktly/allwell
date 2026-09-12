"""Non-Qt export execution for the Batch Export panels (audit A14).

The render-to-file step — apply the export-style prefs, then write the figure
under a restoring ``rc_context`` — pulled out of the 1589-line
``base_panel.BatchExportPanel`` QWidget so it is a plain, unit-testable function
with no Qt / app coupling. The panels keep the UI + per-job orchestration (the
progress bar, job iteration); this owns the actual figure write, shared by the
line / bar / scatter panels.
"""

from __future__ import annotations

from util.json_write import write_json_atomic
from pathlib import Path

# ``apply_export_style_prefs`` lives in the Qt-free core layer (``export.style``
# only re-exports it); import it from there so this module stays Qt-free and adds
# no deferred *feature* import.
from review.core.plot_style import apply_export_style_prefs


def save_export_figure(fig, fig_path, fmt: str, *, export_prefs: dict, plot_bg,
                       on_saved=None) -> None:
    """Apply *export_prefs* to *fig* and write it to *fig_path* as *fmt*.

    **A *fig_path* of ``None`` styles the figure and skips the write.** That is
    a run whose destinations are the gallery, a deck, or both, and which was
    never asked for a folder of image files. The styling still has to happen —
    it is what the captured figure carries — and so does *on_saved*, which is
    where those destinations take their own copy. What is skipped is one
    ``savefig`` into a directory the user did not ask to be filled.

    Wraps ``savefig`` in ``matplotlib.rc_context`` so a failed write can't leak a
    mutated ``svg``/``ps.fonttype`` into later exports (audit X17); the figure is
    always closed afterwards. PNG is written at 300 dpi.

    *on_saved* is called with the figure once the file is on disk and before the
    figure is closed — the only window in which a second render of the *same*
    styled figure can be taken. That is what the extra destinations (the Saved
    Plots gallery, the PowerPoint deck) capture from, so what they hold is the
    picture the user's file holds rather than a second figure that resembles it.

    **After the write, deliberately.** A callback that ran first and raised
    would cost the user the file they actually asked for, to serve a destination
    they merely also ticked. Its exceptions still propagate: a caller that wants
    them contained contains them, and this function is not the place to decide
    that a capture failure does not matter.
    """
    import matplotlib as _mpl
    import matplotlib.pyplot as _plt

    apply_export_style_prefs(fig, export_prefs)
    overrides: dict = {}
    if fmt == "svg":
        overrides["svg.fonttype"] = "none"
    elif fmt == "eps":
        overrides["ps.fonttype"] = 42
    try:
        kw = dict(bbox_inches="tight", facecolor=plot_bg, format=fmt)
        if fmt == "png":
            kw["dpi"] = 300
        with _mpl.rc_context(overrides):
            if fig_path is not None:
                fig.savefig(str(Path(fig_path)), **kw)
        if on_saved is not None:
            on_saved(fig)
    finally:
        _plt.close(fig)


# ── atomic staging (audit X3) ────────────────────────────────────────────────
#
# Batch export used to write each group's CSV + figure straight into the user's
# output directory. A re-run that failed at group K had already overwritten the
# prior *complete* export for groups 1..K-1, with no record of what was written.
# These helpers let the panels stage every write into a temp subdir and commit
# atomically only when the whole run succeeds — a failed run leaves the previous
# export untouched.

MANIFEST_NAME = "batch_export_manifest.json"


def new_staging_dir(out_dir) -> Path:
    """Create and return a fresh staging subdir *inside* ``out_dir``.

    Placing it under ``out_dir`` guarantees the commit's ``os.replace`` calls
    stay on one filesystem (so they're atomic renames, not cross-device copies).
    """
    import tempfile

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=".batch_export_tmp_", dir=str(out)))


def discard_staging(staging) -> None:
    """Remove a staging dir and everything in it (used on failure/abort)."""
    import shutil

    shutil.rmtree(Path(staging), ignore_errors=True)


def commit_staged_export(staging, out_dir, *, manifest_extra: dict | None = None) -> list[str]:
    """Move every staged file into ``out_dir`` and write a manifest.

    Called only after a fully-successful run. Each staged file is ``os.replace``d
    over its final path (an atomic per-file rename on the same filesystem), then
    a ``batch_export_manifest.json`` records exactly what this run produced, and
    the (now empty) staging dir is removed. Returns the committed file names.
    """
    import os
    import shutil

    staging = Path(staging)
    out = Path(out_dir)
    committed: list[str] = []
    for src in sorted(staging.iterdir()):
        if src.is_dir():
            continue
        os.replace(str(src), str(out / src.name))
        committed.append(src.name)
    manifest = {"files": committed, "count": len(committed)}
    if manifest_extra:
        manifest.update(manifest_extra)
    # ``durable=False``: an index of files that are themselves already on
    # disk beside it, written at the end of an export the user is watching.
    write_json_atomic(out / MANIFEST_NAME, manifest, durable=False)
    shutil.rmtree(staging, ignore_errors=True)
    return committed
