"""Pure planning for the Subset / Split Dataset feature.

Copies a filtered slice of a dataset — a set of wells, timepoints, and/or
FOVs — into a fresh destination folder, leaving the source untouched.
Deliberately **keeps every kept row's original timepoint hours** (no
re-basing): a subset of 24h–72h stays labelled 24h–72h. That single decision
is what keeps this feature small — the four coupled timepoint-derived values
ARCHITECTURE §6b documents for Concatenate (the `timepoint` label, the
numeric `timepoint_hours` column, the `filename` column, and the zip member's
own name) never diverge here because **nothing about a kept row or member is
rewritten at all**. Filtering is a pure predicate over what's already there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from util.timepoint import parse_hours
from review.data.image_identity import (
    PipelineSchema,
    load_schema,
    normalize_fov,
)
from review.domain.tp_match import tp_close
from datasets._common.dataset_paths import (
    make_dataset_api,
    DatasetPaths,
    dataset_wells,
    describe_well_collisions,
    well_spelling_collisions,
    destination_is_complete_dataset,
    has_folder_mode_output,
    in_containers,
    out_csvs,
    out_zips,
)
from datasets._common.containers import (
    FILENAME_COLUMN,
    TIMEPOINT_COLUMN,
    TIMEPOINT_HOURS_COLUMN,
)
from datasets._common.member_names import discover_parsed_member
from datasets._common.dataset_paths import known_channel_tokens

IN_PROGRESS_MARKER = ".aw_subset_in_progress"


class SubsetError(Exception):
    """A subset can't proceed. The message is user-facing."""


# The five dataset entry points, bound to this tool's error class and its
# own in-progress marker. See ``_common.dataset_paths.make_dataset_api``
# for why they are generated rather than written out: three tools carried
# the same five wrappers, differing only in these two choices.
_api = make_dataset_api(SubsetError, IN_PROGRESS_MARKER)
resolve_dataset = _api.resolve_dataset
read_info = _api.read_info
destination_has_marker = _api.destination_has_marker
destination_is_failed_run = _api.destination_is_failed_run
destination_has_unrecognized_content = _api.destination_has_unrecognized_content

@dataclass(frozen=True)
class SubsetFilter:
    """``None`` on any field means "keep everything" for that axis.

    ``timepoint_labels`` are the raw ``available_timepoints`` strings the UI
    lets the user check — resolved to hours (via :func:`util.timepoint.parse_hours`)
    once here, so every downstream comparison is numeric and tolerant of
    floating-point drift the way ``review.domain.tp_match.tp_close``
    already is everywhere else in the app. ``fovs`` are likewise the raw
    ``available_fovs`` strings the UI checkboxes carry — resolved to
    :func:`~review.data.image_identity.normalize_fov` tokens once here
    (:attr:`fov_tokens`) rather than at each comparison site, since every row/
    member's own FOV is read through the same normalizer (a purely-numeric
    label like ``"001"`` normalizes to ``"1"``; comparing that against a raw,
    unnormalized ``fovs`` set silently matched nothing).

    Both resolved sets are computed **once**, at construction, into fields the
    comparison sites read straight off. They used to be properties, which put a
    ``parse_hours`` / ``normalize_fov`` pass over the whole label set on every
    read — and :func:`member_matches` reads them up to four times for every
    member of every archive on the plate, so that pass ran hundreds of
    thousands of times a run to answer a question whose answer never changes.
    """

    wells: frozenset[str] | None = None
    timepoint_labels: frozenset[str] | None = None
    fovs: frozenset[str] | None = None
    # Derived, not passed in: kept out of init/repr/eq so the filter's identity
    # and equality stay exactly what its three real fields say.
    timepoint_hours: frozenset[float] | None = field(
        init=False, repr=False, compare=False, default=None,
    )
    fov_tokens: frozenset[str] | None = field(
        init=False, repr=False, compare=False, default=None,
    )

    def __post_init__(self) -> None:
        hours = None
        if self.timepoint_labels is not None:
            parsed = {parse_hours(t) for t in self.timepoint_labels}
            hours = frozenset(h for h in parsed if h is not None)
        object.__setattr__(self, "timepoint_hours", hours)

        tokens = None
        if self.fovs is not None:
            tokens = frozenset(normalize_fov(f) for f in self.fovs)
        object.__setattr__(self, "fov_tokens", tokens)


@dataclass(frozen=True)
class SubsetPlan:
    src: DatasetPaths
    dest: DatasetPaths
    schema: PipelineSchema
    channels: tuple[str, ...]
    filt: SubsetFilter
    wells_kept: tuple[str, ...]
    merged_info: dict
    dest_is_restart: bool = False


def _refuse_well_collisions(paths: DatasetPaths, label: str) -> None:
    """Refuse *paths* when two files claim one well under different
    spellings.

    Every well this tool copies is found through ``in_containers`` /
    ``out_zips`` / ``out_csvs``, each of which maps a canonical well to one
    path — so an ``A1.zip`` sitting beside an ``A01.zip`` is simply not in
    the map the copy walks, and a whole well's images leave no trace in the
    destination or in the summary. The map cannot say that happened, so the
    folder is asked directly, once, before anything is written.
    """
    message = describe_well_collisions(well_spelling_collisions(paths))
    if message:
        raise SubsetError(f"{label} can't be copied. {message}")


def build_subset_plan(
    src_root: Path | str, dest_root: Path | str, filt: SubsetFilter,
) -> SubsetPlan:
    """Validate a source dataset and destination, and produce a
    :class:`SubsetPlan`. Raises :class:`SubsetError` on any incompatibility.
    Read-only — the destination is not created or cleared here.
    """
    src = resolve_dataset(src_root, label="source dataset")
    dest_root = Path(dest_root)
    dest = DatasetPaths(root=dest_root, in_dir=dest_root / "in", out_dir=dest_root / "out")

    if dest.root == src.root:
        raise SubsetError(
            "The destination must be a different folder from the source dataset."
        )
    if destination_is_complete_dataset(dest):
        raise SubsetError(
            f"The destination ({dest.root}) already holds a finished dataset "
            "(out/pipeline_info.json exists there) — choose a new, empty folder."
        )
    if destination_has_unrecognized_content(dest):
        raise SubsetError(
            f"The destination ({dest.root}) already has files that weren't "
            "created by a previous subset attempt — choose an empty folder."
        )
    dest_is_restart = destination_is_failed_run(dest)

    _refuse_well_collisions(src, "The source dataset")

    if has_folder_mode_output(src):
        raise SubsetError(
            "The source dataset stores its QC images as per-well folders "
            "(out/<WELL>/) instead of <WELL>_out.zip archives — Subset only "
            "supports the zipped output layout. Re-run the pipeline with "
            "compressed output, or zip the folders manually, then try again."
        )

    info = read_info(src, label="source dataset")
    schema = load_schema(info)
    channels = known_channel_tokens(info)

    all_wells = dataset_wells(src)
    wells_kept = tuple(sorted(all_wells if filt.wells is None else (all_wells & filt.wells)))
    if not wells_kept:
        raise SubsetError("No wells match the requested filter.")

    merged_info = dict(info)
    if filt.timepoint_labels is not None:
        kept_hours = filt.timepoint_hours or frozenset()
        merged_info["available_timepoints"] = [
            t for t in (info.get("available_timepoints", []) or [])
            if parse_hours(str(t)) in kept_hours
        ]
    if filt.fovs is not None:
        merged_info["available_fovs"] = [
            f for f in (info.get("available_fovs", []) or [])
            if normalize_fov(str(f)) in filt.fov_tokens
        ]

    return SubsetPlan(
        src=src, dest=dest, schema=schema, channels=channels, filt=filt,
        wells_kept=wells_kept, merged_info=merged_info, dest_is_restart=dest_is_restart,
    )


# ── member/row filter predicates ─────────────────────────────────────────────
def member_matches(
    name: str, *, schema: PipelineSchema, channels: tuple[str, ...], filt: SubsetFilter,
) -> bool:
    """Whether a zip member's schema-parsed fields satisfy *filt*.

    Resolves the member through :func:`~datasets._common.member_names.discover_parsed_member`
    rather than a bare ``parse_image_filename`` — a channel-scoped role suffix
    (``_tophat_gfp``, ``_smfish_nir``) doesn't resolve without trying each
    known channel token as the role-suffix discriminator, and without that a
    filtered-out timepoint's role-suffixed image falls through the
    unparseable-member fallback below and leaks into the destination.

    A member the schema can't parse at all (a non-conformant name — the same
    gate ``rewrite_member_field`` applies) is **kept** rather than dropped:
    since nothing here rewrites anything, an unparseable member is exactly the
    kind of "not this dataset's business" file Concatenate also passes
    through untouched, not a reason to lose data silently.
    """
    if filt.fovs is None and filt.timepoint_hours is None:
        return True
    parsed, _discovered_channel = discover_parsed_member(name, schema, channels)
    parts = parsed.stem.split(schema.sep)
    if len(parts) != len(schema.fields):
        return True
    if filt.fovs is not None and parsed.fov not in filt.fov_tokens:
        return False
    if filt.timepoint_hours is not None:
        hours = parse_hours(parsed.fields.get("timepoint", ""))
        if hours is None or not any(tp_close(hours, h) for h in filt.timepoint_hours):
            return False
    return True


#: What :func:`row_verdict` decided about one measurement row. ``UNJUDGEABLE``
#: is the state the row filter used to collapse into "no match": a row the
#: filter could not test at all is not a row the filter rejected, and the
#: difference is the whole of what ``apply._verify`` needs to name the real
#: cause instead of blaming the filter.
ROW_KEEP = "keep"
ROW_DROP = "drop"
ROW_UNJUDGEABLE = "unjudgeable"


def row_hours(
    row: dict, *, schema: PipelineSchema | None = None, channels: tuple[str, ...] = (),
) -> float | None:
    """A measurement row's timepoint in hours, or ``None`` if the row carries
    none this dataset's schema can resolve.

    :func:`member_matches` reads the *filename's* timepoint token; this used to
    read only the ``timepoint_hours`` **column**, and the two therefore answered
    the same question from different places. A legacy CSV — one written before
    the numeric column existed, or by a run whose parser left it blank — carries
    nothing there, so every row failed the timepoint test while every image
    passed it: the images copied, the measurements did not, and the
    verification error blamed the user's filter for a column the pipeline never
    filled.

    So the sources are tried in the order of how directly each states the
    answer: the numeric column, then the ``timepoint`` label column through
    :func:`~util.timepoint.parse_hours` (the same parse the member side does),
    then the ``filename`` column's own timepoint token — which is literally the
    value :func:`member_matches` reads, so the last fallback makes the two
    predicates read the identical token rather than merely a comparable one.
    """
    try:
        return float(row.get(TIMEPOINT_HOURS_COLUMN, ""))
    except (TypeError, ValueError):
        pass
    hours = parse_hours(str(row.get(TIMEPOINT_COLUMN, "") or ""))
    if hours is not None:
        return hours
    name = str(row.get(FILENAME_COLUMN, "") or "")
    if not name or schema is None:
        return None
    parsed, _discovered_channel = discover_parsed_member(name, schema, channels)
    parts = parsed.stem.split(schema.sep)
    if len(parts) != len(schema.fields):
        return None
    return parse_hours(parsed.fields.get("timepoint", ""))


def row_verdict(
    row: dict, filt: SubsetFilter, *,
    schema: PipelineSchema | None = None, channels: tuple[str, ...] = (),
) -> str:
    """Whether *row* survives *filt* — :data:`ROW_KEEP`, :data:`ROW_DROP`, or
    :data:`ROW_UNJUDGEABLE` when the row states no timepoint at all.

    The FOV axis has no unjudgeable state on purpose. A blank ``fov`` column
    normalizes to ``"1"`` app-wide (:func:`~review.data.image_identity.normalize_fov`,
    and ``CellKey`` in the override reader) — on a single-FOV dataset that is
    the FOV, not a missing value — so reading it as undefined here would invent
    a second meaning for a blank the rest of the app has already settled.
    """
    if filt.fovs is not None and normalize_fov(row.get("fov", "")) not in filt.fov_tokens:
        return ROW_DROP
    if filt.timepoint_hours is not None:
        hours = row_hours(row, schema=schema, channels=channels)
        if hours is None:
            return ROW_UNJUDGEABLE
        if not any(tp_close(hours, h) for h in filt.timepoint_hours):
            return ROW_DROP
    return ROW_KEEP


def row_matches(
    row: dict, filt: SubsetFilter, *,
    schema: PipelineSchema | None = None, channels: tuple[str, ...] = (),
) -> bool:
    """Whether *row* survives *filt*. An unjudgeable row is not a match — it is
    dropped, exactly as before. A caller that has to tell "the filter rejected
    it" from "the filter could never test it" (``apply._verify``, which must not
    blame the filter for a CSV that states no timepoint) reads
    :func:`row_verdict` instead."""
    return row_verdict(row, filt, schema=schema, channels=channels) == ROW_KEEP


__all__ = [
    "IN_PROGRESS_MARKER",
    "SubsetError",
    "SubsetFilter",
    "SubsetPlan",
    "build_subset_plan",
    "destination_has_marker",
    "destination_has_unrecognized_content",
    "destination_is_failed_run",
    "in_containers",
    "member_matches",
    "out_csvs",
    "out_zips",
    "read_info",
    "resolve_dataset",
    "ROW_DROP",
    "ROW_KEEP",
    "ROW_UNJUDGEABLE",
    "row_hours",
    "row_matches",
    "row_verdict",
]
