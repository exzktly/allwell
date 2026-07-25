"""Non-Qt export execution for the Batch Export panels (audit A14).

The render-to-file step — apply the export-style prefs, then write the figure
under a restoring ``rc_context`` — pulled out of the 1589-line
``base_panel.BatchExportPanel`` QWidget so it is a plain, unit-testable function
with no Qt / app coupling. The panels keep the UI + per-job orchestration (the
progress bar, job iteration); this owns the actual figure write, shared by the
line / bar / scatter panels.
"""

from __future__ import annotations

from pathlib import Path

# ``apply_export_style_prefs`` lives in the Qt-free core layer (``export.style``
# only re-exports it); import it from there so this module stays Qt-free and adds
# no deferred *feature* import.
from well_viewer.core.plot_style import apply_export_style_prefs


def save_export_figure(fig, fig_path, fmt: str, *, export_prefs: dict, plot_bg) -> None:
    """Apply *export_prefs* to *fig* and write it to *fig_path* as *fmt*.

    Wraps ``savefig`` in ``matplotlib.rc_context`` so a failed write can't leak a
    mutated ``svg``/``ps.fonttype`` into later exports (audit X17); the figure is
    always closed afterwards. PNG is written at 300 dpi.
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
            fig.savefig(str(Path(fig_path)), **kw)
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
    import json
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
    (out / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))
    shutil.rmtree(staging, ignore_errors=True)
    return committed
