"""Saved Plots store — records + on-disk persistence.

A saved plot is a captured panel image plus the exact parameters/thresholds
that produced it. Metadata (everything but the PNG bytes) lives in the
consolidated ``persistence.json`` under the ``"saved_plots"`` section (via
``persistence._doc``); the PNG bytes live alongside it in
``<data_dir>/saved_plots/<id>.png``. Both are keyed to the current dataset, so
the gallery travels with the data and survives restarts.
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
    def add(self, ctx, rec: SavedPlot, png_bytes: bytes) -> bool:
        """Write the PNG and append *rec*. Returns False when there's no
        dataset directory to persist into."""
        idir = self.images_dir(ctx)
        if idir is None:
            return False
        try:
            idir.mkdir(parents=True, exist_ok=True)
            (idir / rec.png_filename).write_bytes(png_bytes or b"")
        except OSError as exc:
            _logger.warning("saved_plots: failed to write image: %s", exc)
            return False
        self.records.append(rec)
        self._persist(ctx)
        return True

    def delete(self, ctx, plot_id: str) -> None:
        keep: list[SavedPlot] = []
        for r in self.records:
            if r.id == plot_id:
                p = self.image_path(ctx, r)
                if p is not None and p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        swallowed()  # best-effort; metadata removal still wins
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
            p = self.image_path(ctx, r)
            if p is not None and p.exists():
                try:
                    p.unlink()
                except OSError:
                    swallowed()
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
