"""The remote client's line vocabulary: what it prints and what it listens for.

Everything here is a string, a pattern or a cadence that two sides of the remote
path have to agree on — the uploader and the ``Fetcher`` that emit transient
progress lines, ``services.pipeline_runner.ProgressTracker`` that routes them,
and the pipeline's own ``Well X — done.`` markers the ``Fetcher`` triggers on.
Stdlib-only and importing nothing else of the remote client's, so every module
on the remote path can read it without a cycle.
"""

from __future__ import annotations

import re


# Lines this module marks as *live state* rather than events: a transfer's
# bytes-so-far, rsync's own percentage stream. ``pipeline_runner`` routes them
# to the dashboard's status line and never writes them to the log.
#
# A log records things that happened, and "still transferring" is a sample of a
# continuous variable. One line per sample also evicted real history: the log
# holds 10 000 blocks, and ``analyze.tab._log_line`` carries a block of
# scroll-compensation whose own comment blames this stream for making that
# trimming common.
#
# Marked at the source with a prefix rather than sniffed by shape downstream —
# the per-file ``[remote] ↑ <well>.zip`` names look similar and are real events
# (they drive the Upload rail), so guessing between them would be fragile. It
# lives in this leaf module, which imports nothing of the remote client's, so the
# uploader, the ``Fetcher`` and ``pipeline_runner`` can all read it without a
# cycle.
TRANSIENT_PREFIX = "[remote] ⋯ "

# Per-well transfer progress, as ``<prefix><direction> <well> <fraction>`` —
# e.g. ``[remote] ⋯ xfer down C02 0.4180``. ``pipeline_runner.ProgressTracker``
# turns these into ``("well_xfer", (well, direction, fraction))`` events that
# fill the well's dial on the run dashboard's plate map.
#
# Deliberately built on ``TRANSIENT_PREFIX`` rather than beside it: a per-well
# transfer emits a sample every second or so, which is the same flood the
# transient marker exists to keep out of the log, and extending the existing
# prefix means the "never write this to the log" rule already covers it. The
# tracker recognises the longer marker first, so these never reach the status
# line as raw text either.
WELL_XFER_PREFIX = TRANSIENT_PREFIX + "xfer "

# How often a transfer writes a durable "still going" line to the log, as
# opposed to the live status updates it emits every ``interval`` seconds.
_BREADCRUMB_INTERVAL_S = 60.0

# How often a per-well transfer emits its dial fraction. Much faster than the
# status-line cadence — a dial that only moves every eight seconds reads as
# stuck — and cheap, since the emission is one formatted string that never
# touches the log.
_WELL_XFER_INTERVAL_S = 1.0

# Tail of the exhausted-budget message for a client-side transfer. The remote
# runner's knobs are different from the pipeline's (there is no ``--workers``
# here), so it carries its own wording rather than the generic one.
_TRANSFER_ADVICE = (
    "A repeated failure on the same file usually means one of the volumes "
    "involved is a network mount that is dropping its session under load. "
    "Keep the input and output folders on local storage for the run and copy "
    "the results across afterwards, or re-run with the plate mounted over a "
    "steadier link."
)

# Markers the pipeline's stdout emits; reused here so the Fetcher can
# trigger on the same lines the GUI's ProgressTracker already parses.
#
# v5.2.17: the actual line emitted by ``process_microscopy.py`` is
# ``log.info("Well %s — done.", well_label)``, which the basicConfig
# formatter renders as ``2026-05-27 10:30:45  INFO      Well A01 —
# done.`` — i.e. the line does NOT start with the well label, it
# starts with the timestamp. The previous regex anchored on ``^`` and
# was matched (not searched), so it never fired in production. As a
# result the Fetcher never pulled any per-well outputs and the user
# saw "rsync never completes". Match with ``.search()`` against the
# "Well A01 — done." substring instead. Also support the legacy bare
# ``A01 — done.`` form just in case anything still emits it.
#
# The separator is matched as a generic ``\s+\S+\s+`` token rather than the
# literal " — " so a per-well download still triggers if the em-dash is mangled
# in transit (a UTF-8 remote stream decoded with a cp1252 Windows locale arrives
# as "Well A01 â€" done."). Without this the Fetcher never fired per-well during
# the run — no download progress, and the out dir only filled from the
# end-of-run backstop.
# One or two digits: the pipeline logs whatever label the input carried, so a
# plate zipped as ``C8.zip`` announces "Well C8 — done." A two-digit-only
# pattern never matched that, the well never entered ``expected_wells``, and
# it was therefore never fetched — the same class of mismatch
# ``_canonical_force_wells`` exists to prevent on the other side of this
# comparison. The label is canonicalised at the match site.
_RE_WELL_DONE = re.compile(r"(?:\bWell\s+)?(?P<well>[A-Ha-h]\d{1,2})\s+\S+\s+done\.")
_RE_MODE_WELLS = re.compile(r"(?:Zip mode|Flat mode|Folder mode):\s+(\d+)\s+well")


def _well_xfer_line(well: str, direction: str, fraction: float) -> str:
    """Format one per-well transfer-progress line (see ``WELL_XFER_PREFIX``).

    ``direction`` is ``"up"`` or ``"down"``; ``fraction`` is clamped to 0..1.
    """
    frac = max(0.0, min(1.0, float(fraction)))
    return f"{WELL_XFER_PREFIX}{direction} {well} {frac:.4f}\n"
