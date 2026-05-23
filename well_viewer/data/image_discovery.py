"""Image-reference + well-image discovery helpers.

Extracted from ``well_viewer.runtime_app``. Owns:
    * ``ImgRef`` — the disk/zip image pointer used across the package.
    * Filename suffix matchers and the per-file classifier.
    * Zip / folder member scanners.
    * ``find_well_images_and_masks`` — the legacy multi-mode discovery API.

The module is import-safe: it does not pull in PySide6 and only touches
numpy/PIL/tifffile lazily through ``preview_controller`` so that pure-data
callers (the smFISH controller, scatter callbacks) avoid the GUI cost.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from well_viewer.core import debug_flags as _debug_flags
from well_viewer.data.data_loading import extract_well_token as _extract_well_token
from well_viewer.data.image_resolver import (
    find_well_subfolder_path as _find_well_subfolder_path,
    normalize_well_token as _normalize_well_token,
    output_suffixes_for_kind as _output_suffixes_for_kind,
    well_token_matches_text as _well_token_matches_text,
)
from well_viewer.features.preview.controller import (
    classify_member as _preview_classify_member,
    open_imgref_as_array as _preview_open_imgref_as_array,
    read_member_bytes as _preview_read_member_bytes,
    scan_zip_members as _preview_scan_zip_members,
)


_logger = logging.getLogger("well_viewer")

try:
    import tifffile as _tifffile
    _TIFFFILE_AVAILABLE = True
except ImportError:
    _tifffile = None  # type: ignore[assignment]
    _TIFFFILE_AVAILABLE = False

try:
    from PIL import Image as _PILImage
    _PIL_AVAILABLE = True
except ImportError:
    _PILImage = None  # type: ignore[assignment]
    _PIL_AVAILABLE = False

try:
    import numpy as _np
    _NP_AVAILABLE = True
except ImportError:
    _np = None  # type: ignore[assignment]
    _NP_AVAILABLE = False


_IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}


def _suffix_matcher(kind: str) -> re.Pattern[str]:
    suffixes = [re.escape(sfx) for sfx in _output_suffixes_for_kind(kind, target_channel="x")]
    if kind in {"fluor_processed", "smfish"}:
        suffixes = [sfx.replace("x", r"\w+") for sfx in suffixes]
    return re.compile(r"(?:%s)$" % "|".join(suffixes), re.I)


_MASK_RE = _suffix_matcher("mask")
_OVERLAY_RE = _suffix_matcher("overlay")
_TOPHAT_FLUOR_RE = _suffix_matcher("fluor_processed")
_OUT_ZIP_RE = re.compile(r"^([A-Ha-h])(\d{1,2})_out\.zip$", re.I)
_PLAIN_ZIP_RE = re.compile(r"^([A-Ha-h])(\d{1,2})\.zip$", re.I)
_FNAME_RE = re.compile(
    r"^(?P<exp>[^_]+)_(?P<channel>[^_]*)_(?P<well>[^_]+)_(?P<fov>[^_]+)_(?P<tp>[^_.]+)",
    re.I,
)


def _build_fname_re(sep: str) -> "re.Pattern[str]":
    """Compose the 5-field fallback regex against an arbitrary separator.

    Datasets using ``-`` or ``.`` separators previously fell through to
    the underscore-hardcoded ``_FNAME_RE`` and got ``("unknown",
    "unknown")`` for every file. Callers can now pass a separator-aware
    extractor in ``find_well_images_and_masks`` to fix that.
    """
    s = re.escape(sep)
    return re.compile(
        rf"^(?P<exp>[^{s}]+){s}(?P<channel>[^{s}]*){s}"
        rf"(?P<well>[^{s}]+){s}(?P<fov>[^{s}]+){s}(?P<tp>[^{s}.]+)",
        re.I,
    )


def _norm_well(raw: str) -> Optional[str]:
    normalized = _normalize_well_token(raw)
    return normalized or None


class ImgRef:
    """Pointer to an image on disk or inside a zip (possibly nested)."""

    __slots__ = ("disk_path", "zip_path", "zip_member")

    def __init__(self, disk_path: Optional[Path] = None,
                 zip_path: Optional[Path] = None,
                 zip_member: Optional[str] = None) -> None:
        self.disk_path = disk_path
        self.zip_path = zip_path
        self.zip_member = zip_member

    @property
    def name(self) -> str:
        if self.disk_path:
            return self.disk_path.name
        if self.zip_member:
            return Path(self.zip_member.split("::")[-1]).name
        return "unknown"

    @property
    def full_path_str(self) -> str:
        if self.disk_path:
            return str(self.disk_path)
        if self.zip_path and self.zip_member:
            return f"{self.zip_path}  >>  {self.zip_member}"
        return "unknown"


def read_member_bytes(zip_path: Path, member: str) -> Optional[bytes]:
    """Read bytes of a zip member; handles nested 'outer::inner' notation."""
    return _preview_read_member_bytes(zip_path=zip_path, member=member, logger=_logger)


def open_imgref_as_array(ref: ImgRef, greyscale: bool = False):
    """Load an image as a numpy array at full native bit depth."""
    return _preview_open_imgref_as_array(
        ref=ref,
        greyscale=greyscale,
        np_available=_NP_AVAILABLE,
        tifffile_available=_TIFFFILE_AVAILABLE,
        pil_available=_PIL_AVAILABLE,
        tifffile_module=_tifffile,
        pil_image_module=_PILImage if _PIL_AVAILABLE else None,
        np_module=_np,
        io_module=io,
        read_member_bytes_fn=read_member_bytes,
        logger=_logger,
    )


def _default_fov_tp_extractor(stem: str) -> Tuple[str, str]:
    """5-field-regex fallback used when callers don't provide a schema extractor.

    Hardcodes ``_`` because the no-pipeline-info path can't know the
    real separator. Callers with a known separator should build a
    schema extractor (``viewer_state.make_schema_extractor``) or
    :func:`make_default_fov_tp_extractor` with the real sep instead.
    """
    m = _FNAME_RE.match(stem)
    if m:
        return m.group("fov"), m.group("tp")
    _logger.debug("_FNAME_RE no match: stem=%r", stem)
    return "unknown", "unknown"


def make_default_fov_tp_extractor(sep: str):
    """Return a 5-field fallback extractor using *sep* as the separator."""
    if sep == "_":
        return _default_fov_tp_extractor
    pattern = _build_fname_re(sep)

    def _extract(stem: str) -> Tuple[str, str]:
        m = pattern.match(stem)
        if m:
            return m.group("fov"), m.group("tp")
        # Last-ditch: also try the underscore form so a folder mixing
        # separators doesn't blank every legacy file.
        m = _FNAME_RE.match(stem)
        if m:
            return m.group("fov"), m.group("tp")
        return "unknown", "unknown"

    return _extract


def extract_pipeline_fields(stem: str, pipeline_info: Optional[dict]) -> Dict[str, str]:
    """Parse *stem* into schema fields from pipeline_info.json when available."""
    if not pipeline_info:
        return {}
    sep = str(pipeline_info.get("separator", "_"))
    schema_fields = [
        str(f).strip() for f in (pipeline_info.get("schema_fields", []) or [])
        if str(f).strip()
    ]
    if not schema_fields:
        schema = str(pipeline_info.get("schema", "")).strip()
        schema_fields = [f.strip() for f in schema.split(":") if f.strip()]
    if not schema_fields:
        return {}
    parts = stem.split(sep)
    return {field: (parts[i] if i < len(parts) else "") for i, field in enumerate(schema_fields)}


def classify_member(
    name: str,
    fluor_lower: str,
    _fov_tp_extractor=None,
    _pipeline_info: Optional[dict] = None,
) -> Tuple[str, str, str]:
    """Return (kind, fov, tp) where kind is fluor/tophat/mask/overlay/smfish/''."""
    kind, fov, tp = _preview_classify_member(
        name=name,
        fluor_lower=fluor_lower,
        mask_re=_MASK_RE,
        overlay_re=_OVERLAY_RE,
        tophat_fluor_re=_TOPHAT_FLUOR_RE,
        fov_tp_extractor=_fov_tp_extractor or _default_fov_tp_extractor,
        pipeline_fields_extractor=lambda stem: extract_pipeline_fields(stem, _pipeline_info),
    )
    if _debug_flags.review_image_channel_switch_debug_enabled():
        _logger.debug(
            "[RI-CHSW step 5] classify_member name=%r fluor=%r -> kind=%r fov=%r tp=%r",
            name, fluor_lower, kind, fov, tp,
        )
    return kind, fov, tp


def scan_zip_members(
    zip_path: Path,
    fluor_lower: str,
    member_prefix: str = "",
    _fov_tp_extractor=None,
    _pipeline_info: Optional[dict] = None,
):
    """Scan a zip file (or nested zip via member_prefix) for fluor/overlay/mask/tophat images."""
    return _preview_scan_zip_members(
        zip_path=zip_path,
        fluor_lower=fluor_lower,
        image_exts=_IMAGE_EXTS,
        classify_member_fn=classify_member,
        imgref_factory=lambda p, m: ImgRef(zip_path=p, zip_member=m),
        logger=_logger,
        member_prefix=member_prefix,
        fov_tp_extractor=_fov_tp_extractor,
        pipeline_info=_pipeline_info,
    )


def find_well_zips_in_dir(data_dir: Path, well_token: str) -> List[Path]:
    """Return [_out.zip, <well>.zip] for this well token, _out first."""
    out_zips, plain_zips = [], []
    for p in sorted(data_dir.glob("*.zip")):
        if p.name.startswith("."):
            continue
        m = _OUT_ZIP_RE.match(p.name)
        if m and _norm_well(m.group(1) + m.group(2)) == well_token:
            out_zips.append(p)
            continue
        m2 = _PLAIN_ZIP_RE.match(p.name)
        if m2 and _norm_well(m2.group(1) + m2.group(2)) == well_token:
            plain_zips.append(p)
    return out_zips + plain_zips


def find_plain_well_zips_in_dir(in_dir: Path, well_token: str) -> List[Path]:
    """Return plain <well>.zip paths from the input directory."""
    result = []
    for p in sorted(in_dir.glob("*.zip")):
        if p.name.startswith("."):
            continue
        m = _PLAIN_ZIP_RE.match(p.name)
        if m and _norm_well(m.group(1) + m.group(2)) == well_token:
            result.append(p)
    return result


def find_out_well_zips_in_dir(out_dir: Path, well_token: str) -> List[Path]:
    """Return <well>_out.zip paths from the output directory."""
    result = []
    for p in sorted(out_dir.glob("*.zip")):
        if p.name.startswith("."):
            continue
        m = _OUT_ZIP_RE.match(p.name)
        if m and _norm_well(m.group(1) + m.group(2)) == well_token:
            result.append(p)
    return result


def find_well_subfolder(parent_dir: Path, well_token: str) -> Optional[Path]:
    """Return well subfolder matching token, accepting both A1/A01 forms."""
    return _find_well_subfolder_path(parent_dir, well_token)


def scan_folder_members(
    folder_path: Path,
    fluor_lower: str,
    _fov_tp_extractor=None,
    _pipeline_info: Optional[dict] = None,
):
    """Scan a plain disk folder for fluor/overlay/mask/tophat/smfish images."""
    fluor: Dict[Tuple[str, str], ImgRef] = {}
    overlay: Dict[Tuple[str, str], ImgRef] = {}
    mask: Dict[Tuple[str, str], ImgRef] = {}
    tophat_fluor: Dict[Tuple[str, str], ImgRef] = {}
    smfish: Dict[Tuple[str, str], ImgRef] = {}
    try:
        _logger.info("Scanning folder %s", folder_path)
        for p in sorted(folder_path.iterdir()):
            if not p.is_file():
                continue
            if p.suffix.lower() not in _IMAGE_EXTS or p.name.startswith("."):
                continue
            kind, fov, tp = classify_member(
                p.name, fluor_lower, _fov_tp_extractor, _pipeline_info=_pipeline_info,
            )
            if not kind:
                continue
            key = (fov, tp)
            ref = ImgRef(disk_path=p)
            if kind == "fluor":
                fluor.setdefault(key, ref)
            elif kind == "tophat":
                tophat_fluor.setdefault(key, ref)
            elif kind == "overlay":
                overlay.setdefault(key, ref)
            elif kind == "mask":
                mask.setdefault(key, ref)
            elif kind == "smfish":
                smfish.setdefault(key, ref)
    except Exception as exc:
        _logger.warning("Failed scanning folder %s: %s", folder_path, exc)
    return fluor, overlay, mask, tophat_fluor, smfish


def find_well_images_and_masks(
    data_dir: Optional[Path],
    well_label: str,
    fluor_token: str = "GFP",
    in_dir: Optional[Path] = None,
    _fov_tp_extractor=None,
    _pipeline_info: Optional[dict] = None,
):
    """Find fluor / overlay / mask / tophat images for a well across all layouts."""
    well_token = _extract_well_token(well_label)
    fluor_lower = fluor_token.lower()
    fluor: Dict[Tuple[str, str], ImgRef] = {}
    overlay: Dict[Tuple[str, str], ImgRef] = {}
    mask: Dict[Tuple[str, str], ImgRef] = {}
    tophat_fluor: Dict[Tuple[str, str], ImgRef] = {}

    _logger.info(
        "Searching images: well=%r token=%r  in_dir=%s  data_dir=%s",
        well_label, well_token,
        str(in_dir) if in_dir else "None",
        str(data_dir) if data_dir else "None",
    )
    image_load_debug = (
        _debug_flags.review_image_load_debug_enabled()
        or _debug_flags.movie_montage_load_debug_enabled()
    )
    channel_switch_debug = _debug_flags.review_image_channel_switch_debug_enabled()
    if channel_switch_debug:
        _logger.debug(
            "[RI-CHSW step 5] find_well_images_and_masks start well=%r token=%r fluor=%r",
            well_label, well_token, fluor_lower,
        )

    # ── 1. Structured in/out directory layout ────────────────────────────────
    if in_dir and in_dir.is_dir() and well_token:
        in_zips = find_plain_well_zips_in_dir(in_dir, well_token)
        for wzip in in_zips:
            g, ov, mk, th, _sm = scan_zip_members(
                wzip, fluor_lower,
                _fov_tp_extractor=_fov_tp_extractor, _pipeline_info=_pipeline_info,
            )
            for k, v in g.items():
                fluor.setdefault(k, v)
            for k, v in ov.items():
                overlay.setdefault(k, v)
            for k, v in mk.items():
                mask.setdefault(k, v)
            for k, v in th.items():
                tophat_fluor.setdefault(k, v)

    if in_dir and data_dir and data_dir.is_dir() and well_token:
        out_zips = find_out_well_zips_in_dir(data_dir, well_token)
        for wzip in out_zips:
            g, ov, mk, th, _sm = scan_zip_members(
                wzip, fluor_lower,
                _fov_tp_extractor=_fov_tp_extractor, _pipeline_info=_pipeline_info,
            )
            for k, v in g.items():
                fluor.setdefault(k, v)
            for k, v in ov.items():
                overlay.setdefault(k, v)
            for k, v in mk.items():
                mask.setdefault(k, v)
            for k, v in th.items():
                tophat_fluor.setdefault(k, v)

    # ── 1b. Well subfolder layout (folder mode, no zips) ─────────────────────
    if in_dir and in_dir.is_dir() and well_token:
        in_folder = find_well_subfolder(in_dir, well_token)
        if in_folder:
            if image_load_debug:
                _logger.info("[image-load-debug] in_folder resolved for %s -> %s", well_token, in_folder)
            g, ov, mk, th, _sm = scan_folder_members(
                in_folder, fluor_lower,
                _fov_tp_extractor=_fov_tp_extractor, _pipeline_info=_pipeline_info,
            )
            for k, v in g.items():
                fluor.setdefault(k, v)
            for k, v in ov.items():
                overlay.setdefault(k, v)
            for k, v in mk.items():
                mask.setdefault(k, v)
            for k, v in th.items():
                tophat_fluor.setdefault(k, v)
        elif image_load_debug:
            _logger.info("[image-load-debug] in_folder missing for token=%s in %s", well_token, in_dir)

    if in_dir and data_dir and data_dir.is_dir() and well_token:
        out_folder = find_well_subfolder(data_dir, well_token)
        if out_folder:
            if image_load_debug:
                _logger.info("[image-load-debug] out_folder resolved for %s -> %s", well_token, out_folder)
            g, ov, mk, th, _sm = scan_folder_members(
                out_folder, fluor_lower,
                _fov_tp_extractor=_fov_tp_extractor, _pipeline_info=_pipeline_info,
            )
            for k, v in g.items():
                fluor.setdefault(k, v)
            for k, v in ov.items():
                overlay.setdefault(k, v)
            for k, v in mk.items():
                mask.setdefault(k, v)
            for k, v in th.items():
                tophat_fluor.setdefault(k, v)
        elif image_load_debug:
            _logger.info("[image-load-debug] out_folder missing for token=%s in %s", well_token, data_dir)

    # ── 2. Flat directory: all zips in data_dir ───────────────────────────────
    if in_dir is None and data_dir and data_dir.is_dir() and well_token:
        for wzip in find_well_zips_in_dir(data_dir, well_token):
            g, ov, mk, th, _sm = scan_zip_members(
                wzip, fluor_lower,
                _fov_tp_extractor=_fov_tp_extractor, _pipeline_info=_pipeline_info,
            )
            for k, v in g.items():
                fluor.setdefault(k, v)
            for k, v in ov.items():
                overlay.setdefault(k, v)
            for k, v in mk.items():
                mask.setdefault(k, v)
            for k, v in th.items():
                tophat_fluor.setdefault(k, v)

    # ── 2b. Flat directory: <well>/ subfolders in data_dir ───────────────────
    if in_dir is None and data_dir and data_dir.is_dir() and well_token:
        flat_folder = find_well_subfolder(data_dir, well_token)
        if flat_folder:
            g, ov, mk, th, _sm = scan_folder_members(
                flat_folder, fluor_lower,
                _fov_tp_extractor=_fov_tp_extractor, _pipeline_info=_pipeline_info,
            )
            for k, v in g.items():
                fluor.setdefault(k, v)
            for k, v in ov.items():
                overlay.setdefault(k, v)
            for k, v in mk.items():
                mask.setdefault(k, v)
            for k, v in th.items():
                tophat_fluor.setdefault(k, v)

    # ── 3. Raw files on disk fallback ─────────────────────────────────────────
    search_dirs = [d for d in (in_dir, data_dir) if d and d.is_dir()] if in_dir else (
        [data_dir] if data_dir and data_dir.is_dir() else []
    )
    if not fluor and search_dirs:
        # Cap rglob recursion depth so a user who accidentally points
        # the viewer at their home directory or a project root doesn't
        # trigger a full filesystem scan. 3 levels covers the canonical
        # data-dir / well-subdir / channel-subdir layout with headroom.
        _MAX_DEPTH = 3
        for search_root in search_dirs:
            search_root_parts = len(search_root.parts)
            for p in sorted(search_root.rglob("*")):
                if (len(p.parts) - search_root_parts) > _MAX_DEPTH:
                    continue
                if p.suffix.lower() not in _IMAGE_EXTS or p.name.startswith("."):
                    continue
                kind, fov, tp = classify_member(
                    p.name, fluor_lower, _fov_tp_extractor, _pipeline_info=_pipeline_info,
                )
                if not kind:
                    continue
                if well_token:
                    parsed = extract_pipeline_fields(p.stem, _pipeline_info)
                    parsed_well = _norm_well(str(parsed.get("well", ""))) if parsed else None
                    if parsed_well:
                        if parsed_well != well_token:
                            if image_load_debug:
                                _logger.info(
                                    "[image-load-debug] skip %s parsed_well=%s token=%s",
                                    p.name, parsed_well, well_token,
                                )
                            continue
                    elif _fov_tp_extractor is None:
                        m = _FNAME_RE.match(p.stem)
                        fw = _norm_well(m.group("well")) if m else None
                        if fw and fw != well_token:
                            if image_load_debug:
                                _logger.info(
                                    "[image-load-debug] skip %s legacy_well=%s token=%s",
                                    p.name, fw, well_token,
                                )
                            continue
                        if not fw and not _well_token_matches_text(p.name, well_token):
                            if image_load_debug:
                                _logger.info(
                                    "[image-load-debug] skip %s no well token match target=%s",
                                    p.name, well_token,
                                )
                            continue
                    elif not _well_token_matches_text(p.name, well_token):
                        if image_load_debug:
                            _logger.info(
                                "[image-load-debug] skip %s schema path no well token match target=%s",
                                p.name, well_token,
                            )
                        continue
                ref = ImgRef(disk_path=p)
                if kind == "fluor":
                    fluor.setdefault((fov, tp), ref)
                elif kind == "tophat":
                    tophat_fluor.setdefault((fov, tp), ref)
                elif kind == "overlay":
                    overlay.setdefault((fov, tp), ref)
                else:
                    mask.setdefault((fov, tp), ref)

    if not fluor:
        _logger.warning("No fluor images found for %r (token=%r)", well_label, well_token)
    if not overlay:
        _logger.info("No overlay images found for %r (token=%r)", well_label, well_token)
    if not mask:
        _logger.warning("No masks found for %r (token=%r)", well_label, well_token)
    if tophat_fluor:
        _logger.info("Pre-filtered tophat images found for %r (%d)", well_label, len(tophat_fluor))
    if channel_switch_debug:
        _logger.debug(
            "[RI-CHSW step 5] find_well_images_and_masks done fluor=%d tophat=%d overlay=%d mask=%d",
            len(fluor), len(tophat_fluor), len(overlay), len(mask),
        )

    return (
        dict(sorted(fluor.items())),
        dict(sorted(overlay.items())),
        dict(sorted(mask.items())),
        dict(sorted(tophat_fluor.items())),
    )
