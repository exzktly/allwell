"""Project the pipeline's per-field quality verdicts onto the row cache.

The pipeline measures six metrics per FOV-group and records them in
``pipeline_info.json``'s ``field_quality`` block
(:mod:`pipeline.field_quality`). :mod:`review.domain.field_quality_scores`
turns that block into one verdict per field. This module is what puts those
verdicts where the rest of the app can act on them: a ``Field_Pass`` column on
each cached well frame, 0 for a row whose field was flagged and 1 otherwise —
the same shape, the same chokepoint and the same fail-open contract as
``Curation``'s ``Included``, QC's ``QC_Pass`` and the crop's ``Crop_Pass``.

**The join is the hard part, and it is the reason this module exists rather
than a lambda at the registration site.** A verdict addresses a field by
``(well, fov, timepoint)``, and this app already documents that ``fov`` cannot
be trusted on every well: :mod:`review.domain.fov_integrity` exists because a
well whose filenames do not match the schema arrives with *every* row labelled
field 1, indistinguishable from a well that genuinely has one field. Joining a
per-field verdict onto such a well does one of two things, and both are wrong
in the way this repository cares most about:

* the well really has six fields pooled under one label, so one field's
  verdict — chosen by whichever row the join happened to match — is applied to
  all six; or
* nothing matches at all, and the well reads as fully included, which is
  indistinguishable from a well that was measured and found clean.

So ``dataset.fov_integrity`` is consulted per well and an untrusted well is
**refused**: no ``Field_Pass`` column is written for it. Downstream that reads
as all-pass, which is the same state a frame predating the feature is in and
the one the whole stack already handles honestly — and the refusal is named on
screen rather than left to be inferred, because a well silently exempted from
a quality filter and a well that passed it look identical in a plot.

The same refusal covers the whole plate when the block itself says it was only
partly measured: ``summarise`` declines there, and a column built from a
biased subset would be a filter nobody could account for.

Qt-free: this is the data layer. It imports the pipeline's block reader and
the domain's scorer and nothing above either.
"""

from __future__ import annotations

import logging

from review.data.cache_projectors import project_verdict_column

_logger = logging.getLogger("review")

#: The column this writes. Named like its three siblings (``QC_Pass``,
#: ``Crop_Pass``) because it is the same kind of thing: a per-row 0/1 that the
#: inclusion chokepoint ANDs with the others.
FIELD_PASS_COLUMN = "Field_Pass"


def exclusion_is_on(ctx) -> bool:
    """Whether the user has asked for flagged fields to be excluded.

    Read straight off ``ctx.state.qc.params`` rather than through the QC
    controller's accessor, because this is the data layer and
    ``tests/test_layering.py`` forbids ``data/`` importing ``features/``. The
    accessor's job is lazy seeding from ``pipeline_info``; every value it can
    seed has this field ``False``, since nothing in a run's own sidecar asks
    for an exclusion the user has not chosen — so an unseeded ``None`` and a
    freshly seeded value give the same answer here, and reading the state
    directly cannot disagree with the accessor about it.
    """
    qc = getattr(ctx.state, "qc", None)
    params = getattr(qc, "params", None)
    return bool(getattr(params, "exclude_flagged_fields", False))


def field_quality_block(ctx) -> dict | None:
    """The ``field_quality`` block for the loaded dataset, or ``None``."""
    from pipeline.field_quality import FIELD_QUALITY_BLOCK

    info = getattr(ctx.state.dataset, "pipeline_info", None) or {}
    block = info.get(FIELD_QUALITY_BLOCK)
    return block if isinstance(block, dict) else None


def flagged_fields(ctx) -> tuple[set[tuple], str]:
    """``(flagged keys, refusal)`` for the loaded dataset.

    A key is ``(well, fov, timepoint)`` as strings, which is what the frames
    carry. The refusal is empty when the plate could be scored; when it is not
    empty **nothing is flagged**, because a partial block scored plate-wide
    gives every field a robust z against a biased median and the number would
    be wrong with nothing saying so.
    """
    from pipeline.field_quality import records_for_scoring
    from review.domain.field_quality_scores import score_fields, summarise

    block = field_quality_block(ctx)
    if not block:
        return set(), ""
    rows = records_for_scoring(block)
    summary = summarise(rows)
    if summary.declined:
        return set(), summary.declined
    flagged = set()
    for verdict, row in zip(score_fields(rows), rows, strict=True):
        if verdict.flagged:
            flagged.add((str(row.get("well") or ""),
                         str(row.get("fov") or ""),
                         str(row.get("timepoint") or "")))
    return flagged, ""


def untrusted_wells(ctx) -> list[str]:
    """Wells whose FOV labels are provably wrong, sorted.

    These are the wells a per-field verdict cannot be joined onto. Read from
    ``dataset.fov_integrity``, which the loader fills per well as it builds
    each frame — this module does not re-derive it, because a second
    derivation is a second chance to disagree with the one the rest of the app
    reads.
    """
    integrity = getattr(ctx.state.dataset, "fov_integrity", None) or {}
    return sorted(w for w, verdict in integrity.items() if not verdict)


def field_quality_status_text(ctx) -> tuple[str, bool]:
    """``(sentence, is_a_problem)`` for the Cell Gating panel.

    Four states and four sentences, because collapsing any two of them is the
    substitution this subsystem exists to prevent:

    * **no block** — the dataset predates field quality. Not "0 fields
      flagged": a plate nobody measured and a plate measured and found clean
      must not read alike.
    * **partly measured** — the scorer's own refusal, quoted rather than
      paraphrased, so the panel and the log say the same thing.
    * **fields flagged** — how many, and what drove them, with the wells whose
      FOV labels are untrusted named separately: those are exempt from the
      exclusion however the toggle is set, and a well silently exempt from a
      filter looks exactly like a well that passed it.
    * **nothing flagged** — said plainly, because it is a real result.

    Returns the text plus whether it should be styled as a problem. A pure
    function of ``ctx``, in the data layer, so the panel formats it and does
    not re-derive it (a second derivation is a second chance to describe
    something that did not happen).
    """
    from review.domain.field_quality_scores import score_fields, summarise
    from pipeline.field_quality import records_for_scoring

    block = field_quality_block(ctx)
    if not block:
        return ("This dataset was analysed before field quality was recorded, "
                "so there is nothing to exclude.", False)
    rows = records_for_scoring(block)
    summary = summarise(rows)
    if summary.declined:
        return (summary.declined, True)

    verdicts = score_fields(rows)
    flagged = [v for v in verdicts if v.flagged]
    untrusted = untrusted_wells(ctx)
    if not flagged:
        base = (f"All {len(verdicts):,} measured field(s) look like the rest "
                f"of the plate.")
    else:
        drivers: dict[str, int] = {}
        for v in flagged:
            drivers[v.driver.metric] = drivers.get(v.driver.metric, 0) + 1
        named = ", ".join(f"{m} ({n})" for m, n in
                          sorted(drivers.items(), key=lambda kv: -kv[1]))
        base = (f"{len(flagged):,} of {len(verdicts):,} field(s) flagged — "
                f"{named}.")
    if untrusted:
        shown = ", ".join(untrusted[:4]) + ("…" if len(untrusted) > 4 else "")
        # Said whether or not anything was flagged, and said as a problem:
        # these wells cannot be filtered at all, and the reason is a defect in
        # their filenames rather than anything about their images.
        return (f"{base} {len(untrusted)} well(s) cannot be filtered because "
                f"their FOV labels do not identify their fields ({shown}) — "
                f"every row there is kept whatever this is set to.", True)
    return (base, bool(flagged))


def flagged_by_well(ctx) -> dict[str, str]:
    """Well → one sentence naming what the run flagged there.

    The plate map's marker and its tooltip both come from this, so a glyph can
    never appear without an explanation and the two can never disagree about
    which wells carry one. Empty when the plate could not be scored, for the
    same reason :func:`flagged_fields` flags nothing there: a refusal is not a
    finding, and a marker is a finding.
    """
    from pipeline.field_quality import records_for_scoring
    from review.domain.field_quality_scores import score_fields, summarise

    block = field_quality_block(ctx)
    if not block:
        return {}
    rows = records_for_scoring(block)
    if summarise(rows).declined:
        return {}
    per_well: dict[str, list[str]] = {}
    for verdict, row in zip(score_fields(rows), rows, strict=True):
        if verdict.flagged:
            per_well.setdefault(str(row.get("well") or ""), []).append(
                verdict.describe())
    out: dict[str, str] = {}
    for well, reasons in per_well.items():
        if not well:
            continue
        head = f"{len(reasons)} field(s) flagged"
        # The worst one, named. Listing all of them turns a tooltip into a
        # paragraph, and naming none of them leaves the user with a count and
        # no idea which control to reach for.
        out[well] = f"{head} — {reasons[0]}" if reasons[0] else head
    return out


def _field_pass(df, well: str, flagged: set[tuple]):
    """The per-row 0/1 verdict for one well's frame."""
    import pandas as pd

    from domain.text_columns import text_column, timepoint_text

    if not flagged:
        return pd.Series(1, index=df.index)
    fovs = text_column(df, "fov")
    tps = timepoint_text(df)
    keys = [(str(well), f, t) for f, t in zip(fovs, tps, strict=True)]
    return pd.Series([0 if k in flagged else 1 for k in keys], index=df.index)


def apply_field_quality_to_cache(ctx, label: str | None = None) -> list[str]:
    """Write ``Field_Pass`` onto cached well frame(s) from the run's own block.

    With *label* given, only that well; otherwise every cached well. Returns
    the labels whose verdict could not be computed, exactly as its three
    siblings do — ``project_verdict_column`` drops the column from those
    frames, which reads downstream as "no field-quality filter applies here".

    Three cases write no column at all, each for its own reason and each
    reported rather than assumed:

    * **no block** — the dataset was analysed before field quality was
      recorded. Nothing to project.
    * **the plate was only partly measured** — the scorer declines, so there
      is nothing to project either, and a column built anyway would be a
      filter derived from a biased subset.
    * **this well's FOV labels are untrusted** — the join would apply one
      field's verdict to every row, or match nothing. See the module
      docstring.
    """
    if not exclusion_is_on(ctx):
        # The user has not asked for it. Nothing is written, so the chokepoint
        # sees no ``Field_Pass`` at all and every row counts — which is the
        # point: this subsystem reports by default and filters only on
        # request. The Cell Gating panel still shows what was flagged, because
        # it reads the verdicts rather than the column.
        return []
    flagged, refusal = flagged_fields(ctx)
    if refusal or not flagged:
        # Nothing to write. Deliberately not an all-pass column: an explicit
        # 1 everywhere would say the plate was measured and found clean, which
        # is exactly what a refusal or an absent block does not say. Leaving
        # the column off puts the frame in the state every consumer already
        # treats as all-pass, and the refusal is named on screen separately.
        return []
    skip = set(untrusted_wells(ctx))
    cache = ctx.state.dataset.cache
    labels = [label] if label is not None else list(cache.keys())
    failed: list[str] = []
    for lbl in labels:
        if lbl in skip:
            continue
        failed.extend(project_verdict_column(
            ctx, lbl,
            column=FIELD_PASS_COLUMN,
            verdict=lambda df, well: _field_pass(df, well, flagged),
            layer="Field quality",
        ))
    return failed
