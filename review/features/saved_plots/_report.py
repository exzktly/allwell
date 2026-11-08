"""Assemble a multi-page PDF report from saved plots.

Pure builder (no Qt, no app) so it's unit-testable: each saved plot becomes one
full-page figure — its captured high-res snapshot embedded large, with a header
(title · originating tab · timestamp) and the parameters that produced it as a
caption — preceded by a cover page. Uses matplotlib's ``PdfPages`` (no extra
deps); vector text over the embedded raster image.
"""

from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path
from collections.abc import Callable, Iterable

from ui import theme

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


class _Flow:
    """Text laid down a page from the top, starting a new page when it runs out.

    The cover and the history are lists of headings, label/value rows and
    paragraphs whose length depends on the dataset, so they are flowed rather
    than placed: a long history or a long list of plots that could not be drawn
    continues on the next page instead of running off the bottom of this one.
    """

    _LINE = 0.0165          # one 9 pt line, as a fraction of the page height
    _LEFT = _MARGIN / _PAGE_W
    _VALUE_X = _LEFT + 0.27  # where a row's value starts
    _WRAP = 100              # characters across the full text width
    _VALUE_WRAP = 72         # characters across the value column

    def __init__(self, pdf, header: str) -> None:
        self.pdf = pdf
        self.header = header
        self.fig = None
        self.y = 0.0

    def _page(self) -> None:
        from matplotlib.figure import Figure

        self.close()
        self.fig = Figure(figsize=(_PAGE_W, _PAGE_H), facecolor=theme.CPub.bg)
        self.fig.text(self._LEFT, 0.955, self.header, ha="left", va="top",
                      fontsize=9, color=theme.CPub.text_muted)
        self.y = 0.925

    def _room(self, lines: int) -> None:
        if self.fig is None or self.y - lines * self._LINE < _MARGIN / _PAGE_H:
            self._page()

    def title(self, text: str, sub: str = "") -> None:
        self._room(4)
        self.fig.text(self._LEFT, self.y, text, ha="left", va="top", fontsize=22,
                      fontweight="bold", color=theme.CPub.text)
        self.y -= 0.045
        if sub:
            self.fig.text(self._LEFT, self.y, sub, ha="left", va="top", fontsize=10,
                          color=theme.CPub.text_muted)
            self.y -= 0.03

    def heading(self, text: str) -> None:
        self._room(4)
        self.y -= 0.008
        self.fig.text(self._LEFT, self.y, text, ha="left", va="top", fontsize=12,
                      fontweight="bold", color=theme.CPub.text)
        self.y -= 0.026

    def row(self, label: str, value: str) -> None:
        lines = textwrap.wrap(str(value), self._VALUE_WRAP) or [""]
        self._room(len(lines))
        self.fig.text(self._LEFT, self.y, label, ha="left", va="top", fontsize=9,
                      color=theme.CPub.text_muted)
        self.fig.text(self._VALUE_X, self.y, "\n".join(lines), ha="left", va="top",
                      fontsize=9, color=theme.CPub.text, linespacing=1.35)
        self.y -= self._LINE * len(lines) + 0.004

    def paragraph(self, text: str, *, bold: bool = False, indent: str = "") -> None:
        lines = textwrap.wrap(str(text), self._WRAP, initial_indent=indent,
                              subsequent_indent=" " * len(indent)) or [""]
        self._room(len(lines))
        self.fig.text(self._LEFT, self.y, "\n".join(lines), ha="left", va="top",
                      fontsize=9, color=theme.CPub.text, linespacing=1.35,
                      fontweight="bold" if bold else "normal")
        self.y -= self._LINE * len(lines) + 0.004

    def close(self) -> None:
        if self.fig is not None:
            self.pdf.savefig(self.fig)
            self.fig = None


def _rich_cover(pdf, cover, *, count: int) -> None:
    """The cover a report with a :class:`~._cover.ReportCover` opens with.

    Problems first, because a reader who stops after the first screen must not
    leave thinking the plate was whole; then what the data is, how it was made,
    what was edited, and what the report itself did; then the history.
    """
    from ._cover import MAX_HISTORY_ROWS_PRINTED

    flow = _Flow(pdf, f"{cover.dataset_name} · Saved Plots Report")
    version = f" · All-Well {cover.app_version}" if cover.app_version else ""
    flow.title("Saved Plots Report",
               f"{cover.dataset_name} · generated {cover.generated_at} "
               f"({cover.timezone}){version}")
    flow.row("Dataset folder", cover.dataset_path)
    flow.row("Plots in this PDF", str(count))
    if cover.warnings:
        flow.heading("Needs attention")
        for text in cover.warnings:
            flow.paragraph(text, indent="- ")
    for name, rows in (("Dataset", cover.dataset), ("Pipeline settings", cover.settings),
                       ("Last run", cover.last_run), ("Analysis state", cover.analysis),
                       ("Report", cover.report)):
        if not rows:
            continue
        flow.heading(name)
        for label, value in rows:
            flow.row(label, value)
    flow.heading("History")
    flow.paragraph(cover.history_status)
    shown = cover.history[:MAX_HISTORY_ROWS_PRINTED]
    for item in shown:
        version = f"  [v{item.app_version}]" if item.app_version else ""
        flow.row(f"{item.when or 'time unknown'}  {item.source}", item.what + version)
    if len(cover.history) > len(shown):
        flow.paragraph(f"{len(cover.history) - len(shown)} earlier row(s) are in "
                       f"manifest.json and the dataset's provenance.jsonl.")
    flow.close()


def _plot_page(pdf, rec, *, image_path: Path | None, summary: str,
               since_saved: str = "") -> None:
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

    # What changed after the plot was saved, under the header: this page was
    # redrawn from the dataset as it is now, which may not be what was saved.
    img_top = 0.90
    if since_saved:
        lines = textwrap.wrap(since_saved, 110)
        fig.text(_MARGIN / _PAGE_W, 0.912, "\n".join(lines), ha="left", va="top",
                 fontsize=8, fontweight="bold", color=theme.CPub.text,
                 linespacing=1.35)
        img_top = 0.905 - 0.0145 * len(lines)

    # Caption block height scales with the summary's line count.
    n_lines = (summary.count("\n") + 1) if summary else 0
    cap_h = min(0.28, 0.03 + 0.022 * n_lines) if n_lines else 0.0

    # Image axis fills the page between the header and the caption.
    img_bottom = _MARGIN / _PAGE_H + cap_h
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
        from .state import undrawn_note
        ax.text(0.5, 0.5, undrawn_note(rec) or "(image unavailable)",
                ha="center", va="center", wrap=True,
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
    cover=None,
) -> int:
    """Write a multi-page PDF report to *out_path*. Returns the number of plot
    pages written (excludes the cover). Raises on I/O errors.

    *cover*, a :class:`~._cover.ReportCover`, replaces the one-line title page
    with the dataset's provenance, settings and completeness, and puts each
    plot's "changed since saved" sentence on its page. The headless report
    passes one; the gallery's own Export menu does not.
    """
    from matplotlib.backends.backend_pdf import PdfPages

    recs = list(records)
    summary_for = summary_for or (lambda r: getattr(r, "summary", "") or "")

    with PdfPages(str(out_path), metadata={
        "Title": "Saved Plots Report",
        "Creator": "AllWell",
    }) as pdf:
        if cover is None:
            _cover_page(pdf, dataset_name=dataset_name, count=len(recs))
        else:
            _rich_cover(pdf, cover, count=len(recs))
        since = getattr(cover, "since_saved", None) or {}
        for rec in recs:
            _plot_page(pdf, rec, image_path=image_path_for(rec),
                       summary=summary_for(rec),
                       since_saved=since.get(getattr(rec, "id", ""), ""))
    return len(recs)
