"""A run that never launches must not leave an output folder behind.

``PipelineRunner._run_pipeline_thread`` creates the output directory, builds the
pipeline's argument list, and opens the run journal. The order of the first two
matters more than it looks: ``build_pipeline_args`` reads required keys off
``opts`` and raises ``KeyError`` on a missing one, so with the directory created
first, a form the pipeline could not be launched from still left an empty
``out/`` on disk — for a run that never started, with no journal beside it to
explain what the folder is.

That folder is not harmless. The Analyze pane reports what an output folder
already holds before a run, and ``plan_input_layout`` classifies a dataset by
what sits next to the input; an ``out/`` nobody asked for is a claim that
something ran there.

Measured before the fix, driving the real runner: the resolver returned, the
argument build raised ``'nuclear_token'``, and the plate was left holding
``in/`` **and** ``out/``, with ``read_journal`` returning ``None``.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from services import run_journal as J
from services.pipeline_runner import PipelineRunner


def _drain(runner) -> list[tuple]:
    out = []
    while True:
        try:
            out.append(runner.log_q.get_nowait())
        except Exception:
            break
    return out


def _run_to_completion(runner, pipeline, opts, resolver):
    runner.start(pipeline, opts, resolve_dirs=resolver)
    deadline = time.time() + 20.0
    time.sleep(0.2)
    while runner.is_running and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)                       # let the worker's ``finally`` land
    assert not runner.is_running, "the run thread never finished"
    return _drain(runner)


@pytest.fixture
def plate(tmp_path):
    """A resolvable plate whose ``out/`` does not exist yet."""
    (tmp_path / "in").mkdir()
    return tmp_path


def _resolver_for(plate):
    def _resolve(opts, log_q, **kw):
        return (plate / "in", plate / "out", None)
    return _resolve


#: Everything ``build_pipeline_args`` needs except ``nuclear_token`` — the key
#: whose absence produced the reported ``KeyError``.
_OPTS_MISSING_A_REQUIRED_KEY = {
    "filename_schema": "well_fov_channel",
    "filename_sep": "_",
    "fluor_tokens": ["GFP"],
    "csv_prefix": "measurements",
}


def test_a_run_that_cannot_be_launched_leaves_no_output_folder(plate):
    events = _run_to_completion(
        PipelineRunner(), Path("/nonexistent/pipeline.py"),
        dict(_OPTS_MISSING_A_REQUIRED_KEY), _resolver_for(plate))

    assert any(kind == "error" for kind, _ in events), (
        "a run that could not be launched must surface an error")
    assert not (plate / "out").exists(), (
        "an empty out/ was left behind by a run that never started")


def test_the_failed_launch_records_no_journal(plate):
    """The other half of the same claim: no folder, and nothing claiming a run.

    A journal opened for a run that never launched would have the pre-run
    readout report "the last run here failed" for a plate nothing was ever
    attempted on.
    """
    _run_to_completion(
        PipelineRunner(), Path("/nonexistent/pipeline.py"),
        dict(_OPTS_MISSING_A_REQUIRED_KEY), _resolver_for(plate))

    assert J.read_journal(plate / "out") is None
    assert not (plate / "out" / J.JOURNAL_DIRNAME).exists()


def test_a_launchable_run_still_gets_its_folder_and_its_journal(plate):
    """The fix must not cost the ordinary path its directory.

    The pipeline binary does not exist, so this run fails at *launch* rather
    than at argument-build time — which is a real run, and the one case that
    must still leave both the output folder and an opened-then-closed journal.
    """
    opts = dict(_OPTS_MISSING_A_REQUIRED_KEY, nuclear_token="DAPI")
    _run_to_completion(PipelineRunner(), Path("/nonexistent/pipeline.py"),
                       opts, _resolver_for(plate))

    assert (plate / "out").is_dir()
    journal = J.read_journal(plate / "out")
    assert journal is not None, "a run that launched recorded no journal"
    assert journal.outcome == "error"
