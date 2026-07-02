"""Audit A14 — the non-Qt batch-export render-write service.

The figure-write step was a method on the 1589-line ``BatchExportPanel``
QWidget. It now lives in ``batch_export/service.save_export_figure`` — a plain
function taking a figure + resolved prefs, exercised here with no app / Qt
fixture. Also re-checks the X17 rcParams-restore invariant.
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from well_viewer.features.batch_export.service import save_export_figure  # noqa: E402


def _fig():
    fig = plt.figure()
    fig.add_subplot(111).plot([0, 1, 2], [0, 1, 4])
    return fig


def test_writes_png(tmp_path):
    out = tmp_path / "plot.png"
    save_export_figure(_fig(), out, "png", export_prefs={}, plot_bg="#000000")
    assert out.exists() and out.stat().st_size > 0


def test_writes_svg_and_restores_rcparams(tmp_path):
    before = matplotlib.rcParams["svg.fonttype"]
    out = tmp_path / "plot.svg"
    save_export_figure(_fig(), out, "svg", export_prefs={}, plot_bg="#ffffff")
    assert out.exists()
    # svg branch sets svg.fonttype='none' inside an rc_context; must be restored.
    assert matplotlib.rcParams["svg.fonttype"] == before


def test_closes_figure_even_on_write_error(tmp_path, monkeypatch):
    fig = _fig()
    num = fig.number

    def _boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(fig, "savefig", _boom)
    try:
        save_export_figure(fig, tmp_path / "x.png", "png",
                           export_prefs={}, plot_bg="#000000")
    except RuntimeError:
        pass
    # finally: plt.close(fig) ran despite the raise.
    assert not plt.fignum_exists(num)
