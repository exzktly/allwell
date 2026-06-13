import os
import sys
import shutil
import argparse
from typing import List, Optional

# ---------------------------------------------------------------------------
# Schema constants (kept in sync with process_microscopy.py)
# ---------------------------------------------------------------------------
SCHEMA_FIELDS   = ("experiment", "channel", "well", "fov", "timepoint", "ignore")
DEFAULT_SCHEMA  = "experiment:channel:well:fov:timepoint"
DEFAULT_SEP     = "_"


def parse_schema(schema_str: str) -> List[str]:
    """Parse a colon-delimited schema string into a list of field names."""
    known = set(SCHEMA_FIELDS)
    return [
        tok if tok in known else "ignore"
        for tok in schema_str.strip().split(":")
        if tok
    ]


def validate_schema(schema: List[str]) -> List[str]:
    """Return a list of human-readable validation errors. Empty list = OK.

    Mirrors process_microscopy.validate_schema so a CLI invocation of
    WellPlateZipper rejects the same bad schemas the GUI rejects.
    """
    errors: List[str] = []
    if "channel" not in schema:
        errors.append('Schema must include a "channel" field.')
    elif schema.count("channel") > 1:
        errors.append('Schema must include "channel" exactly once.')
    if "well" not in schema:
        errors.append('Schema must include a "well" field.')
    elif schema.count("well") > 1:
        errors.append('Schema must include "well" exactly once.')
    return errors


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Group TIFF files into per-well folders by 96-well plate IDs in filenames"
    )
    parser.add_argument("-s", "--search-dir", required=True)
    parser.add_argument("-o", "--output-dir", default=None)
    parser.add_argument("-r", "--recursive", action="store_true")
    parser.add_argument(
        "--filename_schema", default=DEFAULT_SCHEMA,
        help=(
            "Colon-separated ordered field names describing the filename "
            "structure.  \"well\" must appear exactly once.  "
            f"Default: \"{DEFAULT_SCHEMA}\""
        ),
    )
    parser.add_argument(
        "--filename_sep", default=DEFAULT_SEP,
        help=f"Field separator character in filenames.  Default: \"{DEFAULT_SEP}\"",
    )
    parser.add_argument(
        "--move-source-tifs", action="store_true",
        help=(
            "Move each source TIF into its per-well folder instead of "
            "copying. Default is to copy (safer: a crash mid-pipeline "
            "leaves the originals untouched). Use --move-source-tifs "
            "when disk space is tight — peak usage stays close to the "
            "input footprint rather than ~2x during the pipeline run. "
            "A crash mid-zipper leaves a half-converted layout: some "
            "wells in per-well subfolders, the rest still loose."
        ),
    )
    return parser


def generate_wells() -> List[str]:
    return [f"{r}{c:02d}" for r in "ABCDEFGH" for c in range(1, 13)]


def _well_index_from_schema(schema: List[str]) -> Optional[int]:
    """Return the position index of the \"well\" field, or None if absent."""
    try:
        return schema.index("well")
    except ValueError:
        return None


def _extract_well_from_filename(
    fname: str,
    schema: List[str],
    sep: str,
) -> Optional[str]:
    """
    Extract the well token from *fname* using the given schema.

    Returns the normalised well label (e.g. "A01") if the filename contains a
    valid 96-well plate position at the schema's "well" slot, otherwise None.
    """
    from core.well_token import canonical_well_label
    stem = os.path.splitext(fname)[0]
    well_idx = _well_index_from_schema(schema)
    if well_idx is None:
        return None

    parts = stem.split(sep)
    if well_idx >= len(parts):
        return None
    return canonical_well_label(parts[well_idx])


def group_by_well(
    search_dir: str,
    recursive: bool,
    schema: List[str],
    sep: str,
) -> dict:
    """Single pass through *search_dir*; return ``{well_label: [paths]}``.

    Replaces the previous O(96·N) approach where ``main`` walked the
    entire directory once per 96-well slot. For a directory with ~10⁵
    TIFs the old code did ~10⁷ filename inspections — the "Grouping"
    phase the user stares at took minutes when it should be seconds.
    """
    out: dict[str, list[str]] = {}
    for root, _, files in os.walk(search_dir):
        for fname in files:
            if not fname.lower().endswith((".tif", ".tiff")):
                continue
            extracted = _extract_well_from_filename(fname, schema, sep)
            if extracted is None:
                continue
            out.setdefault(extracted, []).append(os.path.join(root, fname))
        if not recursive:
            break
    return out


def folder_well(
    well: str, files: List[str], output_dir: str,
    *, move: bool = False,
) -> None:
    """Place *files* into a per-well subfolder <output_dir>/<well>/.

    ``move=False`` (default) — copy each file with ``shutil.copy2``,
    leaving the source TIFs untouched. Safer: any crash mid-pipeline
    keeps the originals available for a clean retry.

    ``move=True`` — opt-in via ``--move-source-tifs``. Uses
    ``shutil.move`` so peak disk usage stays close to the input
    footprint rather than ~2x during the pipeline run. The trade-off
    is that a crash mid-zipper leaves a half-converted input dir
    (some wells migrated to subfolders, the rest still loose at
    the top level).
    """
    if not files:
        return
    well_dir = os.path.join(output_dir, well)
    os.makedirs(well_dir, exist_ok=True)
    place = shutil.move if move else shutil.copy2
    for f in files:
        place(f, os.path.join(well_dir, os.path.basename(f)))
    print(f"{well}: {len(files)} files")


def main():
    args = build_arg_parser().parse_args()

    search_dir = args.search_dir
    output_dir = args.output_dir or search_dir
    schema     = parse_schema(args.filename_schema)
    sep        = args.filename_sep

    # Validate before walking — matches the GUI's pre-flight checks so
    # a CLI invocation rejects the same schemas (e.g. two "well" slots)
    # the GUI would reject. Previously the zipper silently used
    # `schema.index("well")` and misclassified.
    schema_errors = validate_schema(schema)
    if schema_errors:
        for msg in schema_errors:
            print(f"error: {msg}", file=sys.stderr)
        sys.exit(2)

    if not os.path.isdir(search_dir):
        sys.exit(f"Invalid directory: {search_dir}")

    os.makedirs(output_dir, exist_ok=True)

    # Single-pass grouping (was 96 separate os.walks).
    files_by_well = group_by_well(search_dir, args.recursive, schema, sep)
    # Emit per-well progress in canonical A01…H12 order so the Analyze
    # tab's progress chip can keep tracking.
    for well in generate_wells():
        folder_well(
            well,
            files_by_well.get(well, []),
            output_dir,
            move=args.move_source_tifs,
        )


if __name__ == "__main__":
    main()
