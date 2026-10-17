"""Which fields of view count — the ``FOV_Pass`` inclusion layer.

The Cell Gating tab lets the user restrict every calculation to a chosen set of
FOV numbers: "use fields 1–4 of every well, ignore the rest". The choice lives
on the cell-gating slice as ``ctx.state.cell_gating.fov_selection`` — a set of
FOV labels, or ``None`` for "every field" — is saved in the ``cell_gating``
block of ``pipeline_info.json`` beside the gates, and is joined at the
inclusion chokepoint (:func:`review.data.data_loading.df_included_mask`) like
every other layer, so every plot, statistic and export honours it without any
of them knowing it exists.

**Matching is by FOV number, not by spelling.** The same field reaches the app
as ``"1"`` from ``pipeline_info.json``, as ``1.0`` from a CSV column that pandas
read as float, and as ``"f1"`` from a prefixed schema. :func:`fov_match_key`
reduces each to the number :func:`util.fov.parse_fov` reads from it, and keeps
an unparseable label as its own text, so two distinct opaque labels never merge.

**A well whose FOV labels are untrusted is not filtered.** The refusal
:mod:`review.data.field_exclusions` and :mod:`review.data.field_quality_cache`
make, for their reason: when a well's FOV column does not identify its fields,
"field 3" names arbitrary rows, and a selection that silently kept different
cells than the ones asked for is worse than one that kept them all. The Cell
Gating tab names those wells beside the picker (:func:`fov_selection_status`),
so the shortfall is on screen rather than only in a log.

**No selection writes no column**, rather than an all-pass one, for the reason
``cache_projectors.drop_verdict_column`` gives: an explicit 1 everywhere
asserts a layer ran and found nothing.
"""

from __future__ import annotations

from collections.abc import Iterable

from review.data.cache_projectors import drop_verdict_column, project_verdict_column
from util.fov import parse_fov, sort_key as fov_sort_key

#: The verdict column this layer owns. 1 = the row's field is in the selection.
FOV_PASS_COLUMN = "FOV_Pass"


def fov_match_key(value) -> object:
    """The key two FOV labels are compared under.

    The parsed number when the label carries one (``"1"``, ``"01"``, ``1.0``,
    ``"f1"`` all give ``1.0``), else the stripped text itself.
    """
    text = "" if value is None else str(value).strip()
    number = parse_fov(text)
    return text if number is None else number


def selected_fovs(ctx) -> set[str] | None:
    """The live selection, or ``None`` when every field counts."""
    cell_gating = getattr(ctx.state, "cell_gating", None)
    selection = getattr(cell_gating, "fov_selection", None)
    return None if selection is None else set(selection)


def normalise_selection(selection: Iterable | None, available: Iterable) -> set[str] | None:
    """*selection* as stored state: ``None`` when it keeps every available field.

    A selection holding every field the dataset has is the same population as no
    selection at all, and storing it as ``None`` keeps it that way when the
    dataset later gains a field — "all" stays all rather than freezing into the
    list that happened to exist when it was ticked.
    """
    if selection is None:
        return None
    chosen = {str(f) for f in selection}
    avail_keys = {fov_match_key(f) for f in available}
    if avail_keys and avail_keys <= {fov_match_key(f) for f in chosen}:
        return None
    return chosen


def fov_selection_input_columns(ctx, label: str | None = None) -> tuple[str, ...]:
    """Nothing beyond the identity columns: the verdict reads ``fov`` alone."""
    return ()


def _verdict(df, keys: set):
    import pandas as pd

    from domain.text_columns import fov_text

    fovs = fov_text(df)
    return pd.Series([1 if fov_match_key(f) in keys else 0 for f in fovs],
                     index=df.index)


def apply_fov_selection_to_cache(ctx, label: str | None = None) -> list[str]:
    """Write ``FOV_Pass`` onto cached frame(s); return wells that failed.

    With no selection every frame loses the column; wells whose FOV labels are
    untrusted carry none either — see the module docstring.
    """
    from review.data.field_quality_cache import untrusted_wells

    selection = selected_fovs(ctx)
    cache = getattr(getattr(ctx.state, "dataset", None), "cache", None) or {}
    labels = [label] if label is not None else list(cache.keys())
    if selection is None:
        if not cache:
            return []
        for lbl in labels:
            drop_verdict_column(ctx, lbl, column=FOV_PASS_COLUMN)
        return []
    keys = {fov_match_key(f) for f in selection}
    skip = set(untrusted_wells(ctx))
    failed: list[str] = []
    for lbl in labels:
        if lbl in skip:
            drop_verdict_column(ctx, lbl, column=FOV_PASS_COLUMN)
            continue
        failed.extend(project_verdict_column(
            ctx, lbl, column=FOV_PASS_COLUMN,
            verdict=lambda df, _well, _k=keys: _verdict(df, _k),
            layer="FOV selection"))
    return failed


def format_fov_list(fovs: Iterable) -> str:
    """``"1–3, 5, 7"``: numerically sorted, consecutive integers collapsed."""
    ordered = sorted({str(f) for f in fovs}, key=fov_sort_key)
    parts: list[str] = []
    run: list[tuple[str, int]] = []

    def _flush() -> None:
        if not run:
            return
        if len(run) >= 3:
            parts.append(f"{run[0][0]}–{run[-1][0]}")
        else:
            parts.extend(tok for tok, _n in run)
        run.clear()

    for tok in ordered:
        number = parse_fov(tok)
        as_int = int(number) if number is not None and float(number).is_integer() else None
        if as_int is None:
            _flush()
            parts.append(tok)
            continue
        if run and as_int != run[-1][1] + 1:
            _flush()
        run.append((tok, as_int))
    _flush()
    return ", ".join(parts)


def fov_button_text(selection: set[str] | None) -> str:
    """The picker button's label: what the selection keeps, in a few words."""
    if selection is None:
        return "All FOVs"
    if not selection:
        return "No FOVs"
    return f"FOVs {format_fov_list(selection)}"


def fov_selection_status(selection: set[str] | None, available: list[str],
                         untrusted: list[str]) -> str:
    """The muted line under the picker, or ``""`` when every field counts.

    Says how many of the dataset's fields are kept, names a selected field the
    dataset does not have (a selection carried over from a saved plot or an
    earlier run), and names the wells the selection could not be applied to.
    """
    if selection is None:
        return ""
    avail_keys = {fov_match_key(f) for f in available}
    chosen_keys = {fov_match_key(s) for s in selection}
    kept = [f for f in available if fov_match_key(f) in chosen_keys]
    parts = []
    if available:
        parts.append(f"Using {len(kept)} of {len(available)} fields of view in "
                     f"every well; cells in the others are excluded.")
    missing = [s for s in selection if fov_match_key(s) not in avail_keys]
    if available and missing:
        parts.append(f"Not in this dataset: {format_fov_list(missing)}.")
    if untrusted:
        shown = ", ".join(untrusted[:6]) + (" …" if len(untrusted) > 6 else "")
        parts.append(f"Not applied to {len(untrusted)} well(s) whose FOV labels "
                     f"cannot be trusted ({shown}) — every field there still counts.")
    return " ".join(parts)
