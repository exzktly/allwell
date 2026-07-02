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


def save_export_figure(fig, fig_path, fmt: str, *, export_prefs: dict, plot_bg) -> None:
    """Apply *export_prefs* to *fig* and write it to *fig_path* as *fmt*.

    Wraps ``savefig`` in ``matplotlib.rc_context`` so a failed write can't leak a
    mutated ``svg``/``ps.fonttype`` into later exports (audit X17); the figure is
    always closed afterwards. PNG is written at 300 dpi.
    """
    import matplotlib as _mpl
    import matplotlib.pyplot as _plt
    from well_viewer.features.export.style import apply_export_style_prefs

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
