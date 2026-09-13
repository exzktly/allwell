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
``datasets.core.member_names.rewrite_member_field``), the ``well`` column
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
from domain.image_identity import PipelineSchema, load_schema
from datasets.core.dataset_paths import (
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
from datasets.core.dataset_paths import check_compatible, known_channel_tokens

IN_PROGRESS_MARKER = ".aw_merge_in_progress"


class MergePlatesError(Exception):
    """A plate merge can't proceed. The message is user-facing."""


# The five dataset entry points, bound to this tool's error class and its
# own in-progress marker. See ``core.dataset_paths.make_dataset_api``
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
        raise MergePlatesError(f"{label} can't be merged. {message}")


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


def _remap_wells(wells, well_map: dict[str, str]) -> list:
    return [well_map.get(w, w) for w in (wells or [])]


def merge_sample_definitions(info1: dict, info2: dict, *, well_map: dict[str, str],
                             ds2_label: str) -> dict | None:
    """Merge two ``sample_definitions`` blocks into one, in the current schema.

    Dataset 2's well references are remapped through *well_map* and any of its
    names that collide with dataset 1's are disambiguated with the dataset-2
    folder name rather than overwriting the dataset-1 definition.

    Both inputs go through :func:`domain.selections_format.from_block`, which is the
    only thing in the codebase that knows how to read a saved block: it
    validates a current (v2) one and migrates a legacy (v1) one on the way in.
    Reading the raw ``rep_sets`` / ``groups`` keys directly — as this function
    used to — sees nothing at all in a block written by today's app, and since
    the result *replaces* dataset 1's whole block, a merge of two normally-saved
    plates wrote back an empty v1 shape: every selection, replicate set, plot
    group, well label, current selection and note from both datasets gone, and
    the merge reported as a clean success.

    The output is built with :func:`domain.selections_format.to_block`, so the merged
    plate carries the same shape the app writes for any other plate.
    """
    from domain.selections_format import from_block, to_block

    sd1 = info1.get("sample_definitions") or {}
    sd2 = info2.get("sample_definitions") or {}
    if not sd1 and not sd2:
        return None

    sels1, current_id, labels1, notes1, groups1 = from_block(sd1)
    sels2, _current2, labels2, notes2, groups2 = from_block(sd2)

    # Well labels: dataset 1 wins a collision, which can only happen where the
    # remap put a dataset-2 well on top of a dataset-1 one.
    well_labels = dict(labels1)
    for well, label in labels2.items():
        well_labels.setdefault(well_map.get(well, well), label)

    used_names = {s.get("name", "") for s in sels1}
    selections = [dict(s) for s in sels1]
    for sel in sels2:
        name = _dedup_name(sel.get("name", ""), used_names, ds2_label)
        used_names.add(name)
        merged = dict(sel)
        merged["name"] = name
        wells = _remap_wells(sel.get("wells"), well_map)
        if wells != list(sel.get("wells") or []):
            # The remap moved this condition to a different part of the plate,
            # and a selection's colour is baked from its lowest well's rank —
            # so the stored one now describes a position it no longer occupies.
            # Dropping it lets ``to_block`` re-bake from where the wells landed.
            # Left in place, dataset 1's "Control" at A01 and dataset 2's,
            # remapped to B01, both render #6AB2FB: two conditions the user
            # cannot tell apart in a bar chart.
            merged.pop("color", None)
        merged["wells"] = wells
        # ``replicates`` is deliberately not remapped: the field is reserved and
        # ``make_selection`` collapses it to ``None`` on every selection it
        # builds, so ``to_block`` below discards whatever were put here. A
        # remap would be code that runs and changes nothing.
        # The id is dropped rather than remapped: an id is only unique within one block,
        # and ``to_block``'s validation re-mints a duplicate anyway. Letting it
        # mint them here keeps dataset 1's ids stable, which is what
        # ``current_id`` below points at.
        merged.pop("id", None)
        selections.append(merged)

    # Plot groups are a separate model with its own name uniqueness, so they get
    # the same treatment rather than riding along inside a selection.
    used_group_names = {g.get("name", "") for g in groups1}
    plot_groups = [dict(g) for g in groups1]
    for group in groups2:
        name = _dedup_name(group.get("name", ""), used_group_names, ds2_label)
        used_group_names.add(name)
        merged = dict(group)
        merged["name"] = name
        merged["wells"] = _remap_wells(group.get("wells"), well_map)
        merged.pop("id", None)
        plot_groups.append(merged)

    # Both notes are kept, labelled, because a note is the one field here whose
    # value is prose a person wrote and nothing can reconstruct it.
    parts = [t for t in (notes1.strip(), notes2.strip()) if t]
    if len(parts) == 2:
        notes = f"{parts[0]}\n\n--- from {ds2_label} ---\n{parts[1]}"
    else:
        notes = parts[0] if parts else ""

    return to_block(selections, well_labels=well_labels, notes=notes,
                    current_id=current_id, plot_groups=plot_groups)


#: Analysis blocks that cannot be combined by rule — each is a decision made
#: under one plate's imaging conditions — paired with the words to describe it.
_UNMERGEABLE_BLOCKS: tuple[tuple[str, str], ...] = (
    ("cell_gating", "cell gating"),
    ("ratios", "ratio metrics"),
    ("qc_options", "QC parameters"),
    ("smfish_thresholds", "smFISH thresholds"),
)


def _dropped_blocks_warning(info1: dict, info2: dict, *, ds1_label: str,
                            ds2_label: str) -> str | None:
    """Name what dataset 2 loses, in the two ways it can lose it.

    A block dataset 1 also has is *replaced*: the merged plate carries dataset
    1's version, which is a defensible choice the user should know was made. A
    block only dataset 2 has is *gone*, because the merged sidecar starts as a
    copy of dataset 1 — and saying "the merged plate keeps dataset 1's" about a
    block dataset 1 never had would be the degraded-output-reported-as-clean
    shape this warning exists to prevent, stated in the warning itself.

    Blocks whose two values are equal are not mentioned at all. Nothing was
    lost, and a note that fires on every merge trains the user to skip it.
    """
    replaced: list[str] = []
    lost: list[str] = []
    for key, label in _UNMERGEABLE_BLOCKS:
        theirs = info2.get(key)
        if not theirs:
            continue
        ours = info1.get(key)
        if not ours:
            lost.append(label)
        elif ours != theirs:
            replaced.append(label)
    parts: list[str] = []
    if replaced:
        parts.append(
            f"{ds2_label}'s " + ", ".join(replaced)
            + f" differ from {ds1_label}'s; the merged plate keeps {ds1_label}'s"
        )
    if lost:
        parts.append(
            f"{ds2_label}'s " + ", ".join(lost)
            + f" are not carried over ({ds1_label} has none to merge them with)"
        )
    if not parts:
        return None
    return ". ".join(parts) + ". Sample definitions from both are merged."


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
    #: Named when dataset 2 carries analysis blocks the merge keeps only
    #: dataset 1's copy of. Sample definitions are genuinely merged; the rest
    #: are decisions about the science that cannot be combined by rule, so the
    #: merge takes dataset 1's — and says which ones it dropped, because a
    #: merged plate carrying half the user's gating and reporting nothing is
    #: the shape this codebase refuses to produce.
    blocks_warning: str | None = None


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

    _refuse_well_collisions(ds1, "Dataset 1")
    _refuse_well_collisions(ds2, "Dataset 2")

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

    # ``merged_info`` starts as a copy of dataset 1, so every other analysis
    # block dataset 2 holds is replaced by dataset 1's. Say which — but only
    # where the two datasets actually disagree. ``qc_options`` in particular is
    # written for every run as a full dict of eleven keys, so a note keyed on
    # the block merely being *present* fires on every merge of two real plates
    # and is noise rather than a warning.
    blocks_warning = _dropped_blocks_warning(
        info1, info2, ds1_label=ds1.root.name, ds2_label=ds2.root.name)

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
        blocks_warning=blocks_warning,
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
