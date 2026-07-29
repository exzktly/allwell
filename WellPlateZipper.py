import os
import sys
import shutil
import argparse

# ---------------------------------------------------------------------------
# Schema vocabulary + parser — shared with process_microscopy via util.schema so
# the two entry points cannot drift. They previously kept separate copies whose
# validate_schema reported the same verdict with different wording.
# ---------------------------------------------------------------------------
from util.schema import (  # noqa: E402  (module-level, after the stdlib block)
    DEFAULT_SCHEMA,
    DEFAULT_SEP,
    parse_schema,
    validate_schema,
)


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


def generate_wells() -> list[str]:
    return [f"{r}{c:02d}" for r in "ABCDEFGH" for c in range(1, 13)]


def _well_index_from_schema(schema: list[str]) -> int | None:
    """Return the position index of the \"well\" field, or None if absent."""
    try:
        return schema.index("well")
    except ValueError:
        return None


def _extract_well_from_filename(
    fname: str,
    schema: list[str],
    sep: str,
) -> str | None:
    """
    Extract the well token from *fname* using the given schema.

    Returns the normalised well label (e.g. "A01") if the filename contains a
    valid 96-well plate position at the schema's "well" slot, otherwise None.
    """
    from util.well_token import canonical_well_label
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
    schema: list[str],
    sep: str,
) -> dict:
    """Single pass through *search_dir*; return ``{well_label: [paths]}``.

    Replaces the previous O(96·N) approach where ``main`` walked the
    entire directory once per 96-well slot. For a directory with ~10⁵
    TIFs the old code did ~10⁷ filename inspections — the "Grouping"
    phase the user stares at took minutes when it should be seconds.
    """
    # Stream discovery progress so the Analyze log shows activity during the
    # potentially-long directory walk (datasets can hold 10⁴–10⁵ files). These
    # lines aren't the per-well "A01: N files" markers the GUI parses for
    # progress — they just surface that the scan is alive, so a big plate no
    # longer looks like a hang before processing begins.
    print(f"Scanning {search_dir} for image files…", flush=True)
    out: dict[str, list[str]] = {}
    examined = 0
    matched = 0
    for root, _, files in os.walk(search_dir):
        for fname in files:
            examined += 1
            if examined % 5000 == 0:
                print(f"…discovery: examined {examined} files "
                      f"({matched} image(s) matched a well so far)", flush=True)
            if not fname.lower().endswith((".tif", ".tiff")):
                continue
            extracted = _extract_well_from_filename(fname, schema, sep)
            if extracted is None:
                continue
            matched += 1
            out.setdefault(extracted, []).append(os.path.join(root, fname))
        if not recursive:
            break
    print(f"Discovery complete: examined {examined} file(s); {matched} image(s) "
          f"matched a well token across {len(out)} well(s). Grouping now…",
          flush=True)
    return out


def folder_well(
    well: str, files: list[str], output_dir: str,
    *, move: bool = False,
) -> int:
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

    A source that has vanished between the discovery walk and this copy —
    a partially-written acquisition file that got cleaned up, a file the
    user removed mid-run, or a network mount that dropped it — is skipped
    with a warning rather than aborting the grouping phase for the whole
    plate. Before this guard a single such file raised a raw
    ``FileNotFoundError`` out of ``shutil``, crashed the zipper subprocess
    with a non-zero exit, and took the other 95 wells down with it.

    Returns the number of files actually placed into the well folder.
    """
    if not files:
        return 0
    well_dir = os.path.join(output_dir, well)
    os.makedirs(well_dir, exist_ok=True)
    place = shutil.move if move else shutil.copy2
    placed = 0
    for f in files:
        try:
            place(f, os.path.join(well_dir, os.path.basename(f)))
        except FileNotFoundError:
            print(
                f"warning: {well}: source file no longer exists, skipping: {f}",
                file=sys.stderr, flush=True,
            )
            continue
        placed += 1
    print(f"{well}: {placed} files")
    return placed


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
    total_discovered = sum(len(v) for v in files_by_well.values())
    total_placed = 0
    for well in generate_wells():
        total_placed += folder_well(
            well,
            files_by_well.get(well, []),
            output_dir,
            move=args.move_source_tifs,
        )

    skipped = total_discovered - total_placed
    if skipped:
        # Surface the discrepancy loudly, but keep exit code 0: a handful of
        # vanished source files must not fail the run for the wells that did
        # group. The per-file warnings above name each one.
        print(
            f"warning: grouped {total_placed} of {total_discovered} discovered "
            f"image(s); {skipped} source file(s) vanished before they could be "
            f"placed (see warnings above).",
            file=sys.stderr, flush=True,
        )


if __name__ == "__main__":
    main()
