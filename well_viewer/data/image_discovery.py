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

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

import io
import logging
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path

from core import debug_flags as _debug_flags
from well_viewer.data.data_loading import extract_well_token as _extract_well_token
from well_viewer.data.image_resolver import (
    find_well_subfolder_path as _find_well_subfolder_path,
    normalize_well_token as _normalize_well_token,
    output_suffixes_for_kind as _output_suffixes_for_kind,
    _WELL_TOKEN_IN_TEXT_RE,
    well_token_matches_text as _well_token_matches_text,
)
from well_viewer.data.zip_members import (
    classify_member as _preview_classify_member,
    open_imgref_as_array as _preview_open_imgref_as_array,
    read_member_bytes as _preview_read_member_bytes,
    ScanResult,
    merge_partial,
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


def _build_fname_re(sep: str) -> re.Pattern[str]:
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


def _norm_well(raw: str) -> str | None:
    normalized = _normalize_well_token(raw)
    return normalized or None


class ImgRef:
    """Pointer to an image on disk or inside a zip (possibly nested)."""

    __slots__ = ("disk_path", "zip_path", "zip_member")

    def __init__(self, disk_path: Path | None = None,
                 zip_path: Path | None = None,
                 zip_member: str | None = None) -> None:
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


def read_member_bytes(zip_path: Path, member: str) -> bytes | None:
    """Read bytes of a zip member; handles nested 'outer::inner' notation."""
    return _preview_read_member_bytes(zip_path=zip_path, member=member, logger=_logger)


# ── Decoded-array cache ──────────────────────────────────────────────────────
# ``open_imgref_as_array`` is the single choke point every pixel-decoding path
# funnels through (Image Table, Cell Browser, Review Image, Playground, scatter
# cell viewer, auto-threshold). Decoding a zip member (inflate + tifffile/PIL)
# is the dominant per-image cost — the zip-handle cache below in
# ``zipfile_cache`` only saves the file-open/central-dir parse, not the decode.
# Memoizing decoded arrays here makes a near-identical re-render (a second Image
# Table Generate, a crop/zoom/LUT tweak) nearly free on the decode axis, and it
# collapses the Image Table's prefetch double-decode into one (the warm pass
# fills the cache, the render pass hits it).
DEFAULT_MAX_DECODE_ENTRIES = 2048


def _default_max_decode_bytes() -> int:
    """Byte budget for decoded pixels: ~15% of RAM, within [512 MiB, 2 GiB].

    This cache is the **only** owner of decoded frames — the Image Table's
    render model and its cell widgets keep shapes, not arrays — so this budget
    is the app's ceiling for decoded pixels rather than a hint. That makes it a
    number that has to survive the machine it runs on: microscopy frames are
    commonly 2048×2048, which is 16 MB once widened to float32, so a 96-well
    plate walk is ~1.5 GB of pixels and a multi-timepoint grid is several times
    that. A fixed 4 GiB budget on a 16 GB laptop (that is also holding Qt, the
    dataframes, and possibly a segmentation stack) drives the machine into swap
    and then into failed allocations, which surface as images that decode for
    some cells and not others. Scaling to installed RAM keeps a large grid slow
    (LRU eviction, so frames re-decode) instead of unstable.

    Entries are capped too, so a dataset of very small frames can't grow the
    bookkeeping without bound.
    """
    try:
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):  # pragma: no cover - platform
        return 1 << 30
    return int(max(512 * (1 << 20), min(2 * (1 << 30), total * 0.15)))


DEFAULT_MAX_DECODE_BYTES = _default_max_decode_bytes()


class _DecodedArrayCache:
    """Bounded LRU of decoded numpy arrays, keyed by resolved image identity.

    Thread-safe (the Image Table prefetch warms it from a ``ThreadPoolExecutor``).
    Bounded by BOTH an entry count and a total-bytes budget so a large plate walk
    churns via LRU eviction instead of growing without limit. Values are shared by
    reference — callers must treat a returned array as READ-ONLY (the hot consumers
    already copy-on-write: ``crop_tool.apply_to_array`` returns a view, the RGB
    composer does ``a.astype(float32)``, ``load_mask_array`` copies via ``rint``)."""

    def __init__(self, max_entries: int = DEFAULT_MAX_DECODE_ENTRIES,
                 max_bytes: int = DEFAULT_MAX_DECODE_BYTES) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._store: OrderedDict[tuple, tuple] = OrderedDict()
        self._total_bytes = 0
        self._lock = threading.Lock()

    def get(self, key: tuple) -> np.ndarray | None:
        with self._lock:
            hit = self._store.get(key)
            if hit is None:
                return None
            self._store.move_to_end(key)
            return hit[0]

    def put(self, key: tuple, arr) -> None:
        nbytes = int(getattr(arr, "nbytes", 0) or 0)
        # An array bigger than the whole budget can never fit; don't thrash on it.
        if nbytes > self._max_bytes:
            return
        with self._lock:
            old = self._store.pop(key, None)
            if old is not None:
                self._total_bytes -= old[1]
            self._store[key] = (arr, nbytes)
            self._total_bytes += nbytes
            while self._store and (len(self._store) > self._max_entries
                                   or self._total_bytes > self._max_bytes):
                _k, (_a, nb) = self._store.popitem(last=False)
                self._total_bytes -= nb

    def invalidate(self) -> None:
        with self._lock:
            self._store.clear()
            self._total_bytes = 0


_DECODE_CACHE = _DecodedArrayCache()


def invalidate_decode_cache() -> None:
    """Drop every cached decoded array — called on dataset load (new pixels)."""
    _DECODE_CACHE.invalidate()


def _source_mtime_ns(ref: ImgRef) -> int:
    """mtime of the ref's backing file (disk image or outer zip), 0 if unavailable.

    Part of the cache key so a pipeline re-run that rewrites a zip within the same
    session auto-misses instead of serving stale pixels (mirrors
    ``zipfile_cache._get_or_open``)."""
    src = ref.disk_path or ref.zip_path
    if src is None:
        return 0
    try:
        return os.stat(src).st_mtime_ns
    except OSError:
        return 0


def open_imgref_as_array(ref: ImgRef, greyscale: bool = False) -> np.ndarray | None:
    """Load an image as a numpy array at full native bit depth (memoized).

    Decoded arrays are cached in a bounded LRU keyed by the *resolved* image
    identity — all token normalization (``_match_image_ref``) happens upstream, so
    keying on the concrete ref avoids the ``"1.0"`` vs ``"1"`` pitfalls. A returned
    array is shared by reference; **treat it as read-only** (see ``_DecodedArrayCache``)."""
    if ref is None:
        return None
    key = None
    fps = ref.full_path_str
    if fps and fps != "unknown":
        key = (fps, bool(greyscale), _source_mtime_ns(ref))
        hit = _DECODE_CACHE.get(key)
        if hit is not None:
            return hit
    arr = _preview_open_imgref_as_array(
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
    if key is not None and arr is not None:
        _DECODE_CACHE.put(key, arr)
    return arr


def _default_fov_tp_extractor(stem: str) -> tuple[str, str]:
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

    def _extract(stem: str) -> tuple[str, str]:
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


def extract_pipeline_fields(stem: str, pipeline_info: dict | None) -> dict[str, str]:
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
    _pipeline_info: dict | None = None,
) -> tuple[str, str, str]:
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
    _pipeline_info: dict | None = None,
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


def find_well_zips_in_dir(data_dir: Path, well_token: str) -> list[Path]:
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


def find_plain_well_zips_in_dir(in_dir: Path, well_token: str) -> list[Path]:
    """Return plain <well>.zip paths from the input directory."""
    result = []
    for p in sorted(in_dir.glob("*.zip")):
        if p.name.startswith("."):
            continue
        m = _PLAIN_ZIP_RE.match(p.name)
        if m and _norm_well(m.group(1) + m.group(2)) == well_token:
            result.append(p)
    return result


def find_out_well_zips_in_dir(out_dir: Path, well_token: str) -> list[Path]:
    """Return <well>_out.zip paths from the output directory."""
    result = []
    for p in sorted(out_dir.glob("*.zip")):
        if p.name.startswith("."):
            continue
        m = _OUT_ZIP_RE.match(p.name)
        if m and _norm_well(m.group(1) + m.group(2)) == well_token:
            result.append(p)
    return result


def find_well_subfolder(parent_dir: Path, well_token: str) -> Path | None:
    """Return well subfolder matching token, accepting both A1/A01 forms."""
    return _find_well_subfolder_path(parent_dir, well_token)


def scan_folder_members(
    folder_path: Path,
    fluor_lower: str,
    _fov_tp_extractor=None,
    _pipeline_info: dict | None = None,
):
    """Scan a plain disk folder for fluor/overlay/mask/tophat/smfish images.

    Returns :class:`ScanResult` inventories — same sibling contract as
    :func:`scan_zip_members`: a directory that could not be walked to the end
    yields what it gathered, flagged, rather than a short dict indistinguishable
    from a folder that really holds that little.
    """
    fluor: ScanResult = ScanResult()
    overlay: ScanResult = ScanResult()
    mask: ScanResult = ScanResult()
    tophat_fluor: ScanResult = ScanResult()
    smfish: ScanResult = ScanResult()
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
        _logger.warning(
            "Failed scanning folder %s: %s. Partial inventory returned: "
            "%d fluor, %d tophat, %d smfish, %d overlay(s), %d mask(s) — images "
            "after the failure point are missing from it, so this well may show "
            "fewer frames than the folder holds.",
            folder_path, exc,
            len(fluor), len(tophat_fluor), len(smfish), len(overlay), len(mask),
        )
        for _d in (fluor, overlay, mask, tophat_fluor, smfish):
            _d.mark_partial(f"scanning folder {folder_path} failed: {exc}")
    return fluor, overlay, mask, tophat_fluor, smfish


def _merge_scan(targets, scan_result) -> None:
    """First-hit-wins merge of one ``scan_*_members`` result into the four
    accumulator dicts.

    ``targets`` is ``(fluor, overlay, mask, tophat_fluor)``; ``scan_result`` is
    the ``(g, ov, mk, th, smfish)`` tuple returned by :func:`scan_zip_members` /
    :func:`scan_folder_members`. Mirrors the per-section ``setdefault`` loops
    the discovery function repeated once per layout (audit A4) — ``setdefault``
    preserves the first layout to supply a given ``(fov, tp)``.
    """
    fluor, overlay, mask, tophat_fluor = targets
    g, ov, mk, th, _sm = scan_result
    for k, v in g.items():
        fluor.setdefault(k, v)
    for k, v in ov.items():
        overlay.setdefault(k, v)
    for k, v in mk.items():
        mask.setdefault(k, v)
    for k, v in th.items():
        tophat_fluor.setdefault(k, v)
    # A well is assembled from up to four scans, so an accumulator is only as
    # complete as the least complete scan that fed it. Without this the flag
    # would die at the merge and the well would again look whole.
    merge_partial(fluor, g)
    merge_partial(overlay, ov)
    merge_partial(mask, mk)
    merge_partial(tophat_fluor, th)


def _raw_file_names_some_well(p: Path, _pipeline_info: dict | None) -> str | None:
    """The well *p*'s own filename names, or ``None`` when it names none.

    Runs the same three vocabularies :func:`_raw_file_well_passes` uses, in the
    same order — pipeline schema, the legacy 5-field regex, and finally the
    free-text token scan — asked in the open ("which well?") rather than against
    a target ("is it this one?"). Using anything narrower here would leave the two
    functions disagreeing about what counts as naming a well, and the free-text
    scan is precisely the branch that does the work for the common
    ``A01_GFP_f1_t0.tif`` shape, which neither parser above it matches.

    ``_WELL_TOKEN_IN_TEXT_RE`` is imported rather than re-spelled: it *is* the
    vocabulary ``well_token_matches_text`` matches with, and a second copy here
    would be free to drift from the predicate it has to agree with.
    """
    parsed = extract_pipeline_fields(p.stem, _pipeline_info)
    parsed_well = _norm_well(str(parsed.get("well", ""))) if parsed else None
    if parsed_well:
        return parsed_well
    m = _FNAME_RE.match(p.stem)
    if m:
        legacy = _norm_well(m.group("well"))
        if legacy:
            return legacy
    found = _WELL_TOKEN_IN_TEXT_RE.findall(p.name)
    return _norm_well(found[0]) if found else None


def _raw_file_passes_unknown_well(
    p: Path, _pipeline_info: dict | None, image_load_debug: bool,
) -> bool:
    """Section 3 filter for a well whose LABEL does not parse into a token.

    The label is *unknown*, which is not the same as *unrestricted* — and the
    difference is not academic. With the filter disabled entirely, a directory of
    several wells' loose files admitted all of them into this one well, and since
    they collide on ``(fov, tp)`` and the merge is first-hit-wins, the well simply
    rendered the alphabetically first *other* well's image with no sign that it
    had. A picture of A01 under a label that is not A01 is the worst outcome
    available here, worse than showing nothing.

    So the question flips: rather than "is this file mine?" — unanswerable without
    a token — ask "could this file be someone else's?". A file whose own name
    resolves to a well is a file that belongs to that well, and with no token to
    compare it against it cannot be claimed. A file that names no well at all
    contradicts nothing, which keeps the case this fallback exists for working: a
    single-well directory of images whose names carry no well field.
    """
    other = _raw_file_names_some_well(p, _pipeline_info)
    if other is None:
        return True
    if image_load_debug:
        _logger.info(
            "[image-load-debug] skip %s names well=%s but the requested well's "
            "label does not parse, so it cannot be matched", p.name, other,
        )
    return False


def _raw_file_well_passes(
    p: Path,
    well_token: str,
    _fov_tp_extractor,
    _pipeline_info: dict | None,
    image_load_debug: bool,
) -> bool:
    """Whether a raw on-disk file belongs to *well_token* (Section 3 filter).

    Returns ``False`` (skip) when the filename resolves to a *different* well,
    or — in the default-extractor path — carries no recognisable well token at
    all. Preserves the original three-way branch verbatim: schema-parsed well,
    then the legacy 5-field regex, then a free-text token match.

    With no *well_token* the branch order does not apply and
    :func:`_raw_file_passes_unknown_well` answers instead.
    """
    if not well_token:
        return _raw_file_passes_unknown_well(p, _pipeline_info, image_load_debug)
    parsed = extract_pipeline_fields(p.stem, _pipeline_info)
    parsed_well = _norm_well(str(parsed.get("well", ""))) if parsed else None
    if parsed_well:
        if parsed_well != well_token:
            if image_load_debug:
                _logger.info(
                    "[image-load-debug] skip %s parsed_well=%s token=%s",
                    p.name, parsed_well, well_token,
                )
            return False
        return True
    if _fov_tp_extractor is None:
        m = _FNAME_RE.match(p.stem)
        fw = _norm_well(m.group("well")) if m else None
        if fw and fw != well_token:
            if image_load_debug:
                _logger.info(
                    "[image-load-debug] skip %s legacy_well=%s token=%s",
                    p.name, fw, well_token,
                )
            return False
        if not fw and not _well_token_matches_text(p.name, well_token):
            if image_load_debug:
                _logger.info(
                    "[image-load-debug] skip %s no well token match target=%s",
                    p.name, well_token,
                )
            return False
        return True
    if not _well_token_matches_text(p.name, well_token):
        if image_load_debug:
            _logger.info(
                "[image-load-debug] skip %s schema path no well token match target=%s",
                p.name, well_token,
            )
        return False
    return True


def _scan_raw_disk_files(
    search_dirs,
    well_token: str,
    fluor_lower: str,
    _fov_tp_extractor,
    _pipeline_info: dict | None,
    image_load_debug: bool,
    targets,
) -> None:
    """Section 3 fallback: classify raw image files found by a depth-capped
    ``rglob`` and merge them into the accumulators (audit A4).

    Only reached when no fluor was discovered through the structured layouts.
    """
    fluor, overlay, mask, tophat_fluor = targets
    # Cap rglob recursion depth so a user who accidentally points the viewer at
    # their home directory or a project root doesn't trigger a full filesystem
    # scan. 3 levels covers the canonical data-dir / well-subdir / channel-subdir
    # layout with headroom.
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
            # No ``well_token and`` guard: a token this scan could not derive is
            # the case the filter is MOST needed for, not a reason to drop it.
            if not _raw_file_well_passes(
                p, well_token, _fov_tp_extractor, _pipeline_info, image_load_debug,
            ):
                continue
            ref = ImgRef(disk_path=p)
            if kind == "fluor":
                fluor.setdefault((fov, tp), ref)
            elif kind == "tophat":
                tophat_fluor.setdefault((fov, tp), ref)
            elif kind == "overlay":
                overlay.setdefault((fov, tp), ref)
            elif kind == "mask":
                mask.setdefault((fov, tp), ref)
            else:
                # Anything else classified — smFISH today, phase tomorrow —
                # belongs to none of these four accumulators. This used to be a
                # bare ``else`` that filed every remaining kind as a *mask*, so a
                # smFISH image was handed to ``load_mask_array``, which rounds it
                # to an int32 label image: spot intensities read as nucleus ids.
                if image_load_debug:
                    _logger.info(
                        "[image-load-debug] skip %s kind=%s has no accumulator here",
                        p.name, kind,
                    )


def _discover_from_layouts(
    in_dir: Path | None,
    data_dir: Path | None,
    well_token: str,
    fluor_lower: str,
    image_load_debug: bool,
    targets,
    _fov_tp_extractor,
    _pipeline_info: dict | None,
) -> None:
    """Run the four structured discovery layouts (audit A4), merging each into
    ``targets``: (1) ``in_dir`` plain zips + ``data_dir`` ``_out.zip``,
    (1b) ``in_dir`` / ``data_dir`` ``<well>/`` subfolders, (2) flat zips in
    ``data_dir``, (2b) flat ``<well>/`` subfolder in ``data_dir``. Sections 2/2b
    are the ``in_dir is None`` flat-directory modes. Order is significant —
    ``_merge_scan`` is first-hit-wins, so earlier layouts take precedence."""
    scan_kw = dict(_fov_tp_extractor=_fov_tp_extractor, _pipeline_info=_pipeline_info)

    # ── 1. Structured in/out directory layout ────────────────────────────────
    if in_dir and in_dir.is_dir() and well_token:
        for wzip in find_plain_well_zips_in_dir(in_dir, well_token):
            _merge_scan(targets, scan_zip_members(wzip, fluor_lower, **scan_kw))

    if in_dir and data_dir and data_dir.is_dir() and well_token:
        for wzip in find_out_well_zips_in_dir(data_dir, well_token):
            _merge_scan(targets, scan_zip_members(wzip, fluor_lower, **scan_kw))

    # ── 1b. Well subfolder layout (folder mode, no zips) ─────────────────────
    if in_dir and in_dir.is_dir() and well_token:
        in_folder = find_well_subfolder(in_dir, well_token)
        if in_folder:
            if image_load_debug:
                _logger.info("[image-load-debug] in_folder resolved for %s -> %s", well_token, in_folder)
            _merge_scan(targets, scan_folder_members(in_folder, fluor_lower, **scan_kw))
        elif image_load_debug:
            _logger.info("[image-load-debug] in_folder missing for token=%s in %s", well_token, in_dir)

    if in_dir and data_dir and data_dir.is_dir() and well_token:
        out_folder = find_well_subfolder(data_dir, well_token)
        if out_folder:
            if image_load_debug:
                _logger.info("[image-load-debug] out_folder resolved for %s -> %s", well_token, out_folder)
            _merge_scan(targets, scan_folder_members(out_folder, fluor_lower, **scan_kw))
        elif image_load_debug:
            _logger.info("[image-load-debug] out_folder missing for token=%s in %s", well_token, data_dir)

    # ── 2. Flat directory: all zips in data_dir ───────────────────────────────
    if in_dir is None and data_dir and data_dir.is_dir() and well_token:
        for wzip in find_well_zips_in_dir(data_dir, well_token):
            _merge_scan(targets, scan_zip_members(wzip, fluor_lower, **scan_kw))

    # ── 2b. Flat directory: <well>/ subfolders in data_dir ───────────────────
    if in_dir is None and data_dir and data_dir.is_dir() and well_token:
        flat_folder = find_well_subfolder(data_dir, well_token)
        if flat_folder:
            _merge_scan(targets, scan_folder_members(flat_folder, fluor_lower, **scan_kw))


def _log_find_result(well_label, well_token, targets, channel_switch_debug: bool) -> None:
    """Emit the not-found warnings + the channel-switch trace (audit A4)."""
    fluor, overlay, mask, tophat_fluor = targets
    # Said first, because it changes what every count below means: these are the
    # images that were reachable, not the images the well holds.
    partial = [d for d in targets if getattr(d, "partial", False)]
    if partial:
        _logger.warning(
            "Image discovery for %r (token=%r) is INCOMPLETE — %s. The counts "
            "below describe what could be read, not what the well contains, so "
            "Review will show fewer frames than exist.",
            well_label, well_token,
            "; ".join(dict.fromkeys(d.reason for d in partial if d.reason))
            or "a scan stopped short",
        )
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


def find_well_images_and_masks(
    data_dir: Path | None,
    well_label: str,
    fluor_token: str = "GFP",
    in_dir: Path | None = None,
    _fov_tp_extractor=None,
    _pipeline_info: dict | None = None,
):
    """Find fluor / overlay / mask / tophat images for a well across all layouts."""
    well_token = _extract_well_token(well_label)
    # A label the token parser cannot read is *unknown*, not *unrestricted*.
    # Section 3's per-file filter now handles that case itself
    # (``_raw_file_passes_unknown_well``): it admits only files that name no well
    # of their own, so another well's image can no longer be drawn under this
    # label. The trade is visible and is the right way round — a well that shows
    # nothing is a question the user can act on; a well that shows a neighbour's
    # picture is one they cannot even see.
    if not well_token and str(well_label or "").strip():
        _logger.warning(
            "Image discovery: %r does not parse as a well label. The loose-file "
            "scan therefore accepts only images whose own filenames name no "
            "well; any file that names a well is skipped, because there is no "
            "token to match it against. This well may show fewer images than "
            "the directory holds.",
            well_label,
        )
    fluor_lower = fluor_token.lower()
    fluor: ScanResult = ScanResult()
    overlay: ScanResult = ScanResult()
    mask: ScanResult = ScanResult()
    tophat_fluor: ScanResult = ScanResult()

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

    targets = (fluor, overlay, mask, tophat_fluor)

    # ── 1/1b/2/2b. Structured + flat directory layouts ───────────────────────
    _discover_from_layouts(
        in_dir, data_dir, well_token, fluor_lower, image_load_debug,
        targets, _fov_tp_extractor, _pipeline_info,
    )

    # ── 3. Raw files on disk fallback ─────────────────────────────────────────
    search_dirs = [d for d in (in_dir, data_dir) if d and d.is_dir()] if in_dir else (
        [data_dir] if data_dir and data_dir.is_dir() else []
    )
    if not fluor and search_dirs:
        _scan_raw_disk_files(
            search_dirs, well_token, fluor_lower,
            _fov_tp_extractor, _pipeline_info, image_load_debug, targets,
        )

    _log_find_result(well_label, well_token, targets, channel_switch_debug)

    # ``derive`` and not ``dict(...)``: sorting through the plain constructor is
    # exactly the copy that silently drops the partial flag, which would put the
    # caller back where it started — holding a short inventory with no way to ask
    # whether it is short because the well is or because a scan stopped.
    return (
        fluor.derive(sorted(fluor.items())),
        overlay.derive(sorted(overlay.items())),
        mask.derive(sorted(mask.items())),
        tophat_fluor.derive(sorted(tophat_fluor.items())),
    )
