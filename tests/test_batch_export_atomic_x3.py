"""Audit X3 — batch export stages to a temp dir and commits atomically.

Writers used to open the final path in ``"w"`` and de-dupe only against a
per-run set, so a re-run that failed at job K had already overwritten the prior
*complete* export for jobs 1..K-1. The staging helpers (Qt-free) let the panels
write everything into a temp subdir, then ``os.replace`` into the output dir +
write a manifest only on full success; a failure discards the staging.
"""
from __future__ import annotations

import json

from review.features.batch_export.service import (
    MANIFEST_NAME,
    commit_staged_export,
    discard_staging,
    new_staging_dir,
)


def test_staging_dir_is_inside_out_dir(tmp_path):
    out = tmp_path / "export"
    staging = new_staging_dir(out)
    assert staging.exists() and staging.is_dir()
    assert staging.parent == out          # same filesystem → atomic rename


def test_commit_moves_files_and_writes_manifest(tmp_path):
    out = tmp_path / "export"
    staging = new_staging_dir(out)
    (staging / "batch_A.csv").write_text("a")
    (staging / "batch_A.png").write_text("img")

    committed = commit_staged_export(staging, out)

    assert set(committed) == {"batch_A.csv", "batch_A.png"}
    assert (out / "batch_A.csv").read_text() == "a"
    assert not staging.exists()           # staging cleaned up
    manifest = json.loads((out / MANIFEST_NAME).read_text())
    assert manifest["count"] == 2
    assert set(manifest["files"]) == {"batch_A.csv", "batch_A.png"}


def test_failed_run_discards_staging_and_preserves_prior_export(tmp_path):
    out = tmp_path / "export"
    out.mkdir()
    # A prior complete export already on disk.
    (out / "batch_A.csv").write_text("PRIOR-COMPLETE")

    # A new run stages a (partial) rewrite, then fails before commit.
    staging = new_staging_dir(out)
    (staging / "batch_A.csv").write_text("HALF-WRITTEN")
    discard_staging(staging)

    # The prior export is untouched; nothing half-written leaked in.
    assert (out / "batch_A.csv").read_text() == "PRIOR-COMPLETE"
    assert not staging.exists()
    assert not (out / MANIFEST_NAME).exists()


def test_commit_overwrites_prior_export_atomically(tmp_path):
    out = tmp_path / "export"
    out.mkdir()
    (out / "batch_A.csv").write_text("OLD")
    staging = new_staging_dir(out)
    (staging / "batch_A.csv").write_text("NEW")

    commit_staged_export(staging, out)

    assert (out / "batch_A.csv").read_text() == "NEW"
