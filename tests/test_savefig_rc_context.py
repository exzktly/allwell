"""Audit X17 — figure export must not leak mutated rcParams.

The save paths temporarily set ``svg.fonttype`` / ``ps.fonttype`` for the
target format. They used a hand-rolled save/restore; a ``savefig`` that raised
between the mutation and the restore could leak the changed rcParam into every
later plot. They now use ``matplotlib.rc_context``, which restores on exit even
when the block raises.
"""
from __future__ import annotations

import types

import matplotlib
import matplotlib.pyplot as plt

from well_viewer.core import plot_orchestrator as po


def test_save_restores_rcparams_when_savefig_raises(monkeypatch):
    # Force the SVG branch (mutates svg.fonttype) and a failing savefig.
    monkeypatch.setattr(po.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("/tmp/out.svg", "svg")))
    monkeypatch.setattr(po.QMessageBox, "critical",
                        staticmethod(lambda *a, **k: None))

    # A valid non-"none" value; the SVG branch flips it to "none" inside the
    # context, so a leak would leave it "none" rather than restored to "path".
    sentinel = "path"
    matplotlib.rcParams["svg.fonttype"] = sentinel

    fig = plt.figure()
    def _boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(fig, "savefig", _boom)

    app = types.SimpleNamespace(
        _set_status=lambda *_a, **_k: None,
        _FIG_FILETYPES=[("SVG", "*.svg")],
        _app_state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(data_dir=None)),
    )
    po.save_matplotlib_fig(app, fig, "plot", plot_bg="#000000")

    # rc_context must have restored the pre-call value despite the raise.
    assert matplotlib.rcParams["svg.fonttype"] == sentinel
    plt.close(fig)


def test_save_restores_rcparams_on_success(monkeypatch, tmp_path):
    out = tmp_path / "ok.svg"
    monkeypatch.setattr(po.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "svg")))

    before = matplotlib.rcParams["svg.fonttype"]
    fig = plt.figure()
    fig.add_subplot(111).plot([0, 1], [0, 1])
    app = types.SimpleNamespace(
        _set_status=lambda *_a, **_k: None,
        _FIG_FILETYPES=[("SVG", "*.svg")],
        _app_state=types.SimpleNamespace(
            dataset=types.SimpleNamespace(data_dir=None)),
    )
    po.save_matplotlib_fig(app, fig, "plot", plot_bg="#000000")

    assert out.exists()
    assert matplotlib.rcParams["svg.fonttype"] == before
    plt.close(fig)
