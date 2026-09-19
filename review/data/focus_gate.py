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
very different focus states. The estimator is built to be comparable between
cells, fields, wells and plates so that one number can serve, and the number
comes from the run's own sidecar or from the user, never from this module.

**Three things this refuses to do, each because the alternative is a silent
substitution.**

* A cell with **no** focus value passes. Undefined is not soft. Severe defocus
  degrades segmentation, so the column is missing exactly where defocus is
  worst — but so is a cell too small to carry a measurable ring, and treating
  the two alike would quietly delete the smallest nuclei on every plate. The
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

from review.data.cache_projectors import (
    drop_verdict_column, project_verdict_column,
)

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


def focus_values(df):
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


def compute_focus_pass(df, params):
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


def focus_counts(df, params) -> tuple[int, int, int]:
    """``(soft, unmeasurable, total)`` for one frame.

    ``unmeasurable`` is counted and returned rather than folded into either
    other number, because the panel has to be able to say it out loud: a cell
    nobody could measure is being **kept**, and a reader who believes the gate
    removed everything soft would be wrong about exactly those rows.
    """
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


def plate_focus_counts(ctx) -> tuple[int, int, int]:
    """``(soft, unmeasurable, total)`` summed over every cached well.

    Counted against the **current** cut whether or not the gate is switched
    on, because the panel reports what a cut would do before it is applied —
    a user choosing a threshold needs the consequence in front of them, not
    after the fact.
    """
    params = focus_params(ctx)
    cache = getattr(ctx.state.dataset, "cache", None) or {}
    soft = unmeasurable = total = 0
    for df in cache.values():
        s, u, t = focus_counts(df, params)
        soft += s
        unmeasurable += u
        total += t
    return (soft, unmeasurable, total)


def dataset_has_focus_column(ctx) -> bool:
    """Whether any cached well carries the column at all.

    The distinction the panel needs between "this dataset was analysed before
    focus was measured" and "it was measured and nothing is soft". Those must
    never read alike.
    """
    from pipeline.field_quality_record import FOCUS_COLUMN

    cache = getattr(ctx.state.dataset, "cache", None) or {}
    return any(df is not None and FOCUS_COLUMN in getattr(df, "columns", ())
               for df in cache.values())


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
    if not dataset_has_focus_column(ctx):
        return ("This dataset was analysed before per-cell focus was measured, "
                "so there is nothing to exclude. Re-running it with "
                "Analyze ▸ Quality Control ▸ Measure per-cell focus records "
                "the column.", REGISTER_MUTED)
    soft, unmeasurable, total = plate_focus_counts(ctx)
    if not total:
        return ("", REGISTER_MUTED)
    if params is None or params.focus_min is None:
        base = (f"Focus measured for {_cells(total - unmeasurable)}. No "
                f"threshold set — the Image Playground draws the distribution "
                f"and a cut's live effect, which is where the number comes "
                f"from.")
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


def focus_effect_text(ctx) -> str:
    """What the switch is doing **right now**, or ``""``.

    The sentence above deliberately reports the same finding whether or not
    the gate is on, which would otherwise leave the panel with no statement of
    effect at all — a user ticks a control that says it will drop rows, and
    every word on screen stays the same.
    """
    params = focus_params(ctx)
    if params is None or params.focus_min is None:
        return ""
    soft, _unmeasurable, total = plate_focus_counts(ctx)
    if not total or not soft:
        return ""
    if not params.exclude_soft_cells:
        return f"Currently kept — {_cells(soft)} would be excluded."
    return f"Currently excluding {_cells(soft)}."


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
