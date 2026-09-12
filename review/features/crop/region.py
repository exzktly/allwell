"""Crop-region model + the pure inside-the-box verdict.

The crop "layer" is a non-destructive sibling of the QC layer
(:mod:`review.features.qc.params`): it never deletes rows or mutates the
canonical ``Included`` flag. Instead it computes a separate per-cell
``Crop_Pass`` series from each cell's centroid, and the single inclusion
chokepoint (:func:`review.data.data_loading.df_included_mask`) ANDs it with
``Included`` and ``QC_Pass`` — effective inclusion is
``manual-keep AND passes-QC AND inside-crop``.

The crop is one rectangle in mask-pixel (row, col) space. Because every FOV /
well shares image dimensions, the same rectangle carries across frames; the
cell's centroid is a property of its CSV row, so the verdict is keyed by the
cell, not by the displayed FOV's mask.

*Which* frames it is applied to is a :class:`FrameScope` — the same
wells / FOVs / timepoints mechanism the Segmentation tab's drag-delete arms
(``ReviewImageState.delete_scope_*``). The crop's scope defaults to every frame
in the dataset, so an un-scoped crop is the plate-wide filter it has always
been; a scoped crop leaves every out-of-scope cell passing, which is the same
fail-open direction the rest of this module takes.

This module is Qt-free and dependency-light (pandas only, plus the shared token
normalisers in :mod:`domain.image_identity`) so the verdict is
unit-tested without the app.
"""

from __future__ import annotations

from typing import NamedTuple


# Always-on centroid columns the pipeline emits for every cell
# (``shape_features_core.ALWAYS_ON_KEYS``). ``centroid_col`` is the x pixel,
# ``centroid_row`` is the y pixel.
CENTROID_ROW_COL = "shape_centroid_row"
CENTROID_COL_COL = "shape_centroid_col"

# (row0, col0, row1, col1) in source-mask pixels, normalized so row0<=row1,
# col0<=col1.
CropRegion = tuple[int, int, int, int]


class FrameScope(NamedTuple):
    """Which frames a region verdict applies to.

    Each field is a set of tokens or ``None``. ``None`` means "every value of
    this field" — the un-armed default, under which the crop is the plate-wide
    filter it has always been. An *empty set* is a different thing: the user
    unchecked every entry in that dropdown, which selects no frames at all. The
    two must not collide, which is why this is ``set | None`` rather than the
    empty-set-means-all convention ``ImageTableState.active_fovs`` uses.

    Wells are matched against the dataset's own labels, uncanonicalised, for the
    reason given in ``review_image.callbacks.override_key`` — every reader keys
    off the label the loader produced. FOVs and timepoints are matched in the
    canonical token forms :mod:`domain.image_identity` produces, so a
    combo reading ``T01`` and a CSV column holding ``1.0`` are the same
    timepoint.
    """

    wells: frozenset | None = None
    fovs: frozenset | None = None
    tps: frozenset | None = None

    @property
    def is_armed(self) -> bool:
        """Whether the scope names anything narrower than the whole dataset."""
        return not (self.wells is None and self.fovs is None and self.tps is None)


# Where a frame's normalised (fov, tp) token pair is memoised on the frame
# itself. The tokens are a pure function of two columns the loader writes once
# and nothing afterwards touches, so the only invalidation this needs is the
# frame's own identity and length — and it dies with the frame.
_TOKENS_ATTR = "_aw_frame_tokens"


def _frame_tokens(df):
    """Normalised ``(fov, tp)`` Series for *df*, computed once per frame.

    The normalisation is per row and in Python, which makes it the dominant
    cost of every scope question: ~430 ms on a 200k-row well. Both the row
    verdict and the outline's frame test ask it, on the projection path and on
    every canvas redraw respectively, so it is memoised rather than repeated.
    """
    from domain.image_identity import frame_token_series
    key = (id(df), len(df))
    try:
        cached = df.attrs.get(_TOKENS_ATTR)
        if cached is not None and cached[0] == key:
            return cached[1], cached[2]
    except AttributeError:      # not a DataFrame — let the caller's use fail
        return frame_token_series(df)
    fov_n, tp_n = frame_token_series(df)
    df.attrs[_TOKENS_ATTR] = (key, fov_n, tp_n)
    return fov_n, tp_n


def rows_in_scope(df, scope: FrameScope | None, well: str | None = None):
    """Boolean Series — ``True`` where the row's frame is inside *scope*.

    The well is a property of the frame, not of the row: the caller passes the
    label of the well ``df`` was loaded for, and an out-of-scope well makes the
    whole frame ``False`` in one step rather than per row. FOV and timepoint are
    read off the row through :func:`~domain.image_identity.frame_token_series`,
    the same normalisation the override keys use.

    A frame that carries no timepoint column reads as ``""`` and therefore
    matches no armed timepoint set — the crop then applies to none of its rows,
    which is the fail-open direction (every cell keeps passing). Callers report
    the resulting counts, so a scope that matches nothing shows up as
    "excluded 0 cells" rather than as a silently applied crop.
    """
    import pandas as pd

    if df is None or len(df) == 0:
        return pd.Series([], dtype=bool, index=getattr(df, "index", None))
    if scope is None or not scope.is_armed:
        return pd.Series(True, index=df.index, dtype=bool)
    if scope.wells is not None:
        wanted = {str(w).strip() for w in scope.wells}
        if str(well or "").strip() not in wanted:
            return pd.Series(False, index=df.index, dtype=bool)
    if scope.fovs is None and scope.tps is None:
        return pd.Series(True, index=df.index, dtype=bool)

    from domain.image_identity import normalize_fov, normalize_tp
    fov_n, tp_n = _frame_tokens(df)
    inside = pd.Series(True, index=df.index, dtype=bool)
    # The armed tokens are normalised here as well as where they are picked: a
    # scope can arrive from ``persistence.json`` written by an older session, and
    # a raw ``T01`` compared against a normalised ``1`` would match no row while
    # the button still advertised the timepoint.
    if scope.fovs is not None:
        inside &= fov_n.isin({normalize_fov(f) for f in scope.fovs})
    if scope.tps is not None:
        inside &= tp_n.isin({normalize_tp(t) for t in scope.tps})
    return inside.astype(bool)


def frame_in_scope(df, scope: FrameScope | None, well: str | None,
                   fov, tp) -> bool:
    """Whether the crop actually reaches any cell of one displayed frame.

    The question a *view* asks — "is the box I am drawing filtering this
    picture?" — answered by the same code that decides the rows, rather than by
    a second comparison of the same tokens. Restating it was a bug in both
    directions: a scope holding a non-canonical token (a hand-edited
    ``persistence.json``) read as out-of-scope while the rows were being
    excluded, and a frame whose CSV carries no timepoint column read as
    in-scope while no row of it could match an armed timepoint.

    ``df`` is the well's rows. Only when there are none at all — a well with
    images but no CSV — does the answer fall back to comparing the tokens; a
    frame whose well *has* rows and none of them lands on it reaches no cell,
    whatever its tokens say.
    """
    if scope is None or not scope.is_armed:
        return True
    if scope.wells is not None:
        if str(well or "").strip() not in {str(w).strip() for w in scope.wells}:
            return False

    if scope.fovs is None and scope.tps is None:
        # A well-only scope is decided by the well, which is already checked.
        # Worth its own branch: the row scan below costs a per-row token
        # normalisation, and this runs on every canvas redraw.
        return True

    from domain.image_identity import normalize_fov, normalize_tp
    want_fov, want_tp = normalize_fov(fov), normalize_tp(tp)
    if df is not None and len(df) > 0:
        # One token pass, shared between locating the frame and judging it —
        # calling ``rows_in_scope`` here instead would normalise every row a
        # second time, and this is on the redraw path.
        fov_n, tp_n = _frame_tokens(df)
        here = (fov_n == want_fov) & (tp_n == want_tp)
        if not bool(here.any()):
            # The well has rows and none of them belongs to this frame — the
            # crop can reach nothing here even if the tokens look armed.
            return False
        if scope.fovs is not None and want_fov not in {
                normalize_fov(f) for f in scope.fovs}:
            return False
        if scope.tps is not None and want_tp not in {
                normalize_tp(t) for t in scope.tps}:
            return False
        return True
    # No rows at all — judge the tokens themselves.
    if scope.fovs is not None and want_fov not in {
            normalize_fov(f) for f in scope.fovs}:
        return False
    if scope.tps is not None and want_tp not in {
            normalize_tp(t) for t in scope.tps}:
        return False
    return True


def normalize_region(
    row0: float, col0: float, row1: float, col1: float,
) -> CropRegion | None:
    """Return a sorted integer ``(row0, col0, row1, col1)`` region, or ``None``
    when the box is degenerate (zero area) so callers can treat it as "no crop".
    """
    r0, r1 = sorted((int(round(row0)), int(round(row1))))
    c0, c1 = sorted((int(round(col0)), int(round(col1))))
    if r1 <= r0 or c1 <= c0:
        return None
    return (r0, c0, r1, c1)


def has_centroid_columns(df) -> bool:
    """Whether ``df`` carries both ``shape_centroid_*`` columns.

    A frame without them cannot be placed against any region, so every
    region-based verdict has to decide what that means for it — the crop keeps
    the cell, the region delete spares it.
    """
    if df is None or len(getattr(df, "columns", ())) == 0:
        return False
    return CENTROID_ROW_COL in df.columns and CENTROID_COL_COL in df.columns


def centroids_inside(df, region: CropRegion | None):
    """Return a boolean Series — ``True`` where the cell's centroid is placeable
    *and* lands inside ``region``. Bounds are inclusive.

    The shared geometry behind both region verdicts. It answers only the
    positive question ("is this cell definitely inside the box?"), which is what
    lets the two callers take opposite sides on the unplaceable cells: a NaN or
    missing centroid is ``False`` here, so :func:`compute_crop_pass` inverts it
    into a keep (fail-open) while the Segmentation region delete reads it
    straight and spares the cell (fail-closed). Neither acts on a cell it cannot
    place; they just disagree about which action that is.
    """
    import pandas as pd

    if df is None or len(df) == 0:
        return pd.Series([], dtype=bool, index=getattr(df, "index", None))
    if region is None or not has_centroid_columns(df):
        return pd.Series(False, index=df.index, dtype=bool)

    r0, c0, r1, c1 = region
    row = pd.to_numeric(df[CENTROID_ROW_COL], errors="coerce")
    col = pd.to_numeric(df[CENTROID_COL_COL], errors="coerce")
    inside = (row >= r0) & (row <= r1) & (col >= c0) & (col <= c1)
    # NaN centroid → unplaceable → not inside.
    return (inside & row.notna() & col.notna()).astype(bool)


def compute_crop_pass(df, region: CropRegion | None,
                      scope: FrameScope | None = None,
                      well: str | None = None):
    """Return an int Series (1 = cell kept, 0 = cropped out) for ``df``.

    Pure function of the frame's centroid columns, ``region``, and the
    :class:`FrameScope` the crop is armed for. Fail-open: when ``region`` is
    ``None``, the frame is empty, or either centroid column is absent (legacy
    CSVs without shape features), every row passes — we never exclude a cell we
    cannot place. Cells with a NaN centroid also pass, and so does every cell in
    a frame the scope does not name. Bounds are inclusive.
    """
    import pandas as pd

    passed = pd.Series(1, index=df.index, dtype=int)
    if (
        region is None
        or df is None
        or len(df) == 0
        or not has_centroid_columns(df)
    ):
        return passed

    # Unplaceable cells are False in ``centroids_inside``; OR them back in so
    # they keep passing the crop. Out-of-scope rows join them: the crop is not
    # applied to their frame at all, so the box never decides them.
    row = pd.to_numeric(df[CENTROID_ROW_COL], errors="coerce")
    col = pd.to_numeric(df[CENTROID_COL_COL], errors="coerce")
    inside = centroids_inside(df, region) | row.isna() | col.isna()
    if scope is not None and scope.is_armed:
        inside |= ~rows_in_scope(df, scope, well)
    return inside.astype(int)


def crop_effect_for_df(df, region, scope: FrameScope | None = None,
                       well: str | None = None):
    """Summarise a crop's effect on one frame.

    Returns ``(removed, otherwise_included, has_centroids)``:
    - ``otherwise_included`` — cells that would count toward analysis ignoring
      the crop (``Included==1`` AND ``QC_Pass==1`` when those columns exist).
    - ``removed`` — of those, how many the crop now drops (centroid outside the
      box, in a frame the scope names). This is the crop's marginal effect on
      every plot/stat.
    - ``has_centroids`` — whether the frame carries the ``shape_centroid_*``
      columns the crop needs; when False the crop fails open and ``removed`` is 0.
    """
    import pandas as pd

    if df is None or len(df) == 0:
        return 0, 0, False
    has_centroids = (
        CENTROID_ROW_COL in df.columns and CENTROID_COL_COL in df.columns
    )
    incl = pd.Series(True, index=df.index)
    if "Included" in df.columns:
        incl &= pd.to_numeric(df["Included"], errors="coerce").fillna(0).eq(1)
    if "QC_Pass" in df.columns:
        incl &= pd.to_numeric(df["QC_Pass"], errors="coerce").fillna(1).eq(1)
    otherwise_included = int(incl.sum())
    crop = compute_crop_pass(df, region, scope, well).eq(1)
    removed = int((incl & ~crop).sum())
    return removed, otherwise_included, has_centroids

