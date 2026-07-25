"""Single source of truth for image filenames, cell identity, and the
suffix/role catalog.

Three concerns owned here, deliberately co-located so they cannot drift:

1. **CellKey identity** — `(well, fov, timepoint, nucleus_id)` with one
   canonical normalisation chain. Every site that joins CSV-row data to
   image-derived data builds keys through `CellKey.from_row` /
   `CellKey.from_parsed` / `cell_keys_for_dataframe`.

2. **Filename parse/build** — `parse_image_filename` and
   `build_image_filename` are exact inverses driven by a
   `PipelineSchema`. The schema is loaded once from `pipeline_info.json`
   via `load_schema(...)`; every parser/builder takes it as an argument,
   so there are no parallel regex fallbacks or hardcoded indices.

3. **Suffix/role catalog** — `ROLE_CATALOG` declares the suffix, output
   extensions, channel-scoping, and precedence for each role
   (`fluor`/`tophat`/`mask`/`overlay`/`smfish`). `classify_member`
   (read side) and `build_processed_output_path` (write side) both
   consume it instead of duplicating suffix regexes or hardcoding
   suffix strings inline.

This module is stdlib + pandas only — no PySide6 — so the pipeline
(`process_microscopy.py`) can import it without violating the layering
contract in ARCHITECTURE §6.2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from collections.abc import Mapping

from util.well_token import canonical_well_label

# pandas is imported lazily inside the Series-level helpers so the
# pipeline (process_microscopy.py) can import this module without
# pulling in pandas as a transitive dependency. The cell-key and
# filename-parsing primitives all work without pandas.
if TYPE_CHECKING:
    import pandas as pd  # noqa: F401


# ── Suffix / role catalog ────────────────────────────────────────────────


class Role(str, Enum):
    """Canonical image roles. Values match the legacy ``kind`` strings
    so existing call sites that branch on ``kind == "smfish"`` keep
    working when given ``role.value``."""

    FLUOR_RAW = "fluor"
    FLUOR_TOPHAT = "tophat"
    MASK = "mask"
    OVERLAY = "overlay"
    SMFISH = "smfish"
    PHASE = "phase"


@dataclass(frozen=True)
class RoleSpec:
    role: Role
    suffix: str                           # "", "_tophat", "_labels", "_overlay", "_smfish"
    output_exts: tuple[str, ...]          # canonical write ext first
    accept_exts: tuple[str, ...]          # additional read-side exts
    is_channel_scoped: bool               # accepts "_tophat_<channel>" form
    precedence: int                       # lower wins in resolve_channel_frame_refs

    @property
    def all_exts(self) -> tuple[str, ...]:
        return self.output_exts + tuple(e for e in self.accept_exts if e not in self.output_exts)


ROLE_CATALOG: Mapping[Role, RoleSpec] = {
    Role.FLUOR_RAW: RoleSpec(
        role=Role.FLUOR_RAW, suffix="",
        output_exts=(".tif", ".tiff"),
        accept_exts=(),
        is_channel_scoped=False, precedence=90,
    ),
    Role.FLUOR_TOPHAT: RoleSpec(
        role=Role.FLUOR_TOPHAT, suffix="_tophat",
        output_exts=(".tif", ".tiff"),
        accept_exts=(),
        is_channel_scoped=True, precedence=40,
    ),
    Role.MASK: RoleSpec(
        role=Role.MASK, suffix="_labels",
        output_exts=(".tif", ".tiff"),
        accept_exts=(".png",),
        is_channel_scoped=False, precedence=10,
    ),
    Role.OVERLAY: RoleSpec(
        role=Role.OVERLAY, suffix="_overlay",
        output_exts=(".png",),
        accept_exts=(".jpg", ".jpeg", ".tif", ".tiff"),
        is_channel_scoped=False, precedence=20,
    ),
    Role.SMFISH: RoleSpec(
        role=Role.SMFISH, suffix="_smfish",
        output_exts=(".tif", ".tiff"),
        accept_exts=(),
        is_channel_scoped=True, precedence=30,
    ),
    # Phase / brightfield channel designated as the label-free segmentation
    # input (cellpose / classical). Channel-scoped like smFISH; any processed
    # phase output carries a "_phase" suffix.
    Role.PHASE: RoleSpec(
        role=Role.PHASE, suffix="_phase",
        output_exts=(".tif", ".tiff"),
        accept_exts=(),
        is_channel_scoped=True, precedence=35,
    ),
}


def role_for_kind(kind: str) -> Role | None:
    """Map legacy ``kind`` strings (``"fluor_raw"``, ``"fluor_processed"``,
    ``"tophat"``, …) to ``Role``. Returns ``None`` if not recognised."""
    if not kind:
        return None
    aliases = {
        "fluor": Role.FLUOR_RAW,
        "fluor_raw": Role.FLUOR_RAW,
        "fluor_processed": Role.FLUOR_TOPHAT,
        "tophat": Role.FLUOR_TOPHAT,
        "mask": Role.MASK,
        "overlay": Role.OVERLAY,
        "smfish": Role.SMFISH,
        "phase": Role.PHASE,
    }
    return aliases.get(str(kind).strip().lower())


def role_output_filename(
    base_stem: str, role: Role, *, channel: str = "", ext: str = "",
) -> str:
    """Build the canonical output filename for *base_stem* + *role*.

    Channel-scoped roles include the channel token (lower-cased) in the
    suffix iff *channel* is provided. The extension defaults to the
    role's first ``output_exts`` entry (``.tif`` for fluor-style roles,
    ``.png`` for overlays)."""
    spec = ROLE_CATALOG[role]
    if not ext:
        ext = spec.output_exts[0] if spec.output_exts else ""
    if not ext.startswith(".") and ext:
        ext = "." + ext
    channel_norm = str(channel or "").strip().lower()
    suffix = spec.suffix
    if spec.is_channel_scoped and channel_norm:
        suffix = f"{spec.suffix}_{channel_norm}"
    return f"{base_stem}{suffix}{ext}"


def classify_filename_role(
    name: str, *, fluor_token: str = "",
) -> tuple[Role | None, str]:
    """Strip the longest matching role suffix from *name* and return
    ``(role, stem_without_role_suffix)``. Channel-scoped variants are
    tried before the bare suffix when *fluor_token* is supplied.

    Returns ``(None, stem)`` for files whose name carries no recognised
    role suffix — those are raw fluor images (``Role.FLUOR_RAW``) by
    convention, but callers may want to validate against a channel
    token before classifying that way."""
    p = Path(str(name or "").strip())
    if not p.name:
        return None, ""
    lower = p.name.lower()
    fluor_norm = str(fluor_token or "").strip().lower()

    # Try roles in precedence order, then within a role: channel-scoped
    # variants first (so "_smfish_w3cy5.tif" matches SMFISH not FLUOR_RAW).
    ordered = sorted(
        (r for r in ROLE_CATALOG.values() if r.suffix),
        key=lambda r: r.precedence,
    )
    for spec in ordered:
        for ext in spec.all_exts:
            if spec.is_channel_scoped and fluor_norm:
                channel_suffix = f"{spec.suffix}_{fluor_norm}{ext}".lower()
                if lower.endswith(channel_suffix):
                    stem = p.name[: -len(channel_suffix)]
                    return spec.role, Path(stem).stem
            bare = f"{spec.suffix}{ext}".lower()
            if lower.endswith(bare):
                stem = p.name[: -len(bare)]
                return spec.role, Path(stem).stem
    return None, p.stem


# ── Pipeline schema ──────────────────────────────────────────────────────


_DEFAULT_FIELDS: tuple[str, ...] = ("experiment", "channel", "well", "fov", "timepoint")


@dataclass(frozen=True)
class PipelineSchema:
    """Authoritative description of how filenames decompose into fields.

    Loaded once from ``pipeline_info.json`` via :func:`load_schema`; every
    parser and builder takes a ``PipelineSchema`` argument. Single-FOV
    pipelines are modelled by ``has_fov=False`` rather than a special-case
    extractor that synthesises ``"1"`` — the empty-FOV-to-``"1"``
    convention is then enforced uniformly by :func:`normalize_fov`."""

    fields: tuple[str, ...] = _DEFAULT_FIELDS
    sep: str = "_"
    channels: tuple[str, ...] = ()

    @property
    def well_idx(self) -> int | None:
        return self._index_of("well")

    @property
    def channel_idx(self) -> int | None:
        return self._index_of("channel")

    @property
    def fov_idx(self) -> int | None:
        return self._index_of("fov")

    @property
    def tp_idx(self) -> int | None:
        return self._index_of("timepoint")

    @property
    def has_fov(self) -> bool:
        return self.fov_idx is not None

    def _index_of(self, name: str) -> int | None:
        for i, f in enumerate(self.fields):
            if f == name:
                return i
        return None


def load_schema(pipeline_info: Mapping[str, Any] | None) -> PipelineSchema:
    """Build a :class:`PipelineSchema` from ``pipeline_info.json`` contents.

    Accepts both the ``schema_fields`` list and the legacy ``schema``
    colon-separated string; falls back to the 5-slot default when the
    info is missing or empty so callers always get a usable schema."""
    if not pipeline_info:
        return PipelineSchema()
    sep = str(pipeline_info.get("separator", "_")) or "_"
    fields_raw = pipeline_info.get("schema_fields", []) or []
    fields = tuple(str(f).strip() for f in fields_raw if str(f).strip())
    if not fields:
        legacy = str(pipeline_info.get("schema", "")).strip()
        if legacy:
            fields = tuple(f.strip() for f in legacy.split(":") if f.strip())
    if not fields:
        fov_idx = pipeline_info.get("fov_index")
        tp_idx = pipeline_info.get("tp_index")
        if isinstance(fov_idx, int) and isinstance(tp_idx, int) and fov_idx >= 0 and tp_idx >= 0:
            # Build a minimal field list from explicit indices — used by
            # legacy callers that wrote only the indices.
            max_idx = max(fov_idx, tp_idx)
            placeholder = ["ignore"] * (max_idx + 1)
            placeholder[fov_idx] = "fov"
            placeholder[tp_idx] = "timepoint"
            fields = tuple(placeholder)
        elif isinstance(tp_idx, int) and tp_idx >= 0:
            # Single-FOV legacy: only tp_idx provided.
            placeholder = ["ignore"] * (tp_idx + 1)
            placeholder[tp_idx] = "timepoint"
            fields = tuple(placeholder)
        else:
            fields = _DEFAULT_FIELDS
    channels_raw = pipeline_info.get("smfish_tokens") or pipeline_info.get("channels") or ()
    channels = tuple(str(c).strip().lower() for c in channels_raw if str(c).strip())
    return PipelineSchema(fields=fields, sep=sep, channels=channels)


DEFAULT_SCHEMA = PipelineSchema()


# ── Normalisation primitives (canonical names — all other sites re-export) ──


_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def normalize_well(value: Any) -> str:
    """Canonical 96-well token: ``"a1"`` → ``"A01"``. Returns the
    stripped upper-cased input when the value isn't a valid 96-well
    position so opaque well labels survive."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    canonical = canonical_well_label(raw)
    if canonical is not None:
        return canonical
    return raw.upper()


def normalize_fov(value: Any) -> str:
    """Canonical FOV token. Empty/``"-1"`` → ``"1"`` (single-FOV
    convention shared with :func:`load_well_csv`); purely-numeric tokens
    have leading zeros stripped (``"001"`` → ``"1"``); other tokens
    pass through stripped."""
    raw = str(value or "").strip()
    if raw in ("", "-1"):
        return "1"
    if _NUMERIC_RE.match(raw):
        try:
            return str(int(float(raw)))
        except (TypeError, ValueError):
            return raw
    return raw


def normalize_fov_series(fov):
    """Vectorised :func:`normalize_fov` for a pandas Series. Lazy
    pandas import so this module stays usable for the pipeline (which
    doesn't ship pandas). Used by :func:`load_well_csv` so every CSV
    that flows through the loader arrives with the same FOV convention
    the smFISH writer applies."""
    import pandas as pd  # noqa: F401  (lazy — see module docstring)
    s = fov.astype(str).str.strip()
    # Cheap path: only the empty/-1 sentinel mapping; numeric strip
    # is applied row-wise only when needed to keep this O(n) on the
    # large path.
    s = s.where(~s.isin({"", "-1"}), "1")
    needs_numeric = s.str.match(r"^-?\d+(?:\.\d+)?$", na=False) & ~s.isin({"1"})
    if needs_numeric.any():
        def _strip(v: str) -> str:
            try:
                return str(int(float(v)))
            except (TypeError, ValueError):
                return v
        s = s.where(~needs_numeric, s[needs_numeric].map(_strip))
    return s


# Re-export the timepoint-hours parser from the pipeline so this module
# is the one place callers look up time-format handling.
_TP_FULL_RE = re.compile(
    r"(?:(\d{1,4})d)?(?:(\d{1,2})h)?(?:(\d{1,2})m)?",
    re.IGNORECASE,
)
_TP_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(h(?:ours?)?|d(?:ays?)?|m(?:in(?:utes?)?)?)",
    re.IGNORECASE,
)
_TP_TRAILING_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)$")


def parse_timepoint_hours(value: Any) -> float | None:
    """Parse a timepoint token into fractional hours.

    Handles every form seen in the wild: ``"T01"``, ``"24"``, ``"24h"``,
    ``"1.0"``, ``"day3"``, ``"02d04h30m"``, plus a bare integer/float.
    Returns ``None`` for opaque labels that can't be parsed.

    This is the central time-format resolver; both
    :func:`normalize_tp` (canonical key form) and :func:`tp_hours`
    (numeric arithmetic) consume it. Kept stdlib-only here so the
    module doesn't drag in the pipeline's heavy imports (tifffile)."""
    s = str(value or "").strip()
    if not s:
        return None
    # 1. Full DDdHHhMMm.
    m = _TP_FULL_RE.fullmatch(s)
    if m and any(m.groups()):
        days = int(m.group(1) or 0)
        hours = int(m.group(2) or 0)
        mins = int(m.group(3) or 0)
        return days * 24.0 + hours + mins / 60.0
    # 2. Standalone unit: "48h", "2d", "30m".
    m = _TP_UNIT_RE.fullmatch(s)
    if m:
        val = float(m.group(1))
        unit = m.group(2)[0].lower()
        if unit == "h":
            return val
        if unit == "d":
            return val * 24.0
        if unit == "m":
            return val / 60.0
    # 3. Pure number (treat as hours).
    try:
        return float(s)
    except ValueError:
        pass
    # 4. Prefixed ordinal — "T01" → 1.0, "day02" → 2.0.
    m = _TP_TRAILING_NUM_RE.search(s)
    if m:
        return float(m.group(1))
    return None


def normalize_tp(value: Any) -> str:
    """Canonical timepoint key form. Parseable forms (``T01`` / ``24h``
    / ``1.0`` / bare numeric) collapse to ``f"{hours:g}"``; opaque
    labels (``"treated_arm_2"``) pass through stripped. Used by every
    site that compares timepoint identity."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    hours = parse_timepoint_hours(raw)
    if hours is None:
        return raw
    return f"{hours:g}"


def tp_hours(value: Any) -> float | None:
    """Optional numeric companion to :func:`normalize_tp` for arithmetic
    or sorting. Returns ``None`` for opaque labels."""
    return parse_timepoint_hours(value)


def normalize_nucleus_id(value: Any) -> str:
    """Canonical nucleus-ID form. Per user-confirmed convention nids are
    always integers; any ``.0`` is a pandas float-string artefact, so
    ``"1.0"`` → ``"1"`` uniformly. Non-numeric IDs pass through
    stripped."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if _NUMERIC_RE.match(raw):
        try:
            return str(int(float(raw)))
        except (TypeError, ValueError):
            return raw
    return raw


# ── Filename parse/build ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedName:
    """Decomposition of an image filename. The raw tokens live in
    :attr:`fields` keyed by schema field name; the convenience
    properties apply the canonical normalisation."""

    stem: str
    schema: PipelineSchema
    fields: Mapping[str, str]
    role: Role | None
    channel: str
    ext: str

    @property
    def well(self) -> str:
        return normalize_well(self.fields.get("well", ""))

    @property
    def fov(self) -> str:
        # When the schema lacks a fov field, normalize_fov("") → "1"
        # (single-FOV convention), so callers never see an empty FOV.
        return normalize_fov(self.fields.get("fov", ""))

    @property
    def tp(self) -> str:
        return normalize_tp(self.fields.get("timepoint", ""))


def parse_image_filename(
    name: str, *, schema: PipelineSchema, fluor_token: str = "",
) -> ParsedName:
    """Parse an image filename or zip member name into a
    :class:`ParsedName`.

    Splits the (possibly suffixed) stem on ``schema.sep`` and indexes
    via ``schema.fields``. Strips the longest matching role suffix
    first (channel-scoped variants tried before the bare form), so
    ``A02_w2Turq_T01_smfish.tif`` parses as
    ``(role=SMFISH, channel="w2turq", fields={"well":"A02","timepoint":"T01"})``
    on a single-FOV schema."""
    p = Path(str(name or "").strip())
    ext = p.suffix
    role, stripped_stem = classify_filename_role(p.name, fluor_token=fluor_token)
    parts = stripped_stem.split(schema.sep)
    fields = {
        field: (parts[i] if i < len(parts) and field != "ignore" else "")
        for i, field in enumerate(schema.fields)
    }
    # Channel: take the schema's channel slot if present, else fall back
    # to the fluor_token caller supplied (for role-scoped channel reads).
    channel = str(fields.get("channel", "") or "").strip().lower()
    if not channel:
        channel = str(fluor_token or "").strip().lower()
    return ParsedName(
        stem=stripped_stem, schema=schema, fields=fields,
        role=role, channel=channel, ext=ext,
    )


def build_image_filename(
    *,
    fields: Mapping[str, str],
    schema: PipelineSchema,
    role: Role | None = None,
    channel: str = "",
    ext: str = "",
) -> str:
    """Inverse of :func:`parse_image_filename` (for the parts that
    round-trip cleanly). Returns the on-disk filename for the given
    field values + role. Empty schema slots are emitted as empty
    tokens (preserves separator positions); callers that want them
    omitted should drop those fields before calling."""
    parts: list[str] = []
    for f in schema.fields:
        if f == "ignore":
            parts.append("")
            continue
        parts.append(str(fields.get(f, "") or ""))
    base_stem = schema.sep.join(parts)
    if role is None:
        # Bare filename — caller decides the extension.
        return f"{base_stem}{ext}" if ext else base_stem
    return role_output_filename(base_stem, role, channel=channel, ext=ext)


# ── CellKey ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CellKey:
    """Canonical identity for a single cell row.

    The four-tuple every lookup site uses; built via
    :meth:`from_row` (CSV side) or :meth:`from_parsed` (filename side)
    so the read and write paths can never disagree on normalisation."""

    well: str
    fov: str
    tp: str
    nid: str

    def as_tuple(self) -> tuple[str, str, str, str]:
        return (self.well, self.fov, self.tp, self.nid)

    @classmethod
    def from_row(
        cls,
        row: Mapping[str, Any],
        *,
        well: str | None = None,
    ) -> CellKey:
        """Build a :class:`CellKey` from one CSV row. Honours all the
        column-name aliases seen in real datasets (``FOV``/``fov``,
        ``tp``/``timepoint``/``time``/``time_h``/``timepoint_hours``,
        ``nucleus_id``/``nucleusId``/``nucleusID``)."""
        def _pick(*names: str) -> Any:
            for n in names:
                if n in row:
                    return row[n]
            lowered = {str(k).lower(): v for k, v in row.items()}
            for n in names:
                if n.lower() in lowered:
                    return lowered[n.lower()]
            return ""
        well_raw = well if well is not None else _pick("well", "Well")
        return cls(
            well=normalize_well(well_raw),
            fov=normalize_fov(_pick("fov", "FOV")),
            tp=normalize_tp(_pick(
                "timepoint", "tp", "time", "time_h", "timepoint_hours",
            )),
            nid=normalize_nucleus_id(_pick(
                "nucleus_id", "nucleus id", "nucleusId", "nucleusID",
            )),
        )

    @classmethod
    def from_parsed(
        cls,
        parsed: ParsedName,
        *,
        well: str | None = None,
        nid: Any = "",
    ) -> CellKey:
        """Build a :class:`CellKey` from a :class:`ParsedName` (the
        result of :func:`parse_image_filename`) plus an externally-
        supplied nucleus ID. *well* overrides the parsed well field
        when supplied (e.g. when iterating wells from a higher level)."""
        well_token = well if well is not None else parsed.fields.get("well", "")
        return cls(
            well=normalize_well(well_token),
            fov=parsed.fov,
            tp=parsed.tp,
            nid=normalize_nucleus_id(nid),
        )


def cell_keys_for_dataframe(
    df,
    *,
    default_well: str | None = None,
):
    """Vectorised :meth:`CellKey.from_row`. Returns a pandas ``Series``
    of :class:`CellKey` aligned with ``df.index``. Used by the smFISH
    writer and Review-Image's CSV-row lookup so both sides build keys
    from the same constructor. Lazy pandas import."""
    import pandas as pd
    if df is None or df.empty:
        return pd.Series([], dtype=object)

    def _resolve_col(*names: str) -> str | None:
        lowered = {str(c).lower(): c for c in df.columns}
        for n in names:
            if n in df.columns:
                return n
            if n.lower() in lowered:
                return lowered[n.lower()]
        return None

    well_col = _resolve_col("well", "Well")
    fov_col = _resolve_col("fov", "FOV")
    tp_col = _resolve_col(
        "timepoint", "tp", "time", "time_h", "timepoint_hours",
    )
    nid_col = _resolve_col(
        "nucleus_id", "nucleus id", "nucleusId", "nucleusID",
    )

    if well_col is not None:
        well_series = df[well_col].fillna("").astype(str).str.strip()
        if default_well is not None:
            well_series = well_series.where(well_series != "", str(default_well))
    elif default_well is not None:
        well_series = pd.Series([str(default_well)] * len(df), index=df.index)
    else:
        well_series = pd.Series([""] * len(df), index=df.index)

    well_n = well_series.map(normalize_well)
    fov_n = (normalize_fov_series(df[fov_col].fillna("").astype(str))
             if fov_col else
             pd.Series(["1"] * len(df), index=df.index))
    tp_raw = (df[tp_col].fillna("").astype(str)
              if tp_col else
              pd.Series([""] * len(df), index=df.index))
    tp_n = tp_raw.map(normalize_tp)
    nid_raw = (df[nid_col].fillna("").astype(str)
               if nid_col else
               pd.Series([""] * len(df), index=df.index))
    nid_n = nid_raw.map(normalize_nucleus_id)

    return pd.Series(
        [CellKey(w, f, t, n) for w, f, t, n in zip(well_n, fov_n, tp_n, nid_n)],
        index=df.index,
    )


# ── Back-compat helpers ──────────────────────────────────────────────────


def normalize_well_token(value: Any) -> str:
    """Alias for :func:`normalize_well`. Kept under the legacy name so
    the 10+ call sites that historically defined their own
    ``normalize_well_token`` can import this one without renaming."""
    return normalize_well(value)


def normalize_id(value: Any) -> str:
    """Strip + integer-normalise. Matches the legacy
    ``smfish.controller.normalize_id`` behaviour (digit-only tokens
    collapse leading zeros; non-digit tokens pass through). Prefer
    :func:`normalize_nucleus_id` or :func:`normalize_fov` /
    :func:`normalize_tp` for new code."""
    raw = str(value or "").strip()
    if raw.isdigit():
        return str(int(raw))
    return raw
