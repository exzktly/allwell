"""Regression test: `_well_zip_fingerprints` must canonicalise well tokens.

Before this fix, an unpadded on-disk out-zip (``A1_out.zip``) left the
fingerprint dict keyed by the raw stem (``"A1"``) while every override's
``well`` key is canonical (``"A01"``, via ``CellKey``/``normalize_well``) — so
the save-time stamp and the load-time staleness check both missed the same
way on an unpadded dataset, and a stale override was silently *kept* instead
of dropped (the opposite of the schema-v2 contract).
"""

from __future__ import annotations

from types import SimpleNamespace

from well_viewer.persistence.cell_overrides import _well_zip_fingerprints


def test_fingerprints_keyed_by_canonical_well(tmp_path):
    (tmp_path / "A1_out.zip").write_bytes(b"x")
    (tmp_path / "B02_out.zip").write_bytes(b"x")
    ctx = SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(data_dir=tmp_path)))

    fingerprints = _well_zip_fingerprints(ctx)

    assert set(fingerprints) == {"A01", "B02"}


def test_fingerprints_empty_without_data_dir():
    ctx = SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(data_dir=None)))
    assert _well_zip_fingerprints(ctx) == {}
