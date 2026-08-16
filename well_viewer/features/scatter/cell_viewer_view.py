"""Single-cell image viewer for the per-cell Scatter tab.

The ``ScatterCellViewer`` widget (a right-dock slide-out that loads, crops, and
LUT-adjusts a clicked cell's channel images) and the ``open_scatter_cell_viewer``
factory that mounts it. Split out of ``scatter/callbacks.py`` (audit A7) so the
click/hover handlers there stay focused on canvas events.
"""

from __future__ import annotations

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from well_viewer.data.data_loading import get_rows
from well_viewer.data.image_resolver import (
    normalize_row_filename,
    resolve_filename_candidates,
)
from well_viewer.core.safe import swallowed
import theme

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_DEBUG_TEXT_MIN_H = 140
_IMG_LABEL_MIN_W = 300
_IMG_LABEL_MIN_H = 300


class ScatterCellViewer(QWidget):
    """Slide-out panel for viewing a single cell's fluorescence images.

    Shows one channel at a time, cropped to the cell's boundaries from the mask.
    Uses filename and nuclear_id from CSV to locate images. Mounted in the
    Scatter tab's right dock (shared with the Export-Style sidebar — one panel
    visible at a time) rather than a disruptive modal pop-up window.
    """

    def __init__(
        self,
        parent,
        ctx,
        well_label: str,
        filename: str,
        nuclear_id: str,
        row_idx: int,
    ):
        super().__init__(parent)

        self.ctx = ctx
        self.well_label = well_label
        self.filename = normalize_row_filename(filename)
        self.nuclear_id = nuclear_id
        self.row_idx = row_idx

        self._cell_bounds: tuple[int, int, int, int] | None = None
        self._cell_images: dict[str, Optional] = {}
        self._cell_outline_mask = None
        self._current_channel = None
        self._current_lut: tuple[float, float] = (0.0, 100.0)
        self._channel_luts: dict[str, tuple[float, float]] = {}
        # Image source: "raw" loads the raw input fluor frame; "corrected"
        # loads the pipeline's top-hat output (same notion the Review Image
        # tab calls "corrected"), falling back to raw when absent.
        self._image_source = "raw"
        self._debug_lines: list[str] = []

        self._build_ui()
        self._load_cell_data()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 10)

        # Header row: title + close (✕) so the slide-out can be dismissed
        # without a window chrome (it lives in the shared plot dock).
        head_row = QHBoxLayout()
        self._title_label = QLabel(f"Cell Viewer: {self.well_label}")
        tfont = self._title_label.font()
        tfont.setBold(True)
        self._title_label.setFont(tfont)
        head_row.addWidget(self._title_label, 1)
        close_btn = QPushButton("✕")
        close_btn.setObjectName("Ghost")
        close_btn.setFixedWidth(theme.Metrics.icon_btn_w)
        close_btn.setToolTip("Close the cell viewer")
        close_btn.clicked.connect(self._close_dock)
        head_row.addWidget(close_btn, 0)
        layout.addLayout(head_row)

        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("Channel:"))
        self._channel_dropdown = QComboBox()
        self._channel_dropdown.setEditable(False)
        self._channel_dropdown.currentIndexChanged.connect(self._on_channel_changed)
        ctrl_row.addWidget(self._channel_dropdown)
        ctrl_row.addSpacing(10)
        ctrl_row.addWidget(QLabel("Source:"))
        self._source_dropdown = QComboBox()
        self._source_dropdown.setEditable(False)
        # Data carries the canonical mode; the label is the human-facing text.
        self._source_dropdown.addItem("Raw", "raw")
        self._source_dropdown.addItem("Corrected", "corrected")
        self._source_dropdown.setToolTip(
            "Raw: the unprocessed input frame.\n"
            "Corrected: the pipeline's top-hat output (falls back to raw "
            "when a corrected frame isn't available)."
        )
        self._source_dropdown.currentIndexChanged.connect(self._on_source_changed)
        ctrl_row.addWidget(self._source_dropdown)
        ctrl_row.addStretch(1)
        layout.addLayout(ctrl_row)

        self._img_label = QLabel("Loading...")
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_label.setMinimumSize(_IMG_LABEL_MIN_W, _IMG_LABEL_MIN_H)
        layout.addWidget(self._img_label, 1)

        lut_row = QHBoxLayout()
        lut_row.addWidget(QLabel("LUT min:"))
        self._lut_min_edit = QLineEdit("0")
        self._lut_min_edit.setFixedWidth(theme.Metrics.num_field_w)
        self._lut_min_edit.editingFinished.connect(self._on_lut_change)
        lut_row.addWidget(self._lut_min_edit)
        lut_row.addSpacing(10)
        lut_row.addWidget(QLabel("max:"))
        self._lut_max_edit = QLineEdit("100")
        self._lut_max_edit.setFixedWidth(theme.Metrics.num_field_w)
        self._lut_max_edit.editingFinished.connect(self._on_lut_change)
        lut_row.addWidget(self._lut_max_edit)
        auto_btn = QPushButton("Auto")
        auto_btn.clicked.connect(self._auto_lut)
        lut_row.addWidget(auto_btn)
        lut_row.addStretch(1)
        layout.addLayout(lut_row)

        diag_group = QGroupBox("Diagnostics")
        diag_layout = QVBoxLayout(diag_group)
        self._debug_text = QTextEdit()
        self._debug_text.setReadOnly(True)
        self._debug_text.setMinimumHeight(_DEBUG_TEXT_MIN_H)
        diag_layout.addWidget(self._debug_text)
        layout.addWidget(diag_group)

    def _on_source_changed(self, *_args) -> None:
        """Switch between raw and corrected image sources and reload the cell.

        The full reload re-resolves and re-crops every channel against the new
        source (and recomputes LUTs), so the displayed image, the channel
        dropdown, and the diagnostics all reflect the chosen source.
        """
        data = self._source_dropdown.currentData()
        self._image_source = str(data or "raw")
        self._debug(f"image source -> {self._image_source!r}; reloading cell")
        self._load_cell_data()

    def _close_dock(self) -> None:
        """Hide the shared plot dock this panel lives in."""
        from well_viewer.core.ui_helpers import hide_plot_dock
        hide_plot_dock(self.parentWidget())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._current_channel and self._cell_images.get(self._current_channel) is not None:
            self._display_current_image()

    def _debug(self, message: str) -> None:
        line = str(message)
        self._debug_lines.append(line)
        if getattr(self, "_debug_text", None) is not None:
            self._debug_text.append(line)

    @staticmethod
    def _row_dict_at(df, idx: int) -> dict:
        """Return row ``idx`` of ``get_rows`` as a ``{column: value}`` dict.

        ``get_rows`` returns a ``pandas.DataFrame`` (positional ``df[idx]``
        is a *column* lookup → ``KeyError``), so use ``iloc``.
        """
        return dict(df.iloc[idx])

    def _pick_member_for_fov(self, matches: list[str], target_fov: str) -> str:
        """Pick the most-specific zip-member path from a basename-match set.

        ``zf.namelist()`` is filtered by basename, so several members can
        match the same candidate when the zip groups FOVs under
        directories (``F001/foo_mask.tif`` + ``F002/foo_mask.tif``).
        Without disambiguation, ``namelist()`` order picks the first one,
        which silently shows a cell from a different FOV — same well, same
        numeric id, different cell. Prefer the member whose full path
        contains the target FOV with a non-digit neighbour on each side
        so ``F001`` doesn't falsely match a path containing ``F010``.
        """
        if len(matches) <= 1:
            return matches[0] if matches else ""
        if not target_fov:
            self._debug(
                f"WARNING: {len(matches)} zip members matched basename but "
                f"no FOV from CSV row to disambiguate; falling back to "
                f"first: {matches[0]!r}"
            )
            return matches[0]
        # Tokens to probe — both prefixed and unprefixed numeric forms.
        # Padded forms come first so "F001" wins over bare "1" / "F1".
        fov_str = str(target_fov).strip()
        wanted: list[str] = []
        wanted.append(fov_str.lower())
        try:
            fov_num = int(float(fov_str))
            for variant in (
                f"fov{fov_num:03d}",
                f"f{fov_num:03d}",
                f"fov{fov_num}",
                f"f{fov_num}",
                str(fov_num),
            ):
                if variant not in wanted:
                    wanted.append(variant.lower())
        except (TypeError, ValueError):
            pass

        def _contains_token(haystack: str, tok: str) -> bool:
            """Substring with non-digit neighbours so ``f1`` won't match
            ``f10`` and ``1`` won't match ``10`` / ``f010``."""
            if not tok:
                return False
            idx = haystack.find(tok)
            while idx >= 0:
                before = haystack[idx - 1] if idx > 0 else ""
                end = idx + len(tok)
                after = haystack[end] if end < len(haystack) else ""
                if not before.isdigit() and not after.isdigit():
                    return True
                idx = haystack.find(tok, idx + 1)
            return False

        def _score(member: str) -> tuple[int, int, int]:
            low = member.lower()
            # Index of the highest-priority token found (lower idx = better).
            hit = -1
            for i, tok in enumerate(wanted):
                if _contains_token(low, tok):
                    hit = i
                    break
            # Score ordering: matched > unmatched; among matched, lower
            # token index is better; tie-break on shorter path length.
            if hit < 0:
                return (0, 0, 0)
            return (1, -hit, -len(member))

        ranked = sorted(matches, key=_score, reverse=True)
        chosen = ranked[0]
        # If nothing matched any token, the picker is guessing — flag it.
        if _score(chosen)[0] == 0:
            self._debug(
                f"WARNING: FOV disambig found no token match for "
                f"target={target_fov!r} in {matches!r}; falling back to "
                f"first: {chosen!r}"
            )
        else:
            self._debug(
                f"FOV disambig: target={target_fov!r} candidates={matches!r} "
                f"-> chose {chosen!r}"
            )
        return chosen

    @staticmethod
    def _path_carries_channel(path: str, channel_token: str) -> bool:
        """True iff *path* embeds *channel_token* as a non-letter-bounded token.

        ``resolve_filename_candidates(target_channel=...)`` substitutes the
        channel slot in the schema-derived candidate, but it also appends
        the *original* row filename as a fallback. When the schema-derived
        candidate happens to miss the zip (channel name doesn't match the
        on-disk scheme), the original-filename fallback wins for every
        channel — every channel slot ends up loading the same source
        file, which is exactly the "channel switch shows the wrong /
        same image" symptom.

        Guard against that by accepting only paths whose lineage
        contains the channel token with non-letter / non-digit neighbours
        so ``cit`` does not falsely match ``mcit``.
        """
        if not channel_token:
            return True
        tok = channel_token.lower()
        low = path.lower()
        idx = low.find(tok)
        while idx >= 0:
            before = low[idx - 1] if idx > 0 else ""
            end = idx + len(tok)
            after = low[end] if end < len(low) else ""
            if not (before.isalnum() or after.isalnum()):
                return True
            idx = low.find(tok, idx + 1)
        return False

    def _load_cell_data(self) -> None:
        """Load cell data: find images using filename, get cell bounds from mask.

        Any unexpected failure is surfaced in the diagnostics panel rather than
        propagating — the dialog must always appear so the user can see *why*
        a cell image could not be loaded.
        """
        try:
            self._load_cell_data_impl()
        except Exception as exc:
            import logging
            import traceback
            # The full traceback goes to the dev-gated logger (DEBUG), not into
            # the user-visible diagnostics panel — the panel gets a concise,
            # non-scary one-liner (audit X16). Set ALLWELL logging to DEBUG to
            # see the stack.
            logging.getLogger(__name__).debug(
                "scatter cell-load failed\n%s", traceback.format_exc())
            self._debug(f"Unexpected error while loading cell data: {exc!r}")
            try:
                self._img_label.setText(f"Error loading cell: {exc}")
            except Exception:
                swallowed()  # was: pass (silent except)

    def _load_cell_data_impl(self) -> None:
        # State hygiene — wipe everything that's tied to the *previous*
        # cell before loading the new one. update_cell() reuses the same
        # dialog instance, so without this _cell_images / _current_channel /
        # _cell_bounds carry stale data forward and
        # selecting a not-yet-reloaded channel renders the previous
        # cell's pixmap.
        self._cell_images = {}
        self._current_channel = None
        self._cell_bounds = None
        self._channel_luts = {}
        self._cell_outline_mask = None
        self._debug_lines = []
        if getattr(self, "_debug_text", None) is not None:
            self._debug_text.clear()
        if getattr(self, "_channel_dropdown", None) is not None:
            blocked = self._channel_dropdown.blockSignals(True)
            try:
                self._channel_dropdown.clear()
            finally:
                self._channel_dropdown.blockSignals(blocked)
        self._debug(f"well_label={self.well_label!r}, row_idx={self.row_idx}, filename={self.filename!r}, nuclear_id={self.nuclear_id!r}")
        try:
            nuclear_id = int(float(self.nuclear_id))
        except (ValueError, TypeError):
            self._img_label.setText("Invalid nuclear_id")
            self._debug("Failed to parse nuclear_id as int.")
            return
        self._debug(f"parsed_nuclear_id={nuclear_id}")

        if self.well_label not in self.ctx.state.dataset.well_paths:
            self._img_label.setText(f"Well {self.well_label} is no longer loaded")
            self._debug(f"well_label not in app._app_state.dataset.well_paths: {self.well_label!r}")
            return

        rows = get_rows(self.ctx, self.well_label)
        if not (0 <= self.row_idx < len(rows)):
            self._img_label.setText("Invalid row index")
            self._debug(f"row_idx out of bounds: row_idx={self.row_idx}, rows={len(rows)}")
            return

        row = self._row_dict_at(rows, self.row_idx)
        self._nuclear_token = str(row.get("channel") or "").strip()
        # Capture the row's FOV so zip-member resolution can disambiguate
        # when several members share the same basename across FOVs (e.g.
        # ``F001/mask.tif`` + ``F002/mask.tif``).
        self._row_fov = str(row.get("fov") or "").strip()
        self._row_timepoint = str(row.get("timepoint") or "").strip()
        self._debug(f"csv.channel={self._nuclear_token!r}")
        self._debug(f"csv.fov={self._row_fov!r}  csv.timepoint={self._row_timepoint!r}")
        self._debug(f"fluor channels to probe={sorted(self.ctx.state.channel.fluor_channels)!r}")

        mask_arr, mask_path = self._load_output_image_by_filename("mask")
        # Mask-content sanity: log shape, label cardinality, and whether
        # the requested nuclear_id actually exists in this mask. The
        # "loaded the wrong cell" symptom hides here when the matched
        # mask happens to contain a foreign cell with the same numeric id.
        if mask_arr is not None:
            try:
                import numpy as _np
                _arr = _np.asarray(mask_arr)
                _uniq = _np.unique(_arr)
                _hit = bool(_np.any(_arr == nuclear_id))
                _sample = list(map(int, _uniq[:8]))
                self._debug(
                    f"mask diagnostics: shape={_arr.shape} dtype={_arr.dtype} "
                    f"n_labels={len(_uniq)} min_label={int(_uniq.min()) if _uniq.size else 'n/a'} "
                    f"max_label={int(_uniq.max()) if _uniq.size else 'n/a'} "
                    f"contains_nuclear_id={_hit} sample_labels={_sample}"
                )
            except Exception as _exc:  # pragma: no cover - diagnostic only
                self._debug(f"mask diagnostics failed: {_exc!r}")
        self._cell_bounds = self._get_cell_bounds(mask_arr, nuclear_id, padding_px=25)
        if not self._cell_bounds:
            self._img_label.setText(f"Cell {nuclear_id} not found in mask")
            self._debug("No bounds found for requested nuclear_id.")
            return
        self._debug(f"cell_bounds={self._cell_bounds}")
        self._debug(f"mask_path_for_bounds={mask_path}")

        try:
            import numpy as np

            y_min, x_min, y_max, x_max = self._cell_bounds
            mask_crop = np.asarray(mask_arr)[y_min:y_max, x_min:x_max]
            cell_mask = mask_crop == nuclear_id
            self._cell_outline_mask = self._compute_outline_mask(cell_mask)
        except Exception as e:
            self._cell_outline_mask = None
            self._debug(f"failed to compute cell outline mask: {e!r}")

        for ch in sorted(self.ctx.state.channel.fluor_channels):
            self._debug(f"loading fluor channel {ch!r}...")
            arr = self._load_and_crop_channel(ch)
            if arr is not None:
                self._cell_images[ch] = arr
                self._debug(f"  -> channel {ch!r} loaded, shape={getattr(arr, 'shape', None)}")
            else:
                self._debug(f"  -> channel {ch!r} unavailable (no array)")

        nuc_key = self._nuclear_token.lower() if self._nuclear_token else "nuclear_fluor"
        self._debug(f"loading nuclear stain (key={nuc_key!r}, csv.channel={self._nuclear_token!r})...")
        arr = self._load_and_crop_nuclear()
        if arr is not None:
            self._cell_images[nuc_key] = arr
            self._debug(f"  -> nuclear loaded, shape={getattr(arr, 'shape', None)}")
        else:
            self._debug("  -> nuclear unavailable")

        self._debug("loading mask overlay (stored under key 'nuclear')...")
        arr = self._load_and_crop_channel("mask")
        if arr is not None:
            self._cell_images["nuclear"] = arr
            self._debug(f"  -> mask loaded, shape={getattr(arr, 'shape', None)}")
        else:
            self._debug("  -> mask unavailable")

        available_channels = [ch for ch in sorted(self.ctx.state.channel.fluor_channels) if self._cell_images.get(ch) is not None]
        if nuc_key in self._cell_images and nuc_key not in available_channels:
            available_channels.append(nuc_key)
        if "nuclear" in self._cell_images:
            available_channels.append("nuclear")
        self._debug(
            f"dropdown channels (in order)={available_channels!r}; "
            f"_cell_images keys={sorted(self._cell_images.keys())!r}"
        )

        self._channel_dropdown.blockSignals(True)
        self._channel_dropdown.clear()
        self._channel_dropdown.addItems(available_channels)
        self._channel_dropdown.blockSignals(False)

        if available_channels:
            self._channel_dropdown.setCurrentIndex(0)
            self._on_channel_changed()
        else:
            self._img_label.setText("No images could be loaded")
            self._debug("No channels were successfully loaded.")

    def _load_output_image_by_filename(self, image_type: str, target_channel: str = ""):
        """Load an output image (mask / overlay / top-hat) from the output zip
        via the canonical filename resolver.

        *target_channel* is forwarded to the resolver so channel-templated
        suffixes (e.g. ``_tophat_{channel}.tif``) resolve for the requested
        channel — required for the "Corrected" source, which reads top-hat
        output per channel."""
        try:
            import zipfile
            from pathlib import Path as _Path
            from well_viewer.data.data_loading import extract_well_token
            from well_viewer.data.image_discovery import (
                ImgRef,
                find_out_well_zips_in_dir,
                find_plain_well_zips_in_dir,
                find_well_zips_in_dir,
                open_imgref_as_array,
            )

            pipeline_info = self.ctx.state.dataset.pipeline_info
            candidates = resolve_filename_candidates(
                self.filename,
                pipeline_info=pipeline_info,
                output_kind=image_type,
                target_channel=target_channel,
                filename_variants_fn=self._filename_variants,
            )
            if not candidates:
                return None, f"unknown image_type {image_type!r}"
            self._debug(
                f"output lookup type={image_type}, source={self.filename!r}, candidates={candidates!r}"
            )

            well_token = extract_well_token(self.well_label)
            if well_token is None:
                return None, f"could not extract well_token from {self.well_label!r}"

            data_dir = self.ctx.state.dataset.data_dir
            in_dir = self.ctx.state.dataset.in_dir
            zips: list = []
            if data_dir and data_dir.is_dir():
                zips = find_out_well_zips_in_dir(data_dir, well_token)
                zips += find_plain_well_zips_in_dir(data_dir, well_token)
            if not zips and data_dir and data_dir.is_dir():
                zips = find_well_zips_in_dir(data_dir, well_token)
            self._debug(f"output zip search well_token={well_token!r}, zips={[str(z) for z in zips]!r}")

            candidate_lowers = [c.lower() for c in candidates]
            target_fov = getattr(self, "_row_fov", "") or ""
            for zip_path in zips:
                self._debug(f"scan zip for {image_type}: {zip_path}")
                with zipfile.ZipFile(zip_path, "r") as zf:
                    members = zf.namelist()
                    matches = [m for m in members
                               if _Path(m).name.lower() in candidate_lowers]
                    if matches:
                        self._debug(
                            f"{image_type}: {len(matches)} member(s) in "
                            f"{zip_path.name} match basename — {matches!r}"
                        )
                        chosen = self._pick_member_for_fov(matches, target_fov)
                        ref = ImgRef(zip_path=zip_path, zip_member=chosen)
                        arr = open_imgref_as_array(ref=ref, greyscale=(image_type != "overlay"))
                        self._debug(f"loaded {image_type} from zip: {zip_path}::{chosen}")
                        return arr, f"{zip_path}::{chosen}"

            search_dirs = [d for d in (data_dir, in_dir) if d and d.is_dir()]
            self._debug(f"output disk search dirs={[str(d) for d in search_dirs]!r}")
            for d in search_dirs:
                for candidate in candidates:
                    paths = list(d.rglob(candidate))
                    if not paths:
                        continue
                    self._debug(
                        f"{image_type}: {len(paths)} disk path(s) match "
                        f"{candidate!r} under {d} — {[str(p) for p in paths]!r}"
                    )
                    chosen = self._pick_member_for_fov([str(p) for p in paths], target_fov)
                    img_path = _Path(chosen)
                    ref = ImgRef(disk_path=img_path)
                    arr = open_imgref_as_array(ref=ref, greyscale=(image_type != "overlay"))
                    self._debug(f"loaded {image_type} from disk: {img_path}")
                    return arr, str(img_path)

            self._debug(f"{image_type} not found. candidates={candidates!r}")
            return None, f"not found: {candidates[0]!r}"

        except Exception as e:
            self._debug(f"_load_output_image_by_filename exception for {image_type}: {e!r}")
            return None, f"exception: {e}"

    def _well_token_variants(self, name: str) -> list[str]:
        import re as _re

        m = _re.match(r"^([A-Ha-h])(\d{1,2})(.*)$", name)
        if not m:
            return []
        row = m.group(1).upper()
        col = int(m.group(2))
        rest = m.group(3)
        variants = [
            f"{row}{col}{rest}",
            f"{row}{col:02d}{rest}",
        ]
        out: list[str] = []
        seen: set[str] = set()
        for v in variants:
            if v != name and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def _filename_variants(self, filename: str) -> list[str]:
        from pathlib import Path as _Path

        p = _Path(filename)
        stem_variants = [p.stem, *self._well_token_variants(p.stem)]
        variants: list[str] = []
        seen: set[str] = set()
        for stem in stem_variants:
            candidate = f"{stem}{p.suffix}"
            if candidate not in seen:
                seen.add(candidate)
                variants.append(candidate)
        return variants

    def _get_cell_bounds(self, mask_arr, nuclear_id: int, padding_px: int = 0) -> tuple[int, int, int, int] | None:
        try:
            import numpy as np

            if mask_arr is None:
                self._debug("mask unavailable.")
                return None

            mask_arr = np.asarray(mask_arr)
            self._debug(
                f"mask loaded: shape={tuple(mask_arr.shape)}, dtype={mask_arr.dtype}, min={mask_arr.min()}, max={mask_arr.max()}"
            )
            cell_pixels = np.where(mask_arr == nuclear_id)

            if len(cell_pixels[0]) == 0:
                try:
                    uniq = np.unique(mask_arr)
                    sample = uniq[:25].tolist()
                    self._debug(
                        f"nuclear_id={nuclear_id} not in mask. unique_count={len(uniq)}, sample_unique={sample}"
                    )
                except Exception:
                    self._debug(f"nuclear_id={nuclear_id} not in mask. failed to compute unique labels.")
                return None

            y_min, y_max = int(cell_pixels[0].min()), int(cell_pixels[0].max()) + 1
            x_min, x_max = int(cell_pixels[1].min()), int(cell_pixels[1].max()) + 1

            if padding_px > 0:
                y_min = max(0, y_min - padding_px)
                x_min = max(0, x_min - padding_px)
                y_max = min(mask_arr.shape[0], y_max + padding_px)
                x_max = min(mask_arr.shape[1], x_max + padding_px)
            return (y_min, x_min, y_max, x_max)

        except Exception as e:
            self._debug(f"_get_cell_bounds exception: {e!r}")
            return None

    def _compute_outline_mask(self, cell_mask) -> np.ndarray | None:
        import numpy as np

        if cell_mask is None:
            return None

        cell_mask = np.asarray(cell_mask, dtype=bool)
        if cell_mask.size == 0:
            return None

        up = np.roll(cell_mask, 1, axis=0)
        down = np.roll(cell_mask, -1, axis=0)
        left = np.roll(cell_mask, 1, axis=1)
        right = np.roll(cell_mask, -1, axis=1)

        up[0, :] = False
        down[-1, :] = False
        left[:, 0] = False
        right[:, -1] = False

        interior = cell_mask & up & down & left & right
        return cell_mask & ~interior

    @staticmethod
    def _resize_mask_nearest(mask, shape):
        """Nearest-neighbour resize of a boolean *mask* to ``shape`` (h, w).

        Keeps the outline usable when the displayed channel crop and the
        segmentation-mask crop differ in size (they're cropped with the same
        bounds but out of full frames that may not share a resolution)."""
        import numpy as np
        mask = np.asarray(mask, dtype=bool)
        src_h, src_w = mask.shape[:2]
        dst_h, dst_w = int(shape[0]), int(shape[1])
        if min(src_h, src_w, dst_h, dst_w) == 0:
            return np.zeros((dst_h, dst_w), dtype=bool)
        ys = np.minimum((np.arange(dst_h) * src_h) // dst_h, src_h - 1)
        xs = np.minimum((np.arange(dst_w) * src_w) // dst_w, src_w - 1)
        return mask[ys][:, xs]

    @classmethod
    def _paint_outline(cls, rgb, outline_mask) -> None:
        """Paint the red cell outline into the ``(H, W, 3)`` uint8 *rgb* buffer.

        Resamples the outline to the buffer's shape when it differs (previously a
        shape mismatch silently dropped the outline — the "boundary circle gone"
        bug); an exact-shape outline takes the fast path unchanged."""
        import numpy as np
        if outline_mask is None:
            return
        outline = np.asarray(outline_mask, dtype=bool)
        target = rgb.shape[:2]
        if outline.shape != target:
            outline = cls._resize_mask_nearest(outline, target)
        if outline.shape == target:
            rgb[outline] = np.array([255, 0, 0], dtype=np.uint8)

    def _load_and_crop_channel(self, channel: str) -> Optional:
        try:
            if channel in ("mask", "overlay"):
                arr, src = self._load_output_image_by_filename(channel)
                self._debug(f"channel={channel} loaded_from={src}")
            elif getattr(self, "_image_source", "raw") == "corrected":
                # Corrected source: prefer the per-channel top-hat output, fall
                # back to the raw input frame when the dataset ships no top-hat
                # for this channel (mirrors Review Image's corrected→raw fallback).
                arr, src = self._load_output_image_by_filename("tophat", target_channel=channel)
                if arr is not None:
                    self._debug(f"channel={channel} corrected(tophat) loaded_from={src}")
                else:
                    self._debug(f"channel={channel} corrected(tophat) unavailable ({src}); falling back to raw")
                    arr = self._load_input_channel_by_filename(channel)
            else:
                arr = self._load_input_channel_by_filename(channel)
            if arr is None or not self._cell_bounds:
                self._debug(f"channel={channel} unavailable or missing bounds.")
                return None

            y_min, x_min, y_max, x_max = self._cell_bounds
            return arr[y_min:y_max, x_min:x_max]

        except Exception as e:
            self._debug(f"_load_and_crop_channel exception for {channel}: {e!r}")
            return None

    def _load_and_crop_nuclear(self) -> Optional:
        try:
            import zipfile
            from pathlib import Path as _Path
            from well_viewer.data.data_loading import extract_well_token
            from well_viewer.data.image_discovery import (
                ImgRef,
                find_plain_well_zips_in_dir,
                find_well_zips_in_dir,
                open_imgref_as_array,
            )

            if not self._cell_bounds:
                self._debug("skip nuclear image load: no cell bounds.")
                return None

            # Corrected source: prefer the nuclear channel's top-hat output,
            # falling through to the raw nuclear frame below when absent.
            if getattr(self, "_image_source", "raw") == "corrected":
                arr, src = self._load_output_image_by_filename(
                    "tophat", target_channel=self._nuclear_token)
                if arr is not None:
                    self._debug(f"nuclear corrected(tophat) loaded_from={src}")
                    y_min, x_min, y_max, x_max = self._cell_bounds
                    return arr[y_min:y_max, x_min:x_max]
                self._debug(f"nuclear corrected(tophat) unavailable ({src}); falling back to raw")

            well_token = extract_well_token(self.well_label)
            if well_token is None:
                self._debug(f"could not parse well token from {self.well_label!r} for nuclear image load")
                return None

            in_dir = self.ctx.state.dataset.in_dir
            data_dir = self.ctx.state.dataset.data_dir

            zips: list = []
            if in_dir and in_dir.is_dir():
                zips = find_plain_well_zips_in_dir(in_dir, well_token)
            if not zips and data_dir and data_dir.is_dir():
                zips = find_well_zips_in_dir(data_dir, well_token)
            self._debug(f"nuclear image zip search target={self.filename!r}, zips={[str(z) for z in zips]!r}")

            pipeline_info = self.ctx.state.dataset.pipeline_info
            target_names = resolve_filename_candidates(
                self.filename,
                pipeline_info=pipeline_info,
                filename_variants_fn=self._filename_variants,
            )
            self._debug(f"nuclear image filename candidates={target_names!r}")
            target_lowers = {name.lower() for name in target_names}
            target_fov = getattr(self, "_row_fov", "") or ""
            for zip_path in zips:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    members = zf.namelist()
                    matches = [m for m in members
                               if _Path(m).name.lower() in target_lowers]
                    if matches:
                        self._debug(
                            f"nuclear: {len(matches)} member(s) in "
                            f"{zip_path.name} match basename — {matches!r}"
                        )
                        chosen = self._pick_member_for_fov(matches, target_fov)
                        arr = open_imgref_as_array(
                            ref=ImgRef(zip_path=zip_path, zip_member=chosen),
                            greyscale=True,
                        )
                        if arr is not None:
                            self._debug(f"loaded nuclear image from zip: {zip_path}::{chosen}")
                            y_min, x_min, y_max, x_max = self._cell_bounds
                            return arr[y_min:y_max, x_min:x_max]

            search_dirs = [d for d in (in_dir, data_dir) if d and d.is_dir()]
            self._debug(f"nuclear image disk search dirs={[str(d) for d in search_dirs]!r}")
            for d in search_dirs:
                for target_name in target_names:
                    paths = list(d.rglob(target_name))
                    if not paths:
                        continue
                    self._debug(
                        f"nuclear: {len(paths)} disk path(s) match "
                        f"{target_name!r} under {d} — {[str(p) for p in paths]!r}"
                    )
                    chosen = self._pick_member_for_fov([str(p) for p in paths], target_fov)
                    img_path = _Path(chosen)
                    arr = open_imgref_as_array(ref=ImgRef(disk_path=img_path), greyscale=True)
                    if arr is not None:
                        self._debug(f"loaded nuclear image from disk: {img_path}")
                        y_min, x_min, y_max, x_max = self._cell_bounds
                        return arr[y_min:y_max, x_min:x_max]

            self._debug(f"nuclear image not found for filename={self.filename!r}")
            return None

        except Exception as e:
            self._debug(f"_load_and_crop_nuclear exception: {e!r}")
            return None

    def _load_input_channel_by_filename(self, channel_token: str) -> Optional:
        try:
            import zipfile
            from pathlib import Path as _Path
            from well_viewer.data.data_loading import extract_well_token
            from well_viewer.data.image_discovery import (
                ImgRef,
                find_plain_well_zips_in_dir,
                find_well_zips_in_dir,
                open_imgref_as_array,
            )

            pipeline_info = self.ctx.state.dataset.pipeline_info
            target_names = resolve_filename_candidates(
                self.filename,
                pipeline_info=pipeline_info,
                target_channel=channel_token,
                filename_variants_fn=self._filename_variants,
            )
            self._debug(f"channel={channel_token}: filename candidates={target_names!r} from source={self.filename!r}")
            if not target_names:
                return None

            well_token = extract_well_token(self.well_label)
            if well_token is None:
                self._debug(f"channel={channel_token}: could not parse well token from {self.well_label!r}")
                return None

            in_dir = self.ctx.state.dataset.in_dir
            data_dir = self.ctx.state.dataset.data_dir

            zips: list = []
            if in_dir and in_dir.is_dir():
                zips = find_plain_well_zips_in_dir(in_dir, well_token)
            if not zips and data_dir and data_dir.is_dir():
                zips = find_well_zips_in_dir(data_dir, well_token)
            self._debug(f"channel={channel_token}: zip search zips={[str(z) for z in zips]!r}")

            target_lowers = {name.lower() for name in target_names}
            target_fov = getattr(self, "_row_fov", "") or ""
            for zip_path in zips:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    members = zf.namelist()
                    matches = [m for m in members
                               if _Path(m).name.lower() in target_lowers]
                    if matches:
                        self._debug(
                            f"channel={channel_token}: {len(matches)} "
                            f"member(s) in {zip_path.name} match basename "
                            f"— {matches!r}"
                        )
                        # Reject members that don't carry the channel
                        # token. Without this the original-filename
                        # fallback inside resolve_filename_candidates
                        # silently won for every channel — every entry
                        # in _cell_images ended up pointing at the same
                        # source image, so switching channels in the
                        # popup never changed the displayed pixmap.
                        channel_matches = [
                            m for m in matches
                            if self._path_carries_channel(m, channel_token)
                        ]
                        if not channel_matches:
                            self._debug(
                                f"channel={channel_token}: rejecting all "
                                f"matches — none carry the channel token. "
                                f"Likely the schema-derived candidate "
                                f"missed the zip and the original filename "
                                f"fallback would load the wrong channel."
                            )
                            continue
                        if len(channel_matches) < len(matches):
                            self._debug(
                                f"channel={channel_token}: filtered to "
                                f"{len(channel_matches)} channel-carrying "
                                f"match(es) — {channel_matches!r}"
                            )
                        chosen = self._pick_member_for_fov(channel_matches, target_fov)
                        ref = ImgRef(zip_path=zip_path, zip_member=chosen)
                        self._debug(f"channel={channel_token}: loaded from zip {zip_path}::{chosen}")
                        return open_imgref_as_array(ref=ref, greyscale=True)

            search_dirs = [d for d in (in_dir, data_dir) if d and d.is_dir()]
            self._debug(f"channel={channel_token}: disk search dirs={[str(d) for d in search_dirs]!r}")
            for d in search_dirs:
                for candidate_name in target_names:
                    paths = list(d.rglob(candidate_name))
                    if not paths:
                        continue
                    self._debug(
                        f"channel={channel_token}: {len(paths)} disk path(s) "
                        f"match {candidate_name!r} under {d} — "
                        f"{[str(p) for p in paths]!r}"
                    )
                    channel_paths = [
                        p for p in paths
                        if self._path_carries_channel(str(p), channel_token)
                    ]
                    if not channel_paths:
                        self._debug(
                            f"channel={channel_token}: rejecting all disk "
                            f"matches for {candidate_name!r} — none carry "
                            f"the channel token."
                        )
                        continue
                    chosen = self._pick_member_for_fov([str(p) for p in channel_paths], target_fov)
                    img_path = _Path(chosen)
                    self._debug(f"channel={channel_token}: loaded from disk {img_path}")
                    return open_imgref_as_array(ref=ImgRef(disk_path=img_path), greyscale=True)

            self._debug(f"channel={channel_token}: not found candidates={target_names!r}")
            return None

        except Exception as e:
            self._debug(f"_load_input_channel_by_filename exception for {channel_token}: {e!r}")
            return None

    def _on_channel_changed(self, *_args) -> None:
        prev = self._current_channel
        self._current_channel = self._channel_dropdown.currentText()
        # Surface the switch in the popup's debug box so the user can
        # check exactly which array the display is about to fetch from
        # _cell_images.
        try:
            cached_keys = sorted(self._cell_images.keys())
        except Exception:
            cached_keys = []
        self._debug(
            f"channel switch: {prev!r} -> {self._current_channel!r}; "
            f"cached_channels={cached_keys}"
        )
        if not self._current_channel:
            self._debug("channel switch aborted: empty currentText().")
            return

        arr = self._cell_images.get(self._current_channel)
        if arr is None:
            self._img_label.setText("Image not found")
            self._debug(
                f"channel switch: no cached image for "
                f"{self._current_channel!r}; available keys={cached_keys}"
            )
            return

        try:
            arr_shape = getattr(arr, "shape", None)
            arr_dtype = getattr(arr, "dtype", None)
        except Exception:
            arr_shape = arr_dtype = None
        self._debug(
            f"channel switch: drawing {self._current_channel!r} "
            f"shape={arr_shape} dtype={arr_dtype}"
        )

        if self._current_channel in self._channel_luts:
            lo, hi = self._channel_luts[self._current_channel]
            self._debug(f"channel switch: reusing LUT {self._current_channel!r}=({lo}, {hi})")
        else:
            try:
                lo, hi = float(arr.min()), float(arr.max())
            except (ValueError, TypeError, AttributeError):
                lo, hi = 0.0, 100.0

            if hi <= lo:
                hi = lo + 1.0

            self._channel_luts[self._current_channel] = (lo, hi)
            self._debug(
                f"channel switch: new LUT computed for "
                f"{self._current_channel!r}=({lo}, {hi})"
            )

        self._current_lut = (lo, hi)
        self._lut_min_edit.setText(f"{lo:.1f}")
        self._lut_max_edit.setText(f"{hi:.1f}")

        self._display_current_image()

    def _on_lut_change(self) -> None:
        try:
            lo = float(self._lut_min_edit.text())
            hi = float(self._lut_max_edit.text())
            self._current_lut = (lo, hi)
            if self._current_channel:
                self._channel_luts[self._current_channel] = (lo, hi)
            self._display_current_image()
        except ValueError:
            pass

    def _auto_lut(self) -> None:
        arr = self._cell_images.get(self._current_channel)
        if arr is None:
            return

        try:
            lo, hi = float(arr.min()), float(arr.max())
        except (ValueError, TypeError, AttributeError):
            return

        if hi <= lo:
            hi = lo + 1.0

        self._current_lut = (lo, hi)
        if self._current_channel:
            self._channel_luts[self._current_channel] = (lo, hi)
        self._lut_min_edit.setText(f"{lo:.1f}")
        self._lut_max_edit.setText(f"{hi:.1f}")
        self._display_current_image()

    def _display_current_image(self) -> None:
        arr = self._cell_images.get(self._current_channel)
        if arr is None:
            self._img_label.setText("No image")
            return

        label_width = max(self._img_label.width(), 300)
        label_height = max(self._img_label.height(), 300)

        lo, hi = self._current_lut
        pixmap = self._make_magnified_thumb(
            arr,
            label_width,
            label_height,
            lo,
            hi,
            outline_mask=self._cell_outline_mask,
        )

        if pixmap is not None:
            self._img_label.setPixmap(pixmap)
        else:
            self._img_label.setText("Failed to render image")

    def _make_magnified_thumb(self, arr, sz_w: int, sz_h: int, lo, hi, outline_mask=None) -> QPixmap | None:
        try:
            import numpy as np

            arr = np.asarray(arr, dtype=np.float32)
            alo = lo if lo is not None else float(arr.min())
            ahi = hi if hi is not None else float(arr.max())
            if ahi <= alo:
                ahi = alo + 1.0

            disp = ((np.clip(arr, alo, ahi) - alo) / (ahi - alo) * 255).astype(np.uint8)
            rgb = np.stack([disp, disp, disp], axis=-1)

            self._paint_outline(rgb, outline_mask)

            h, w, _ = rgb.shape
            rgb_c = np.ascontiguousarray(rgb)
            qimg = QImage(rgb_c.data, w, h, 3 * w, QImage.Format_RGB888).copy()
            pixmap = QPixmap.fromImage(qimg)
            return pixmap.scaled(
                int(sz_w), int(sz_h),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        except Exception:
            return None

    def update_cell(self, well_label: str, filename: str, nuclear_id: str, row_idx: int) -> None:
        self.well_label = well_label
        self.filename = normalize_row_filename(filename)
        self.nuclear_id = nuclear_id
        self.row_idx = row_idx
        if getattr(self, "_title_label", None) is not None:
            self._title_label.setText(f"Cell Viewer: {well_label}")
        self._load_cell_data()


# Width of the cell-viewer slide-out (matches the Export-Style sidebar's order
# of magnitude so the shared dock doesn't jump when swapping panels).
_CELL_VIEWER_WIDTH = 380


def open_scatter_cell_viewer(
    ctx,
    well_label: str,
    filename: str,
    nuclear_id: str,
    row_idx: int,
) -> None:
    """Open (or update) the cell viewer as a slide-out in the Scatter tab's
    right dock — the same shared dock as the Export-Style sidebar, so only one
    of them is visible at a time (no disruptive modal pop-up window)."""
    from well_viewer.core.ui_helpers import mount_widget_in_plot_dock

    # The dock is a registered widget; the viewer itself is a live session
    # object in the scatter runtime namespace (never persisted).
    dock = ctx.widgets.get_or_none("_scatter_export_dock")
    viewer = ctx.runtime.scatter.cell_viewer
    if viewer is None:
        viewer = ScatterCellViewer(dock, ctx, well_label, filename, nuclear_id, row_idx)
        ctx.runtime.scatter.cell_viewer = viewer
    else:
        viewer.update_cell(well_label, filename, nuclear_id, row_idx)

    if dock is not None:
        mount_widget_in_plot_dock(dock, viewer, _CELL_VIEWER_WIDTH)
    else:  # pragma: no cover — the per-cell scatter tab always builds a dock
        viewer.show()
        viewer.raise_()
