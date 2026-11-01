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
    """The layout of the user's input. There is one: the pipeline reads one
    ``{well}.zip`` per well, and staging refuses anything else before a run
    starts (``services.remote_staging``). Kept as a type on :class:`_RemoteRun`
    and ``ZippedInput`` rather than removed, because a run's record names its
    layout; it is never persisted, so no earlier value needs reading back.
    """

    ZIP = "zip"
    """Input is ``{well}.zip`` files; the run produces ``{well}_out.zip``
    files in ``output_dir`` and the Fetcher drops them in unchanged."""


def _canonical_well_from_path(stem: str) -> str | None:
    """Normalize a path stem like ``A1`` or ``A01`` to canonical
    ``A01``. Returns None if the stem isn't a 96-well token."""
    from util.well_token import canonical_well_label
    return canonical_well_label(stem)



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
    #: Wells an earlier attempt held for an end-of-run re-anchoring it never ran, taken into this run's ``keep/``
    #: (``remote_final_pass.adopt_earlier_holds``). In none of this run's batches and often never fetched by it,
    #: they are still this run's to release and pull again once its final pass re-anchors them.
    nanowell_adopted_holds: set[str] = field(default_factory=set)

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
    #: The host folder holding this run's wells' stored registration (``services.remote_registration``), passed to
    #: the host's pipeline as ``--nanowell_registration_from``; ``""`` when none was sent.
    registration_dir: str = ""
    #: Set once this run has launched the ssh that asks the scheduler for its
    #: own allocation. Until then the job named by ``<pidfile>.slurm`` — and the
    #: PID in ``<pidfile>`` — can only be somebody else's: ``run_id`` is
    #: deterministic in the plate and the settings, so a second session on the
    #: same plate reads the first session's files, and a run refused before it
    #: launched used to ``scancel`` the very job its refusal was protecting.
    #: :func:`services.remote_jobs.close_remote_job` and
    #: :meth:`RemoteRunner.cancel` act only when it is set.
    owns_remote_job: bool = False
    # Why each well the Fetcher declined to transfer was declined, ``well ->
    # reason``. "Why was this well not fetched?" was answerable only from
    # stdout as it streamed, which is no help once the log window is closed;
    # this is what the end-of-run reconciliation reads.
    skipped_wells: dict = field(default_factory=dict)
    #: ``well -> why`` for each well whose fetch fell short in a way the files on disk cannot show: an incremental
    #: merge that failed (the local CSV and zip are then the old ones, which also look whole), a CSV of several
    #: that did not arrive beside ones that did, a local archive that could not be read and was set aside.
    #: Verification fails every well named here until the end-of-run retry replaces it whole
    #: (``services.remote_fetch_verify``); the remote copy is kept meanwhile.
    fetch_shortfalls: dict = field(default_factory=dict)
