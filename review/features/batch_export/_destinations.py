"""Where a batch-exported figure goes besides its own file.

A batch run writes one figure and one CSV per export group into an output
folder, and that is the whole of what it produced: to get those figures into
the Saved Plots gallery, or into a slide deck, the user had to open each one
and re-make it by hand on a plotting tab. This module is the other two
destinations — the gallery, and a PowerPoint deck of editable shapes — filled
from the same render the file write already performs.

**Both destinations are additive.** Neither replaces the per-group file: a run
with both switched on writes its PNGs, its CSVs, a ``.pptx`` beside them and a
gallery record per figure. Turning them on changes what a run *also* produces,
never what it produces instead, so a user who ticks the wrong box loses
nothing.

**Nothing reaches either destination until the run commits.** The file writes
are staged and committed atomically (``service.commit_staged_export``) so a run
that fails at group K leaves the previous complete export untouched; a gallery
that had filled with K-1 records by then would be the same defect wearing
different clothes — half a run, presented as a run. So the captures are staged
in a directory of this spool's own, and the gallery records are filed only
after the export's own commit has succeeded. The deck is staged *inside* the
export's staging directory instead, because it is an ordinary output file and
belongs under the same manifest as the figures it is built from.

**A figure with no vector capture is placed as a picture and says so**, which
is the rule the Saved Plots deck already follows — see
``features/saved_plots/_deck.py``. This module never fabricates the missing
half: a capture that could not be rendered is counted and reported rather than
substituted, and :meth:`DestinationSpool.shortfall` is what the panel puts in
front of the user.

The heavy lifting is deliberately not re-implemented here. The gallery record
is written by ``saved_plots.controller.save_figure_to_gallery`` and the deck by
``saved_plots._deck.build_saved_plots_deck`` — the same two functions the
gallery's own Export menu calls, reached through ``core.lazy`` proxies because
Saved Plots is a removable feature and Batch Export must import without it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from review.core.lazy import lazy as _lazy

_logger = logging.getLogger("review")

#: Saved Plots is one-line removable, so every symbol this module borrows from
#: it is a proxy resolved on first call rather than an ``import`` statement. A
#: build without the feature never ticks either checkbox (the panel hides them),
#: so these are never resolved there.
_save_figure_to_gallery = _lazy("review.features.saved_plots.controller",
                                "save_figure_to_gallery")
_refresh_gallery = _lazy("review.features.saved_plots.controller",
                         "refresh_saved_plots_gallery")
_build_deck = _lazy("review.features.saved_plots._deck",
                    "build_saved_plots_deck")
_build_summary = _lazy("review.features.saved_plots.collect",
                       "build_summary")
_get_store = _lazy("review.features.saved_plots.state", "get_store")

#: The subdirectory of the spool's own temp dir the captures live in. Its own
#: directory rather than the export's staging dir because the export's is
#: deleted by ``commit_staged_export`` — and the gallery is filled *after* that
#: commit, precisely so a run that never committed files nothing.
_CAPTURE_DIRNAME = "captures"


@dataclass
class _Captured:
    """One rendered batch figure, staged on disk for the destinations.

    Duck-types the ``SavedPlot`` fields the deck builder reads — ``id``,
    ``title``, ``tab``, ``created_at`` — so ``build_saved_plots_deck`` can walk
    these directly instead of needing a gallery record to exist first. A run
    that asks for a deck and not for the gallery therefore writes no gallery
    record at all, rather than writing forty and deleting them again.
    """

    id: str
    title: str
    tab: str
    created_at: str
    png_path: Path
    #: The vector twin, or ``None`` when it could not be rendered. ``None``
    #: rather than a path that happens not to exist: "this figure has no vector
    #: capture" and "this file went missing" are different facts, and the deck
    #: builder says something different about each.
    svg_path: Path | None
    params: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    summary: str = ""


class DestinationSpool:
    """The extra destinations one batch run was asked for.

    Built once per run, before the first job, from the panel's two checkboxes.
    A spool nobody asked for (:attr:`wanted` false) does nothing at every step,
    so the panels call it unconditionally rather than guarding each call.
    """

    def __init__(self, *, to_gallery: bool, to_deck: bool, tab: str,
                 mode_key: str, dataset_name: str = "") -> None:
        self.to_gallery = bool(to_gallery)
        self.to_deck = bool(to_deck)
        self.tab = str(tab or "")
        self.mode_key = str(mode_key or "batch")
        self.dataset_name = str(dataset_name or "")
        self._dir: Path | None = None
        self._captures: list[_Captured] = []
        #: Figures whose capture raised. Counted rather than silently dropped:
        #: a deck of nine slides for a run that exported ten groups is a
        #: shortfall the user cannot see from the deck.
        self.capture_failures: int = 0
        #: Figures that captured a raster but no vector. They still reach both
        #: destinations — as a picture — and the deck's own slide says so.
        self.raster_only: int = 0
        #: How many figures were staged. A counter beside ``_captures`` rather
        #: than ``len(_captures)``, because the staged captures are discarded
        #: before the run reports — and a report derived from a list that has
        #: just been emptied says a run produced nothing. That cost a status
        #: line that named no deck, and it is what this counter is for.
        self.captured: int = 0
        #: Set once the deck is on disk, for the same reason.
        self.deck_written: bool = False
        #: Set when the deck could not be written at all.
        self.deck_error: str = ""
        #: Set when a committed run left an **earlier** run's deck for this
        #: mode sitting in the output folder. A stale figure is named for its
        #: group, so a group this run did not export is visible in the file
        #: name; a deck is named for the mode, so a stale one has exactly the
        #: name a fresh one would and reads as this run's output. It is not
        #: deleted — nothing here removes a file the user may want — it is
        #: named.
        self.stale_deck: bool = False
        #: Set when filing the gallery records failed outright.
        self.gallery_error: str = ""
        #: Records that were offered to the gallery and did not land — a
        #: read-only or full ``saved_plots/`` directory is the usual cause.
        #: Counted separately from ``gallery_error``, which is the whole step
        #: failing: a run can lose one record and file nine, and reporting
        #: nothing then is the shortfall the destinations exist not to hide.
        self.gallery_failures: int = 0
        #: Figures whose parameters could not be captured, filed as a picture
        #: with nothing to restore. Counted rather than left to look like an
        #: ordinary record: in the gallery it is indistinguishable from one
        #: whose Restore works, right up until somebody presses it.
        self.undescribed: int = 0
        self.filed: int = 0

    @property
    def wanted(self) -> bool:
        """Whether this run has any destination beyond its own files."""
        return self.to_gallery or self.to_deck

    @property
    def deck_name(self) -> str:
        """The deck's file name, which names the mode that produced it.

        One name per mode rather than one for the whole feature: a user who
        exports the bar figures and then the line figures into one folder wants
        two decks, and a shared name would leave them with the second one only,
        silently.
        """
        return f"batch_export_{self.mode_key}.pptx"

    # ── lifecycle ────────────────────────────────────────────────────────
    def begin(self, stage_under) -> None:
        """Open the spool's own staging directory under *stage_under*.

        On the same filesystem as whatever the captures are eventually copied
        *to*, which is the point of taking the directory as an argument: a run
        writing files is handed the output folder, and a gallery-only run — one
        that must not create that folder at all — is handed the dataset folder,
        which is where ``saved_plots/`` lives. A temp dir on another filesystem
        would turn either copy into a cross-device one for every figure in the
        run.
        """
        if not self.wanted:
            return
        import tempfile

        out = Path(stage_under)
        out.mkdir(parents=True, exist_ok=True)
        self._dir = Path(tempfile.mkdtemp(prefix=".batch_dest_tmp_", dir=str(out)))
        (self._dir / _CAPTURE_DIRNAME).mkdir(parents=True, exist_ok=True)

    def discard(self) -> None:
        """Remove everything staged. Safe to call twice, and always called.

        The staged *files* and the list that names them, not the counters: the
        run reports after this has run, and a report that counted the list
        would say a committed run had produced nothing.
        """
        import shutil

        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
        self._dir = None
        self._captures = []

    # ── capture ──────────────────────────────────────────────────────────
    def capture(self, fig, *, title: str, params: dict | None = None,
                thresholds: dict | None = None) -> None:
        """Stage *fig* as a PNG and (where it renders) an SVG.

        Called after the figure's own file has been written and before it is
        closed, so the bytes staged here are the same render the user's PNG
        holds — a second render from the same figure, not a second figure.

        A failure to render is counted, never raised: the file this run was
        asked for is already on disk, and losing it to a gallery capture would
        be the destination the user did not ask about breaking the one they
        did. What it must not do is pass unmentioned, which is
        :attr:`shortfall`.
        """
        if not self.wanted or self._dir is None:
            return
        from datetime import datetime

        index = len(self._captures)
        stem = f"{index:04d}"
        base = self._dir / _CAPTURE_DIRNAME
        png_path = base / f"{stem}.png"
        svg_path = base / f"{stem}.svg"
        facecolor = fig.get_facecolor()
        try:
            fig.savefig(str(png_path), format="png", bbox_inches="tight",
                        dpi=300, facecolor=facecolor)
        except Exception:
            self.capture_failures += 1
            _logger.exception(
                "Batch export: could not capture %r for the extra destinations",
                title)
            return
        svg_written: Path | None = None
        try:
            from widgets.fig_clipboard import render_figure_svg

            svg_bytes = render_figure_svg(fig, bbox_inches="tight",
                                          facecolor=facecolor)
            if svg_bytes:
                svg_path.write_bytes(svg_bytes)
                svg_written = svg_path
        except Exception:
            _logger.exception(
                "Batch export: vector capture of %r failed; it will be placed "
                "as a picture", title)
        if svg_written is None:
            self.raster_only += 1
        self.captured += 1
        self._captures.append(_Captured(
            id=f"{self.mode_key}-{stem}",
            title=str(title or ""),
            tab=self.tab,
            created_at=datetime.now().isoformat(timespec="seconds"),
            png_path=png_path,
            svg_path=svg_written,
            params=dict(params or {}),
            thresholds=dict(thresholds or {}),
        ))

    # ── delivery ─────────────────────────────────────────────────────────
    def write_deck(self, staging) -> None:
        """Build the PowerPoint deck into the export's *staging* directory.

        Into the staging directory, not the output folder: the deck is one of
        the run's output files and is committed with the rest of them, so a run
        that fails after the deck is built leaves no deck behind either.
        """
        if not self.to_deck or not self._captures:
            return
        try:
            path = Path(staging) / self.deck_name
            _build_deck(
                self._captures,
                lambda rec: rec.svg_path,
                lambda rec: rec.png_path,
                path,
                dataset_name=self.dataset_name,
                summary_for=self._summary_for,
            )
            self.deck_written = True
        except Exception as exc:
            self.deck_error = str(exc)
            _logger.exception("Batch export: PowerPoint deck failed")

    def note_stale_deck(self, out_dir) -> None:
        """Notice a previous run's deck left beside this run's output.

        Called after a commit. Only where this run did **not** write one: a run
        that did has already replaced it.
        """
        if self.to_deck:
            return
        try:
            self.stale_deck = (Path(out_dir) / self.deck_name).exists()
        except OSError:
            _logger.warning("Batch export: could not check for an earlier deck")

    def file_in_gallery(self, ctx) -> None:
        """Add one Saved Plots record per captured figure.

        Called only after the export's own commit has succeeded, so the gallery
        and the output folder describe the same run.

        The whole batch is written with ``persist=False`` and one
        :meth:`SavedPlotsStore.persist` at the end: ``persistence.json`` also
        holds the view state and Batch Export's own group definitions, and
        rewriting all of it once per figure is forty read/write cycles of a file
        this is not the only writer of.
        """
        if not self.to_gallery or not self._captures:
            return
        try:
            store = _get_store(ctx)
            store.ensure_loaded(ctx)
            for cap in self._captures:
                png = _read_bytes(cap.png_path)
                if not png:
                    self.capture_failures += 1
                    continue
                # Counted from the store, not from the call returning. The
                # gallery's own writer reports a failed image write by
                # *returning a status string* — it does not raise and it does
                # not append a record — so incrementing here unconditionally
                # made a run whose ``saved_plots/`` directory was read-only
                # report "10 to Saved Plots" over an empty gallery, with
                # nothing anywhere saying otherwise. The record landing is the
                # fact worth asserting, and it is the one the store holds.
                before = len(store.records)
                _save_figure_to_gallery(
                    ctx, tab=cap.tab, title=cap.title,
                    png_bytes=png, svg_bytes=_read_bytes(cap.svg_path),
                    params=cap.params, thresholds=cap.thresholds,
                    persist=False, refresh=False,
                )
                if len(store.records) > before:
                    self.filed += 1
                else:
                    self.gallery_failures += 1
            store.persist(ctx)
            _refresh_gallery(ctx)
        except Exception as exc:
            self.gallery_error = str(exc)
            _logger.exception("Batch export: filing figures in Saved Plots failed")

    # ── reporting ────────────────────────────────────────────────────────
    def summary(self) -> str:
        """What the destinations produced, for the run's status line.

        ``""`` when nothing extra was asked for, so a caller can append it
        unconditionally.
        """
        parts: list[str] = []
        if self.to_gallery and self.filed:
            parts.append(f"{self.filed} to Saved Plots")
        if self.deck_written:
            parts.append(self.deck_name)
        return " · ".join(parts)

    def shortfall(self) -> str:
        """What the destinations could **not** produce. ``""`` when nothing.

        Separate from :meth:`summary` and never folded into it: a deck of nine
        slides for a run of ten groups looks exactly like a deck of ten unless
        something says otherwise, and the deck itself cannot — the tenth slide
        is not there to carry the note.
        """
        parts: list[str] = []
        # Before the ``wanted`` gate, deliberately: the run this matters most
        # to is the one that ticked nothing, came back to a folder it had
        # exported to before, and left the earlier deck standing over new
        # figures.
        if self.stale_deck:
            parts.append(f"{self.deck_name} is from an earlier run — this one "
                         f"did not write a deck")
        if not self.wanted:
            return " · ".join(parts)
        if self.capture_failures:
            parts.append(f"{self.capture_failures} figure(s) could not be "
                         f"captured for the extra destinations")
        if self.gallery_failures:
            parts.append(f"{self.gallery_failures} could not be written to "
                         f"Saved Plots")
        if self.undescribed:
            parts.append(f"{self.undescribed} filed without their settings, so "
                         f"Restore will not reproduce them")
        # Gated on the deck existing, not on it having been asked for: a run
        # that errored discards its staging before the deck is ever built, and
        # a status line reporting how many figures are pictures *in a deck
        # nobody wrote* describes an artifact that is not there.
        if self.raster_only and self.deck_written:
            parts.append(f"{self.raster_only} placed as a picture rather than "
                         f"editable shapes")
        if self.deck_error:
            parts.append(f"PowerPoint deck failed: {self.deck_error}")
        if self.gallery_error:
            parts.append(f"Saved Plots failed: {self.gallery_error}")
        return " · ".join(parts)

    # ── internals ────────────────────────────────────────────────────────
    def _summary_for(self, cap: _Captured) -> str:
        """The parameter blurb under a deck slide's figure.

        Through Saved Plots' own ``build_summary`` so a batch figure's slide
        reads exactly like the slide the same figure would get if the user had
        saved it from its tab — two vocabularies for one set of parameters is
        how a reader ends up believing they describe two different things. A
        capture with no parameters has no blurb, which is honest: it is a figure
        the run could not describe rather than one with nothing to say.
        """
        if cap.summary:
            return cap.summary
        if not cap.params and not cap.thresholds:
            return ""
        try:
            cap.summary = _build_summary(cap.tab, cap.params, cap.thresholds)
        except Exception:
            _logger.exception("Batch export: parameter summary failed")
            cap.summary = ""
        return cap.summary


def _read_bytes(path) -> bytes:
    """The file's bytes, or ``b""`` for a path that is ``None`` or unreadable."""
    if path is None:
        return b""
    try:
        return Path(path).read_bytes()
    except OSError:
        _logger.warning("Batch export: could not read staged capture %s", path)
        return b""
