"""Shared image resolution helpers used by Review/Preview/Scatter/smFISH tabs.

This module is the canonical source for:
- filename suffix families (`raw`, `tophat`, `smfish`, `overlay`, `mask`)
- per-file kind classification
- per-frame kind precedence selection for `(fov, timepoint)` keys

`resolve_channel_frame_refs` is the high-level API for tabs/controllers to
obtain display-ready frame refs with deterministic reason codes.
"""

from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Mapping, Optional


OUTPUT_SUFFIXES: dict[str, tuple[str, ...]] = {
    "mask": ("_labels.tif", "_labels.tiff", "_labels.png"),
    "overlay": ("_overlay.png", "_overlay.jpg", "_overlay.jpeg", "_overlay.tif", "_overlay.tiff"),
    "tophat": (
        "_tophat.tif",
        "_tophat.tiff",
        "_tophat_{channel}.tif",
        "_tophat_{channel}.tiff",
    ),
    # Backwards-compatible alias for old callers.
    "fluor_processed": (
        "_tophat.tif",
        "_tophat.tiff",
        "_tophat_{channel}.tif",
        "_tophat_{channel}.tiff",
    ),
    "fluor_raw": (),
    "smfish": (
        "_smfish.tif",
        "_smfish.tiff",
        "_smfish_{channel}.tif",
        "_smfish_{channel}.tiff",
    ),
}


OUTPUT_KIND_PRECEDENCE: tuple[str, ...] = (
    "mask",
    "overlay",
    "smfish",
    "tophat",
    "fluor_processed",
)


CANONICAL_SUFFIX_FAMILIES: tuple[str, ...] = (
    "fluor_raw",
    "tophat",
    "smfish",
    "overlay",
    "mask",
)


CHANNEL_FRAME_PRECEDENCE: tuple[str, ...] = (
    "smfish",
    "tophat",
    "fluor_raw",
    "overlay",
    "mask",
)


KIND_ALIASES: dict[str, str] = {
    "fluor_processed": "tophat",
}

_WELL_TOKEN_RE = re.compile(r"^([A-Ha-h])(\d{1,2})$")
_WELL_TOKEN_IN_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Ha-h](?:0?[1-9]|1[0-2]))(?![A-Za-z0-9])"
)


@dataclass(frozen=True)
class ResolvedFrameRef:
    """Resolved frame selection for a single `(fov, timepoint)` key."""

    key: tuple[str, str]
    kind: str
    ref: object | None
    reason: str


def output_suffixes_for_kind(output_kind: str, *, target_channel: str = "") -> tuple[str, ...]:
    """Return resolved suffixes for output kind, applying channel templates."""
    canonical_kind = KIND_ALIASES.get(output_kind, output_kind)
    raw = OUTPUT_SUFFIXES.get(canonical_kind, ())
    channel = str(target_channel or "").strip().lower()
    out: list[str] = []
    for suffix in raw:
        if "{channel}" in suffix:
            if not channel:
                continue
            out.append(suffix.format(channel=channel))
        else:
            out.append(suffix)
    return tuple(out)


def classify_filename_kind(name: str, *, fluor_token: str = "") -> tuple[str, str]:
    """Classify filename into canonical kind and return (kind, stem_without_kind_suffix)."""
    p = Path(str(name or "").strip())
    if not p.name:
        return "", ""
    lower_name = p.name.lower()

    for kind in OUTPUT_KIND_PRECEDENCE:
        suffixes = output_suffixes_for_kind(kind, target_channel=fluor_token)
        for suffix in suffixes:
            lowered = suffix.lower()
            if lower_name.endswith(lowered):
                stripped = p.name[: -len(suffix)] if len(suffix) else p.name
                return kind, Path(stripped).stem

    return "fluor_raw", p.stem


def normalize_row_filename(filename: str) -> str:
    """Normalize metadata-provided filename to basename only."""
    raw = str(filename or "").strip()
    if not raw:
        return ""
    return Path(raw).name


def normalize_numeric_token(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return f"{float(raw):g}"
    except Exception:
        return raw


def normalize_well_token(value: object) -> str:
    """Normalize well tokens so `A1` and `A01` are treated as identical."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    m = _WELL_TOKEN_RE.match(raw)
    if not m:
        return raw.upper()
    return f"{m.group(1).upper()}{int(m.group(2)):02d}"


def find_well_subfolder_path(parent_dir: Path, well_token: object) -> Optional[Path]:
    """Find matching well folder path under *parent_dir* for A1/A01 token forms."""
    normalized_target = normalize_well_token(well_token)
    if not normalized_target:
        return None

    requested = str(well_token or "").strip()
    if requested:
        direct = parent_dir / requested
        if direct.is_dir():
            return direct

    padded = parent_dir / normalized_target
    if padded.is_dir():
        return padded

    for entry in sorted(parent_dir.iterdir()):
        if entry.is_dir() and normalize_well_token(entry.name) == normalized_target:
            return entry
    return None


def well_token_matches_text(text: object, well_token: object) -> bool:
    """Return True if *text* contains a well token equal to *well_token*."""
    normalized_target = normalize_well_token(well_token)
    if not normalized_target:
        return False

    raw_text = str(text or "").strip()
    if not raw_text:
        return False

    matched_tokens = _WELL_TOKEN_IN_TEXT_RE.findall(raw_text)
    if matched_tokens:
        return any(normalize_well_token(tok) == normalized_target for tok in matched_tokens)

    row = normalized_target[0]
    col = str(int(normalized_target[1:]))
    variants = {normalized_target.lower(), f"{row}{col}".lower()}
    lowered = raw_text.lower()
    return any(v in lowered for v in variants)


def resolve_ref_by_fov_tp(
    refs: Mapping[tuple[str, str], object],
    *,
    fov_raw: str,
    tp_raw: str,
    norm_timepoint: Callable[[object], str],
) -> Optional[object]:
    """Resolve image ref by exact key, then normalized key."""
    exact = refs.get((fov_raw, tp_raw))
    if exact is not None:
        return exact
    fov_norm = normalize_numeric_token(fov_raw)
    tp_norm = norm_timepoint(tp_raw)
    for (k_fov, k_tp), k_ref in refs.items():
        if normalize_numeric_token(k_fov) == fov_norm and norm_timepoint(k_tp) == tp_norm:
            return k_ref
    return None


def resolve_channel_frame_refs(
    *,
    refs_by_kind: Mapping[str, Mapping[tuple[str, str], object]],
    precedence: tuple[str, ...] = CHANNEL_FRAME_PRECEDENCE,
    expected_keys: set[tuple[str, str]] | None = None,
    logger=None,
    context: Mapping[str, object] | None = None,
) -> dict[tuple[str, str], ResolvedFrameRef]:
    """Resolve one canonical frame ref per `(fov, timepoint)` using precedence.

    Args:
        refs_by_kind: Mapping of canonical output kind -> per-key references.
        precedence: Kind ordering to evaluate for each key.
        expected_keys: Optional key set to include unresolved placeholders for.
        logger: Optional logger used for INFO/DEBUG diagnostics.
        context: Optional metadata (well/channel/paths/etc.) added to logs.

    Returns:
        Mapping from key -> `ResolvedFrameRef`, including reason codes:
        - `selected_first_preference`
        - `selected_fallback_preference`
        - `missing_all_candidates`
    """
    normalized_refs_by_kind: dict[str, Mapping[tuple[str, str], object]] = {}
    for in_kind, refs in refs_by_kind.items():
        canonical = KIND_ALIASES.get(in_kind, in_kind)
        if canonical in normalized_refs_by_kind:
            merged = dict(normalized_refs_by_kind[canonical])
            merged.update(refs)
            normalized_refs_by_kind[canonical] = merged
        else:
            normalized_refs_by_kind[canonical] = refs

    valid_precedence = tuple(
        KIND_ALIASES.get(kind, kind)
        for kind in precedence
        if KIND_ALIASES.get(kind, kind) in CANONICAL_SUFFIX_FAMILIES
    )
    if not valid_precedence:
        valid_precedence = CHANNEL_FRAME_PRECEDENCE

    if logger is not None:
        logger.info(
            "image_resolver context=%s precedence=%s",
            dict(context or {}),
            " > ".join(valid_precedence),
        )

    discovered: set[tuple[str, str]] = set(expected_keys or set())
    counts: dict[str, int] = {}
    for kind in CANONICAL_SUFFIX_FAMILIES:
        per_kind = normalized_refs_by_kind.get(kind, {})
        counts[kind] = len(per_kind)
        discovered.update(per_kind.keys())

    if logger is not None:
        logger.info(
            "image_resolver discovered counts: %s",
            ", ".join(f"{kind}={counts.get(kind, 0)}" for kind in CANONICAL_SUFFIX_FAMILIES),
        )

    resolved: dict[tuple[str, str], ResolvedFrameRef] = {}
    for key in sorted(discovered, key=lambda k: (str(k[0]), str(k[1]))):
        chosen_kind = ""
        chosen_ref: object | None = None
        for idx, kind in enumerate(valid_precedence):
            ref = normalized_refs_by_kind.get(kind, {}).get(key)
            if ref is None:
                continue
            chosen_kind = kind
            chosen_ref = ref
            reason = "selected_first_preference" if idx == 0 else "selected_fallback_preference"
            break
        else:
            reason = "missing_all_candidates"
            chosen_kind = "missing"

        resolved[key] = ResolvedFrameRef(
            key=key,
            kind=chosen_kind,
            ref=chosen_ref,
            reason=reason,
        )
        if logger is not None:
            logger.debug(
                "image_resolver decision key=%s kind=%s reason=%s",
                key,
                chosen_kind,
                reason,
            )

    return resolved


def schema_fields(pipeline_info: Optional[dict]) -> list[str]:
    if not pipeline_info:
        return []
    fields = [
        str(f).strip()
        for f in (pipeline_info.get("schema_fields", []) or [])
        if str(f).strip()
    ]
    if fields:
        return fields
    schema = str(pipeline_info.get("schema", "")).strip()
    if not schema:
        return []
    return [f.strip() for f in schema.split(":") if f.strip()]


def parse_stem_with_schema(stem: str, pipeline_info: Optional[dict]) -> dict[str, str]:
    fields = schema_fields(pipeline_info)
    if not fields:
        return {}
    sep = str((pipeline_info or {}).get("separator", "_"))
    parts = stem.split(sep)
    return {field: (parts[i] if i < len(parts) else "") for i, field in enumerate(fields)}


def compose_stem_from_schema(values: dict[str, str], pipeline_info: Optional[dict]) -> str:
    fields = schema_fields(pipeline_info)
    if not fields:
        return ""
    sep = str((pipeline_info or {}).get("separator", "_"))
    parts: list[str] = []
    for field in fields:
        val = str(values.get(field, "")).strip()
        if val:
            parts.append(val)
    return sep.join(parts)


def resolve_filename_candidates(
    filename: str,
    *,
    pipeline_info: Optional[dict],
    target_channel: str = "",
    output_kind: str = "",
    filename_variants_fn: Callable[[str], list[str]] | None = None,
) -> list[str]:
    """Canonical filename resolver used across image-consuming tabs.

    - Normalizes input filename to basename.
    - Uses schema metadata from pipeline_info.json when available.
    - Never drops tokens from output name generation.
    """
    base_name = normalize_row_filename(filename)
    if not base_name:
        return []
    p = Path(base_name)
    stem = p.stem

    # 1) Schema-driven candidate (authoritative when available).
    out: list[str] = []
    fields = parse_stem_with_schema(stem, pipeline_info)
    if fields:
        if target_channel:
            fields["channel"] = target_channel
        schema_stem = compose_stem_from_schema(fields, pipeline_info)
        if schema_stem:
            out.append(f"{schema_stem}{p.suffix}" if p.suffix else schema_stem)

    # 2) Original filename fallback (still rooted to active dataset dirs).
    out.append(base_name)

    # 3) Expand output suffixes where needed.
    if output_kind:
        resolved_channel = target_channel or str(fields.get("channel", "") if fields else "")
        suffixes = output_suffixes_for_kind(output_kind, target_channel=resolved_channel)
        expanded: list[str] = []

        for stem_name in [Path(name).stem for name in out]:
            if output_kind == "fluor_raw":
                expanded.append(f"{stem_name}{p.suffix}" if p.suffix else stem_name)
            else:
                for suffix in suffixes:
                    expanded.append(f"{stem_name}{suffix}")
        out = expanded

    # 4) Expand with variant function (well padding etc).
    if filename_variants_fn is not None:
        variants: list[str] = []
        for name in out:
            variants.extend(filename_variants_fn(name))
        out = variants or out

    # Deduplicate while preserving order.
    ordered: list[str] = []
    seen: set[str] = set()
    for name in out:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered
