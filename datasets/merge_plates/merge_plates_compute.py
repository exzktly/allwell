"""Pure planning for the Merge Plates feature.

The well-axis sibling of Concatenate. Concatenate joins **the same plate at
different times** (a well present in both datasets is stitched — more
timepoints for the same sample); this joins **different plates at the same
times** (two 24-well runs becoming one 96-well dataset). The semantics are
therefore the inverse of Concatenate's on the one point that matters most: a
well present in both datasets here is **two different biological samples**,
so it is **refused**, not merged — unless the user remaps dataset 2's wells
onto free positions first.

A well is carried by four values, and all four must move together — the same
lock-step discipline ARCHITECTURE §6b documents for Concatenate's timepoints,
with a wider surface because three of these are filenames rather than file
contents: the ``well`` field inside each zip member's filename (via
``datasets._common.member_names.rewrite_member_field``), the ``well`` column
in every CSV row, the three container names
(``in/<WELL>.zip``/``out/<WELL>_out.zip``/``out/<csv_prefix>_<WELL>.csv``),
and the ``well`` keys inside ``cell_overrides.json`` and
``pipeline_info.json::sample_definitions``.

When dataset 2's wells are already disjoint from dataset 1's (the identity
map, the common case), **nothing is rewritten at all** and the merge is a
pure copy — this is deliberately the fast, simple path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from util.timepoint import parse_hours, sort_key as tp_sort_key
from util.well_token import canonical_well_label
from well_viewer.data.image_identity import PipelineSchema, load_schema
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
from datasets._common.dataset_paths import check_compatible, known_channel_tokens

IN_PROGRESS_MARKER = ".aw_merge_in_progress"


class MergePlatesError(Exception):
    """A plate merge can't proceed. The message is user-facing."""


# The five dataset entry points, bound to this tool's error class and its
# own in-progress marker. See ``_common.dataset_paths.make_dataset_api``
# for why they are generated rather than written out: three tools carried
# the same five wrappers, differing only in these two choices.
_api = make_dataset_api(MergePlatesError, IN_PROGRESS_MARKER)
resolve_dataset = _api.resolve_dataset
read_info = _api.read_info
destination_has_marker = _api.destination_has_marker
destination_is_failed_run = _api.destination_is_failed_run
destination_has_unrecognized_content = _api.destination_has_unrecognized_content

_PLATE_ROWS = "ABCDEFGH"
_PLATE_MAX_COL = 12


def block_shift_well_map(wells, *, target_origin: str) -> dict[str, str]:
    """Translate *wells* as a rigid block so their minimum (row, col) corner
    lands at *target_origin* — the "place dataset 2 starting at E01" UI
    control. Covers the common quadrant-placement case; an arbitrary,
    non-rectangular remap still goes through ``well_map`` directly.

    Raises :class:`MergePlatesError` if the shifted block would run off the
    96-well plate (a column past 12 or a row past H).
    """
    tokens = [canonical_well_label(w) for w in wells]
    if any(t is None for t in tokens):
        raise MergePlatesError("Cannot shift: one or more wells aren't valid 96-well positions.")
    if not tokens:
        return {}
    target = canonical_well_label(target_origin)
    if target is None:
        raise MergePlatesError(f"{target_origin!r} isn't a valid 96-well position.")

    rcs = [(_PLATE_ROWS.index(t[0]), int(t[1:]) - 1) for t in tokens]
    min_row = min(r for r, _ in rcs)
    min_col = min(c for _, c in rcs)
    target_row = _PLATE_ROWS.index(target[0])
    target_col = int(target[1:]) - 1
    delta_row = target_row - min_row
    delta_col = target_col - min_col

    result: dict[str, str] = {}
    for token, (row, col) in zip(tokens, rcs):
        new_row, new_col = row + delta_row, col + delta_col
        if not (0 <= new_row < len(_PLATE_ROWS)) or not (0 <= new_col < _PLATE_MAX_COL):
            raise MergePlatesError(
                f"Shifting to {target_origin} would place well {token} outside "
                "the 96-well plate."
            )
        result[token] = f"{_PLATE_ROWS[new_row]}{new_col + 1:02d}"
    return result


def _ordered_union(first, second) -> list:
    merged = list(first)
    for item in second:
        if item not in merged:
            merged.append(item)
    return merged


def _dedup_name(name: str, existing: set[str], suffix: str) -> str:
    """Disambiguate *name* against *existing* by suffixing with *suffix* (the
    dataset-2 folder name) rather than silently overwriting a same-named
    dataset-1 definition."""
    if name not in existing:
        return name
    candidate = f"{name} ({suffix})"
    n = 2
    while candidate in existing:
        candidate = f"{name} ({suffix} {n})"
        n += 1
    return candidate


def merge_sample_definitions(info1: dict, info2: dict, *, well_map: dict[str, str], ds2_label: str) -> dict | None:
    """Merge ``sample_definitions`` blocks, remapping dataset 2's well
    references through *well_map* and disambiguating dataset-2 rep-set/group
    names that collide with dataset 1's rather than overwriting them."""
    sd1 = info1.get("sample_definitions") or {}
    sd2 = info2.get("sample_definitions") or {}
    if not sd1 and not sd2:
        return None

    well_labels = dict(sd1.get("well_labels") or {})
    for well, label in (sd2.get("well_labels") or {}).items():
        well_labels.setdefault(well_map.get(well, well), label)

    existing_rep_names = {r.get("name", "") for r in (sd1.get("rep_sets") or [])}
    rep_sets = list(sd1.get("rep_sets") or [])
    rep_name_remap: dict[str, str] = {}
    for rep in (sd2.get("rep_sets") or []):
        old_name = rep.get("name", "")
        new_name = _dedup_name(old_name, existing_rep_names, ds2_label)
        rep_name_remap[old_name] = new_name
        existing_rep_names.add(new_name)
        rep_sets.append({
            "name": new_name,
            "wells": [well_map.get(w, w) for w in rep.get("wells", [])],
        })

    existing_group_names = {g.get("name", "") for g in (sd1.get("groups") or [])}
    groups = list(sd1.get("groups") or [])
    for group in (sd2.get("groups") or []):
        new_name = _dedup_name(group.get("name", ""), existing_group_names, ds2_label)
        existing_group_names.add(new_name)
        groups.append({
            "name": new_name,
            "hidden": group.get("hidden", False),
            "members": [rep_name_remap.get(m, m) for m in group.get("members", [])],
            "solo_wells": [well_map.get(w, w) for w in group.get("solo_wells", [])],
        })

    return {"well_labels": well_labels, "rep_sets": rep_sets, "groups": groups}


@dataclass(frozen=True)
class PlateMergePlan:
    ds1: DatasetPaths
    ds2: DatasetPaths
    dest: DatasetPaths
    schema: PipelineSchema
    channels: tuple[str, ...]
    well_map: dict           # ds2 source well -> dest well (identity if unmapped)
    wells_ds1: tuple[str, ...]
    wells_ds2: tuple[str, ...]   # dest tokens
    merged_info: dict
    dest_is_restart: bool = False
    timepoint_warning: str | None = None


def build_plate_merge_plan(
    ds1_root: Path | str, ds2_root: Path | str, dest_root: Path | str,
    well_map: dict[str, str] | None = None,
) -> PlateMergePlan:
    """Validate two datasets, a destination, and an optional well remap, and
    produce a :class:`PlateMergePlan`. Raises :class:`MergePlatesError` on any
    incompatibility. Read-only — the destination is not created/cleared here.
    """
    ds1 = resolve_dataset(ds1_root, label="dataset 1")
    ds2 = resolve_dataset(ds2_root, label="dataset 2")
    dest_root = Path(dest_root)
    dest = DatasetPaths(root=dest_root, in_dir=dest_root / "in", out_dir=dest_root / "out")

    if dest.root in (ds1.root, ds2.root):
        raise MergePlatesError("The destination must be a different folder from both datasets.")
    if ds1.root == ds2.root:
        raise MergePlatesError(
            "Dataset 1 and Dataset 2 are the same folder — merging a plate "
            "with itself isn't supported."
        )
    if destination_is_complete_dataset(dest):
        raise MergePlatesError(
            f"The destination ({dest.root}) already holds a finished dataset — "
            "choose a new, empty folder."
        )
    if destination_has_unrecognized_content(dest):
        raise MergePlatesError(
            f"The destination ({dest.root}) already has files that weren't "
            "created by a previous merge attempt — choose an empty folder."
        )
    dest_is_restart = destination_is_failed_run(dest)

    info1 = read_info(ds1, label="dataset 1")
    info2 = read_info(ds2, label="dataset 2")
    check_compatible(info1, info2, error_cls=MergePlatesError)

    if has_folder_mode_output(ds1) or has_folder_mode_output(ds2):
        which = "dataset 1" if has_folder_mode_output(ds1) else "dataset 2"
        raise MergePlatesError(
            f"{which} stores its QC images as per-well folders instead of "
            "<WELL>_out.zip archives — Merge plates only supports the "
            "zipped output layout."
        )

    wells1 = dataset_wells(ds1)
    wells2 = dataset_wells(ds2)
    if not wells1 and not wells2:
        raise MergePlatesError("Neither dataset has any wells to merge.")

    resolved_map = dict(well_map or {})
    invalid_targets = sorted(
        v for v in resolved_map.values() if canonical_well_label(v) is None
    )
    if invalid_targets:
        raise MergePlatesError(
            f"Well remap target(s) aren't valid 96-well positions: {invalid_targets}."
        )
    resolved_map = {canonical_well_label(k) or k: canonical_well_label(v) or v
                    for k, v in resolved_map.items()}

    mapped2 = [resolved_map.get(w, w) for w in wells2]
    # A dict comprehension over wells2 (well_map.get(w, w) collected into a
    # set) would silently collapse two distinct dataset-2 wells that map to
    # the same destination — the set has one element either way, so the
    # collision check below would never see it, and execute_plan would then
    # submit both source wells as separate ThreadPoolExecutor futures that
    # write the same destination path concurrently. Check the *list* for an
    # internal duplicate before deduping it into a set.
    dupes = sorted({w for w in mapped2 if mapped2.count(w) > 1})
    if dupes:
        raise MergePlatesError(
            "The well remap maps more than one dataset-2 well onto the same "
            f"destination position: {dupes}. Each dataset-2 well must land "
            "on its own free position."
        )
    dest_from_ds2 = set(mapped2)
    collisions = sorted(dest_from_ds2 & wells1)
    if collisions:
        raise MergePlatesError(
            "These wells exist in both datasets and would collide after "
            f"remapping — a well present in both is treated as two different "
            f"samples, not one to stitch: {collisions}. Remap dataset 2's "
            "wells onto free positions to resolve this."
        )

    tp1 = [str(t).strip() for t in info1.get("available_timepoints", []) or [] if str(t).strip()]
    tp2 = [str(t).strip() for t in info2.get("available_timepoints", []) or [] if str(t).strip()]
    for label, tps in (("dataset 1", tp1), ("dataset 2", tp2)):
        for tok in tps:
            hours = parse_hours(tok)
            if hours is None or not math.isfinite(hours):
                raise MergePlatesError(
                    f"{label}: timepoint {tok!r} can't be read as a time. Merge "
                    "plates needs every timepoint to be time-valued."
                )
    timepoint_warning = None
    if set(tp1) != set(tp2):
        timepoint_warning = (
            "The two datasets don't share the same timepoints — the merged "
            "plate will be ragged (some wells have timepoints others don't); "
            "plots will show gaps for the missing combinations."
        )

    merged_timepoints = sorted(_ordered_union(tp1, tp2), key=tp_sort_key)
    merged_fovs = _ordered_union(info1.get("available_fovs", []) or [], info2.get("available_fovs", []) or [])

    merged_info = dict(info1)
    merged_info["available_timepoints"] = merged_timepoints
    merged_info["available_fovs"] = merged_fovs
    for key in ("fluor_tokens", "smfish_tokens", "shape_features"):
        merged_info[key] = _ordered_union(info1.get(key, []) or [], info2.get(key, []) or [])
    sample_defs = merge_sample_definitions(
        info1, info2, well_map=resolved_map, ds2_label=ds2.root.name,
    )
    if sample_defs is not None:
        merged_info["sample_definitions"] = sample_defs

    return PlateMergePlan(
        ds1=ds1, ds2=ds2, dest=dest,
        schema=load_schema(info1),
        channels=known_channel_tokens(info1, info2),
        well_map=resolved_map,
        wells_ds1=tuple(sorted(wells1)),
        wells_ds2=tuple(sorted(dest_from_ds2)),
        merged_info=merged_info,
        dest_is_restart=dest_is_restart,
        timepoint_warning=timepoint_warning,
    )


__all__ = [
    "IN_PROGRESS_MARKER",
    "MergePlatesError",
    "PlateMergePlan",
    "block_shift_well_map",
    "build_plate_merge_plan",
    "destination_has_marker",
    "destination_has_unrecognized_content",
    "destination_is_failed_run",
    "in_containers",
    "merge_sample_definitions",
    "out_csvs",
    "out_zips",
    "read_info",
    "resolve_dataset",
]
