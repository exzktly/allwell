"""Empty-well (blank) reference models: sampling the frames, building the fields.

A plate can be corrected against a well that holds medium and no cells. Two
corrections read that blank, and both are requested by name:
``--flatfield empty_well`` divides every frame by the blank's illumination
field, and ``--background empty_well`` subtracts the blank's additive
background. When both are on they are folded into one physically-correct step,
``(raw − B)/(B/mean(B))`` — see :func:`pipeline.background.combined_empty_well_correct`.

Everything that decides *which frames the blank contributes and what field they
make* lives here: the sampler, the two model builders, the per-image lookup, and
the guard that stops a run whose requested correction could not be built.
``process_microscopy`` re-exports every name, so its call sites and the tests
that reach them through ``pm.<name>`` are unchanged.

**The model shape is ``{channel_token: {tp_key: field, _POOLED_TP: field}}``.**
Background drifts over time, so a field is built per timepoint and each
timepoint is corrected on its own; ``_POOLED_TP`` holds every frame for that
channel across all timepoints and is the fallback for a sample whose timepoint
the blank does not cover. Frames from all named blank wells pool, which is the
point of naming several: pooling de-noises the estimate.

Extracted from ``process_microscopy`` because that file is against its line
budget and this is a self-contained cluster with one entry point per question.
Qt-free and free of ``well_viewer`` / ``widgets``, like every ``pipeline``
module — the remote worker imports it
(``test_architecture_contracts.py::test_pipeline_is_self_sufficient``,
and ``services/remote_manifest.py`` ships it).
"""

from __future__ import annotations

import io as _io
import logging
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
from tifffile import imread

from pipeline.background import estimate_background_field
from pipeline.flatfield import estimate_basic_flatfield
from pipeline.input_layout import find_well_folders, find_well_zips
from pipeline.io import load_2d_gray
from util.image_exts import is_run_input_member
from util.timepoint import parse_hours as _tp_parse_hours
from util.well_token import (
    background_well_key as _bg_well_key,
    split_background_wells as _split_background_wells,
)

# The run log's lines keep coming from where they always did: this is code
# lifted out of ``process_microscopy``, and a user filtering the log on that
# logger name would otherwise stop seeing the breadcrumb a skipped blank frame
# leaves. Same choice as `pipeline.background`, `pipeline.io` and
# `pipeline.stardist`, which were extracted from the same file.
log = logging.getLogger("process_microscopy")

#: Empty-well models are keyed ``{token: {tp_key: field, _POOLED_TP: field}}`` so
#: a per-timepoint field tracks background drift; ``_POOLED_TP`` is the
#: all-timepoints fallback used when a sample's timepoint has no blank frame.
_POOLED_TP = "__pooled__"


def _empty_well_tp_key(token_value) -> str:
    """Canonical timepoint key for grouping/matching empty-well frames.

    Parseable forms (``T01`` / ``24`` / ``24h`` / ``day1``) collapse to
    ``f"{hours:g}"`` so the empty-well frames and the sample image key
    identically; opaque labels pass through stripped. Empty when there's no
    timepoint.
    """
    raw = str(token_value or "").strip()
    if not raw:
        return ""
    hours = _tp_parse_hours(raw)
    return raw if hours is None else f"{hours:g}"


def _empty_well_field(models, token: str, tp_key: str):
    """Look up the per-(channel, timepoint) empty-well field, falling back to the
    pooled all-timepoints field when this timepoint has no blank frame. Returns
    None when the channel has no model at all."""
    per_tok = (models or {}).get(str(token).lower())
    if not isinstance(per_tok, dict):
        return per_tok            # defensive: legacy flat shape
    field = per_tok.get(tp_key)
    return field if field is not None else per_tok.get(_POOLED_TP)


def _load_image_source(source) -> np.ndarray | None:
    """Load a 2-D float32 image from either a ``Path`` on disk or a
    ``(zip_path, member_name)`` tuple (a TIFF stored inside a per-well zip).
    Returns None on any read error so sampling can skip it."""
    try:
        if isinstance(source, tuple):
            zpath, member = source
            with zipfile.ZipFile(zpath, "r") as zf:
                arr = imread(_io.BytesIO(zf.read(member)))
            while arr.ndim > 2:
                arr = arr[0]
            return arr.astype(np.float32)
        return load_2d_gray(source)
    except (OSError, zipfile.BadZipFile, ValueError, KeyError) as exc:
        log.debug("skipping unreadable image source %r: %s", source, exc)
        return None
    except Exception:  # unexpected (a bug, not bad data) — surface, still skip
        log.warning("unexpected error loading image %r; skipping", source, exc_info=True)
        return None


class _StemReader:
    """Reads a channel token and a timepoint key out of a filename stem.

    The schema's slot indices and the run's channel/well filters, resolved once
    and asked per file. It was three closures over the sampler's locals; as an
    object the sampler can hand it to each layout helper instead of nesting them
    all in one function, which is what kept that function at the edge of the
    complexity ratchet.
    """

    def __init__(self, *, schema: list[str] | None, sep: str,
                 wells: set[str], tokens: list[str]) -> None:
        fields = list(schema or [])
        self.chan_idx = fields.index("channel") if "channel" in fields else -1
        self.well_idx = fields.index("well") if "well" in fields else -1
        self.tp_idx = fields.index("timepoint") if "timepoint" in fields else -1
        self.sep = sep
        self.wells = wells
        self.want = {str(t).strip().lower() for t in (tokens or [])}

    @property
    def usable(self) -> bool:
        """Whether a channel slot and at least one blank well were both given."""
        return self.chan_idx >= 0 and bool(self.wells)

    def token_of(self, stem: str, *, require_well: bool) -> str | None:
        """The channel token this stem names, or None if it is not wanted.

        *require_well* is the difference between the two layouts that establish
        the well from the **container** — a ``<WELL>.zip`` or a ``<WELL>/``
        folder, whose members need not carry the well token — and loose TIFFs,
        where the filename is the only thing that says which well a frame is
        from.
        """
        parts = stem.split(self.sep)
        if self.chan_idx >= len(parts):
            return None
        if require_well:
            if self.well_idx < 0 or self.well_idx >= len(parts) \
                    or _bg_well_key(parts[self.well_idx]) not in self.wells:
                return None
        tok = parts[self.chan_idx].strip().lower()
        if self.want and tok not in self.want:
            return None
        return tok

    def tp_of(self, stem: str) -> str:
        parts = stem.split(self.sep)
        if 0 <= self.tp_idx < len(parts):
            return _empty_well_tp_key(parts[self.tp_idx])
        return ""


#: A frame's source: a ``Path`` on disk, or ``(zip_path, member_name)``.
_Sources = dict[str, dict[str, list]]


def _sources_from_well_zips(input_dir: Path, reader: _StemReader,
                            found: _Sources) -> bool:
    """Collect blank frames from ``<input_dir>/<WELL>.zip`` — the default layout.

    Returns whether any blank's archive was **seen**, which is what decides
    whether the per-well-folder tier below runs at all. Seen, not read
    successfully: an archive that is present but unreadable still means this
    plate is zip-grouped, and falling through to the folder tier would search a
    layout the plate does not use.
    """
    seen = False
    for well, zpath in find_well_zips(input_dir):
        if _bg_well_key(well) not in reader.wells:
            continue
        seen = True
        try:
            with zipfile.ZipFile(zpath, "r") as zf:
                for member in zf.infolist():
                    if not is_run_input_member(member.filename):
                        continue
                    stem = Path(Path(member.filename).name).stem
                    tok = reader.token_of(stem, require_well=False)
                    if tok is not None:
                        found[tok][reader.tp_of(stem)].append(
                            (zpath, member.filename))
        except (OSError, zipfile.BadZipFile):
            continue
    return seen


def _sources_from_well_folders(input_dir: Path, reader: _StemReader,
                               found: _Sources) -> None:
    """Collect blank frames from ``<input_dir>/<WELL>/…`` — the folder layout."""
    for well, folder in find_well_folders(input_dir):
        if _bg_well_key(well) not in reader.wells:
            continue
        for path in sorted(folder.rglob("*.tif*")):
            tok = reader.token_of(path.stem, require_well=False)
            if tok is not None:
                found[tok][reader.tp_of(path.stem)].append(path)


def _sources_from_loose_tifs(input_dir: Path, reader: _StemReader,
                             found: _Sources) -> None:
    """Collect blank frames from loose TIFFs anywhere under *input_dir*.

    The last resort, and the only tier where the filename must carry the well
    token: nothing else here says which well a loose frame came from.
    """
    for path in sorted(input_dir.rglob("*.tif*")):
        tok = reader.token_of(path.stem, require_well=True)
        if tok is not None:
            found[tok][reader.tp_of(path.stem)].append(path)


def _load_capped(sources: list, max_samples: int) -> list[np.ndarray]:
    """Load an evenly-strided sample of at most *max_samples* of *sources*.

    Strided rather than truncated so the sample spans the whole set — a blank's
    frames are written in FOV order, and the first N of them are one corner of
    the well. Unreadable sources are dropped rather than failing the sample.
    """
    step = max(1, len(sources) // max_samples)
    return [a for a in (_load_image_source(s) for s in sources[::step][:max_samples])
            if a is not None]


def _stacks_from_sources(found: _Sources,
                         max_samples: int) -> dict[str, dict[str, list[np.ndarray]]]:
    """Turn collected sources into the per-(channel, timepoint) image stacks.

    The pooled entry is built from **every** source for that channel, not from
    the union of the per-timepoint samples, so the fallback field is drawn from
    the whole blank rather than from a sample of samples.
    """
    out: dict[str, dict[str, list[np.ndarray]]] = {}
    for tok, by_tp in found.items():
        per_tp: dict[str, list[np.ndarray]] = {}
        pooled_sources: list = []
        for tp_key, sources in by_tp.items():
            imgs = _load_capped(sources, max_samples)
            if imgs:
                per_tp[tp_key] = imgs
            pooled_sources.extend(sources)
        pooled = _load_capped(pooled_sources, max_samples)
        if pooled:
            per_tp[_POOLED_TP] = pooled
        if per_tp:
            out[tok] = per_tp
    return out


def sample_empty_well_images(
    input_dir: Path,
    *,
    tokens: list[str],
    schema: list[str] | None,
    sep: str,
    background_wells: list[str],
    max_samples: int = 30,
) -> dict[str, dict[str, list[np.ndarray]]]:
    """Sample empty/background-well frames, pooled across the given *background_
    wells* and **grouped by timepoint**.

    Returns ``{token_lower: {tp_key: [array, ...]}}`` — per-channel,
    per-timepoint stacks the empty-well estimators consume. ``tp_key`` is the
    canonical timepoint (:func:`_empty_well_tp_key`); the special ``_POOLED_TP``
    key holds every frame for that channel across all timepoints (the
    missing-timepoint fallback). Frames from all named blank wells (and all
    their FOVs) at a given timepoint are pooled. Reads from whichever input
    layout holds each well: a per-well ``<WELL>.zip`` (default), a per-well
    folder, or loose TIFFs.

    Background drifts over time, so a per-timepoint field corrects each
    timepoint on its own; pooling multiple blank wells de-noises the estimate.

    The three layouts are tried in that order and are **not** additive: a plate
    is in one of them, and reading a second would mix a well's archived frames
    with whatever loose copies happen to be lying beside it.
    """
    reader = _StemReader(
        schema=schema, sep=sep, tokens=tokens,
        wells={_bg_well_key(w) for w in _split_background_wells(background_wells)},
    )
    if not reader.usable:
        return {}

    found: _Sources = defaultdict(lambda: defaultdict(list))
    if not _sources_from_well_zips(input_dir, reader, found):
        _sources_from_well_folders(input_dir, reader, found)
    if not found:
        _sources_from_loose_tifs(input_dir, reader, found)
    return _stacks_from_sources(found, max_samples)


def _require_empty_well_models(what: str, mode: str, models, wells, source) -> None:
    """Stop a run that asked for empty-well correction and could not build it.

    A missing model is not a degraded correction, it is **no correction**:
    ``_empty_well_field`` returns None and the frame passes through untouched.
    The output then carries the raw background while looking exactly like a
    corrected well — there is no marker on the image, no column in the CSV and
    nothing in the run record to tell the two apart later.

    That is the one thing this codebase is not allowed to do (CLAUDE.md §7), so
    it fails instead. It fired for real: a batched remote run stages one batch at
    a time, and every batch that did not happen to contain the blank silently
    produced uncorrected numbers.

    Only when blanks were actually named — a run with none is a configuration
    the GUI already refuses, and failing here would only restate it worse.
    """
    if mode != "empty_well" or not wells or models:
        return
    raise SystemExit(
        f"--{what.replace('-', '')} empty_well was requested with background "
        f"well(s) {', '.join(wells)}, but no usable frames for them were found "
        f"in {source}. Nothing would be corrected, and the output would be "
        f"indistinguishable from a corrected run — so this run stops instead. "
        f"Check the well name(s) against the archives present, and on a batched "
        f"remote run check that --background_dir points at the directory the "
        f"blank was uploaded to."
    )


def build_background_models(
    input_dir: Path,
    *,
    tokens: list[str],
    schema: list[str] | None,
    sep: str,
    background_wells: list[str] | str = "",
    max_samples: int = 30,
    background_dir: Path | None = None,
) -> dict[str, dict[str, np.ndarray]]:
    """Per-channel, **per-timepoint** additive background fields from the empty/
    background well(s) (for ``--background empty_well``). Pools frames across all
    named blank wells and groups by timepoint via :func:`sample_empty_well_images`,
    then builds one :func:`estimate_background_field` per (channel, timepoint) plus
    a pooled all-timepoints field. Returns ``{token_lower: {tp_key: field,
    _POOLED_TP: field}}``; channels with no usable empty-well images omitted.
    """
    stacks = sample_empty_well_images(
        background_dir or input_dir, tokens=tokens, schema=schema, sep=sep,
        background_wells=background_wells, max_samples=max_samples)
    return _fields_from_stacks(stacks, estimate_background_field)


def _fields_from_stacks(stacks, estimator) -> dict[str, dict[str, np.ndarray]]:
    """Run *estimator* over every (channel, timepoint) stack, dropping the empties.

    The one place the two builders differ is the estimator: an additive
    background field keeps its level (:func:`estimate_background_field`), a
    flat-field is normalised to mean 1 (:func:`estimate_basic_flatfield`). The
    nesting and the drop rule are shared, so the two model dicts cannot come out
    of step in shape — which matters because the combined empty-well correction
    indexes both with the same key.
    """
    models: dict[str, dict[str, np.ndarray]] = {}
    for tok, by_tp in stacks.items():
        per_tp: dict[str, np.ndarray] = {}
        for tp_key, imgs in by_tp.items():
            field = estimator(imgs)
            if field is not None:
                per_tp[tp_key] = field
        if per_tp:
            models[tok] = per_tp
    return models


def build_flatfield_models(
    input_dir: Path,
    *,
    tokens: list[str],
    schema: list[str] | None,
    sep: str,
    mode: str,
    background_wells: list[str] | str = "",
    max_samples: int = 30,
    background_dir: Path | None = None,
) -> dict[str, dict[str, np.ndarray]]:
    """Precompute per-channel flat-field models for the ``basic`` / ``empty_well`` modes.

    Returns the uniform nested shape ``{token_lower: {tp_key: field, _POOLED_TP:
    field}}`` so the per-image lookup (:func:`_empty_well_field`) is the same for
    both modes. For ``empty_well`` the field is built **per timepoint** (and a
    pooled fallback) from frames pooled across all named blank wells, via
    :func:`sample_empty_well_images`. ``basic`` samples the whole plate
    (time-collapsed) from TIFFs on disk and stores one ``_POOLED_TP`` field per
    channel. Each field is a mean-1 :func:`estimate_basic_flatfield`; channels
    with no usable images are omitted (so :func:`apply_flatfield` no-ops them).
    """
    if mode not in ("basic", "empty_well"):
        return {}
    reader = _StemReader(schema=schema, sep=sep, tokens=tokens, wells=set())
    if reader.chan_idx < 0:
        return {}

    if mode == "empty_well":
        if not _split_background_wells(background_wells):
            return {}
        # Only the empty-well branch reads the blank; `basic` samples the whole
        # plate and must keep looking at the run's own input.
        stacks = sample_empty_well_images(
            background_dir or input_dir, tokens=tokens, schema=schema, sep=sep,
            background_wells=background_wells, max_samples=max_samples)
        return _fields_from_stacks(stacks, estimate_basic_flatfield)
    return _basic_flatfield_models(input_dir, reader, max_samples)


def _basic_flatfield_models(input_dir: Path, reader: _StemReader,
                            max_samples: int) -> dict[str, dict[str, np.ndarray]]:
    """``basic`` mode: one mean-1 field per channel, sampled across the whole plate.

    Time-collapsed on purpose — this mode models the microscope's illumination
    rather than the plate's background, and that does not drift the way an
    additive background does. The single field is stored under ``_POOLED_TP`` so
    the lookup is the same shape as the empty-well models beside it.
    """
    by_token: dict[str, list[Path]] = {}
    for path in sorted(input_dir.rglob("*.tif*")):
        parts = path.stem.split(reader.sep)
        if reader.chan_idx >= len(parts):
            continue
        tok = parts[reader.chan_idx].strip().lower()
        if reader.want and tok not in reader.want:
            continue
        by_token.setdefault(tok, []).append(path)

    models: dict[str, dict[str, np.ndarray]] = {}
    for tok, paths in by_token.items():
        imgs = _load_capped(paths, max_samples)
        if not imgs:
            continue
        field = estimate_basic_flatfield(imgs)
        if field is not None:
            models[tok] = {_POOLED_TP: field}
    return models
