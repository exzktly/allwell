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
    canonical_well_label,
    parse_background_well_map,
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
        f"--{what.lower().replace('-', '')} empty_well was requested with background "
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


# ─────────────────────────────────────────────────────────────────────────────
# Pairing a blank with the wells it corrects
#
# Everything above builds ONE reference for the whole plate, pooled across every
# named blank. That is the right model when the blanks differ only by noise, and
# the wrong one when they differ because of the treatment: a compound that
# fluoresces, or a different medium in one block, puts real signal into its own
# blank, and pooling then spreads that signal across every well on the plate.
# Nothing in the output says so — the numbers look exactly like corrected ones.
#
# So a run may instead pair each blank with the wells it corrects. The models
# gain one dimension, the group, and the per-image lookup does not change at
# all: each well's worker is handed its own group's models and goes on asking
# the same `_empty_well_field(models, token, tp_key)` it always did.
# ─────────────────────────────────────────────────────────────────────────────

#: The single group of a run with no pairing map — every named blank pooled.
#: Empty string rather than a sentinel object so the grouped dict is plain data
#: that pickles into a worker and reads in a log without a repr of its own.
_POOLED_GROUP = ""

#: Private ``shared_kwargs`` keys: the grouped models and the well→group map.
#: Leading underscore because that is this pipeline's convention for "reaches
#: the worker pool but not ``process_image_group``" — ``_process_groups_in_worker``
#: filters them, as it does ``_fov_threads`` and ``_stardist_params``.
GROUPED_FLATFIELD_KEY = "_grouped_flatfield_models"
GROUPED_BACKGROUND_KEY = "_grouped_background_models"
WELL_GROUP_KEY = "_blank_group_of_well"


def _refuse_blanks_the_map_does_not_use(background_well, mapping) -> None:
    """Stop a run whose ``--background_well`` names a blank the map never uses.

    The two flags would otherwise be two disagreeing answers to "which wells are
    blanks". :func:`util.well_token.blank_wells_for_run` — the remote staging
    planner, the batched hold-out and both incremental-scope readers — takes the
    union, so such a well is held out of the batches and uploaded as a
    reference; this class would take only the map's keys, so the same well is a
    sample that some blank must claim. One well, treated as a blank by the
    machinery that moves it and as a sample by the machinery that corrects it.

    Refused rather than resolved, in the voice the map's own parser uses: every
    rule that could absorb it — ignore the field, adopt the well as a blank
    correcting nothing — ends with the run doing something the user did not ask
    for and cannot see in the output.
    """
    stray = [w for w in _split_background_wells(background_well)
             if (canonical_well_label(w) or str(w).strip()) not in mapping]
    if not stray:
        return
    raise ValueError(
        f"--background_well names {', '.join(stray)}, which "
        f"--background_well_map does not use as a blank. A pairing map decides "
        f"which blank corrects which well on its own, so a blank named only "
        f"beside it would be staged as a reference and then corrected as a "
        f"sample. Give it an entry in the map, or drop it from "
        f"--background_well."
    )


class BlankGroups:
    """Which blank corrects which well, for one run.

    Two shapes in one object because they are two readings of one decision:
    *by_group* is what to build a model from, and *well_group* is which model a
    given well then gets. Deriving either from the other at a call site is how
    the two would come to disagree.

    A run with no map has exactly one group, :data:`_POOLED_GROUP`, holding every
    named blank — so the pooled behaviour is not a special case threaded through
    the code below, it is this class with one entry.
    """

    def __init__(self, background_well="", background_well_map="", *,
                 corrections=()) -> None:
        mapping = parse_background_well_map(background_well_map)
        self.paired = bool(mapping)
        #: Whether either correction actually asked for the blank. A map given
        #: alongside `--flatfield basic` shapes nothing, so it must not refuse
        #: anything either; deciding that here, once, keeps the question off
        #: every site that acts on the plan.
        self.in_force = "empty_well" in tuple(corrections)
        if self.paired:
            _refuse_blanks_the_map_does_not_use(background_well, mapping)
            self.by_group: dict[str, list[str]] = {b: [b] for b in mapping}
            #: A blank is corrected by the model built from itself, which is what
            #: pooling did to it: the pooled reference contained every blank, so
            #: every blank was corrected by a field that included its own frames.
            #: Seeding the blanks first and the wells they correct second is safe
            #: in either order — ``parse_background_well_map`` refuses a well that
            #: is both — and this order says which of the two is the definition.
            self.well_group: dict[str, str] = {b: b for b in mapping}
            self.well_group.update({
                well: blank for blank, wells in mapping.items() for well in wells})
        else:
            self.by_group = {_POOLED_GROUP: _split_background_wells(background_well)}
            self.well_group = {}

    def group_of(self, well_label) -> str:
        """The group key whose model corrects *well_label*.

        Every well a paired run processes has an answer here, blanks included:
        a blank is a well the pipeline segments and quantifies like any other,
        so a blank with no group would be written to its own CSV carrying the
        raw background while looking exactly like a corrected well.

        An unmapped well answers :data:`_POOLED_GROUP`, which on a paired run is
        a group that does not exist and therefore an empty model — but no run
        reaches that, because :meth:`uncovered` refuses the run first. The
        fallback is here so this cannot be the thing that decides; a well with no
        blank is a refusal, never a quiet no-op.
        """
        if not self.paired:
            return _POOLED_GROUP
        canon = canonical_well_label(well_label)
        return self.well_group.get(canon or str(well_label), _POOLED_GROUP)

    def uncovered(self, well_labels) -> list[str]:
        """The wells of *well_labels* that no blank claims, canonical and sorted.

        A blank is covered by itself, so it never appears here. Empty on an
        unpaired run: pooling covers the plate by construction.
        """
        if not self.paired:
            return []
        out = set()
        for label in well_labels:
            canon = canonical_well_label(label) or str(label).strip()
            if canon in self.well_group:
                continue
            out.add(canon)
        return sorted(out)

    def for_mode(self, mode: str) -> "BlankGroups":
        """This plan as one correction sees it.

        Pairing says which blank a well is corrected *by*, so it shapes a
        correction only when that correction reads a blank at all. A ``basic``
        flat-field is estimated from the plate's own images and takes no blank,
        so on a paired run it is still one field for the whole plate.

        Asking this per correction rather than once per run is not a nicety.
        ``--flatfield basic`` alongside a map used to build the identical
        plate-wide field once per blank and then install it in the grouped
        shape, which left every well the map does not name with no flat-field at
        all — and the coverage refusal does not fire, because a map alongside
        ``basic`` is not in force. Two silent no-ops from one question asked in
        the wrong place.
        """
        if mode == "empty_well" or not self.paired:
            return self
        return BlankGroups(", ".join(self.blanks()), "", corrections=(mode,))

    def blanks(self) -> list[str]:
        """Every well this run treats as a blank, in the order they were named."""
        return [w for wells in self.by_group.values() for w in wells]


def build_grouped_models(
    input_dir: Path,
    *,
    groups: BlankGroups,
    builder,
    **kwargs,
) -> dict[str, dict[str, dict[str, np.ndarray]]]:
    """Run *builder* once per group, returning ``{group: {channel: {tp: field}}}``.

    *builder* is :func:`build_background_models` or :func:`build_flatfield_models`
    — the pooled builders, unchanged and called with one group's blanks each
    time. That is the whole of the grouping: a group is a run of the existing
    sampler over a subset of the blanks, so a paired run and a pooled run of the
    same blank produce the same field by construction rather than by agreement.
    """
    return {
        group: builder(input_dir, background_wells=blanks, **kwargs)
        for group, blanks in groups.by_group.items()
    }


def models_for_well(grouped, groups: BlankGroups, well_label) -> dict:
    """The one group's models that correct *well_label*."""
    return (grouped or {}).get(groups.group_of(well_label), {})


def shared_kwargs_for_well(shared_kwargs: dict, well_label: str) -> dict:
    """*shared_kwargs* with this well's own empty-well models in place.

    The slice happens here, at the point a well is handed to a worker, and this
    is why nothing below it changes: ``process_image_group`` and
    ``_correct_image_group`` go on receiving one flat ``{channel: {tp: field}}``
    per correction and go on knowing nothing about wells or groups.

    It also keeps the pickle small. Every well already receives its own copy of
    ``shared_kwargs`` — the pool is ``spawn``, so there is no sharing — and
    shipping every group's fields to every worker would multiply that by the
    number of groups. The grouped dicts are dropped on the way out.

    A run with no grouped models is returned untouched, same object: the pooled
    path costs nothing and copies nothing.
    """
    grouped_ff = shared_kwargs.get(GROUPED_FLATFIELD_KEY)
    grouped_bg = shared_kwargs.get(GROUPED_BACKGROUND_KEY)
    groups = shared_kwargs.get(WELL_GROUP_KEY)
    if groups is None:
        return shared_kwargs
    out = {k: v for k, v in shared_kwargs.items()
           if k not in (GROUPED_FLATFIELD_KEY, GROUPED_BACKGROUND_KEY,
                        WELL_GROUP_KEY)}
    if grouped_ff is not None:
        out["flatfield_models"] = models_for_well(grouped_ff, groups, well_label)
    if grouped_bg is not None:
        out["background_models"] = models_for_well(grouped_bg, groups, well_label)
    return out


def require_grouped_models(what: str, mode: str, grouped, groups: BlankGroups,
                           source) -> None:
    """:func:`_require_empty_well_models`, asked once per pairing group.

    The plate-wide guard asks whether *any* model was built. On a paired run that
    is the wrong question: three of four blocks losing their blank still leaves
    one model standing, and the guard that exists to stop an uncorrected well
    reporting success would wave the run through. So each group answers for
    itself, and the message names the blank that produced nothing.
    """
    if mode != "empty_well":
        return
    for group, blanks in groups.by_group.items():
        _require_empty_well_models(
            what, mode, (grouped or {}).get(group), blanks, source)


def refuse_uncovered_wells(groups: BlankGroups, *well_lists) -> None:
    """Stop a paired run that does not say which blank corrects every well.

    The alternative rules were all considered and all end the same way. Pooling
    the unmapped wells corrects them with a reference containing other
    treatments' autofluorescence; leaving them uncorrected puts two differently
    processed populations in one CSV; and both produce output indistinguishable
    from the correct thing. A run that cannot say what corrects a well does not
    start.

    Only when a map is actually in force and an empty-well correction is
    actually requested — see :attr:`BlankGroups.in_force`.

    Called the moment the plate's own well list is known, which is the first
    moment a map can be held against it and well before any image is read: a
    run refused here has cost the user nothing.
    """
    if not (groups.paired and groups.in_force):
        return
    # Each list is `find_well_zips` / `find_well_folders`' own `(well, path)`
    # shape, taken rather than pre-flattened so the caller cannot build the
    # label list a different way than the run's own discovery did.
    missing = groups.uncovered(
        well for wells in well_lists for well, _path in wells)
    if not missing:
        return
    raise SystemExit(
        f"--background_well_map does not say which blank corrects "
        f"{len(missing)} well(s): {', '.join(missing)}.\n\n"
        f"Every well the run processes needs a blank, because a well corrected "
        f"by the wrong block's reference produces numbers that look exactly "
        f"like correct ones. Add the well(s) to a blank's entry, or drop the "
        f"map to pool every blank into one plate-wide reference."
    )


def plan_blank_groups(args, input_dir: Path) -> tuple[BlankGroups, Path]:
    """The run's blank pairing, and the directory its blanks are read from.

    Takes the parsed namespace because the three values it reads are one
    decision made in one place — ``pipeline.cli_args`` declares all three — and
    splitting them across a call site is how two of them come to disagree.

    *background_dir* is normally the run's own input. A batched remote run is
    the exception it exists for: that run stages one batch at a time, so the
    blank is not in most batches' input at all, and it is uploaded once to a
    directory of its own and named here. Every batch then builds the same
    reference from it.
    """
    groups = BlankGroups(args.background_well, args.background_well_map,
                         corrections=(args.flatfield, args.background))
    source = (Path(args.background_dir).expanduser()
              if args.background_dir else input_dir)
    if groups.paired:
        log.info("Empty-well pairing: %s", describe_pairing(groups))
    return groups, source


def prepare_models(shared_kwargs: dict, *, what: str, mode: str,
                   input_dir: Path, groups: BlankGroups, builder,
                   flat_key: str, grouped_key: str, source: Path,
                   **kwargs) -> None:
    """Build one correction's models, say what was built, refuse a gap, install.

    The four steps are one sequence and belong together: a model built and not
    checked is the silent-no-op this module exists to prevent, and a model
    checked and installed in the wrong shape is a well corrected by another
    block's blank. Keeping them in one call means a future correction cannot
    pick up three of the four.

    *source* is both the directory the blanks are read from and the directory
    the refusal message names, and it is one argument for that reason: passed
    separately they can differ, and a guard that names a directory the builder
    did not read sends the user to check the wrong place.

    The log line names the group on a paired run and omits it on a pooled one,
    so an unpaired run's log reads exactly as it did before pairing existed.

    The plan is narrowed to *mode* first, so a correction that does not read a
    blank is built and installed pooled however the run is paired — see
    :meth:`BlankGroups.for_mode`.
    """
    groups = groups.for_mode(mode)
    grouped = build_grouped_models(
        input_dir, groups=groups, builder=builder,
        background_dir=source, **kwargs)
    for group, models in grouped.items():
        log.info("%s (%s)%s: built field(s) for channel(s): %s",
                 what, mode, f" [{group}]" if group else "",
                 sorted(models) or "(none found)")
    require_grouped_models(what, mode, grouped, groups, source)
    install_grouped_models(shared_kwargs, groups, flat_key, grouped_key, grouped)


def describe_pairing(groups: BlankGroups) -> str:
    """One line naming each blank and how many wells it corrects, or ``""``."""
    if not groups.paired:
        return ""
    counts = {blank: 0 for blank in groups.by_group}
    for _well, blank in groups.well_group.items():
        counts[blank] = counts.get(blank, 0) + 1
    return "; ".join(f"{blank} corrects {n} well(s)" for blank, n in counts.items())


def install_grouped_models(shared_kwargs: dict, groups: BlankGroups,
                           flat_key: str, grouped_key: str, grouped) -> None:
    """Put one run's models into *shared_kwargs*, in the shape that run needs.

    The two shapes are deliberately not unified. An **unpaired** run installs
    the flat ``{channel: {tp: field}}`` under its usual key and nothing else —
    the same object, in the same place, that every run installed before pairing
    existed, so the pooled path is not merely equivalent to the old one but is
    the old one. A **paired** run installs the grouped dict and the groups under
    private keys instead, and :func:`shared_kwargs_for_well` swaps in each
    well's own group as that well is submitted.

    The flat key is emptied rather than left absent on a paired run, because
    ``process_image_group`` reads it either way and an absent key would be a
    second spelling of "no model" beside the empty dict.
    """
    if not groups.paired:
        shared_kwargs[flat_key] = grouped.get(_POOLED_GROUP, {})
        return
    shared_kwargs[flat_key] = {}
    shared_kwargs[grouped_key] = grouped
    shared_kwargs[WELL_GROUP_KEY] = groups
