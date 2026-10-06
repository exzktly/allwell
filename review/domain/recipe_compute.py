"""What can be copied from one dataset's analysis to another, and what will not fit.

A *recipe* here is the analysis decisions one dataset holds — its sample
variables and definitions, its gating, its ratio channels, its Batch Export
groups, its saved plots — applied to a second dataset. The source is either
another dataset folder or a **recipe file**: the same blocks written to one
JSON document (:func:`recipe_document`) so they can be kept, shared and applied
without the dataset they came from. A recipe file is read back into the same
:class:`DatasetBlocks` a folder is (:func:`blocks_from_document`), so the fit
check and the copy cannot tell the two sources apart.

This module is the pure half: the roster of what can be copied, and the check of
how well each piece fits the target, run **before anything is written**. The
reading and the writing are :mod:`review.persistence.recipe`; the dialog is
:mod:`review.core.recipe_view`.

**The roster is the reset roster.** ``persistence/reset_scopes.ANALYSIS_SCOPES``
already names every piece of analysis state and where it lives, so each recipe
scope is one of those keys, and every key is either here or in
:data:`DECLINED` with its reason. ``tests/test_recipes.py`` holds the two to
each other, so a new analysis scope cannot be silently left out of — or
silently copied by — a recipe.

**Fit is reported, not repaired.** A copied block names wells and channels the
target may not have. :func:`check_fit` finds every such name and says so per
scope; it never rewrites the block to drop or rename them. A gate on a channel
the target does not carry is inert there, and a definition naming a well the
target lacks attaches to nothing — both harmless to keep and both worth
knowing about before pressing Copy, which is the whole point of the preview.

The detection is a walk over the block's strings and keys, because the blocks
have half a dozen shapes and a shape-specific reader per scope is a second
definition of each format that can drift from the first. A string counts as a
well when it is a canonical 96-well label; as a channel when it equals a source
channel token or starts with one followed by ``_`` (the per-cell column
convention, ``gfp_mean_intensity``), case-insensitively. That can over-count —
a group literally named ``B02`` reads as a well — and over-counting a warning
is the safe direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from util.provenance_log import block_digest
from util.well_token import canonical_well_label

PIPELINE_INFO = "pipeline_info.json"
PERSISTENCE = "persistence.json"


@dataclass(frozen=True)
class RecipeScope:
    """One copyable piece of analysis state."""

    key: str
    title: str
    #: ``(file, key)`` for every block the scope owns, in the order applied.
    blocks: tuple[tuple[str, str], ...]


#: What a recipe can copy, keyed like ``ANALYSIS_SCOPES`` where it is one.
#: ``saved_plots`` is the one entry that is not an analysis scope: a saved plot
#: is a figure, which no reset clears, but carrying the figures across is half
#: of what a recipe is for.
RECIPE_SCOPES: tuple[RecipeScope, ...] = (
    RecipeScope("sample_variables", "Sample variables",
                ((PIPELINE_INFO, "variables"),)),
    RecipeScope("sample_definitions", "Sample definitions",
                ((PIPELINE_INFO, "sample_definitions"), (PERSISTENCE, "plot_groups"))),
    RecipeScope("gating", "Gating",
                ((PIPELINE_INFO, "cell_gating"), (PERSISTENCE, "qc_params"))),
    RecipeScope("ratios", "Ratio metrics", ((PERSISTENCE, "ratios"),)),
    RecipeScope("export_groups", "Batch Export groups",
                ((PERSISTENCE, "export_groups"),)),
    RecipeScope("saved_plots", "Saved plots", ((PERSISTENCE, "saved_plots"),)),
)

#: Analysis scopes a recipe deliberately does not copy, with the reason.
DECLINED: dict[str, str] = {
    "curation": (
        "per-cell overrides are keyed by the source's nucleus ids, a crop by "
        "its pixel geometry, and a field exclusion by a field the source run "
        "measured; none of them identifies anything on another plate"),
    "smfish": (
        "a saved smFISH threshold is the one the per-well spot counts were "
        "baked with by Apply Global Threshold; copied alone it would describe "
        "counts the target's CSVs were never computed at"),
}


def scope_by_key(key: str) -> RecipeScope | None:
    for scope in RECIPE_SCOPES:
        if scope.key == key:
            return scope
    return None


@dataclass(frozen=True)
class ScopeFit:
    """How one scope fits the target.

    ``present`` is False when the source holds nothing for this scope — there
    is nothing to copy, which is different from copying an empty value over the
    target's own. ``missing_wells`` / ``missing_channels`` are names the source
    block uses that the target does not have. ``replaces`` is True when the
    target already holds a different value that copying would overwrite.
    ``refusal`` is non-empty when the scope cannot be applied at all.
    """

    key: str
    title: str
    present: bool
    missing_wells: tuple[str, ...] = ()
    missing_channels: tuple[str, ...] = ()
    replaces: bool = False
    refusal: str = ""
    notes: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        """``refused`` / ``absent`` / ``warning`` / ``clean``."""
        if self.refusal:
            return "refused"
        if not self.present:
            return "absent"
        if self.missing_wells or self.missing_channels or self.notes:
            return "warning"
        return "clean"

    def describe(self) -> str:
        """One sentence for the preview."""
        if self.refusal:
            return f"Cannot copy: {self.refusal}"
        if not self.present:
            return "The source has none to copy."
        parts = []
        if self.missing_wells:
            parts.append(f"names {len(self.missing_wells)} well(s) this dataset "
                         f"does not have ({_short(self.missing_wells)})")
        if self.missing_channels:
            parts.append(f"uses channel(s) this dataset does not carry "
                         f"({_short(self.missing_channels)})")
        parts.extend(self.notes)
        if self.replaces:
            parts.append("replaces what this dataset has now")
        return ("Copies cleanly." if not parts
                else "Copies, but " + "; ".join(parts) + ".")


def _short(names, limit: int = 4) -> str:
    names = list(names)
    head = ", ".join(names[:limit])
    return head + (f" and {len(names) - limit} more" if len(names) > limit else "")


@dataclass(frozen=True)
class DatasetBlocks:
    """What a dataset holds, as read from disk.

    ``blocks`` maps ``(file, key)`` to the value, ``None`` when absent.
    ``refused`` maps a file name to why it could not be read; every block in a
    refused file is unknown rather than absent.
    """

    label: str
    #: The folder the blocks were read from, as given.
    root: str = ""
    blocks: dict = field(default_factory=dict)
    channels: frozenset[str] = frozenset()
    wells: frozenset[str] = frozenset()
    refused: dict = field(default_factory=dict)


def _strings(value):
    """Every string in *value*, including dict keys, depth-first."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str):
                yield k
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def wells_named(value) -> set[str]:
    out = set()
    for text in _strings(value):
        label = canonical_well_label(text)
        if label:
            out.add(label)
    return out


def channels_named(value, channels) -> set[str]:
    """Which of *channels* the strings in *value* name (case-insensitive)."""
    lowered = {c.lower(): c for c in channels if c}
    out = set()
    for text in _strings(value):
        t = text.lower()
        for low, original in lowered.items():
            if t == low or t.startswith(low + "_"):
                out.add(original)
    return out


def check_fit(source: DatasetBlocks, target: DatasetBlocks,
              keys=None) -> list[ScopeFit]:
    """The fit of every scope in *keys* (default: all), in roster order."""
    wanted = set(keys) if keys is not None else None
    target_channels = {c.lower() for c in target.channels}
    fits = []
    for scope in RECIPE_SCOPES:
        if wanted is not None and scope.key not in wanted:
            continue
        refusal = "; ".join(
            f"{name} in the {side} could not be read ({why})"
            for side, ds in (("source", source), ("target", target))
            for name, why in sorted(ds.refused.items())
            if any(name == f for f, _k in scope.blocks))
        values = [source.blocks.get(b) for b in scope.blocks]
        present = any(v not in (None, [], {}) for v in values)
        if refusal or not present:
            fits.append(ScopeFit(scope.key, scope.title, present, refusal=refusal))
            continue
        wells = set()
        chans = set()
        for v in values:
            wells |= wells_named(v)
            chans |= channels_named(v, source.channels)
        missing_wells = tuple(sorted(wells - set(target.wells)))
        missing_channels = tuple(sorted(c for c in chans
                                        if c.lower() not in target_channels))
        replaces = any(
            target.blocks.get(b) not in (None, [], {})
            and block_digest(target.blocks.get(b)) != block_digest(source.blocks.get(b))
            for b in scope.blocks)
        notes = ()
        if scope.key == "saved_plots":
            n = len(source.blocks.get((PERSISTENCE, "saved_plots")) or [])
            notes = (f"its {n} plot(s) arrive undrawn — each is drawn from this "
                     f"dataset's data when you restore it",)
            # Plots are added beside the target's own, never in place of them.
            replaces = False
        fits.append(ScopeFit(scope.key, scope.title, True,
                             missing_wells=missing_wells,
                             missing_channels=missing_channels,
                             replaces=replaces, notes=notes))
    return fits


#: What a recipe file says it is. A file without it is not a recipe file.
RECIPE_FILE_KIND = "allwell-recipe"
#: Bumped when the document's shape changes incompatibly; a reader meeting a
#: higher number refuses the file rather than guessing at it.
RECIPE_FILE_SCHEMA = 1
#: The suffix the save dialog offers and the open dialog filters on.
RECIPE_FILE_SUFFIX = ".allwell-recipe.json"


def recipe_document(source: DatasetBlocks, *, app_version: str | None = None) -> dict:
    """*source*'s blocks as a recipe file's document.

    Only blocks the source holds are written — an absent block says nothing, and
    writing it as ``null`` would read back the same as absent anyway. The source
    channels go with them, because :func:`check_fit` needs them to recognise a
    channel name inside a block.
    """
    return {
        "kind": RECIPE_FILE_KIND,
        "schema_version": RECIPE_FILE_SCHEMA,
        "app_version": app_version,
        "label": source.label,
        "channels": sorted(source.channels),
        "blocks": [{"file": f, "key": k, "value": source.blocks[(f, k)]}
                   for scope in RECIPE_SCOPES for f, k in scope.blocks
                   if source.blocks.get((f, k)) is not None],
    }


def blocks_from_document(doc, *, path: str = "") -> DatasetBlocks:
    """A recipe file's document as the :class:`DatasetBlocks` a folder would give.

    Raises ``ValueError`` naming what is wrong: not a recipe file, a schema
    this build does not read, or a block naming a file or key no recipe scope
    owns — the last because applying an unknown block would write somewhere
    no reset, no fit check and no provenance domain knows about.
    """
    if not isinstance(doc, dict) or doc.get("kind") != RECIPE_FILE_KIND:
        raise ValueError("not an All-Well recipe file")
    version = doc.get("schema_version")
    if not isinstance(version, int) or version > RECIPE_FILE_SCHEMA:
        raise ValueError(f"written under recipe schema {version!r}, which this "
                         f"build does not read")
    known = {b for scope in RECIPE_SCOPES for b in scope.blocks}
    blocks: dict = dict.fromkeys(known)
    for entry in doc.get("blocks") or []:
        if not isinstance(entry, dict):
            raise ValueError("a block entry is not an object")
        where = (entry.get("file"), entry.get("key"))
        if where not in known:
            raise ValueError(f"names a block no recipe scope owns: {where[0]}::{where[1]}")
        blocks[where] = entry.get("value")
    label = str(doc.get("label") or "recipe file")
    return DatasetBlocks(label=label, root=path, blocks=blocks,
                         channels=frozenset(str(c) for c in doc.get("channels") or ()))
