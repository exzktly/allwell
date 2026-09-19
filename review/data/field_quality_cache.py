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

from review.data.cache_projectors import (
    drop_verdict_column, project_verdict_column,
)

_logger = logging.getLogger("review")

#: The column this writes. Named like its three siblings (``QC_Pass``,
#: ``Crop_Pass``) because it is the same kind of thing: a per-row 0/1 that the
#: inclusion chokepoint ANDs with the others.
FIELD_PASS_COLUMN = "Field_Pass"


#: How each metric is said to a reader. The tooltip already speaks this way;
#: the panel used to print the pipeline's own variable names.
METRIC_WORDS = {
    "focus": "out of focus",
    "sat_frac": "clipped at the sensor maximum",
    "dyn_range": "low contrast",
    "nuc_count": "an unlike nucleus count",
    "nuc_area_median": "unlike nuclear sizes",
    "bg_level": "an unlike background level",
}


def _n_of(n: int, total: int) -> str:
    """``"1 of 18 fields"`` — and ``"field"`` where there really is one.

    The app writes "field(s)" in several places and it is consistently poor.
    The domain's own ``PlateQualitySummary.describe`` already writes the
    better form, so this keeps the two spellings of one sentence together.
    """
    noun = "field" if total == 1 else "fields"
    return f"{n:,} of {total:,} {noun}"


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


#: What the panel may do with a sentence. Returned rather than inferred from
#: the sentence's emptiness: every branch below returns text, so an
#: emptiness test could never choose the muted register and "nothing was
#: measured" was painted in the same ink as a finding.
REGISTER_MUTED = "muted"      # nothing to say about this dataset
REGISTER_PLAIN = "plain"      # a result
REGISTER_PROBLEM = "problem"  # the software could not do what was asked


def field_quality_status(ctx) -> tuple[str, str]:
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

    Returns the text plus one of :data:`REGISTER_MUTED` /
    :data:`REGISTER_PLAIN` / :data:`REGISTER_PROBLEM`. Only the middle two
    states are problems: a **finding** is a result, not a failure, and
    dressing "1 of 18 fields flagged" in the same amber as "this plate cannot
    be scored" collapses the distinction the registers exist for. The "no
    block" state is muted, because a dataset that predates the feature is not
    making a statement about itself. A pure
    function of ``ctx``, in the data layer, so the panel formats it and does
    not re-derive it (a second derivation is a second chance to describe
    something that did not happen).
    """
    from review.domain.field_quality_scores import score_fields, summarise
    from pipeline.field_quality import records_for_scoring

    block = field_quality_block(ctx)
    if not block:
        return ("This dataset was analysed before field quality was recorded, "
                "so there is nothing to exclude.", REGISTER_MUTED)
    rows = records_for_scoring(block)
    summary = summarise(rows)
    if summary.declined:
        return (summary.declined, REGISTER_PROBLEM)

    verdicts = score_fields(rows)
    flagged = [v for v in verdicts if v.flagged]
    untrusted = untrusted_wells(ctx)
    if not flagged:
        base = (f"All {len(verdicts):,} measured fields look like the rest "
                f"of the plate.")
    else:
        drivers: dict[str, int] = {}
        for v in flagged:
            drivers[v.driver.metric] = drivers.get(v.driver.metric, 0) + 1
        # Named the way the tooltip names them rather than by the pipeline's
        # variable names. "1 of 18 field(s) flagged — focus (1)" put the
        # number 1 in one sentence twice with two meanings, and the second
        # reads far too easily as a focus score of 1.
        if len(drivers) == 1:
            named = f"all {METRIC_WORDS.get(next(iter(drivers)), '')}"
        else:
            named = ", ".join(f"{METRIC_WORDS.get(m, m)} ({n})" for m, n in
                              sorted(drivers.items(), key=lambda kv: -kv[1]))
        # And the wells, because the only other surface that shows this is a
        # plate map on a different tab: a count with no address leaves the
        # reader hunting for which well to hover.
        hit = sorted({str(row.get("well") or "")
                      for v, row in zip(verdicts, rows, strict=True)
                      if v.flagged and row.get("well")})
        where = ", ".join(hit[:4]) + ("…" if len(hit) > 4 else "")
        base = (f"{_n_of(len(flagged), len(verdicts))} flagged, {named}"
                f"{f' — in {where}' if where else ''}.")
    if untrusted:
        shown = ", ".join(untrusted[:4]) + ("…" if len(untrusted) > 4 else "")
        # Said whether or not anything was flagged, and said as a problem:
        # these wells cannot be filtered at all, and the reason is a defect in
        # their filenames rather than anything about their images.
        return (f"{base} {len(untrusted)} well(s) cannot be filtered because "
                f"their FOV labels do not identify their fields ({shown}) — "
                f"every row there is kept whatever this is set to.",
                REGISTER_PROBLEM)
    # A finding is NOT a problem. The registers exist so a reader can tell a
    # refusal from a result at a glance; returning the problem register here
    # made "1 of 18 fields flagged" wear the same amber band as "this plate
    # cannot be scored", which is the conflation they are for. Amber is
    # reserved for the two states where the software could not do the thing
    # asked of it: a plate it may not score, and wells it may not filter.
    return (base, REGISTER_PLAIN)


def flagged_detail_by_well(ctx) -> dict[str, tuple[int, str]]:
    """Well → ``(how many fields were flagged, the sentence saying which)``.

    Both halves from one walk, because the composite on Home needs the count
    and the plate's tooltip needs the sentence, and reading the count back out
    of its own prose would be a second derivation of a fact this function
    already has — one that could then disagree with the marker beside it.
    :func:`flagged_by_well` is the sentence-only view of this, so the two
    cannot describe different wells.
    """
    from pipeline.field_quality import records_for_scoring
    from review.domain.field_quality_scores import score_fields, summarise

    block = field_quality_block(ctx)
    if not block:
        return {}
    rows = records_for_scoring(block)
    if summarise(rows).declined:
        return {}
    per_well: dict[str, list[tuple[str, str]]] = {}
    for verdict, row in zip(score_fields(rows), rows, strict=True):
        if verdict.flagged:
            per_well.setdefault(str(row.get("well") or ""), []).append(
                (str(row.get("fov") or ""), verdict.describe()))
    out: dict[str, tuple[int, str]] = {}
    for well, hits in per_well.items():
        if not well:
            continue
        noun = "field" if len(hits) == 1 else "fields"
        head = f"{len(hits)} {noun} flagged"
        fov, why = hits[0]
        # The worst one, **named and addressed**. Listing all of them turns a
        # tooltip into a paragraph, and naming none leaves the user with a
        # count and nothing to look at — which is what the first version did:
        # it gave the metric and the z but not the field, so neither this
        # tooltip nor the Cell Gating sentence yielded the (well, field)
        # address the Image Browser needs, and a flagged well was a dead end.
        where = f"field {fov}" if fov else "an unnamed field"
        out[well] = (len(hits),
                     f"{head} — worst: {where}, {why}" if why else head)
    return out


def flagged_by_well(ctx) -> dict[str, str]:
    """Well → one sentence naming what the run flagged there.

    The sentence-only view of :func:`flagged_detail_by_well`. The plate map's
    marker and its tooltip both come from this, so a glyph can never appear
    without an explanation and the two can never disagree about which wells
    carry one. Empty when the plate could not be scored, for the same reason
    :func:`flagged_fields` flags nothing there: a refusal is not a finding,
    and a marker is a finding.
    """
    return {w: note for w, (_n, note) in flagged_detail_by_well(ctx).items()}


def exclusion_can_apply(ctx) -> bool:
    """Whether ticking the box would actually exclude anything.

    **Not "is there a block".** That was the first predicate here and it is
    strictly weaker: a plate only partly measured has a block and is refused,
    so the box stayed live, the user ticked it, it stayed ticked, and nothing
    was excluded — every number in the app then read by someone who believed a
    quality filter was on. Degraded output indistinguishable from good output,
    arriving through the control rather than through the data.

    So the question is the one the projector will actually ask: are there
    flagged fields, on a plate that may be scored at all.
    """
    flagged, refusal = flagged_fields(ctx)
    return bool(flagged) and not refusal


def exclusion_effect_text(ctx) -> str:
    """What ticking the box is doing **right now**, or ``""``.

    The finding sentence deliberately does not change when the box is ticked —
    the flags are reported either way — which left the panel with no statement
    of effect at all: a user ticks a control that says it will drop rows, and
    every word on the panel stays the same. The debris gate one control above
    carries a live "flagged N / M" consequence readout; this is its
    counterpart, and it is the only line here that depends on the checkbox.
    """
    flagged, refusal = flagged_fields(ctx)
    if refusal or not flagged:
        return ""
    noun = "field" if len(flagged) == 1 else "fields"
    if not exclusion_is_on(ctx):
        return f"Currently kept — {len(flagged):,} {noun} would be excluded."
    return f"Currently excluding {len(flagged):,} {noun}."


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
        # The user has not asked for it, so the chokepoint must see no
        # ``Field_Pass`` at all and every row must count — this subsystem
        # reports by default and filters only on request. The Cell Gating
        # panel still shows what was flagged, because it reads the verdicts
        # rather than the column.
        #
        # **Removed, not merely unwritten.** Returning early leaves the
        # previous setting's verdict on any frame that already carried one, so
        # unticking the box left its rows excluded while the panel said
        # "currently kept" over them.
        drop_verdict_column(ctx, label, column=FIELD_PASS_COLUMN)
        return []
    flagged, refusal = flagged_fields(ctx)
    if refusal or not flagged:
        drop_verdict_column(ctx, label, column=FIELD_PASS_COLUMN)
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
