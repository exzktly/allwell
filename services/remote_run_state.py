"""The record one remote run carries from staging through fetch.

``_RemoteRun`` is read and written by the staging planner, the remote process
wrappers and the ``Fetcher`` alike, so it lives in none of them: each imports
it from here, and none has to import another to reach it. ``OutputFilter`` is
the part of that record the user chooses — what comes back from the remote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path



class OutputFilter(str, Enum):
    """What to bring back from the remote when a run completes."""

    FULL = "full"
    """Full equivalence — every file a local run would have produced."""

    CSV_ONLY = "csv-only"
    """Just the per-well CSVs and ``pipeline_info.json``. Also injects
    ``--no_save_tophat`` so the remote skips QC generation entirely."""



class LocalInputMode(str, Enum):
    """How the user's input would have been processed locally.

    Recorded up-front so the Fetcher can reconstruct an output tree that's
    indistinguishable from a local run.
    """

    ZIP = "zip"
    """Input was already ``{well}.zip`` files. Local zip-mode run would
    produce ``{well}_out.zip`` files in ``output_dir`` — Fetcher drops
    them in unchanged."""

    FOLDER = "folder"
    """Input was ``{well}/`` subfolders. Local run extracts the work into
    loose files in ``output_dir`` — Fetcher unzips each remote
    ``{well}_out.zip`` after fetch."""

    FLAT = "flat"
    """Input was loose TIFs in the root. Local run would compress them
    into per-well zips first (since ``compress_input_well_folders``
    defaults to True), then proceed identically to zip mode. Treated
    like ZIP from the Fetcher's perspective."""

    MIXED = "mixed"
    """Input has BOTH ``{well}.zip`` files AND ``{well}/`` folders for
    different wells — the signature of a previous run that died
    partway through ``compress_input_well_folders`` (or a manual
    half-conversion). v5.2.21: instead of silently picking ZIP mode
    and ignoring the remaining folders, treat the input as a resume
    point: trust the existing zips, and zip the leftover folders
    into the staging dir so the run includes every well."""


def _canonical_well_from_path(stem: str) -> str | None:
    """Normalize a path stem like ``A1`` or ``A01`` to canonical
    ``A01``. Returns None if the stem isn't a 96-well token."""
    from util.well_token import canonical_well_label
    return canonical_well_label(stem)



#: Said by Analyze and by the run record alike, so the two refusals cannot drift.
NANOWELL_REMOTE_RESUME_REFUSAL = (
    "This plate has results from an earlier run that did not finish, so this run would add to them incrementally, "
    "and a nanowell run cannot do that on a remote host yet. Tick Force to analyse the plate again in full on the "
    "remote, or run it on this computer.")

NANOWELL_REMOTE_INCREMENTAL_REFUSAL = (
    "An incremental nanowell run cannot run on a remote host yet: the remote would analyse the new timepoints "
    "without the earlier ones' roster and template, and its tables would replace yours. Run it incrementally "
    "on this computer, or run the remote plate in full (untick Incremental).")



def refuse_remote_nanowell_incremental(argv) -> None:
    """Raise when *argv* is an incremental nanowell run bound for a remote host.

    Called by the runner before anything is staged or sent, rather than only when the run record is built at the
    end of ``RemoteRunner._prepare_run`` (which still refuses too): by then the remote output had been cleared for a
    run that could not start (roll-up review X29).
    """
    argv = list(argv)
    if "--nanowell_format" in argv and "--incremental" in argv:
        raise ValueError(NANOWELL_REMOTE_INCREMENTAL_REFUSAL)

@dataclass
class _RemoteRun:
    """Internal handle: everything one in-flight run needs to track."""

    run_id: str
    cache_key: str
    remote_input_dir: str
    remote_output_dir: str
    remote_pidfile: str
    local_output_dir: Path
    local_input_mode: LocalInputMode
    #: The user's own input directory, as the local argv named it — not the
    #: staged upload, which for an incremental run holds only the new-FOV
    #: split. The end-of-run completeness check compares against it, and
    #: comparing against the split would report a plate as finished because
    #: only the part that was re-uploaded was checked.
    local_input_dir: Path | None = None
    # v5.2.29: tracked here so the verify step can check the right
    # local artifacts (CSV always; out_zip / extracted folder only
    # when the user opted into the full output set).
    output_filter: OutputFilter = OutputFilter.FULL
    expected_wells: set[str] = field(default_factory=set)
    fetched_wells: set[str] = field(default_factory=set)
    #: The file :meth:`RemoteRunner.request_suspend` touches, and the path the
    #: pipeline was given with ``--suspend_file``. Empty for a run that was
    #: never told one (a cache hit runs ``true``), which cannot be suspended.
    suspend_request: str = ""
    well_count_total: int = 0
    # Incremental remote run: fetched outputs are PARTIAL (new FOVs only), so the
    # Fetcher merges them into the existing local CSV / out-zip instead of
    # overwriting.
    incremental: bool = False
    # What the run was told to reprocess. Carried here because the decisions
    # that act on it are downstream of the staging planner, which is where the
    # intent used to stop: the Fetcher decides whether a local artefact is
    # replaced, and "it already exists locally" is precisely the wrong reason to
    # skip a well the user asked to have replaced.
    force: bool = False
    force_wells: frozenset[str] = frozenset()
    #: A nanowell run (``--nanowell_format`` on its argv). Its wells are revisited by the re-anchoring at the end of
    #: each pipeline invocation, which re-runs some of them from their input archives and earlier output, so a well
    #: is deleted from the remote when its batch ends rather than when it is fetched (``remote_fetch_nanowells``).
    nanowell: bool = False
    #: The nanowell wells whose remote files ``release_batch`` has already pulled again and deleted.
    nanowell_released: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        # A remote incremental run uploads only the new fields and analyses them alone, without the earlier
        # timepoints' roster, template or grid; a nanowell well would come back as only its new timepoints and
        # replace the local tables. Refused here as the backstop; Analyze refuses it before anything is staged.
        if self.nanowell and self.incremental:
            raise ValueError(NANOWELL_REMOTE_INCREMENTAL_REFUSAL)
    # Pre-run fingerprint of the local output artefacts, ``path -> (size,
    # mtime_ns)``. Verify compares against it so a run that was told to replace
    # a well's output has to prove it did, rather than proving the old file is
    # still there.
    pre_run_artifacts: dict = field(default_factory=dict)
    # Set when this run's output directory came from the journal rather than
    # from its own ``run_id`` (see ``_choose_remote_output_dir``). Cleanup has
    # to know: the run directory it would otherwise derive from ``run_id`` is
    # not the one holding this run's output, so removing that would report a
    # deletion while leaving the real outputs on the remote for ever — and the
    # directory it *did* delete belongs to a run this one only borrowed a name
    # from.
    reattached: bool = False
    # Why each well the Fetcher declined to transfer was declined, ``well ->
    # reason``. "Why was this well not fetched?" was answerable only from
    # stdout as it streamed, which is no help once the log window is closed;
    # this is what the end-of-run reconciliation reads.
    skipped_wells: dict = field(default_factory=dict)
