"""Single source of truth for the filename-schema vocabulary and its parser.

The schema is the colon-delimited description of how a raw image filename is
laid out — ``"experiment:channel:well:fov:timepoint"`` — and both entry points
that read raw filenames need to agree on it exactly:

* ``process_microscopy`` — the pipeline, which parses every input filename.
* ``well_plate_zipper`` — the pre-pass that groups loose TIFs into per-well
  folders, which the pipeline and the Analyze pane both shell out to.

They previously kept separate copies. ``parse_schema`` was character-identical;
``validate_schema`` reached the same verdict by different branches and reported
it with different wording, so the same bad schema produced two different CLI
messages depending on which entry point the user happened to invoke. The
zipper's copy carried a comment saying it "mirrors process_microscopy" — this
module makes that structural rather than aspirational.

Stdlib-only, so the pipeline can import it without breaching the "no
``well_viewer`` / ``widgets`` / Qt imports" contract from ARCHITECTURE §6.2 —
the same reason ``util.well_token`` lives here. Listed in
``services.remote_runner._REQUIRED_REMOTE_FILES`` so a remote run receives it.
"""

from __future__ import annotations


# The field names a schema token may take. Anything else parses to "ignore",
# which is what keeps an older schema string forward-compatible when a new
# field name is added later.
SCHEMA_FIELDS = ("experiment", "channel", "well", "fov", "timepoint", "ignore")

DEFAULT_SCHEMA = "experiment:channel:well:fov:timepoint"
DEFAULT_SEP = "_"


def parse_schema(schema_str: str) -> list[str]:
    """Parse a colon-delimited schema string into a list of field names.

    Unknown tokens become ``"ignore"`` rather than raising, so a schema string
    saved by an older version still loads when new field names appear. Empty
    tokens (from a trailing colon, say) are dropped.

    >>> parse_schema("experiment:channel:well:fov:timepoint")
    ['experiment', 'channel', 'well', 'fov', 'timepoint']
    """
    known = set(SCHEMA_FIELDS)
    return [
        tok if tok in known else "ignore"
        for tok in schema_str.strip().split(":")
        if tok
    ]


#: The fields the analysis pipeline cannot run without. ``channel`` says which
#: measurement a file holds, ``well`` which plate position it came from.
PIPELINE_REQUIRED_FIELDS = ("channel", "well")


def validate_schema(schema: list[str],
                    required: tuple[str, ...] = PIPELINE_REQUIRED_FIELDS) -> list[str]:
    """Return human-readable error messages for *schema*; empty means valid.

    Each name in *required* must appear exactly once; everything else is
    optional or repeatable. The default is what the analysis pipeline needs.

    A tool that reads fewer fields passes fewer: ``well_plate_zipper`` groups
    files by well and never looks at the channel slot — its own ``--filename_schema``
    help says so — yet it validated against the pipeline's pair, so a perfectly
    good ``well:fov`` was rejected with an error naming a field the tool does
    not use.
    """
    errors: list[str] = []
    for field in required:
        count = schema.count(field)
        if count != 1:
            errors.append(
                f'"{field}" must appear exactly once in the schema '
                f'(found {count}).'
            )
    return errors
