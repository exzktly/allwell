"""Do a well's FOV labels actually identify its fields of view?

Every per-FOV number in the app — the error bars on the bar and line plots, the
Statistics tab's ``n``, the events-above-threshold panel — is computed by
grouping a well's rows on the CSV's ``fov`` column. That column is only as good
as the filename parsing that produced it: ``process_microscopy.parse_filename``
fills a schema slot it cannot find with ``""``, and the viewer's loader maps
``""`` and ``"-1"`` onto ``"1"`` (the single-field convention). A well whose
filenames don't match the schema therefore arrives with *every* row labelled
field 1, and nothing downstream can tell that from a well that genuinely has one
field.

The consequences are not cosmetic. Six fields pooled under one label make the
per-field spread undefined (so the error bar silently disappears), and turn the
"mean events per field" into the *total* events — six times too large, plotted
next to correctly-labelled wells as if it were the same quantity.

The labels are checkable against evidence the CSV already carries, without
re-reading a single image:

* ``filename`` records the nuclear image each row's cell came from — one image
  per (well, field, timepoint). More distinct images than distinct field labels
  within a timepoint means the labels do not separate the images.
* ``nucleus_id`` is assigned per label image, so it is unique within a real
  (field, timepoint). A duplicate means rows from different images were pooled
  under one label.

Either signal is conclusive on its own; neither can fire for a genuinely
single-field well. Qt-free and pandas-only, so the loader, the aggregation, and
the tests can all use it.
"""

from __future__ import annotations

from dataclasses import dataclass

# Tokens that mean "no field of view was recorded", as opposed to a real label.
# ``""`` is what ``parse_filename`` writes for a slot it could not fill; the rest
# are what the various extractors emit for the same situation.
UNRECORDED_FOV_TOKENS = frozenset({"", "-1", "nan", "none", "null", "unknown"})


@dataclass(frozen=True)
class FovIntegrity:
    """Verdict on one well's FOV labels.

    ``trusted`` is False only when the labels are *provably* wrong — every
    other case (including "we can't tell") is trusted, so a dataset the checker
    doesn't understand keeps working exactly as before.
    """

    trusted: bool
    n_labels: int = 0      # distinct FOV tokens
    n_images: int = 0      # distinct source images in the worst timepoint (0 = unknown)
    reason: str = ""       # human-readable, empty when trusted

    def __bool__(self) -> bool:
        return self.trusted


_TRUSTED = FovIntegrity(trusted=True)


def check_fov_labels(df) -> FovIntegrity:
    """Verdict on *df*'s ``fov`` column (one well's rows).

    Cheap enough to run once per well as its frame is built: two groupby-nunique
    passes and one duplicate check over columns that are already in memory.
    """
    if df is None or len(df) == 0:
        return _TRUSTED
    cols = set(df.columns)
    if "fov" not in cols:
        # No column at all: every row aggregates as field "1". Only worth
        # reporting when there is evidence of more than one image behind it.
        n_images = int(df["filename"].nunique()) if "filename" in cols else 0
        if n_images > 1:
            return FovIntegrity(
                trusted=False, n_labels=1, n_images=n_images,
                reason=(f"no 'fov' column, but {n_images} source images — every "
                        "field is pooled under one label"),
            )
        return _TRUSTED

    fov = df["fov"].fillna("").astype(str).str.strip()
    n_labels = int(fov.nunique())

    # Signal 1: more distinct source images than field labels, within a single
    # timepoint. Compared per timepoint because the image count grows with the
    # timepoints while the field labels do not.
    if "filename" in cols:
        if "timepoint" in cols:
            key = df["timepoint"].fillna("").astype(str)
        else:
            key = fov * 0  # one group; still a valid comparison
        grouped = df.groupby(key, sort=False)
        images_per_tp = grouped["filename"].nunique()
        labels_per_tp = grouped["fov"].nunique()
        gap = images_per_tp - labels_per_tp
        if len(gap) and int(gap.max()) > 0:
            worst = gap.idxmax()
            n_img = int(images_per_tp.loc[worst])
            n_lbl = int(labels_per_tp.loc[worst])
            return FovIntegrity(
                trusted=False, n_labels=n_lbl, n_images=n_img,
                reason=(f"{n_img} source images share {n_lbl} FOV label(s) at one "
                        "timepoint — the field labels do not identify the images"),
            )

    # Signal 2: nucleus ids repeat inside one (field, timepoint). Ids come from a
    # single label image, so this can only happen when several images were pooled.
    # Survives an incremental merge that already dropped the collision rows.
    if "nucleus_id" in cols:
        key_cols = ["fov", "nucleus_id"]
        if "timepoint" in cols:
            key_cols.insert(1, "timepoint")
        dupes = int(df.duplicated(subset=key_cols).sum())
        if dupes:
            return FovIntegrity(
                trusted=False, n_labels=n_labels, n_images=0,
                reason=(f"{dupes} rows repeat a nucleus id within one field and "
                        "timepoint — rows from several images are pooled under "
                        "one FOV label"),
            )

    return FovIntegrity(trusted=True, n_labels=n_labels)


