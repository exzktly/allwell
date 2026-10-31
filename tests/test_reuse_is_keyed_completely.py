"""A decision to reuse earlier work must be keyed on everything that invalidates it.

This is the guard for the defect class that shipped, unnoticed, in the run
journal: a predicate that decides "we already did this, use the old result"
whose key covered *less* than the set of things that make the old result wrong.
The consequence is not a crash. It is a run that quietly returns stale numbers
and reports success — the one outcome CLAUDE.md §7 says must never be
indistinguishable from a good one.

The concrete case: `stored_remote_address` handed back a remote output
directory keyed on the pipeline arguments alone. The upload cache key that
triggers the lookup churns for **two** reasons — staging rewrote the archives
(same images, new files) or the user replaced the images — and the argv cannot
tell them apart. So a plate re-exported with corrected images, with no setting
touched and Force not ticked, re-attached to the previous run's directory, was
found complete by the pre-flight check, never ran the pipeline, and had the old
results fetched back as its own.

Two things are pinned here, and the split matters because only one of them is
mechanically decidable.

**Every declared key is load-bearing.** Change one alone and the lookup must
decline. A key that is recorded but not compared is decoration, and it reads in
review as protection that is not there. This half is enforced below,
exhaustively, by mutating each key in turn.

**Nothing outside the keys can invalidate the result.** That half no test can
decide for you — it is a claim about the domain. What this file does is make the
claim *explicit*: `REUSE_CONTRACTS` names the keys and, beside each, the reason
it exists. Adding a reuse decision without adding it here is the omission to
catch in review; adding it with a key whose justification you cannot write down
is the signal that you have not finished thinking about what makes the old
result wrong.
"""

from __future__ import annotations

import pytest

from services import run_journal as J
from util.settings_digest import KEYED_ON
from tests._repo_scan import parse_source

#: Every "reuse earlier work" decision in the app, the keys it compares, and why
#: each key is required. One entry per decision; the reason is not a comment, it
#: is the part a reviewer checks.
REUSE_CONTRACTS = {
    "run_journal.stored_remote_address": {
        "argv_digest": (
            "the pipeline arguments decide the bytes of the output, so a "
            "differently-configured run's results are not interchangeable"),
        "source_key": (
            "fingerprints the user's own input archives. The upload cache key "
            "churns both when staging rewrites and when the images change; "
            "only this separates them, and without it a re-exported plate "
            "reuses the results of the images it replaced. It folds in the "
            "acquisition record and in/nanowell_anchors.json too: an anchor "
            "set by hand changes a nanowell well's addresses with no image "
            "touched, so a re-run after the click must not re-attach to the "
            "run that left the well unaddressed"),
        "host_alias": (
            "the address is a path on one machine. Two hosts can share a "
            "filesystem, so a path recorded against one resolves against the "
            "other and its listing succeeds — which is why the machine has to "
            "be compared rather than inferred from the directory being there. "
            "A 'gpu'/'cpu' pair of entries for the same cluster, differing "
            "only in compute_launcher or extra_args, is the live case"),
    },
    # The pipeline's own two "we already did this" predicates —
    # `well_skip.output_exists_for_well` and the incremental done-set — both
    # consult one digest, so they are one decision here. Its keys are
    # `util.settings_digest.KEYED_ON` *by reference* rather than restated:
    # a second copy would be a second definition of what makes two runs
    # interchangeable, and the reason each key is in it is written there.
    "settings_record.well_settings_allow_reuse": dict(KEYED_ON),
    # The packed-file converters' resume markers. A conversion publishes
    # each `<dest>/in/<WELL>.zip` atomically, so a cancelled run leaves whole,
    # correct wells and only the rest to do — worth hours on a plate. What
    # makes finishing them dangerous is that two *different* mappings of one
    # source write identical filenames: nothing on disk distinguishes an
    # A01.zip made from scene 0 from one made from scene 7, so only the marker
    # can tell a run that may carry on from one that must refuse. The keys are
    # named per converter rather than shared, because they resolve wells from
    # different inputs and a field that decides the mapping for one is absent
    # from another: only the CZI converter can widen a file to its folder, and
    # only the OME-TIFF one splits a flat frame axis or takes a clicked well
    # order.
    "ome_import.resume_state": {
        "grid_rows": "the imaged block's height; a different one re-labels "
                     "every position after the first row",
        "grid_cols": "the imaged block's width, for the same reason",
        "origin": "which well position 0 is. Off by one well here shifts the "
                  "whole plate and the well count stays right",
        "direction": "whether the stage swept rows or columns; the same "
                     "positions land on a transposed set of wells",
        "serpentine": "snake or raster. A raster read as a snake labels every "
                      "second line backwards, again with the right count",
        "fovs_per_well": "how many positions each well takes, which decides "
                         "where every well after the first begins",
        "frame_order": "how a packed file's flat frame axis splits into "
                       "positions and timepoints — the same frames become "
                       "different images under each reading",
        "experiment": "the first token of every member name, so two values "
                      "produce two disjoint sets of filenames in one archive",
        "well_order": "an explicit click-defined acquisition path, which "
                      "replaces the grid outright when it is set",
        "timepoint_stride": (
            "how many of the timelapse's timepoints are written. The "
            "dangerous direction is the quiet one: a stride-3 run's member "
            "set is a strict SUBSET of a stride-1 run's, and "
            "``well_is_complete`` tests ``issubset``, so without this key a "
            "well already filled by a complete run would be judged done and "
            "left alone while every freshly-converted well beside it held a "
            "third as many timepoints. The plate would go ragged with nothing "
            "raising"),
        "timepoint_subset": (
            "which of the timelapse's timepoints are written — the stride's "
            "danger again, by hand: a run over t0..t5 has a member set that is "
            "a strict SUBSET of a whole-timelapse run's, so without this key a "
            "well filled by the wider run would pass ``well_is_complete`` and "
            "be left holding timepoints its neighbours do not. Recorded only "
            "when set, so a marker written before the field existed still "
            "matches a run that leaves it empty"),
    },
    "czi_import.resume_state": {
        "well_source": "whether the wells came from the file's scene names or "
                       "from the grid. The same file yields two entirely "
                       "different plates under the two",
        "use_folder": "whether the run converts the selected file alone or "
                      "every .czi beside it as one plate. It is the difference "
                      "between a one-well dataset and a ninety-six-well one "
                      "from the same selection, and the marker's own source "
                      "fingerprint cannot see it — that names the file the "
                      "user picked, which is identical either way. The "
                      "converter's signature carries a fingerprint of the "
                      "whole set for the same reason, so a sibling added or "
                      "re-exported between two runs declines the resume too",
        "grid_rows": "the imaged block's height, when the grid is what maps",
        "grid_cols": "the imaged block's width, for the same reason",
        "origin": "which well position 0 is",
        "direction": "whether the stage swept rows or columns",
        "serpentine": "snake or raster",
        "fovs_per_well": "how many tiles each well takes",
        "experiment": "the first token of every member name",
        "timepoint_stride": (
            "how many of the timelapse's timepoints are written. The "
            "dangerous direction is the quiet one: a stride-3 run's member "
            "set is a strict SUBSET of a stride-1 run's, and "
            "``well_is_complete`` tests ``issubset``, so without this key a "
            "well already filled by a complete run would be judged done and "
            "left alone while every freshly-converted well beside it held a "
            "third as many timepoints. The plate would go ragged with nothing "
            "raising"),
        "timepoint_subset": (
            "which of the timelapse's timepoints are written — the stride's "
            "danger again, by hand: a run over t0..t5 has a member set that is "
            "a strict SUBSET of a whole-timelapse run's, so without this key a "
            "well filled by the wider run would pass ``well_is_complete`` and "
            "be left holding timepoints its neighbours do not. Recorded only "
            "when set, so a marker written before the field existed still "
            "matches a run that leaves it empty"),
    },
    "nd2_import.resume_state": {
        "well_source": "whether the wells came from the positions' own names "
                       "or from the grid. The same file yields two entirely "
                       "different plates under the two",
        "grid_rows": "the imaged block's height, when the grid is what maps",
        "grid_cols": "the imaged block's width, for the same reason",
        "origin": "which well position 0 is",
        "direction": "whether the stage swept rows or columns",
        "serpentine": "snake or raster",
        "fovs_per_well": "how many positions each well takes. Only a "
                         "grid-mapped file takes it from here — when the "
                         "positions name their wells the file has already "
                         "said — but it decides where every well after the "
                         "first begins whenever it does apply",
        "experiment": "the first token of every member name",
        "timepoint_stride": (
            "how many of the timelapse's timepoints are written. The "
            "dangerous direction is the quiet one: a stride-3 run's member "
            "set is a strict SUBSET of a stride-1 run's, and "
            "``well_is_complete`` tests ``issubset``, so without this key a "
            "well already filled by a complete run would be judged done and "
            "left alone while every freshly-converted well beside it held a "
            "third as many timepoints. The plate would go ragged with nothing "
            "raising"),
        "timepoint_subset": (
            "which of the timelapse's timepoints are written — the stride's "
            "danger again, by hand: a run over t0..t5 has a member set that is "
            "a strict SUBSET of a whole-timelapse run's, so without this key a "
            "well filled by the wider run would pass ``well_is_complete`` and "
            "be left holding timepoints its neighbours do not. Recorded only "
            "when set, so a marker written before the field existed still "
            "matches a run that leaves it empty"),
    },
    # A nanowell run's tile registration, reused from an earlier run's grid
    # record (``--nanowell_registration_from``, or the output folder's own).
    # Reusing it is only right when registering again would give the same
    # positions, so the key is everything registration reads.
    "nanowell_run.registration_key": {
        "REGISTRATION_SCHEME": (
            "the registration algorithm itself. A fix to tile_register or the "
            "per-tile lattice fits would otherwise go on serving the positions "
            "the broken one solved"),
        "record": (
            "the acquisition record's tile positions, tile size, pixel size and "
            "declared overlap: they seed the solve and bound its search window"),
        "pixels": (
            "overlap correlation and the lattice fits read the tiles. A plate "
            "re-exported with corrected images keeps its record, so without "
            "this it would keep the old images' registration"),
        "geometry": "decides how each tile's lattice is fitted (by contrast or at the format's diameter)",
        "nominal_pitch": "seeds the working pitch and the fits on a format or custom plate",
        "diameter": "the matched filter the hexagonal tile fits find sites with",
        "working_pitch": "the fits' seed and the overlap search window, measured on this timepoint",
        "kind": "the lattice kind (hex or square) an inferred geometry fits every tile as",
        "preferred": (
            "the tile offsets the well's other timepoints agree on, given when a timepoint is registered again "
            "because a pair sat a lattice step off (run_well's layout agreement). They change which whole lattice "
            "vector a pair takes, so a placement solved without them is the very one being corrected"),
    },
    "scan_cache.load": {
        "size": (
            "the CZI's byte count. What is being reused is everything the "
            "reader concluded from those bytes — the scenes, the wells, the "
            "tiles per scene, the channel names — so a file re-exported from "
            "ZEN with different settings would be converted under the "
            "previous export's plate map, with the right well count and the "
            "wrong wells in them"),
        "mtime_ns": (
            "the other half of 'this file changed'. Size alone misses a "
            "re-export that lands on the same byte count, which re-exporting "
            "one well of a plate with the settings untouched produces. "
            "Nanoseconds because a whole second is long enough for a writer "
            "to replace a file within one"),
        "read_times": (
            "whether the layout was read with its acquisition stamps. A "
            "layout read without them carries no ``time_scan`` at all, so "
            "serving one to the caller that asked would report a timelapse "
            "as having no elapsed times and name every image positionally"),
        "PARSER_VERSION": (
            "the thing being reused is not the file, it is "
            "``read_czi_layout``'s and ``parse_plate_metadata``'s *output* — "
            "a function of the bytes AND of the reader that parsed them. That "
            "reader is not stable: it has gained the missing-image count, the "
            "master-file drop, the pyramid exclusion and the scene-name well "
            "patterns. Without this key a fix to it keeps serving the answers "
            "the broken one gave, off every user's disk, with nothing saying "
            "so"),
        "FORMAT_VERSION": (
            "the on-disk shape, as distinct from the reader that filled it. A "
            "field added to CziLayout makes an older entry a *partial* answer "
            "rather than a wrong one, and a partial answer decoded into a "
            "full object takes that field's default — which is the silent "
            "substitution, not a crash"),
    },
    # ── the remote run's reuse decisions (tracker F8) ───────────────────────
    # Each is mutated key by key below (`test_a_remote_reuse_decision_declines_when_one_key_changes`), and where it
    # lives is held against a scan of the source (`DECISION_SITES`), not against this roster.
    "remote_finished_wells.finished_here": {
        "settings": "the well's settings marker against this run's settings key (argv plus the host's flags): a well "
                    "made under other settings is not this run's result",
        "per_cell_csv": "the well's measurements, present and not empty: nothing else this check reads is a result",
        "completeness_record": "the sidecar's record of the well, when there is one, whole: a well wound down part "
                               "way, or with failed groups, writes a CSV and a settings marker too",
        "force": "--force asks for every well to be computed again",
        "force_wells": "a well named in --force_wells is to be computed again",
        "incremental": "an incremental run decides per field in staging; a whole well is never skipped here",
        "field_coverage": "every (fov, timepoint) the well's input holds now is in its CSV: an archive that gained a "
                          "field or a timepoint since is not finished",
        "nanowell_coverage": "a nanowell well's grid record holds a verdict for every timepoint its archive holds",
        "nanowell_anchors": "a nanowell well made under other anchors than the ones set now — an anchor set, moved or "
                            "cleared since — is to be run again: the anchors are not among the settings",
        "nanowell_confluence_reference": "a nanowell well whose confluence was read against another empty reference "
                                         "than the one saved here now (one measured, re-exported or put right since) "
                                         "is to be run again: the settings name the reference well, not what it holds",
        "nanowell_reference_sent": "when the reference well is itself sent, the host measures it before every other "
                                   "well, so no well read against the reference as it is here can be left out",
        "blank": "an empty-well blank is the correction's input, never a well left out",
    },
    "nanowell_task.confluence_reference_current": {
        "template": "the reference's template at the timepoint, by content: a re-exported reference gives another "
                    "template and every well read against the old one must be read again. By content rather than by "
                    "file, because each save of the reference rewrites its .npz files with a new timestamp; the "
                    "incremental path (`nanowell_task._prior_state`) takes the same key per timepoint",
        "measured": "whether the reference was measured in this folder at all: a well read before it was (a missing or "
                    "failed reference) has no confluence and must be read again once it is",
        "verdict": "the reference's verdict at the timepoint: one judged loaded gives no template, so every well's "
                   "confluence there is blank; judged empty since, they must be read again",
    },
    "remote_batch_resume.take_from_remote": {
        "argv_digest": "an earlier attempt is taken from only when it was configured the same way",
        "source_key": "and computed from the same images: a re-exported plate's earlier attempt is not this one's",
        "host_alias": "and recorded on this host: a path on another machine may resolve here and mean nothing",
        "completion_seal": "a well is adopted only when its archive and its completion seal are both there: an "
                           "archive alone is what a well left part way leaves",
        "force": "--force adopts nothing: everything is to be computed again",
        "force_wells": "a forced well is not adopted, per well; the rest of the attempt still is",
    },
    "remote_recover.recover_finished_outputs": {
        "argv_digest": "a recorded directory is downloaded from only when configured the same way",
        "source_key": "and computed from the same images",
        "host_alias": "and on this host",
        "force": "--force downloads nothing it is about to recompute",
        "force_wells": "nor does a run that forces wells (its pull is verification's, which cannot leave one out)",
        "completion_seal": "only a sealed well is downloaded as finished",
    },
    "remote_run_id._pipeline_launch_is_pointless": {
        "force": "a forced run lands in the same directory as the run it repeats, which looks complete",
        "force_wells": "the same for named wells",
        "incremental": "an incremental run's upload is a split an archive's existence says nothing about",
        "every_uploaded_well_finished": "the pipeline is replaced by `true` only when every uploaded well — not as "
                                        "many wells — is finished there, sealed",
    },
    "pipeline_runner.PipelineRunner._nothing_left_to_upload": {
        "staging_left_wells_out": "only a run whose staging left wells out (or an incremental one) can be empty",
        "only_blanks_staged": "a staged well that is not an empty-well blank is work to send",
        "blank_finished_here": "the blank is skipped only when it is itself finished here (finished_here)",
    },
    "well_skip.finished_output": {
        "output": "the well's CSV and out.zip (or folder) present and not empty",
        "settings": "made under this run's settings (well_settings_allow_reuse)",
        "completion_seal": "sealed whole, or — output a build before the seal wrote — not withdrawn: a seal "
                           "withdrawn and never written again is a well left part way or died mid-rewrite",
        "completeness_record": "an unsealed well whose last completeness record ended short is run again",
    },
    "parquet_cache.read_cached": {
        "st_size": (
            "the CSV's byte count. The pipeline rewrites these files — an "
            "incremental run merges new rows into an existing well — and a "
            "cache that survived that serves the old plate's numbers under the "
            "new plate's name"),
        "st_mtime_ns": (
            "the other half of 'this file changed'. Size alone misses a "
            "rewrite that happens to land on the same byte count, which a "
            "re-run producing the same row count with different values does"),
        "PARSER_VERSION": (
            "the thing being reused is not the CSV, it is load_well_csv's "
            "*output* — a function of the file AND of _STRING_COLS, "
            "_CATEGORY_MAX_UNIQUE_SHARE, the per-channel column rename, the "
            "FOV normalisation, the Included default and the shape_circularity "
            "back-fill. Every one of those has changed at least once. Without "
            "it, adding a derived column means an untouched CSV keeps serving "
            "the frame from before the change: the column is simply absent, "
            "the dropdown offering it does not appear, and nothing says so"),
    },
}

#: What ``argv_digest`` has to cover, and why each item is not optional.
#: Not a second roster of keys — a note on the completeness of one of them,
#: because the digest is computed from the *local* argv and three of the flags
#: that decide the output bytes are appended after that, by the host.
ARGV_DIGEST_MUST_COVER = {
    "--no_save_tophat": (
        "added when the Fetch filter is CSV_ONLY. It drops the tophat QC "
        "images from the output zip, so a FULL run and a CSV_ONLY run do not "
        "produce interchangeable output"),
    "host.extra_args": (
        "arbitrary per-host pipeline flags — segmentation thresholds among "
        "them — so two hosts can compute different numbers from one form"),
    "--cpu_only": (
        "added for a CPU-only host, which changes the execution path"),
}


def _record(out_dir, **overrides):
    keys = {"argv_digest": "argv-1", "source_key": "src-1",
            "host_alias": "hpc"}
    keys.update(overrides)
    J.record_remote(
        out_dir, run_id="run-1", cache_key="k1",
        remote_input_dir="/staging/datasets/k1",
        remote_output_dir="/staging/runs/run-1/out", **keys)
    return keys


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")
    d = tmp_path / "out"
    d.mkdir()
    J.open_run(d, spec={}, mode="remote", input_dir=tmp_path / "in")
    return d


#: Calling one of these makes the caller a site where earlier work is reused (or declined): the journal's recorded
#: addresses, the one definition of a finished well, the settings check, and the two whole-run predicates.
REUSE_PRIMITIVES = frozenset({
    "stored_remote_address", "stored_remote_addresses", "recorded_remote_attempts",
    "finished_wells", "finished_on_remote", "finished_on_this_host", "finished_here",
    "_pipeline_launch_is_pointless", "well_settings_allow_reuse", "confluence_reference_current",
})

#: Every site the scan finds in the run's code (``services/``, ``pipeline/``, ``process_microscopy.py``), and the
#: registered decision it belongs to. Derived from the source by :func:`reuse_sites`, so the check below compares the
#: roster with the code rather than with itself (tracker F8): a new caller of a reuse primitive fails until it is
#: either a registered decision's or named in :data:`NOT_A_REUSE_DECISION` with a reason.
DECISION_SITES = {
    "pipeline/nanowell_task.py::_prior_state": "settings_record.well_settings_allow_reuse",
    "process_microscopy.py::process_well_zip_task": "settings_record.well_settings_allow_reuse",
    "pipeline/well_skip.py::finished_output": "well_skip.finished_output",
    "process_microscopy.py::process_well_zips": "nanowell_task.confluence_reference_current",
    "services/remote_finished_wells.py::_read_against_the_reference": "remote_finished_wells.finished_here",
    "pipeline/run_report.py::finished_on_this_host": "well_skip.finished_output",
    "services/remote_finished_wells.py::finished_here": "remote_finished_wells.finished_here",
    "services/pipeline_runner.py::PipelineRunner._finished_here": "remote_finished_wells.finished_here",
    "services/remote_process.py::_BatchedRemoteProcess._run_batches": "remote_finished_wells.finished_here",
    "services/pipeline_runner.py::PipelineRunner._only_measured_blanks":
        "pipeline_runner.PipelineRunner._nothing_left_to_upload",
    "services/remote_batch_resume.py::take_from_remote": "remote_batch_resume.take_from_remote",
    "services/remote_batch_resume.py::find_leftovers": "remote_batch_resume.take_from_remote",
    "services/remote_recover.py::_candidate_dirs": "remote_recover.recover_finished_outputs",
    "services/remote_recover.py::_recover_from": "remote_recover.recover_finished_outputs",
    "services/remote_reuse.py::preflight_cache_hit": "remote_run_id._pipeline_launch_is_pointless",
    "services/remote_reuse.py::reattach_output_dir": "run_journal.stored_remote_address",
    "services/remote_batch_resume.py::reattach_output_dir": "run_journal.stored_remote_address",
    "services/run_journal.py::stored_remote_addresses": "run_journal.stored_remote_address",
}

#: Sites that call a reuse primitive and reuse nothing themselves, each with what it does instead.
NOT_A_REUSE_DECISION = {
    "pipeline/run_report.py::wells_left_part_way": "names the wells a run must *resume*; it reuses no output",
    "services/remote_finished_wells.py::RemoteOutputs.finished": "the definition of a finished well itself; its "
                                                                 "callers are the decisions",
    "services/remote_batch_resume.py::background_aliases": "widens which settings digests a checkpoint may carry; "
                                                           "the checkpoint's reuse is the pipeline's settings check",
    "services/remote_final_pass.py::_earlier_run_dirs": "where held archives may be; a held well is re-anchored, "
                                                        "never reused as a result",
    "services/remote_reuse.py::clean_earlier_attempts": "deletes earlier attempts; reuses nothing",
    "services/remote_sweep.py::earlier_attempt_dirs": "lists earlier attempts' run directories so their jobs are "
                                                      "swept before this run writes; reuses nothing",
}


def reuse_sites() -> set[str]:
    """``path::qualname`` of every function in the run's code that calls a :data:`REUSE_PRIMITIVES` name."""
    import ast

    from tests._repo_scan import python_sources, rel

    found: set[str] = set()

    def visit(node, scope, path):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = (*scope, node.name)
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
            if name in REUSE_PRIMITIVES and scope:
                found.add(f"{path}::{'.'.join(scope)}")
        for child in ast.iter_child_nodes(node):
            visit(child, scope, path)

    for path in python_sources():
        r = rel(path)
        if r.startswith(("services/", "pipeline/")) or r == "process_microscopy.py":
            visit(parse_source(path), (), r)
    return found


def test_every_reuse_decision_is_registered():
    """A decision missing from the roster is one nobody checked the keys of — compared with the code, not with the
    roster: this test once asserted that eight hand-written names were in the hand-written roster they came from,
    and none of the remote run's five reuse decisions was among them."""
    found = reuse_sites()
    unregistered = sorted(found - set(DECISION_SITES) - set(NOT_A_REUSE_DECISION))
    assert not unregistered, (
        "these reuse earlier work (they call one of REUSE_PRIMITIVES) and belong to no registered decision: add "
        "each to DECISION_SITES under the decision whose keys it takes, or to NOT_A_REUSE_DECISION with why it "
        "reuses nothing:\n  " + "\n  ".join(unregistered))
    stale = sorted((set(DECISION_SITES) | set(NOT_A_REUSE_DECISION)) - found)
    assert not stale, "rostered sites that no longer call a reuse primitive:\n  " + "\n  ".join(stale)
    unknown = sorted({d for d in DECISION_SITES.values() if d not in REUSE_CONTRACTS})
    assert not unknown, f"sites filed under decisions with no keys registered: {unknown}"


def test_the_roster_names_every_key_the_decision_actually_takes():
    """The reverse direction, which the roster alone cannot give you.

    ``test_changing_one_key_alone_declines_the_reuse`` proves each *listed* key
    is compared. It says nothing about a key added later — one with a default
    value would be compared by the code, absent from the roster, and carry none
    of the written justification the roster exists to hold. Read off the
    signature, so adding a parameter without a reason to keep it fails here.
    """
    import inspect

    params = inspect.signature(J.stored_remote_address).parameters
    keyed = {name for name, p in params.items()
             if p.kind is inspect.Parameter.KEYWORD_ONLY}
    listed = set(REUSE_CONTRACTS["run_journal.stored_remote_address"])
    assert keyed == listed, (
        f"the decision takes {sorted(keyed)} but the roster names "
        f"{sorted(listed)}. Every key needs its written reason beside it — "
        f"that roster is the only record of what makes the old result wrong.")


def test_matching_every_key_reuses(out_dir):
    """The baseline. Without it, a lookup that declines unconditionally would
    pass every mutation test below while reusing nothing."""
    keys = _record(out_dir)
    assert J.stored_remote_address(out_dir, **keys) is not None


@pytest.mark.parametrize(
    "key", sorted(REUSE_CONTRACTS["run_journal.stored_remote_address"]))
def test_changing_one_key_alone_declines_the_reuse(out_dir, key):
    """Each key, mutated by itself, with the others held identical.

    Mutating one at a time is the point: a lookup that compared only the first
    key would still decline when *everything* changed, and a test that changed
    everything at once would call that protection.
    """
    keys = _record(out_dir)
    changed = dict(keys, **{key: keys[key] + "-changed"})
    assert J.stored_remote_address(out_dir, **changed) is None, (
        f"{key} is recorded but not compared. It exists because "
        f"{REUSE_CONTRACTS['run_journal.stored_remote_address'][key]}.")


@pytest.mark.parametrize(
    "key", sorted(REUSE_CONTRACTS["run_journal.stored_remote_address"]))
def test_an_unknown_key_on_either_side_declines(out_dir, key):
    """An empty key is "I could not determine this", never "it matched".

    ``source_fingerprint`` returns ``""`` for an input directory it cannot
    read, and a record written before a key existed carries ``""`` for it. Both
    are the absence of evidence, and treating absence as agreement is how a
    reuse decision fires in exactly the situation nobody understood.
    """
    keys = _record(out_dir, **{key: ""})
    assert J.stored_remote_address(out_dir, **dict(keys, **{key: ""})) is None
    assert J.stored_remote_address(out_dir, **dict(keys, **{key: "x"})) is None


# ── the pipeline's own reuse decision ───────────────────────────────────────
#
# Registering a decision in ``REUSE_CONTRACTS`` above is not, by itself, a
# test: every parametrize in this file names ``stored_remote_address``
# explicitly, so an entry added beside it generates nothing and reads in review
# as protection that is not there — the failure mode CLAUDE.md §7.3 opens with.
# These drive the second decision through the same two questions.

_SETTINGS_KEY = "settings_record.well_settings_allow_reuse"


def _settings_out(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    return out


def test_matching_settings_reuse_the_well(tmp_path):
    """The baseline, for the same reason the remote one has one."""
    from pipeline.settings_record import (
        record_well_settings, well_settings_allow_reuse)
    from util.settings_digest import settings_digest

    out = _settings_out(tmp_path)
    key = settings_digest({"segmentation_method": "stardist_nuclei"})
    record_well_settings(out, "A01", key)
    assert well_settings_allow_reuse(out, "A01", key)


@pytest.mark.parametrize("key", sorted(REUSE_CONTRACTS[_SETTINGS_KEY]))
def test_changing_one_setting_alone_declines_the_reuse(tmp_path, key):
    """Every declared key, mutated by itself, driven through the real decision.

    ``tests/test_reuse_is_keyed_on_the_settings.py`` proves each key moves the
    digest. This proves the decision that reads the digest back off disk then
    declines — the two are separate functions in separate modules, and the
    digest moving is worth nothing if the comparison does not happen.
    """
    from pipeline.settings_record import (
        record_well_settings, well_settings_allow_reuse)
    from util.settings_digest import settings_digest

    base = {"segmentation_method": "stardist_nuclei", "nuclear_token": "NIR",
            "fluor_tokens": ["GFP"], "filename_sep": "_"}
    if key.startswith("nanowell_") and key != "nanowell_format":
        base["nanowell_format"] = "100um"   # the nanowell keys count only on a nanowell run
    if key.startswith("cellpose_"):
        base["segmentation_method"] = "cellpose"   # and the Cellpose keys only on a Cellpose run
    # A value that differs from the baseline's for this key, whatever its type.
    was = base.get(key)
    other = "___changed___" if was is None or isinstance(was, str) else None
    if isinstance(was, list):
        other = [*was, "___changed___"]
    changed = dict(base, **{key: other})

    out = _settings_out(tmp_path)
    record_well_settings(out, "A01", settings_digest(base))

    assert not well_settings_allow_reuse(out, "A01", settings_digest(changed)), (
        f"{key} is in KEYED_ON but the well was reused across a change to it, "
        f"so the old numbers stand under a sidecar advertising the new value. "
        f"It is keyed because: {REUSE_CONTRACTS[_SETTINGS_KEY][key]}.")


def test_an_unrecorded_well_is_grandfathered_deliberately(tmp_path):
    """The one place this decision differs from the remote one, on purpose.

    ``stored_remote_address`` treats an unknown key as a refusal. This treats an
    unrecorded well as acceptable, because refusing would force every user to
    reprocess every plate they have. The cost — a plate written before this
    record existed keeps the old behaviour until its next forced run — is
    stated in the manual, and pinned here so it cannot be changed silently in
    either direction.
    """
    from pipeline.settings_record import well_settings_allow_reuse
    from util.settings_digest import settings_digest

    out = _settings_out(tmp_path)
    assert well_settings_allow_reuse(
        out, "A01", settings_digest({"segmentation_method": "cellpose"}))


# ── the completeness of one key, rather than the roster of keys ─────────────

def _host(**kw):
    from services.remote_config import RemoteHost
    base = dict(
        name="test", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0,
    )
    base.update(kw)
    return RemoteHost(**base)


def _local_argv():
    """The argv ``build_pipeline_args`` really produces, not a sketch of it.

    A hand-written five-flag argv carries none of the flags the form always
    emits, so a host override that *collides* with one — the shape that broke
    the pairing — could not arise in it. Fixtures have to carry the mess of the
    real thing (CLAUDE.md §7.1 #4).
    """
    from pathlib import Path

    from services.pipeline_service import build_pipeline_args

    return build_pipeline_args(
        Path("/p/process_microscopy.py"), Path("/in"), Path("/out"),
        {"nuclear_token": "DAPI", "fluor_tokens": ["GFP"], "csv_prefix": "m",
         "filename_schema": "well_fov_channel", "filename_sep": "_",
         "segmentation_method": "stardist_nuclei"})


@pytest.mark.parametrize("covered", sorted(ARGV_DIGEST_MUST_COVER))
def test_the_digest_covers_what_the_host_adds_to_the_argv(covered):
    """Each host-side addition, alone, must move the digest and the run id.

    ``argv_digest`` is computed from the local argv, and ``_build_remote_argv``
    appends these afterwards — so before this they were invisible to both the
    reuse key and the remote address. A run with a different segmentation
    threshold found the previous run's outputs complete and fetched them back.
    """
    from services.remote_runner import (
        OutputFilter,
        RemoteRunner,
    )
    from services.remote_run_id import (
        _compute_run_id,
        pipeline_argv_digest,
    )

    plain = RemoteRunner(_host())
    variants = {
        "--no_save_tophat": (RemoteRunner(_host()), OutputFilter.CSV_ONLY),
        "host.extra_args": (
            RemoteRunner(_host(extra_args=("--min_nucleus_area_px", "500"))),
            OutputFilter.FULL),
        "--cpu_only": (RemoteRunner(_host(cpu_only=True)), OutputFilter.FULL),
    }
    other, other_filter = variants[covered]

    base = plain._host_argv_extras(OutputFilter.FULL)
    changed = other._host_argv_extras(other_filter)
    assert base != changed, f"{covered} did not reach _host_argv_extras"

    why = ARGV_DIGEST_MUST_COVER[covered]
    assert (pipeline_argv_digest(_local_argv(), extras=base)
            != pipeline_argv_digest(_local_argv(), extras=changed)), (
        f"{covered} does not move the reuse key, and it must: {why}")
    assert (_compute_run_id("k" * 32, _local_argv(), extras=base)
            != _compute_run_id("k" * 32, _local_argv(), extras=changed)), (
        f"{covered} does not move the remote address, and it must: {why}")


def test_the_launched_command_and_the_digest_read_the_same_extras():
    """One definition, two callers — the shape §7.5 #5 exists for.

    If ``_build_remote_argv`` grew a flag the digest did not, the two would
    disagree again in exactly the direction that reuses stale output.
    """
    from services.remote_runner import LocalInputMode, OutputFilter, RemoteRunner

    # The override deliberately names a flag ``build_pipeline_args`` always
    # emits, so its value token collides with one already in the argv.
    runner = RemoteRunner(_host(cpu_only=True,
                                extra_args=("--min_nucleus_area_px", "500")))
    extras = runner._host_argv_extras(OutputFilter.CSV_ONLY)
    launched = runner._build_remote_argv(
        list(_local_argv()), "/r/in", "/r/out", OutputFilter.CSV_ONLY,
        LocalInputMode.ZIP)
    # A *subsequence*, not a set of members. A membership test passes on a
    # command whose flag/value pairs have come apart — every token is present
    # and `--min_nucleus_area_px 500` has become a bare `500` that argparse
    # rejects — which is exactly what the first version of this test waved
    # through.
    # De-duplicated first: `_host_argv_extras` can name a bare switch twice
    # when a host lists `--cpu_only` in `extra_args` *and* sets `cpu_only`, and
    # `_build_remote_argv` correctly emits it once. The invariant is about the
    # flags reaching the command, not about their multiplicity.
    wanted, seen = [], set()
    for e in extras:
        if e in _DEDUPED and e in seen:
            continue
        seen.add(e)
        wanted.append(e)
    it = iter(launched)
    matched = [e for e in wanted if any(tok == e for tok in it)]
    assert matched == wanted, (
        f"the digest folds in {wanted}, which the launched command does "
        f"not carry in order — it matched only {matched}.\n"
        f"launched tail: {launched[-10:]}")


def test_the_launched_command_still_parses_with_a_host_override():
    """The end of that: a command the pipeline's own parser accepts.

    The two shapes a host's ``extra_args`` really takes are overriding a flag
    ``build_pipeline_args`` always emits, and the ``--workers 0`` in
    ``remote_config``'s own docstring. Both put a token in the extras that
    already appears in the argv, and a blanket de-duplication dropped it and
    left the value stranded.
    """
    import subprocess
    import sys
    from pathlib import Path

    from services.pipeline_service import build_pipeline_args
    from services.remote_runner import LocalInputMode, OutputFilter, RemoteRunner

    opts = {"nuclear_token": "DAPI", "fluor_tokens": ["GFP"], "csv_prefix": "m",
            "filename_schema": "well_fov_channel", "filename_sep": "_",
            "segmentation_method": "stardist_nuclei"}
    runner = RemoteRunner(_host(cpu_only=True,
                                extra_args=("--min_nucleus_area_px", "500")))
    local = build_pipeline_args(Path("/p/pm.py"), Path("/in"), Path("/out"), opts)
    out = runner._build_remote_argv(list(local), "/r/in", "/r/out",
                                    OutputFilter.CSV_ONLY, LocalInputMode.ZIP)

    # The override must survive as a pair, and it must be the last word on the
    # flag — a host override that lands before the form's own value is not an
    # override.
    assert out[-3:] == ["--min_nucleus_area_px", "500", "--cpu_only"], out[-6:]

    start = next(i for i, a in enumerate(out) if a == "--input_dir")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).parent.parent /
                             "process_microscopy.py"), *out[start:], "--help"],
        capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"the launched command does not parse: {proc.stderr.strip()[-200:]}")


# ── the half-swept sibling ──────────────────────────────────────────────────

#: The two functions that turn a configuration into an address or a reuse key.
#: Every call to either in ``services/remote_runner.py`` must fold in the
#: host-side additions, because a call that does not produces a *smaller* key
#: than its siblings — and a smaller key matches configurations it should not.
_KEYING_CALLS = ("_compute_run_id", "pipeline_argv_digest")

#: The host additions that are bare switches, mirrored from
#: ``RemoteRunner._DEDUPED_HOST_FLAGS``. A host may name one in ``extra_args``
#: *and* set the field that produces it, so the extras list can carry it twice
#: while the command carries it once.
_DEDUPED = frozenset({"--no_save_tophat", "--cpu_only"})


def test_every_keying_call_folds_in_the_host_extras():
    """One call site out of four is enough to re-open the whole defect.

    The first fix updated `spawn`'s two calls and `_BatchedRemoteProcess`'s
    run id, and missed the journal record forty-five lines below it. That made
    the batched digest the smaller key: it equalled a plain FULL run's exactly,
    so a later FULL run re-attached to a batched CSV-only run's directory, was
    found complete, and had output with no tophat images fetched back as its
    own. In the other direction — a host with overrides — the two never matched
    and re-attach silently never fired at all.

    CLAUDE.md §7.5 #5: convert every sibling that answers the same question.
    A grep finds the ones you thought of; this finds the ones you did not.

    It checks the argument's *value*, not merely that the keyword is present:
    ``extras=()`` re-introduced the identical defect and walked past the first
    version of this guard along with 878 other tests.
    """
    import ast
    from pathlib import Path

    source_path = Path(__file__).parent.parent / "services" / "remote_runner.py"
    tree = parse_source(source_path)

    def _is_extras_call(node) -> bool:
        return (isinstance(node, ast.Call)
                and (getattr(node.func, "attr", "")
                     or getattr(node.func, "id", "")) == "_host_argv_extras")

    def _names_holding_extras(fn) -> set[str]:
        """Locals in *fn* bound from ``_host_argv_extras``, plus its own params.

        ``spawn`` binds ``host_extras`` once and passes it twice; and
        ``_compute_run_id`` forwards its own ``extras`` parameter through to
        ``pipeline_argv_digest``. Both are correct, and a guard that could not
        see them would be answered by weakening it rather than by fixing code.
        """
        names = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and _is_extras_call(n.value):
                names.update(t.id for t in n.targets
                             if isinstance(t, ast.Name))
        return names

    # Which enclosing function each call sits in, so a name can be resolved.
    enclosing = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ok_names = _names_holding_extras(fn)
            for n in ast.walk(fn):
                enclosing.setdefault(n, ok_names)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", "")
        if name not in _KEYING_CALLS:
            continue
        extras = next((kw.value for kw in node.keywords if kw.arg == "extras"),
                      None)
        if extras is None:
            offenders.append(
                f"remote_runner.py:{node.lineno} — {name}(…) has no extras=")
            continue
        # The *value*, not just the keyword. `extras=()` types the argument and
        # folds in nothing, so it re-introduces the exact defect this guard was
        # written for — and it did: with that one token, all 878 tests across
        # every file touching this module passed. A guard that proves a keyword
        # was typed proves nothing about the key being complete (§7.5 #5, the
        # sibling spelled differently).
        known = enclosing.get(node, set())
        if not (_is_extras_call(extras)
                or (isinstance(extras, ast.Name) and extras.id in known)):
            offenders.append(
                f"remote_runner.py:{node.lineno} — {name}(…, extras="
                f"{ast.unparse(extras)}) does not come from "
                f"_host_argv_extras(...)")

    assert not offenders, (
        "A call that decides a remote address or a reuse key does not fold in "
        "`RemoteRunner._host_argv_extras`. It therefore produces a smaller key "
        "than its siblings, and a smaller key matches configurations whose "
        "output is not interchangeable — which is how a run fetches another "
        "configuration's numbers and reports success.\n  "
        + "\n  ".join(offenders))


# ── the reuse predicate, driven directly ────────────────────────────────────
#
# Four critical slips in this predicate pass the end-to-end remote harness,
# because that harness exercises `spawn`'s control-file plumbing rather than
# the decisions behind it. These are pure functions and need no harness at all,
# which is exactly why there is no excuse for them being untested: they are the
# highest-stakes decision in the module, and every one of them returns a
# previous run's numbers and reports success when it goes wrong.

def test_a_forced_run_never_re_attaches():
    """`--force` means "compute it again", and re-attaching defeats that.

    A forced run that lands in the previous run's directory finds it full of
    that run's output, the pre-flight check calls the plate complete, the
    pipeline never launches, and yesterday's numbers come back as a successful
    forced re-run — the one outcome a user pressing Force is explicitly trying
    to rule out.
    """
    from services.remote_run_id import _choose_remote_output_dir

    fresh, stored = "/stg/runs/run-new/out", "/stg/runs/run-old/out"
    chosen, _reason = _choose_remote_output_dir(
        fresh, stored, forced=True, remote_has_outputs=True)
    assert chosen == fresh, (
        "a forced run re-attached to a populated directory; its outputs would "
        "be found complete and the pipeline would never run")


def test_an_unforced_run_re_attaches_to_the_same_directory():
    """The other half, so the test above cannot pass by never re-attaching."""
    from services.remote_run_id import _choose_remote_output_dir

    fresh, stored = "/stg/runs/run-new/out", "/stg/runs/run-old/out"
    chosen, reason = _choose_remote_output_dir(
        fresh, stored, forced=False, remote_has_outputs=True)
    assert chosen == stored and reason


def test_an_empty_recorded_directory_is_not_re_attached_to():
    """A recorded address says where work was *sent*, not that it is there."""
    from services.remote_run_id import _choose_remote_output_dir

    fresh, stored = "/stg/runs/run-new/out", "/stg/runs/run-old/out"
    chosen, _reason = _choose_remote_output_dir(
        fresh, stored, forced=False, remote_has_outputs=False)
    assert chosen == fresh


#: What this configuration hashes to. Pinned as a literal because re-attach
#: reads a digest journalled by an **earlier launch**, so the instability that
#: kills it is *cross-process* — and two calls inside one interpreter cannot
#: see that. A `hash()` or `id()` of an unordered container anywhere in the
#: filtered-argv path (what an innocuous "order-insensitive" refactor to
#: `frozenset` produces) gives a different digest every launch, re-attach dies
#: silently, and every run re-uploads and recomputes with no error anywhere.
#:
#: Changing this literal deliberately is a real decision, not a test fix: it
#: orphans every address already journalled on a user's machine.
#:
#: The literal moved once, when the digest stopped hashing the launcher. It had
#: been folding in `sys.executable` and the local path to
#: `process_microscopy.py`, so it was not one value at all — it differed
#: between machines, between a frozen bundle and a source run, and after any
#: interpreter change. The pin was therefore *machine-specific*, and passed
#: here while failing in CI; `test_the_launcher_does_not_reach_the_digest`
#: below is the guard that can see the defect a pin cannot.
_PINNED_DIGEST = "ed34655eac9f2335"
_PINNED_RUN_ID = "kkkkkkkkkkkkkkkk-ed34655eac9f2335"


def test_the_keys_are_the_same_on_every_launch():
    """The keys must be *discriminating*, not merely different every time.

    A digest that changed on every call would satisfy every test above and make
    re-attach dead — which is how the feature shipped unreachable once already.
    """
    from services.remote_run_id import (
        _compute_run_id,
        pipeline_argv_digest,
    )

    argv, key = _local_argv(), "k" * 32
    assert pipeline_argv_digest(argv, extras=["--cpu_only"]) == _PINNED_DIGEST
    assert _compute_run_id(key, argv, extras=["--cpu_only"]) == _PINNED_RUN_ID


def test_the_launcher_does_not_reach_the_digest():
    """How this machine starts the pipeline is not a property of the run.

    `build_pipeline_args` opens the argv with `sys.executable` and then either
    the local path to `process_microscopy.py` or, in a frozen bundle, the
    `--run-pipeline` sentinel. None of it reaches the worker: the remote runs
    its own interpreter against its own uploaded copy of the script. Hashing it
    keyed the reuse decision on facts that cannot change a byte of the output,
    so an identical plate re-attached or did not depending on whether the user
    launched the .app or ran from source, on where the checkout lived, and on
    which Python was on PATH — each of them re-uploading the whole plate and
    recomputing it with nothing logged.

    The pinned literal above cannot see this, because a pin computed from
    `sys.executable` is itself machine-specific: it passed on the machine that
    wrote it and failed in CI. This test compares the shapes directly.
    """
    from services.remote_run_id import pipeline_argv_digest

    flags = ["--input_dir", "/in", "--nuclear_token", "DAPI",
             "--segmentation_method", "stardist_nuclei"]
    source = ["/usr/local/bin/python3", "/home/me/allwell/process_microscopy.py",
              *flags]
    frozen = ["/Applications/All-Well.app/Contents/MacOS/allwell",
              "--run-pipeline", *flags]
    other_machine = ["/opt/hostedtoolcache/Python/3.11.16/x64/bin/python",
                     "/somewhere/else/process_microscopy.py", *flags]

    digests = {name: pipeline_argv_digest(argv) for name, argv in
               (("source", source), ("frozen", frozen),
                ("other machine", other_machine))}
    assert len(set(digests.values())) == 1, (
        "the same configuration hashes differently depending on how the "
        f"pipeline was launched, so re-attach dies on any change to it: "
        f"{digests}")


#: What each definition of the argv digest gives one argv that carries a flag every definition disagrees about —
#: the current one first, then ``remote_run_id.EARLIER_ARGV_DEFINITIONS`` in order. Pinned per build, as the
#: settings digest's earlier definitions are (tracker F7): a change to which flags count must append the definition it
#: replaces to that roster, and its literal here, or every address journalled before it stops matching in silence.
_PINNED_PER_DEFINITION = ("ed34655eac9f2335", "47a674a74375043b", "0ec0017dc8cc630a")


def test_every_earlier_argv_definition_is_pinned_and_searched():
    from services.remote_run_id import EARLIER_ARGV_DEFINITIONS, _digest, legacy_argv_digests, pipeline_argv_digest

    argv = [*_local_argv(), "--workers", "6", "--force_wells", "A01", "B02", "--no_auto_threshold"]
    extras = ["--cpu_only"]
    got = (pipeline_argv_digest(argv, extras=extras),
           *(_digest(argv, extras, d.skip_with_value, d.skip_flags, d.skip_multi) for d in EARLIER_ARGV_DEFINITIONS))
    assert got == _PINNED_PER_DEFINITION, (
        "a definition of the argv digest changed without its predecessor joining EARLIER_ARGV_DEFINITIONS (and its "
        f"literal joining this pin): {got}")
    assert set(got[1:]) <= set(legacy_argv_digests(argv, extras=extras)), (
        "an earlier definition's digest is not searched, so the attempts it recorded are never found")


# ── the per-well frame cache ────────────────────────────────────────────────
#
# Same discipline as the two above, and for the same reason: an entry in the
# roster generates no test by itself. Each key here is mutated alone and the
# read must decline.

_CACHE_KEY = "parquet_cache.read_cached"


def _cached_plate(tmp_path):
    """A well whose frame has been parsed once and cached."""
    import csv as _csv

    from review.data.data_loading import read_well_frame

    path = tmp_path / "measurements_A01.csv"
    with path.open("w", newline="") as fh:
        writer = _csv.writer(fh)
        writer.writerow(["well", "fov", "timepoint_hours", "area_px"])
        for i in range(50):
            writer.writerow(["A01", "1", 0.0, 100.0 + i])
    read_well_frame(path)
    return path


def test_a_matching_key_reuses_the_cached_frame(tmp_path):
    """The baseline. Without it, a read that declined unconditionally would
    pass every mutation below while reusing nothing — which is exactly what a
    review of this cache found, one line and the whole lane still green."""
    pytest.importorskip("pyarrow")
    from review.data import parquet_cache as pc

    plate = _cached_plate(tmp_path)
    assert pc.read_cached(plate) is not None


def test_a_changed_size_declines(tmp_path):
    pytest.importorskip("pyarrow")
    from review.data import parquet_cache as pc

    plate = _cached_plate(tmp_path)
    with plate.open("a") as fh:
        fh.write("A01,1,0.0,999.0\n")
    assert pc.read_cached(plate) is None, REUSE_CONTRACTS[_CACHE_KEY]["st_size"]


def test_a_changed_mtime_at_the_same_size_declines(tmp_path):
    """A re-run that produces the same row count with different values."""
    pytest.importorskip("pyarrow")
    from review.data import parquet_cache as pc

    plate = _cached_plate(tmp_path)
    body = plate.read_text().replace("100.0", "900.0")
    assert len(body) == len(plate.read_text()), "the fixture changed the size"
    plate.write_text(body)
    assert pc.read_cached(plate) is None, (
        REUSE_CONTRACTS[_CACHE_KEY]["st_mtime_ns"])


def test_a_changed_parser_version_declines(tmp_path, monkeypatch):
    """The half no file-stat can see: the same bytes parsing to a new frame."""
    pytest.importorskip("pyarrow")
    from review.data import parquet_cache as pc

    plate = _cached_plate(tmp_path)
    monkeypatch.setattr(pc, "PARSER_VERSION", pc.PARSER_VERSION + 1)
    assert pc.read_cached(plate) is None, (
        REUSE_CONTRACTS[_CACHE_KEY]["PARSER_VERSION"])


# ── the CZI header scan's on-disk cache ────────────────────────────────────
#
# Each key is watched declining in ``test_the_scan_is_remembered_on_disk.py``,
# against a real plate and counting the files that get opened. What belongs
# here is the reverse direction the roster exists for: that the set of keys the
# code compares is the set with a written reason beside it.

def test_the_scan_cache_compares_exactly_the_keys_it_declares():
    """Read off the code rather than restated, so a key added without a reason
    fails here and a reason left behind for a key nobody compares does too."""
    import inspect

    from pipeline import scan_cache

    listed = set(REUSE_CONTRACTS["scan_cache.load"])
    source = inspect.getsource(scan_cache.load) + inspect.getsource(
        scan_cache._read_file) + inspect.getsource(scan_cache._entry_for)
    for key in listed:
        assert key in source, (
            f"{key!r} has a written reason in the roster and appears nowhere "
            f"in the code that decides the reuse — either it is not compared, "
            f"or it is spelled differently and nobody is checking what the "
            f"reason describes")
    # ``read_times`` is compared by being part of the in-memory layout key
    # rather than by name in the entry, so it is named in the roster and
    # exercised end to end rather than grepped for here.
    assert listed == {"size", "mtime_ns", "read_times", "PARSER_VERSION",
                      "FORMAT_VERSION"}


# ── the converters' resume markers ─────────────────────────────────────────
#
# Each converter's own test file proves that changing one declared key alone
# declines the resume (``test_ome_import_compute``, ``test_czi_import_compute``,
# ``test_nd2_import_compute``). What those cannot prove is the *reverse*
# direction, because they parametrise over the roster itself: delete a field
# from ``_MAPPING_FIELDS`` and the parametrize simply stops generating a case
# for it, the lane stays green, and a setting that changes the mapping is no
# longer part of the key. That is the roster-against-a-roster problem
# CLAUDE.md §7.5 #10 names, and this is where it is held against the thing it
# is a list of: the converter's own settings dataclass.

#: Settings a converter carries that deliberately do not key its resume, with
#: the reason. An exemption is a row with a written reason, never an absence —
#: a field left out on purpose and one nobody remembered look identical in code
#: six months later.
MAPPING_EXEMPTIONS = {
    "workers": ("the size of the per-well thread pool. It changes how fast "
                "the conversion goes and nothing about what it writes, so "
                "refusing a resume over it would refuse one that was safe"),
    "well_subset": (
        "which wells this run converts. It filters the well *indices* walked "
        "and never the mapping, so a converted well's archive is byte-identical "
        "to the one a whole-plate run would have written — verified by "
        "comparing the two. The signature's question is 'which image lands "
        "under which filename', and the selection does not change that "
        "answer for any file it produces.\n\n"
        "Keying on it would refuse the workflow the setting exists for: "
        "convert six wells today, the rest next week, one dataset. That "
        "top-up is instead permitted by `convert_marker.DONE_MARKER`, which "
        "records the completed run's mapping so a second run can tell its own "
        "dataset from a stranger's plate pointed at the same folder — the "
        "narrower check, on the thing that really does have to match."),
}


@pytest.mark.parametrize("decision,module_path", [
    ("ome_import.resume_state", "datasets.ome_import.ome_import_compute"),
    ("czi_import.resume_state", "datasets.czi_import.czi_import_compute"),
    ("nd2_import.resume_state", "datasets.nd2_import.nd2_import_compute"),
])
def test_the_roster_names_every_setting_that_decides_the_mapping(
        decision, module_path):
    """Read off the settings dataclass, so a new setting fails here by default.

    A converter setting is either part of the key or exempted in writing.
    Adding one and forgetting both is how a resume comes to accept a folder it
    should refuse, and the symptom is a plate that looks complete.
    """
    import dataclasses
    import importlib

    module = importlib.import_module(module_path)
    fields = {f.name for f in dataclasses.fields(module.ConversionParams)}
    keyed = set(module._MAPPING_FIELDS)
    listed = set(REUSE_CONTRACTS[decision])

    assert keyed == listed, (
        f"{module_path} keys on {sorted(keyed)} but the roster names "
        f"{sorted(listed)}. Every key needs its written reason beside it.")
    unaccounted = fields - keyed - set(MAPPING_EXEMPTIONS)
    assert not unaccounted, (
        f"{module_path}.ConversionParams carries {sorted(unaccounted)}, which "
        f"neither keys the resume nor appears in MAPPING_EXEMPTIONS. If the "
        f"setting changes which image lands under which filename it belongs in "
        f"_MAPPING_FIELDS; if it does not, say so in MAPPING_EXEMPTIONS.")
    assert set(MAPPING_EXEMPTIONS) <= fields, (
        "MAPPING_EXEMPTIONS names a setting no converter has any more — an "
        "exemption that stopped describing a real decision.")


# ── nanowell_run.registration_key ───────────────────────────────────────────

_REGISTRATION = "nanowell_run.registration_key"


def _registration_inputs():
    """Small but complete inputs to ``registration_key``: two tiles of one timepoint of one well."""
    import numpy as np

    from pipeline import nanowell_run as nr

    record = {"tiles": [{"well": "A01", "timepoint": "0h", "field": f, "mosaic_xy_px": [x, 0], "stage_xy_um": None}
                        for f, x in (("F0", 0), ("F1", 90))],
              "timepoints": {"0h": {"tile_size_px": {"x": 100, "y": 100}, "pixel_size_um": {"x": 1.0, "y": 1.0},
                                    "declared_overlap_percent": 10.0}}}
    images = {f: np.full((100, 100), k + 1.0, dtype=np.float32) for k, f in enumerate(("F0", "F1"))}
    return {"record": record, "images": images, "settings": nr.NanowellSettings("custom", custom=(172.0, 100.0)),
            "pitch_um": 170.0, "kind": "auto"}


def _changed(name: str, inputs: dict, monkeypatch) -> dict:
    """*inputs* with the one registration input *name* changed and every other left as it was."""
    from dataclasses import replace

    from pipeline import nanowell_run as nr

    out = dict(inputs)
    if name == "REGISTRATION_SCHEME":
        monkeypatch.setattr(nr, "REGISTRATION_SCHEME", nr.REGISTRATION_SCHEME + 1)
    elif name == "record":
        out["record"] = {**inputs["record"], "tiles": [dict(t) for t in inputs["record"]["tiles"]]}
        out["record"]["tiles"][1]["mosaic_xy_px"] = [91, 0]
    elif name == "pixels":
        out["images"] = {f: im.copy() for f, im in inputs["images"].items()}
        out["images"]["F1"][50, 50] += 1.0
    elif name == "geometry":
        out["settings"] = replace(inputs["settings"], geometry=nr.GEOMETRY_INFER)
    elif name == "nominal_pitch":
        out["settings"] = replace(inputs["settings"], custom=(160.0, 100.0))
    elif name == "diameter":
        out["settings"] = replace(inputs["settings"], custom=(172.0, 90.0))
    elif name == "working_pitch":
        out["pitch_um"] = 171.0
    elif name == "kind":
        out["kind"] = "square"
    elif name == "preferred":
        out["preferred"] = {("F0", "F1"): (90.0, 0.0)}
    else:
        raise AssertionError(f"no mutation written for {name!r}: add one beside its reason in REUSE_CONTRACTS")
    return out


def _registration_key(inputs: dict) -> str:
    from pipeline import nanowell_run as nr

    return nr.registration_key(inputs["record"], "A01", "0h", inputs["images"], inputs["settings"],
                               inputs["pitch_um"], inputs["kind"], preferred=inputs.get("preferred"))


@pytest.mark.parametrize("key", sorted(REUSE_CONTRACTS[_REGISTRATION]))
def test_changing_one_registration_input_alone_changes_the_key(key, monkeypatch):
    inputs = _registration_inputs()
    before = _registration_key(inputs)
    after = _registration_key(_changed(key, inputs, monkeypatch))
    assert before != after, (
        f"registration_key ignores {key!r}, so a placement solved from other inputs would be reused. It is keyed "
        f"because: {REUSE_CONTRACTS[_REGISTRATION][key]}.")


@pytest.mark.parametrize("setting, value", [
    ("cell_diameter_um", 11.0), ("margin_um", 3.0), ("occluded_below", 0.3), ("clipped_below", 0.7),
    ("square_edge_deg", 5.0)])
def test_segmentation_and_measurement_settings_leave_the_registration_key_alone(setting, value):
    """The point of the feature: these act after registration, so changing them must not cost a re-registration."""
    from dataclasses import replace

    inputs = _registration_inputs()
    other = {**inputs, "settings": replace(inputs["settings"], **{setting: value})}
    assert _registration_key(other) == _registration_key(inputs)


def test_a_stored_placement_is_taken_only_under_its_own_key_and_for_the_same_tiles():
    from pipeline import nanowell_run as nr

    tiles = [{"tile": "F0", "left_um": 0.0, "top_um": 0.0, "source": "lattice", "residual_um": 0.1,
              "correction_um": 0.2},
             {"tile": "F1", "left_um": 90.25, "top_um": -0.5, "source": "overlap", "residual_um": 0.3,
              "correction_um": 0.4}]
    block = {"tiles": tiles, "registration": {"key": "k1", "warnings": ["w"], "tile_lattice_fits": 1}}
    old = {"tiles": tiles}                                          # written before registration was keyed
    taken = nr.stored_placement([("/old", old), ("/a", {**block, "registration": {**block["registration"],
                                                                                   "key": "k0"}}),
                                 ("/b", block)], "k1", ["F1", "F0"])
    assert taken == nr.Placement(tuple(tiles), ("w",), "k1", 1, "/b")
    assert taken.positions() == {"F0": (0.0, 0.0), "F1": (90.25, -0.5)}
    assert nr.stored_placement([("/b", block)], "k1", ["F0", "F1", "F2"]) is None   # another grid of tiles
    assert nr.stored_placement([("/old", old)], "k1", ["F0", "F1"]) is None


# ── the remote run's reuse decisions, key by key (tracker F8) ───────────────
#
# Each decision is built in a state where it reuses (the baseline, asserted first so a decision that always declines
# cannot pass), then one key is changed and it must decline. The fixtures are real files and, for the remote ones, a
# real directory under bash standing in for the host; only verification's pull is given.

import json as _json
import subprocess as _subprocess
import types as _types
import zipfile as _zipfile
from pathlib import Path as _Path

_SCHEMA = "experiment:channel:well:fov:timepoint"


def _opts(**extra) -> dict:
    return {"filename_schema": _SCHEMA, "filename_sep": "_", "fluor_tokens": ["GFP"], "nuclear_token": "DAPI",
            "csv_prefix": "measurements", **extra}


def _field_plate(tmp_path, opts: dict):
    """A01 measured in full under *opts*: its archive's two fields, its CSV, its settings marker."""
    from pipeline.cli_args import settings_key_from_argv
    from pipeline.settings_record import record_well_settings
    from services.pipeline_service import build_pipeline_args

    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    out.mkdir()
    with _zipfile.ZipFile(inp / "A01.zip", "w") as zf:
        for fov in (1, 2):
            for ch in ("DAPI", "GFP"):
                zf.writestr(f"e_{ch}_A01_{fov}_T01.tif", b"x")
    (out / "measurements_A01.csv").write_text("well,fov,timepoint\nA01,1,T01\nA01,2,T01\n")
    argv = build_pipeline_args(_Path("process_microscopy.py"), inp, out, opts)
    record_well_settings(out, "A01", settings_key_from_argv(argv[2:]))
    if opts.get("nanowell_format"):
        (out / "nanowells").mkdir()
        (out / "nanowells" / "A01_grid.json").write_text(_json.dumps({"timepoints": {"T01": {"status": "ok"}}}))
    return inp, out, argv


def _finished_here_case(tmp_path, key: str) -> tuple[list, list]:
    """``(baseline, mutated)`` answers of ``finished_here`` for A01."""
    from services.remote_finished_wells import argv_forcing, finished_here

    nanowell = key.startswith("nanowell_")
    referenced = key in ("nanowell_confluence_reference", "nanowell_reference_sent")
    opts = _opts(**({"nanowell_format": "100um", "phase_token": "DAPI",
                     "segmentation_method": "cellpose_labelfree"} if nanowell else {}),
                 **({"nanowell_empty_well": "H12"} if referenced else {}))
    inp, out, argv = _field_plate(tmp_path, opts)
    if referenced:                       # A01 read while the reference was not measured here, and said so
        _stamp_reference_key(out, "none:unmeasured")
    baseline = finished_here(out, argv, ["A01"])
    keep: tuple = ()
    if key == "settings":
        from pipeline.settings_record import record_well_settings

        record_well_settings(out, "A01", "0123456789abcdef")
    elif key == "per_cell_csv":
        (out / "measurements_A01.csv").unlink()
    elif key == "completeness_record":
        (out / "pipeline_info.json").write_text(_json.dumps({"run_report": {"wells": {
            "A01": {"groups_total": 2, "groups_covered": 1, "aborted_reason": "suspended"}}}}))
    elif key == "force":
        argv = [*argv, "--force"]
    elif key == "force_wells":
        argv = argv_forcing(argv, ["A01"])
    elif key == "incremental":
        argv = [*argv, "--incremental"]
    elif key == "field_coverage":
        with _zipfile.ZipFile(inp / "A01.zip", "a") as zf:
            for ch in ("DAPI", "GFP"):
                zf.writestr(f"e_{ch}_A01_3_T01.tif", b"x")
    elif key == "nanowell_coverage":
        (out / "nanowells" / "A01_grid.json").write_text(_json.dumps({"timepoints": {}}))
    elif key == "nanowell_anchors":
        from util.nanowell_anchors import Anchor, anchors_path, write_anchors

        write_anchors(anchors_path(inp), {("A01", "T01"): Anchor("A01", "T01", 10.0, 20.0)})
    elif key == "nanowell_confluence_reference":
        _save_reference(out, _reference_template())                 # measured since A01 was read without one
    elif key == "nanowell_reference_sent":
        return baseline, finished_here(out, argv, ["A01", "H12"])  # H12 has no output here: it is sent
    elif key == "blank":
        keep = ("A01",)
    else:
        raise AssertionError(f"no mutation written for {key!r}: add one beside its reason in REUSE_CONTRACTS")
    return baseline, finished_here(out, argv, ["A01"], keep=keep)


def _bash_cache():
    from services.remote_transport import _remote_join

    def run(script):
        done = _subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True, timeout=20)
        return _subprocess.CompletedProcess(["bash"], done.returncode, done.stdout, done.stderr)

    return _types.SimpleNamespace(_ssh_script=lambda script, *, check: run(script),
                                  _ssh_run=lambda argv, *, check, **k: run(_remote_join(argv)))


def _sealed_on(out_dir: _Path, well: str) -> None:
    from pipeline.run_report import complete_seal_path

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{well}_out.zip").write_bytes(b"zip")
    (out_dir / f"measurements_{well}.csv.gz").write_bytes(b"rows")
    complete_seal_path(out_dir, well).parent.mkdir(exist_ok=True)
    complete_seal_path(out_dir, well).write_text(_json.dumps({"well": well}))


def _journal_keys_case(tmp_path, monkeypatch, key: str, lookup) -> tuple[list, list]:
    """``(baseline, mutated)`` of a journal lookup *lookup(out, digests, source, host)* with one key changed."""
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = tmp_path / "local"
    out.mkdir()
    J.open_run(out, spec={}, mode="remote-batched", input_dir="/in", argv=["--x"])
    keys = _record(out)
    baseline = lookup(out, [keys["argv_digest"]], keys["source_key"], keys["host_alias"])
    changed = {**keys, key: f"{keys[key]}-other"}
    return baseline, lookup(out, [changed["argv_digest"]], changed["source_key"], changed["host_alias"])


def _take_from_remote_case(tmp_path, monkeypatch, key: str) -> tuple[list, list]:
    """The batched resume's adoption from an earlier attempt: ``(baseline, mutated)`` wells adopted."""
    from services.remote_batch_resume import take_from_remote

    if key in ("argv_digest", "source_key", "host_alias"):
        return _journal_keys_case(tmp_path, monkeypatch, key, lambda out, d, s, h: J.recorded_remote_attempts(
            out, argv_digests=d, source_key=s, host_alias=h))

    def adopt(argv, *, seal=True):
        old = tmp_path / f"remote-{len(list(tmp_path.iterdir()))}" / "runs" / "run-old"
        _sealed_on(old / "out", "A01")
        if not seal:
            (old / "out" / ".aw_complete" / "A01.json").unlink()
        monkeypatch.setattr(J, "recorded_remote_attempts", lambda *a, **k: [
            {"remote_output_dir": str(old / "out"), "remote_input_dir": str(old / "in")}])
        monkeypatch.setattr(J, "previous_well_states", lambda *_a: {})
        run = _types.SimpleNamespace(remote_output_dir=str(old.parent / "run-new" / "out"))
        proc = _types.SimpleNamespace(
            _journal_keys=dict(argv_digest="d", source_key="s", host_alias="h"), _legacy_digests=(),
            _local_output_dir=tmp_path, _pipeline_argv=argv, _run_id="run-new", _log=lambda m: None,
            _fetcher=_types.SimpleNamespace(run=_types.SimpleNamespace(nanowell=False)),
            _runner=_types.SimpleNamespace(_cache=_bash_cache(), _run=run,
                                           host=_types.SimpleNamespace(remote_staging_dir=str(old.parent.parent))))
        return take_from_remote(proc, [_Path("A01.zip")], force="--force" in argv)[1]

    baseline = adopt([])
    if key == "completion_seal":
        return baseline, adopt([], seal=False)
    if key == "force":
        return baseline, adopt(["--force"])
    if key == "force_wells":
        return baseline, adopt(["--force_wells", "A01"])
    raise AssertionError(f"no mutation written for {key!r}: add one beside its reason in REUSE_CONTRACTS")


def _recover_case(tmp_path, monkeypatch, key: str):
    """The pre-staging download: ``(baseline, mutated)`` wells it would bring home."""
    from services import remote_recover as rr
    from services.remote_fetch import FetchedOutputVerifier, FetchVerification
    from services.remote_run_state import OutputFilter

    if key in ("argv_digest", "source_key", "host_alias"):
        return _journal_keys_case(tmp_path, monkeypatch, key, lambda out, d, s, h: J.stored_remote_addresses(
            out, argv_digests=d, source_key=s, host_alias=h))
    monkeypatch.setattr(FetchedOutputVerifier, "verify", lambda self, ok, *, log: FetchVerification(passed=True))
    local = tmp_path / "local"
    local.mkdir(exist_ok=True)
    remote = tmp_path / "remote" / "runs" / "run-old" / "out"
    _sealed_on(remote, "A01")
    runner = _types.SimpleNamespace(host=_host(), _cache=_bash_cache(), _host_argv_extras=lambda f: [])
    argv = ["python", "pm.py", "--input_dir", str(tmp_path / "in"), "--output_dir", str(local)]

    def pulled(argv, *, unseal=False):
        if unseal:
            (remote / ".aw_complete" / "A01.json").unlink()
        if key in ("force", "force_wells") and argv is not None and _forced(argv):
            monkeypatch.setattr(J, "read_journal", lambda *_a: object())
            monkeypatch.setattr(rr, "_candidate_dirs", lambda *a, **k: [str(remote)])
            return sorted(rr.recover_finished_outputs(runner, local, argv, output_filter=OutputFilter.FULL)
                          .missing_locally)
        return sorted(rr._recover_from(runner, str(remote), local, argv, OutputFilter.FULL, log=lambda m: None,
                                       stop=__import__("threading").Event()).missing_locally)

    baseline = pulled(argv)
    if key == "completion_seal":
        return baseline, pulled(argv, unseal=True)
    if key == "force":
        return baseline, pulled([*argv, "--force"])
    if key == "force_wells":
        return baseline, pulled([*argv, "--force_wells", "A01"])
    raise AssertionError(f"no mutation written for {key!r}: add one beside its reason in REUSE_CONTRACTS")


def _forced(argv) -> bool:
    return "--force" in argv or "--force_wells" in argv


def _launch_case(key: str):
    from services.remote_run_id import _pipeline_launch_is_pointless as pointless

    argv = ["python", "pm.py", "--nuclear_token", "DAPI"]
    baseline = pointless(argv, 2, {"A01", "B02"}, uploaded={"A01", "B02"})
    mutated = {
        "force": lambda: pointless([*argv, "--force"], 2, {"A01", "B02"}, uploaded={"A01", "B02"}),
        "force_wells": lambda: pointless([*argv, "--force_wells", "B02"], 2, {"A01", "B02"}, uploaded={"A01", "B02"}),
        "incremental": lambda: pointless(argv, 2, {"A01", "B02"}, uploaded={"A01", "B02"}, incremental=True),
        # As many finished wells as uploaded, but not the uploaded ones.
        "every_uploaded_well_finished": lambda: pointless(argv, 2, {"A01", "C03"}, uploaded={"A01", "B02"}),
    }
    if key not in mutated:
        raise AssertionError(f"no mutation written for {key!r}: add one beside its reason in REUSE_CONTRACTS")
    return baseline, mutated[key]()


def _nothing_left_case(tmp_path, key: str):
    """``(baseline, mutated)`` of ``_nothing_left_to_upload`` for a plate whose only staged well is its blank B02."""
    import services.pipeline_runner as pr

    opts = _opts(background="empty_well", background_well="B02")
    inp, out, argv = _field_plate(tmp_path, opts)
    (out / "measurements_A01.csv").rename(out / "measurements_B02.csv")
    from pipeline.cli_args import settings_key_from_argv
    from pipeline.settings_record import record_well_settings

    record_well_settings(out, "B02", settings_key_from_argv(argv[2:]))
    with _zipfile.ZipFile(inp / "B02.zip", "w") as zf:
        for fov in (1, 2):
            for ch in ("DAPI", "GFP"):
                zf.writestr(f"e_{ch}_B02_{fov}_T01.tif", b"x")
    r = pr.PipelineRunner()
    r._remote_runner = _types.SimpleNamespace(_host_argv_extras=lambda f: [])

    def ask(well_zips=(inp / "B02.zip",), up_to_date=("A01",)):
        zipped = _types.SimpleNamespace(well_zips=tuple(well_zips), up_to_date_wells=tuple(up_to_date),
                                        incremental=False)
        return r._nothing_left_to_upload(zipped, opts, out, argv)

    baseline = ask()
    if key == "staging_left_wells_out":
        return baseline, ask(up_to_date=())
    if key == "only_blanks_staged":
        return baseline, ask(well_zips=(inp / "B02.zip", inp / "A01.zip"))
    if key == "blank_finished_here":
        (out / "measurements_B02.csv").unlink()
        return baseline, ask()
    raise AssertionError(f"no mutation written for {key!r}: add one beside its reason in REUSE_CONTRACTS")


_REFERENCE_SPEC = {"plate_format": "100um", "empty_well": "H12"}


def _reference_template(texture_cut: float = 0.1, level: float = 1.0):
    import numpy as np

    from pipeline import nanowell_measure as nm

    return nm.Template(np.full((5, 5), level), 2, "own", 10, 12, texture_cut, (0, 1))


def _save_reference(out, template, status: str = "empty"):
    """Save H12 as the empty reference in *out*, its 0h and T01 judged *status*, the way the reference well's run
    does (``nanowell_reference.save``)."""
    from pipeline import nanowell_reference as nref
    from pipeline.nanowell_task import settings_from_kwargs

    verdict = {"status": status, "loaded_share": 0.0, "nanowells_counted": 50, "max_loaded_share": 0.05}
    nref.save(out, "H12", settings_from_kwargs({"_nanowell": _REFERENCE_SPEC}),
              {tp: (template, verdict) for tp in ("0h", "T01")})


def _stamp_reference_key(out, key: str, timepoint: str = "T01") -> None:
    from pipeline import nanowell_reference as nref

    path = out / "nanowells" / "A01_grid.json"
    path.parent.mkdir(exist_ok=True)
    grid = _json.loads(path.read_text()) if path.is_file() else {"timepoints": {}}
    grid["timepoints"].setdefault(timepoint, {"status": "ok"})[nref.KEY_FIELD] = key
    path.write_text(_json.dumps(grid))


def _reference_current_case(tmp_path, key: str):
    """``(baseline, mutated)`` of ``confluence_reference_current`` for A01, read at 0h against the saved H12."""
    from pipeline import nanowell_reference as nref
    from pipeline.nanowell_task import confluence_reference_current

    out = tmp_path / "out"
    out.mkdir()
    template = {"_nanowell": _REFERENCE_SPEC}
    if key == "measured":
        _stamp_reference_key(out, "none:unmeasured", "0h")
        baseline = confluence_reference_current(out, "A01", template)
        _save_reference(out, _reference_template())
        return baseline, confluence_reference_current(out, "A01", template)
    if key == "verdict":
        _save_reference(out, _reference_template(), status="loaded")
        _stamp_reference_key(out, "none:loaded", "0h")
        baseline = confluence_reference_current(out, "A01", template)
        _save_reference(out, _reference_template())
        return baseline, confluence_reference_current(out, "A01", template)
    if key == "template":
        _save_reference(out, _reference_template())
        _stamp_reference_key(out, nref.template_key(_reference_template()), "0h")
        baseline = confluence_reference_current(out, "A01", template)
        _save_reference(out, _reference_template(level=1.01))     # re-exported: another template
        return baseline, confluence_reference_current(out, "A01", template)
    raise AssertionError(f"no mutation written for {key!r}: add one beside its reason in REUSE_CONTRACTS")


def _finished_output_case(tmp_path, key: str):
    """``(baseline, mutated)`` of the host's own skip for a well its run sealed."""
    from pipeline.run_report import seal_well_complete, withdraw_seal, write_well_report
    from pipeline.settings_record import record_well_settings
    from pipeline.well_skip import finished_output

    out = tmp_path / "out"
    out.mkdir()
    (out / "A01_out.zip").write_bytes(b"zip")
    (out / "measurements_A01.csv").write_text("well,fov\nA01,1\n")
    record_well_settings(out, "A01", "abcdef0123456789")
    write_well_report(out, "A01", groups_total=1, groups_covered=1)
    seal_well_complete(out, "A01")
    records: dict = {}
    baseline = finished_output(out, "A01", "measurements", "abcdef0123456789")
    if key == "output":
        (out / "A01_out.zip").unlink()
    elif key == "settings":
        record_well_settings(out, "A01", "0123456789abcdef")
    elif key == "completion_seal":
        withdraw_seal(out, "A01")
    elif key == "completeness_record":
        from pipeline.run_report import complete_seal_path

        complete_seal_path(out, "A01").unlink()
        records = {"A01": {"groups_total": 2, "groups_covered": 1, "aborted_reason": "suspended"}}
    else:
        raise AssertionError(f"no mutation written for {key!r}: add one beside its reason in REUSE_CONTRACTS")
    return baseline, finished_output(out, "A01", "measurements", "abcdef0123456789", records=records)


_REMOTE_DECISIONS = {
    "remote_finished_wells.finished_here": lambda tmp_path, mp, key: _finished_here_case(tmp_path, key),
    "remote_batch_resume.take_from_remote": _take_from_remote_case,
    "remote_recover.recover_finished_outputs": _recover_case,
    "remote_run_id._pipeline_launch_is_pointless": lambda tmp_path, mp, key: _launch_case(key),
    "pipeline_runner.PipelineRunner._nothing_left_to_upload": lambda tmp_path, mp, key: _nothing_left_case(tmp_path,
                                                                                                           key),
    "well_skip.finished_output": lambda tmp_path, mp, key: _finished_output_case(tmp_path, key),
    "nanowell_task.confluence_reference_current": lambda tmp_path, mp, key: _reference_current_case(tmp_path, key),
}


@pytest.mark.parametrize("decision, key", [(d, k) for d in _REMOTE_DECISIONS for k in sorted(REUSE_CONTRACTS[d])])
def test_a_remote_reuse_decision_declines_when_one_key_changes(tmp_path, monkeypatch, decision, key):
    baseline, mutated = _REMOTE_DECISIONS[decision](tmp_path, monkeypatch, key)
    assert baseline, f"{decision}: the fixture must be one the decision reuses, or every mutation passes vacuously"
    assert not mutated, (
        f"{decision} still reused earlier work with {key!r} changed alone ({mutated!r}). It is keyed because: "
        f"{REUSE_CONTRACTS[decision][key]}.")
