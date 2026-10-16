"""Fields the user excluded by hand, with a reason — the ``Excluded_Field_Pass`` layer.

Three inclusion layers already speak about whole fields or parts of them, and
none of them is a decision a person made about one field: the run's own
field-quality verdict (``Field_Pass``) is all-or-nothing across every flagged
field, the focus cut (``Focus_Pass``) is a threshold, and manual curation
(``Included``) is per cell. QC triage (``features/home/triage``) needs the
missing one: "exclude field 3 of B4, it is out of focus", recorded with the
reason, reversible, and joined at the inclusion chokepoint like every other
layer (:func:`review.data.data_loading.df_included_mask`).

The live set is ``ctx.state.dataset.field_exclusions``: a map from
``(well, fov, timepoint)`` — the strings a well's frame carries, the same key
:mod:`review.data.field_quality_cache` joins on — to ``{"reason", "at"}``. It is
persisted by :mod:`review.persistence.field_exclusions` and cleared with the
rest of manual curation by the *Curation* reset scope.

**A well whose FOV labels are untrusted is not filtered.** Exactly the refusal
the field-quality layer makes, for exactly its reason: when a well's FOV column
does not identify its fields, a per-field key matches the wrong rows or none,
and an exclusion that silently removed a different field than the one named is
worse than one that removed nothing. The triage table says so beside the row;
the projector simply does not write the column for that well.

**No exclusions writes no column**, rather than an all-pass one, for the reason
``cache_projectors.drop_verdict_column`` gives: an explicit 1 everywhere asserts
a layer ran and found nothing.
"""

from __future__ import annotations

from review.data.cache_projectors import drop_verdict_column, project_verdict_column

#: The verdict column this layer owns. 1 = the row's field was not excluded.
EXCLUDED_FIELD_COLUMN = "Excluded_Field_Pass"


def field_key(well, fov, timepoint) -> tuple[str, str, str]:
    """The key a field is excluded under, as strings."""
    return (str(well or ""), str(fov or ""), str(timepoint or ""))


def excluded_fields(ctx) -> dict[tuple[str, str, str], dict]:
    """The live exclusion map (never ``None``)."""
    return getattr(ctx.state.dataset, "field_exclusions", None) or {}


def _verdict(df, well: str, keys: set[tuple[str, str, str]]):
    import pandas as pd

    from domain.text_columns import text_column, timepoint_text

    fovs = text_column(df, "fov")
    tps = timepoint_text(df)
    return pd.Series([0 if (str(well), f, t) in keys else 1
                      for f, t in zip(fovs, tps, strict=True)], index=df.index)


def field_exclusions_input_columns(ctx, label: str | None = None) -> tuple[str, ...]:
    """Nothing beyond the identity columns: the verdict joins on field and timepoint."""
    return ()


def apply_field_exclusions_to_cache(ctx, label: str | None = None) -> list[str]:
    """Write ``Excluded_Field_Pass`` onto cached frame(s); return wells that failed.

    Wells with no exclusion, and wells whose FOV labels are untrusted, carry no
    column — see the module docstring.
    """
    from review.data.field_quality_cache import untrusted_wells

    exclusions = excluded_fields(ctx)
    by_well: dict[str, set[tuple[str, str, str]]] = {}
    for key in exclusions:
        by_well.setdefault(key[0], set()).add(key)
    skip = set(untrusted_wells(ctx))
    cache = ctx.state.dataset.cache
    labels = [label] if label is not None else list(cache.keys())
    failed: list[str] = []
    for lbl in labels:
        keys = by_well.get(str(lbl))
        if not keys or lbl in skip:
            drop_verdict_column(ctx, lbl, column=EXCLUDED_FIELD_COLUMN)
            continue
        failed.extend(project_verdict_column(
            ctx, lbl, column=EXCLUDED_FIELD_COLUMN,
            verdict=lambda df, well, _k=keys: _verdict(df, well, _k),
            layer="Field exclusions"))
    return failed
