"""Saved Plots store — records + on-disk persistence.

A saved plot is a captured panel image plus the exact parameters/thresholds
that produced it. Metadata (everything but the image bytes) lives in the
consolidated ``persistence.json`` under the ``"saved_plots"`` section (via
``persistence._doc``); the bytes live alongside it in
``<data_dir>/saved_plots/`` — the raster snapshot as ``<id>.png``, and the same
render in vector as ``<id>.svg``. Both are keyed to the current dataset, so the
gallery travels with the data and survives restarts.

The vector twin is what the PowerPoint export draws from, and it is optional by
design rather than by accident: a record written before the app captured vector
has no ``.svg``, and one whose vector render failed has none either. Neither is
a broken record — both are a plot that exports as a picture instead of as
editable shapes, which the deck builder states on the slide. ``svg_filename`` is
empty in exactly those cases, so "has a vector capture" is one field to read
rather than a guess from a filename.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from well_viewer.persistence import _doc
from well_viewer.core.safe import swallowed

_logger = logging.getLogger("well_viewer")

_SECTION = "saved_plots"
_IMAGES_DIRNAME = "saved_plots"


@dataclass
class SavedPlot:
    """One saved plot: the captured panel image + the params that made it."""

    id: str
    created_at: str            # ISO-8601 timestamp
    tab: str                   # originating leaf tab (e.g. "Bar Plots")
    title: str                 # user-facing title (panel label or tab name)
    png_filename: str          # basename under the images dir
    params: dict               # view_state.snapshot(app) — drives Restore
    svg_filename: str = ""     # vector twin of the PNG; "" when none was captured
    thresholds: dict = field(default_factory=dict)
    summary: str = ""          # human-readable parameter summary

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SavedPlot:
        return cls(
            id=str(d.get("id", "")),
            created_at=str(d.get("created_at", "")),
            tab=str(d.get("tab", "")),
            title=str(d.get("title", "")),
            png_filename=str(d.get("png_filename", "")),
            svg_filename=str(d.get("svg_filename", "") or ""),
            params=dict(d.get("params", {}) or {}),
            thresholds=dict(d.get("thresholds", {}) or {}),
            summary=str(d.get("summary", "")),
        )


class SavedPlotsStore:
    """In-memory list of :class:`SavedPlot`, backed by the dataset's
    ``persistence.json`` section + a sibling images directory."""

    def __init__(self) -> None:
        self.records: list[SavedPlot] = []
        self._loaded_dir: Path | None = None

    # ── disk paths ────────────────────────────────────────────────────────
    @staticmethod
    def _data_dir(ctx) -> Path | None:
        d = getattr(ctx.state.dataset, "data_dir", None)
        return Path(d) if d else None

    @classmethod
    def images_dir(cls, ctx) -> Path | None:
        d = cls._data_dir(ctx)
        return (d / _IMAGES_DIRNAME) if d is not None else None

    def image_path(self, ctx, rec: SavedPlot) -> Path | None:
        idir = self.images_dir(ctx)
        return (idir / rec.png_filename) if idir is not None else None

    def vector_path(self, ctx, rec: SavedPlot) -> Path | None:
        """Where this record's vector capture lives, or ``None`` if it has none.

        ``None`` for a record with no ``svg_filename`` — not a path that happens
        not to exist. The two are different facts (a plot saved without vector
        versus a file that went missing), and only the first is normal.
        """
        idir = self.images_dir(ctx)
        if idir is None or not rec.svg_filename:
            return None
        return idir / rec.svg_filename

    # ── load / persist ────────────────────────────────────────────────────
    def load(self, ctx) -> None:
        """(Re)load records for the current dataset from persistence.json."""
        self.records = []
        self._loaded_dir = self._data_dir(ctx)
        # ``persistence._doc`` reads/writes ``persistence.json`` off the dataset
        # state (ctx-native; it unwraps the app internally).
        section = _doc.get_section(ctx, _SECTION)
        if isinstance(section, list):
            for entry in section:
                if isinstance(entry, dict):
                    self.records.append(SavedPlot.from_dict(entry))

    def ensure_loaded(self, ctx) -> None:
        """Load lazily, and reload when the dataset directory changed."""
        if self._loaded_dir != self._data_dir(ctx):
            self.load(ctx)

    def _persist(self, ctx) -> None:
        _doc.set_section(ctx, _SECTION, [r.to_dict() for r in self.records])

    # ── mutations ─────────────────────────────────────────────────────────
    def add(self, ctx, rec: SavedPlot, png_bytes: bytes,
            svg_bytes: bytes = b"") -> bool:
        """Write the captured bytes and append *rec*.

        The PNG is the record: failing to write it fails the save. The vector
        twin is an enhancement, so a failure to write *it* clears
        ``svg_filename`` and lets the save succeed — the plot is in the gallery
        and exports as a picture, which beats losing the save outright. Clearing
        the field rather than leaving it set is the point: a name pointing at a
        file that was never written would make the deck builder report "no
        vector capture" for a record that claims to have one.

        Returns False when there's no dataset directory to persist into.
        """
        idir = self.images_dir(ctx)
        if idir is None:
            return False
        try:
            idir.mkdir(parents=True, exist_ok=True)
            (idir / rec.png_filename).write_bytes(png_bytes or b"")
        except OSError as exc:
            _logger.warning("saved_plots: failed to write image: %s", exc)
            return False
        if svg_bytes and rec.svg_filename:
            try:
                (idir / rec.svg_filename).write_bytes(svg_bytes)
            except OSError as exc:
                _logger.warning(
                    "saved_plots: failed to write vector capture: %s", exc)
                rec.svg_filename = ""
        else:
            rec.svg_filename = ""
        self.records.append(rec)
        self._persist(ctx)
        return True

    def _discard_files(self, ctx, rec: SavedPlot) -> None:
        """Remove every file a record owns — the snapshot and its vector twin.

        Both, not just the PNG: a delete that took the raster and left the SVG
        behind would leave the dataset folder accumulating vector captures for
        plots the gallery no longer lists, with nothing left to name them.
        """
        for path in (self.image_path(ctx, rec), self.vector_path(ctx, rec)):
            if path is not None and path.exists():
                try:
                    path.unlink()
                except OSError:
                    swallowed()  # best-effort; metadata removal still wins

    def delete(self, ctx, plot_id: str) -> None:
        keep: list[SavedPlot] = []
        for r in self.records:
            if r.id == plot_id:
                self._discard_files(ctx, r)
            else:
                keep.append(r)
        self.records = keep
        self._persist(ctx)

    def rename(self, ctx, plot_id: str, new_title: str) -> None:
        rec = self.get(plot_id)
        if rec is None:
            return
        rec.title = str(new_title)
        self._persist(ctx)

    def move(self, ctx, plot_id: str, up: bool) -> bool:
        """Reorder a saved plot one slot within the *displayed* gallery
        (newest-first = ``reversed(records)``). ``up`` moves it toward the top.

        Returns True when a swap happened (so callers can skip a needless
        repaint at the list ends)."""
        disp = list(reversed(self.records))
        idx = next((i for i, r in enumerate(disp) if r.id == plot_id), None)
        if idx is None:
            return False
        swap = idx - 1 if up else idx + 1
        if swap < 0 or swap >= len(disp):
            return False
        disp[idx], disp[swap] = disp[swap], disp[idx]
        self.records = list(reversed(disp))
        self._persist(ctx)
        return True

    def clear(self, ctx) -> None:
        for r in list(self.records):
            self._discard_files(ctx, r)
        self.records = []
        self._persist(ctx)

    def get(self, plot_id: str) -> SavedPlot | None:
        return next((r for r in self.records if r.id == plot_id), None)


def get_store(ctx) -> SavedPlotsStore:
    """The app-wide store, created on first use.

    Lives in the feature's runtime namespace (``ctx.runtime.saved_plots.store``),
    which stamps/reads the legacy ``app._saved_plots_store`` attribute."""
    store = ctx.runtime.saved_plots.store
    if store is None:
        store = SavedPlotsStore()
        ctx.runtime.saved_plots.store = store
    return store
