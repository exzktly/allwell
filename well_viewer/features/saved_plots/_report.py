"""Assemble a multi-page PDF report from saved plots.

Pure builder (no Qt, no app) so it's unit-testable: each saved plot becomes one
full-page figure — its captured high-res snapshot embedded large, with a header
(title · originating tab · timestamp) and the parameters that produced it as a
caption — preceded by a cover page. Uses matplotlib's ``PdfPages`` (no extra
deps); vector text over the embedded raster image.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from collections.abc import Callable, Iterable

import theme

# Letter page in inches.
_PAGE_W, _PAGE_H = 8.5, 11.0
_MARGIN = 0.6


def _cover_page(pdf, *, dataset_name: str, count: int) -> None:
    from matplotlib.figure import Figure

    fig = Figure(figsize=(_PAGE_W, _PAGE_H), facecolor=theme.CPub.bg)
    fig.text(0.5, 0.70, "Saved Plots Report", ha="center", va="center",
             fontsize=28, fontweight="bold", color=theme.CPub.text)
    sub = []
    if dataset_name:
        sub.append(f"Dataset: {dataset_name}")
    sub.append(f"{count} saved plot{'' if count == 1 else 's'}")
    sub.append("Generated " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    fig.text(0.5, 0.55, "\n".join(sub), ha="center", va="top", fontsize=13,
             color=theme.CPub.text, linespacing=1.6)
    pdf.savefig(fig)


def _plot_page(pdf, rec, *, image_path: Path | None, summary: str) -> None:
    import matplotlib.image as mpimg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(_PAGE_W, _PAGE_H), facecolor=theme.CPub.bg)

    # Header: title (left) + tab badge (right), then timestamp.
    title = getattr(rec, "title", "") or getattr(rec, "tab", "") or "Saved plot"
    fig.text(_MARGIN / _PAGE_W, 0.955, title, ha="left", va="top",
             fontsize=16, fontweight="bold", color=theme.CPub.text)
    tab = getattr(rec, "tab", "") or ""
    if tab:
        fig.text(1 - _MARGIN / _PAGE_W, 0.955, tab, ha="right", va="top",
                 fontsize=11, color=theme.CPub.text_muted)
    created = getattr(rec, "created_at", "") or ""
    if created:
        fig.text(_MARGIN / _PAGE_W, 0.930, created.replace("T", "  "),
                 ha="left", va="top", fontsize=9, color=theme.CPub.text_muted)

    # Caption block height scales with the summary's line count.
    n_lines = (summary.count("\n") + 1) if summary else 0
    cap_h = min(0.28, 0.03 + 0.022 * n_lines) if n_lines else 0.0

    # Image axis fills the page between the header and the caption.
    img_bottom = _MARGIN / _PAGE_H + cap_h
    img_top = 0.90
    ax = fig.add_axes([_MARGIN / _PAGE_W, img_bottom,
                       1 - 2 * _MARGIN / _PAGE_W, img_top - img_bottom])
    ax.axis("off")
    drawn = False
    if image_path is not None and Path(image_path).exists():
        try:
            ax.imshow(mpimg.imread(str(image_path)))
            drawn = True
        except Exception:
            drawn = False
    if not drawn:
        ax.text(0.5, 0.5, "(image unavailable)", ha="center", va="center",
                fontsize=11, color=theme.CPub.text_faint, transform=ax.transAxes)

    if summary:
        fig.text(_MARGIN / _PAGE_W, _MARGIN / _PAGE_H + cap_h - 0.012, summary,
                 ha="left", va="top", fontsize=8, family="monospace",
                 color=theme.CPub.text, linespacing=1.4)

    pdf.savefig(fig)


def build_saved_plots_report(
    records: Iterable,
    image_path_for: Callable[[object], Path | None],
    out_path: str | Path,
    *,
    dataset_name: str = "",
    summary_for: Callable[[object], str] | None = None,
) -> int:
    """Write a multi-page PDF report to *out_path*. Returns the number of plot
    pages written (excludes the cover). Raises on I/O errors."""
    from matplotlib.backends.backend_pdf import PdfPages

    recs = list(records)
    summary_for = summary_for or (lambda r: getattr(r, "summary", "") or "")

    with PdfPages(str(out_path), metadata={
        "Title": "Saved Plots Report",
        "Creator": "AllWell",
    }) as pdf:
        _cover_page(pdf, dataset_name=dataset_name, count=len(recs))
        for rec in recs:
            _plot_page(pdf, rec, image_path=image_path_for(rec),
                       summary=summary_for(rec))
    return len(recs)
