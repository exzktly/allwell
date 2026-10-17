"""Exclude the cells whose own outline read as soft, and say what that did.

The pipeline writes one number per cell — ``qc_edge_focus``, the sharpness of
the intensity transition at that nucleus's own boundary, divided by the cell's
own contrast and corrected for the background's noise floor. This module is
what turns that column into a ``Focus_Pass`` verdict the inclusion chokepoint
can AND with its four siblings.

**Why this is per cell when ``Field_Pass`` is per field.** They are two
different questions and neither subsumes the other. The run's field verdict is
a six-metric robust z against the rest of the plate: it sees a field that is
clipped, low-contrast or unlike its neighbours, and it removes all of that
field or none of it. This one is an absolute cut on each cell, so it removes
the soft *part* of a field — a focus gradient across one FOV, a coverslip tilt
— which is not a whole-field fact and, as the user who asked for it put it, is
rarely a reason to throw the whole frame away. A field whose median is fine can
still have a soft third, and no whole-field verdict can express that.

**Why the cut is absolute.** A score relative to its own frame cannot see a
frame that is uniformly soft, because every cell in it deviates from its
neighbours by nothing — measured, the relative median is exactly 1.000 at three
very different focus states. The estimator is a transition *width* in pixels,
which is what makes one number able to serve: gain and pedestal do not move it
at all, and brightness, nucleus size and crowding move it by under 5 %. Two
things do move it, and a user comparing plates should know both — a clipped
nucleus reads sharper (up to 1.45x when the ceiling cuts well into the
interior), and chromatin texture reaches 1.14x. The number itself comes from
the run's own sidecar or from the user, never from this module.

**Three things this refuses to do, each because the alternative is a silent
substitution.**

* A cell with **no** focus value passes. Undefined is not soft. Severe defocus
  degrades segmentation, so the column is missing exactly where defocus is
  worst — but so is a nucleus whose boundary profile cannot be read at all,
  and treating the two alike would quietly delete cells on every plate. The
  count of them is reported instead, and the *field* metric is what covers the
  case the per-cell one cannot see.
* A dataset with **no column at all** gets no ``Focus_Pass`` column, rather
  than an all-pass one. An explicit 1 everywhere would say the plate was
  measured and found sharp, which is not what a plate that predates the column
  says about itself.
* The gate needs **both** the switch and a cut. A cut with the switch off is a
  number the user has recorded; the switch on with no cut is a request nothing
  here can honour, because no threshold can be guessed —
  ``_Docs/Markdowns/INFERENCE_SUBSYSTEMS_TRACKER.md`` §D.2d has the measurement
  that refused to try.

Qt-free: this is the data layer. It imports the pipeline's column name and the
domain's parameters and nothing above either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from review.data.data_loading import get_rows
from review.data.cache_projectors import (
    drop_verdict_column, project_verdict_column,
)

if TYPE_CHECKING:  # annotations only — this module must not import pandas at
    import pandas as pd  # noqa: F401   module scope (the data layer is light)

#: The column this writes. Named like its four siblings (``QC_Pass``,
#: ``Crop_Pass``, ``Field_Pass``) because it is the same kind of thing: a
#: per-row 0/1 that the inclusion chokepoint ANDs with the others.
FOCUS_PASS_COLUMN = "Focus_Pass"


def focus_params(ctx):
    """The loaded dataset's :class:`~review.domain.qc_params.QCParams`.

    Read straight off ``ctx.state.qc.params`` rather than through the QC
    controller, because this is the data layer and ``tests/test_layering.py``
    forbids ``data/`` importing ``features/``.
    """
    qc = getattr(ctx.state, "qc", None)
    return getattr(qc, "params", None)


def gate_is_on(ctx) -> bool:
    """Whether the focus cut is switched on **and** has a number to apply."""
    params = focus_params(ctx)
    return bool(params is not None and params.focus_gate_is_active)


def focus_values(df) -> "pd.Series | None":
    """The frame's focus column as floats, or ``None`` when it has none.

    ``None`` and an all-NaN column are different answers and both occur: the
    first is a dataset analysed before the column existed, the second is a
    dataset that measured and could not read a single outline. Only the first
    returns ``None`` here.
    """
    from pipeline.field_quality_record import FOCUS_COLUMN

    if df is None or len(df) == 0 or FOCUS_COLUMN not in df.columns:
        return None
    import pandas as pd

    return pd.to_numeric(df[FOCUS_COLUMN], errors="coerce")


def compute_focus_pass(df, params) -> "pd.Series":
    """``1`` where a cell is usable, ``0`` where it read as soft.

    Pure function of the frame and *params*, so a test can ask it directly.
    Every row passes when the gate is off, when the column is absent, and
    wherever the value is missing — see the module docstring for why the last
    of those is not a defect.
    """
    import pandas as pd

    passed = pd.Series(1, index=df.index, dtype=int)
    if params is None or not params.focus_gate_is_active:
        return passed
    vals = focus_values(df)
    if vals is None:
        return passed
    # ``vals < cut`` is False at NaN, which is the fail-open this needs — but
    # it is written as an explicit notna() because relying on NaN's comparison
    # semantics for a correctness-critical default is how the opposite
    # behaviour arrives in a later refactor with nothing to notice it.
    cut = float(params.focus_min)
    soft = vals.notna() & (vals < cut)
    return passed.where(~soft, 0)


def candidate_rows(df):
    """The rows this gate's numbers are **about**: what the analysis would
    otherwise keep.

    Every count here used to walk every row of the frame, consulting none of
    the sibling verdicts. That is wrong in both directions and the second one
    matters more. ``edge_focus`` returns nothing for an outline it cannot
    read, and the fragments it cannot read are largely the same population
    ``qc_region_area`` exists to remove — debris StarDist shattered into many
    small cells. So a debris-heavy plate marked **every** well "needs review —
    N of M cells carry no focus value", about cells the user had already
    filtered out, and the marker meant least on exactly the plates that need
    it most. In the other direction, "currently excluding N cells" counted
    rows the focus layer marks rather than rows it removes from the analysis,
    overstating the effect by however much the other gates had already taken.

    Composed from the chokepoint itself
    (:func:`review.data.data_loading.df_included_mask`) minus ``Focus_Pass``,
    rather than from a second list of layers here: a surface that filtered its
    own evidence by its own verdict would report nothing to exclude the moment
    the gate was switched on.
    """
    from review.data.data_loading import df_included_mask

    if df is None or len(df) == 0:
        return df
    return df.loc[df_included_mask(df, except_layer=FOCUS_PASS_COLUMN)]


def focus_counts(df, params) -> tuple[int, int, int]:
    """``(soft, unmeasurable, total)`` for one frame.

    Counted over :func:`candidate_rows` — the cells the analysis would keep if
    this gate were off — not over every row in the frame.

    ``unmeasurable`` is counted and returned rather than folded into either
    other number, because the panel has to be able to say it out loud: a cell
    nobody could measure is being **kept**, and a reader who believes the gate
    removed everything soft would be wrong about exactly those rows.
    """
    if df is None or len(df) == 0:
        return (0, 0, 0)
    df = candidate_rows(df)
    if df is None or len(df) == 0:
        return (0, 0, 0)
    vals = focus_values(df)
    if vals is None:
        return (0, 0, int(len(df)))
    unmeasurable = int(vals.isna().sum())
    if params is None or params.focus_min is None:
        return (0, unmeasurable, int(len(df)))
    cut = float(params.focus_min)
    soft = int((vals.notna() & (vals < cut)).sum())
    return (soft, unmeasurable, int(len(df)))


def _every_well_narrow(ctx, kind: str, params, compute) -> dict:
    """``{label: compute(rows)}`` over every well, each read narrowly and remembered.

    The plate-wide passes here want one column of each well — the focus column
    — and the inclusion verdicts :func:`candidate_rows` composes. Read through
    ``plate_scan.every_well`` that was ``get_rows``: the whole frame, from disk
    for every well the bounded row cache does not hold. Home asks for the
    tallies on every paint, and a gating pass bumps ``rows_version`` (a
    dataset-wide counter) once per well, so after every gating change each well
    was read whole again — 8 full reads and 8.0 s on an 8-well plate of
    1.5M-cell wells (``REREAD_TRACKER.md``, E5 and R5).

    So each well goes through ``narrow_rows`` with the focus column as the
    caller's own: that read adds every verdict layer's declared inputs and runs
    the projectors, so ``candidate_rows`` sees the inclusion the whole frame
    would have, and the frame is not put in the row cache. A well already held
    is used as held. The memo is ``plate_scan``'s — the same key, in the
    statistics cache, carrying ``rows_version`` read before the compute — so it
    is invalidated exactly when that one is. ``tests/test_focus_tallies_read_narrowly.py``
    holds the answers to the whole frame's with every verdict layer on.
    """
    from pipeline.field_quality_record import FOCUS_COLUMN
    from review.data.narrow_rows import narrow_rows

    store = getattr(ctx.state, "stats_cache", None)
    version = getattr(ctx.state.dataset, "rows_version", None)
    if version is None:
        store = None  # a context too thin to say when its rows changed
    out = {}
    for label in list(ctx.state.dataset.well_paths):
        key = ("plate_scan", kind, label, params, version)
        if store is not None and key in store.cache:
            out[label] = store.cache[key]
            continue
        result = compute(narrow_rows(ctx, label, {FOCUS_COLUMN}))
        if store is not None:
            store.cache[key] = result
        out[label] = result
    return out


def plate_focus_counts(ctx) -> tuple[int, int, int]:
    """``(soft, unmeasurable, total)`` summed over every well of the plate.

    Every well, not every held one — the panel says "across the plate", and the
    row cache is bounded — read one at a time, narrowly, and remembered per
    well (:func:`_every_well_narrow`) until the rows, the verdicts or *params*
    change.

    Counted against the **current** cut whether or not the gate is switched
    on, because the panel reports what a cut would do before it is applied —
    a user choosing a threshold needs the consequence in front of them, not
    after the fact.
    """
    params = focus_params(ctx)
    soft = unmeasurable = total = 0
    for s_, u, t in _every_well_narrow(ctx, "focus_counts", params,
                                       lambda df: focus_counts(df, params)).values():
        soft += s_
        unmeasurable += u
        total += t
    return (soft, unmeasurable, total)


def dataset_has_focus_column(ctx) -> bool | None:
    """Whether the dataset carries the column — ``None`` when it cannot be told.

    Three answers, not two, and the third is the one that was missing. Every
    predicate here reads the **row cache**, and the cache is emptied on
    purpose by the Gating and Curation resets so frames reload their original
    verdicts. Between the clear and the next lazy load there are no wells to
    look at — which is not the same as looking and finding no column, and the
    panel said the second: "This dataset was analysed before per-cell focus
    was measured", about a dataset that had been, naming a remedy (re-run the
    plate) that was not needed.

    So an empty cache answers ``None``: *unknown*. The caller says nothing
    rather than saying something false, which is this module's whole contract
    applied to its own state rather than to the data.
    """
    from pipeline.field_quality_record import FOCUS_COLUMN

    # Every well's columns are in its summary, held or not; a dataset opened
    # without summaries (a test seeding frames) answers from the held frames.
    summaries = getattr(ctx.state.dataset, "summaries", None) or {}
    if summaries:
        return any(FOCUS_COLUMN in s.columns for s in summaries.values())
    cache = getattr(ctx.state.dataset, "cache", None) or {}
    frames = [df for df in cache.values() if df is not None and len(df)]
    if not frames:
        return None
    return any(FOCUS_COLUMN in getattr(df, "columns", ()) for df in frames)


def focus_input_columns(ctx, label: str | None = None) -> tuple[str, ...]:
    """The column :func:`apply_focus_gate_to_cache`'s verdict reads (``narrow_rows``)."""
    from pipeline.field_quality_record import FOCUS_COLUMN

    return (FOCUS_COLUMN,) if gate_is_on(ctx) else ()


def apply_focus_gate_to_cache(ctx, label: str | None = None) -> list[str]:
    """Write ``Focus_Pass`` onto cached well frame(s).

    With *label* given, only that well; otherwise every cached well. Returns
    the labels whose verdict could not be computed, exactly as its siblings do.

    Switched off, the column is **removed** rather than simply not written.
    Not writing it is right only for a frame that never had it; a frame that
    does would keep the previous setting's verdict and go on being filtered by
    a control the user has just unticked.
    """
    if not gate_is_on(ctx):
        drop_verdict_column(ctx, label, column=FOCUS_PASS_COLUMN)
        return []
    params = focus_params(ctx)
    cache = ctx.state.dataset.cache
    labels = [label] if label is not None else list(cache.keys())
    failed: list[str] = []
    for lbl in labels:
        failed.extend(project_verdict_column(
            ctx, lbl,
            column=FOCUS_PASS_COLUMN,
            verdict=lambda df, _well: compute_focus_pass(df, params),
            layer="Cell focus",
        ))
    return failed


#: The registers a sentence may be said in, mirroring
#: :mod:`review.data.field_quality_cache` so the two gates on one panel speak
#: the same language. Returned rather than inferred from the sentence, because
#: every branch returns text and an emptiness test could never choose between
#: them.
REGISTER_MUTED = "muted"      # nothing to say about this dataset
REGISTER_PLAIN = "plain"      # a result
REGISTER_PROBLEM = "problem"  # the software could not do what was asked


def _cells(n: int) -> str:
    return f"{n:,} cell" if n == 1 else f"{n:,} cells"


def _carry(n: int) -> tuple[str, str]:
    """``(carry, are)`` agreeing with *n*.

    The sentence below reads "N cells carry no focus value and are kept", and
    at N = 1 both verbs are wrong. Caught by printing the real panel's text
    rather than by reading the f-string, which is the only way this kind of
    defect is ever caught.
    """
    return ("carries", "is") if n == 1 else ("carry", "are")


def focus_status(ctx) -> tuple[str, str]:
    """``(sentence, register)`` for the Cell Gating panel's focus row.

    Four states, and collapsing any two of them is the substitution this
    subsystem exists to prevent:

    * **no column** — the dataset was analysed before focus was measured.
      Muted, and explicitly *not* "no cells are out of focus": a plate nobody
      measured and a plate measured and found sharp must not read alike. It
      names the remedy, because the remedy is a re-run and the user is
      entitled to know that before going looking for a control.
    * **no cut stated** — the column is there and no threshold has been
      chosen. A result, not a problem: this is the normal state of a dataset
      whose run recorded nothing, and the sentence points at where a number
      comes from rather than inventing one (§D.2d measured why none can be).
    * **nothing below the cut** — said plainly, because it is a real result.
    * **cells below the cut** — how many, out of how many, and how many could
      not be measured at all. The last of those is the one a reader would
      otherwise get wrong: those cells are **kept**, so a plate can be gated
      and still carry soft cells nobody could see.
    """
    params = focus_params(ctx)
    has_column = dataset_has_focus_column(ctx)
    if has_column is None:
        # No wells loaded to look at -- after a reset emptied the cache, or
        # before the first load. Saying nothing is the honest answer; the
        # sentence returns of its own accord once a frame is read.
        return ("", REGISTER_MUTED)
    if not has_column:
        return ("This dataset was analysed before per-cell focus was measured, "
                "so there is nothing to exclude. Re-running it with "
                "Analyze ▸ Quality Control ▸ Measure per-cell focus records "
                "the column.", REGISTER_MUTED)
    soft, unmeasurable, total = plate_focus_counts(ctx)
    if not total:
        return ("", REGISTER_MUTED)
    if params is None or params.focus_min is None:
        # One line at the panel's width. The first version ran to two dense
        # lines of muted text under a control, which is where the reader's eye
        # is least willing to go; the control's own tooltip carries the rest.
        base = (f"Focus measured for {_cells(total - unmeasurable)}. No "
                f"threshold set — the Image Playground draws the "
                f"distribution.")
        register = REGISTER_PLAIN
    elif not soft:
        base = (f"All {_cells(total - unmeasurable)} measured are at or above "
                f"{params.focus_min:g}.")
        register = REGISTER_PLAIN
    else:
        base = (f"{_cells(soft)} of {total:,} read below {params.focus_min:g}.")
        register = REGISTER_PLAIN
    if unmeasurable:
        # Said whether or not anything is below the cut, and said as a problem:
        # these cells cannot be judged at all, and a cell silently exempt from
        # a filter looks exactly like a cell that passed it.
        carry, are = _carry(unmeasurable)
        return (f"{base} {_cells(unmeasurable)} {carry} no focus value and "
                f"{are} kept whatever this is set to — an outline too small "
                f"or too faint to measure, which is also what severe defocus "
                f"leaves behind.", REGISTER_PROBLEM)
    return (base, register)


def recorded_cut_note(ctx) -> str:
    """What the *run* recorded, when that is not what is in force, or ``""``.

    The run writes its cut into ``pipeline_info.json`` and Review seeds from
    it. A user may then type their own, which is the control working as
    designed — but from that moment the dataset carries two numbers, and
    Analyze's form (restored from the sidecar) shows the other one. Nothing
    anywhere used to say so, and there was no surface in Review that showed
    the recorded value at all, so the divergence was not discoverable even by
    looking for it.

    The commoner half of that problem is fixed where it is caused: the
    persistence layer stores the cut only when it differs from the run's, so
    an untouched cut re-seeds and a later re-run is picked up. This sentence
    covers what is left -- a cut the user really did choose -- and it names
    both numbers rather than warning about a mismatch in the abstract.
    """
    from review.domain.qc_params import QCParams

    params = focus_params(ctx)
    if params is None:
        return ""
    recorded = QCParams.from_pipeline_info(
        getattr(ctx.state.dataset, "pipeline_info", None)).focus_min
    if recorded is None or recorded == params.focus_min:
        return ""
    if params.focus_min is None:
        return (f"This dataset's run recorded a cut of {recorded:g}, which is "
                f"not in force — the box above is blank.")
    return (f"Yours, not the run's: this dataset's run recorded {recorded:g}.")


def focus_effect_text(ctx) -> str:
    """What the switch is doing **right now**, or ``""``.

    The sentence above deliberately reports the same finding whether or not
    the gate is on, which would otherwise leave the panel with no statement of
    effect at all — a user ticks a control that says it will drop rows, and
    every word on screen stays the same.

    **"across the plate" is said out loud.** The count is plate-wide while
    every plot the user is looking at is scoped to their selection, so the
    number here and the number of rows any one figure loses are different
    quantities. Naming the scope is cheaper than making them agree and more
    honest than letting the reader assume.
    """
    params = focus_params(ctx)
    if params is None or params.focus_min is None:
        return ""
    soft, _unmeasurable, total = plate_focus_counts(ctx)
    if not total or not soft:
        return ""
    if not params.exclude_soft_cells:
        return f"Currently kept — {_cells(soft)} across the plate would be excluded."
    return f"Currently excluding {_cells(soft)} across the plate."


def focus_gate_can_apply(ctx) -> bool:
    """Whether ticking the box would actually exclude anything.

    **Not "is there a column".** That predicate is strictly weaker, and its
    failure mode is the one this application has shipped before: the box stays
    live on a dataset with no threshold, the user ticks it, it stays ticked,
    and nothing is excluded — every number then read by someone who believes a
    focus filter is on.
    """
    params = focus_params(ctx)
    if params is None or params.focus_min is None:
        return False
    soft, _u, _t = plate_focus_counts(ctx)
    return soft > 0


def per_well_focus_tallies(ctx, cut: float | None) -> dict:
    """``{well: FocusTally}`` against *cut*, for every well of the plate.

    A tally — four numbers — rather than the well's focus values: holding every
    well's per-cell column at once to score the plate is what the bounded row
    cache exists to prevent, and the composite reads nothing else. Each well is
    read narrowly — the focus column and the verdicts' inputs — and its tally
    remembered (:func:`_every_well_narrow`).

    The composite's shares are shares **of the cells being analysed**; see
    :func:`candidate_rows` for why counting every row marked every well of a
    debris-heavy plate. A well with no focus column tallies as empty, so
    "measured and empty" and "never measured" stay distinguishable one layer up
    exactly as an empty value list did.
    """
    from review.domain.well_composite import tally_focus

    def _tally(df):
        vals = focus_values(candidate_rows(df))
        if vals is None:
            return tally_focus((), cut)
        return tally_focus([None if v != v else float(v) for v in vals], cut)

    return _every_well_narrow(ctx, "focus_tally", cut, _tally)


def wells_needing_review(ctx) -> dict[str, str]:
    """``{well: why}`` for the wells the plate map should mark.

    The one place the two image-quality layers are combined, so the marker,
    the tooltip and the legend cannot be built from three different ideas of
    which wells are in trouble. The composite itself is
    :mod:`review.domain.well_composite`, which takes plain numbers and knows
    nothing about a dataset — this is the adapter that reads them.
    """
    from review.data.field_quality_cache import flagged_detail_by_well
    from review.domain.well_composite import score_plate_tallies
    from review.domain.well_composite import (
        wells_needing_review as _needing,
    )

    params = focus_params(ctx)
    cut = None if params is None else params.focus_min
    return _needing(score_plate_tallies(per_well_focus_tallies(ctx, cut), cut=cut,
                                flagged=flagged_detail_by_well(ctx)))


def well_focus_distribution(ctx, well: str):
    """One well's per-cell focus values as a
    :class:`~review.domain.focus_distribution.FocusDistribution`.

    **The plate-wide companion to the Playground's per-frame view**, and the
    one the threshold is actually chosen against: the cut applies to a whole
    dataset, while the Playground measures one field of one channel. A well
    pools every field and timepoint it holds, from the column the run already
    wrote — no image decode, no ``_out.zip`` opened.

    Cells with no value are **counted, not dropped**, so the readout can say
    how much of the well could not be judged at all. The cut comes from the
    same ``QCParams`` the gate applies, so the line on this plot and the rows
    the gate removes cannot be different numbers — and the curve is drawn over
    :func:`candidate_rows` for the same reason, so a user choosing a cut off
    this plot sees the population the cut will actually meet rather than one
    padded with debris the QC gate has already taken.
    """
    from review.domain.focus_distribution import distribution_from_values

    params = focus_params(ctx)
    cut = None if params is None else params.focus_min
    # Read through ``get_rows``, not looked up in the cache: the cache is
    # bounded, and an evicted well is not a well with no measurements.
    df = get_rows(ctx, well) if well in ctx.state.dataset.well_paths else None
    if df is None or len(df) == 0:
        # NOT ``NO_CELLS``: that refusal is worded for one frame ("segment it
        # first"), and the remedy here is different -- this well has no rows
        # loaded at all.
        return distribution_from_values(
            (), cut=cut, subject=str(well),
            declined=f"No measurements are loaded for {well}, so there is "
                     f"nothing to draw. Select a well that this dataset holds.")
    vals = focus_values(candidate_rows(df))
    if vals is None:
        # Measured-and-empty and never-measured are different facts; this is
        # the second, and it gets the sentence that names the remedy.
        return distribution_from_values(
            (), cut=cut, subject=str(well),
            declined="This dataset was analysed before per-cell focus was "
                     "measured, so this well has no distribution to draw. "
                     "Re-running it with Analyze \u25b8 Quality Control "
                     "\u25b8 Measure per-cell focus records the column.")
    n_fields = 0
    if "fov" in df.columns:
        n_fields = int(df["fov"].astype(str).nunique())
    where = f"{well} \u00b7 {n_fields} field(s)" if n_fields else str(well)
    return distribution_from_values(
        [None if v != v else float(v) for v in vals], cut=cut, subject=where)
