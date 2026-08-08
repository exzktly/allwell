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
from well_viewer.data.image_identity import (
    PipelineSchema,
    load_schema,
    normalize_fov,
)
from well_viewer.domain.tp_match import tp_close
from datasets._common.dataset_paths import (
    make_dataset_api,
    DatasetPaths,
    dataset_wells,
    destination_is_complete_dataset,
    has_folder_mode_output,
    in_containers,
    out_csvs,
    out_zips,
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
    floating-point drift the way ``well_viewer.domain.tp_match.tp_close``
    already is everywhere else in the app. ``fovs`` are likewise the raw
    ``available_fovs`` strings the UI checkboxes carry — resolved to
    :func:`~well_viewer.data.image_identity.normalize_fov` tokens once here
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


def row_matches(row: dict, filt: SubsetFilter) -> bool:
    if filt.fovs is not None and normalize_fov(row.get("fov", "")) not in filt.fov_tokens:
        return False
    if filt.timepoint_hours is not None:
        try:
            hours = float(row.get("timepoint_hours", ""))
        except (TypeError, ValueError):
            return False
        if not any(tp_close(hours, h) for h in filt.timepoint_hours):
            return False
    return True


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
    "row_matches",
]
