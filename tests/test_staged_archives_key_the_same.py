"""A retry of an unchanged plate keys its upload the same; a change keys it anew.

The upload cache key (``services.acquisition_transport.acquisition_cache_key``,
over ``services.remote_scratch.fingerprint_archives``) decides which remote
``datasets/<key>/`` a run uploads into, and ``run_id`` is derived from it. When
the channel filter or the incremental split rewrites a well archive, the copy
that is uploaded is written fresh by staging — and it used to carry the time it
was written as its mtime. Every attempt at the same plate under the same
settings therefore had a different key: the whole plate was re-uploaded into an
empty directory each time, and each failed attempt left a full copy behind on
the remote (remote review S6).

The staged copy is now byte-deterministic and stamped with its source archive's
mtime. That alone would have opened the opposite hole — two channel selections
of one plate whose channels happen to be the same size (uncompressed TIFs of one
acquisition usually are) would key the same and reuse each other's upload — so
the key also hashes each archive's member list (name, size, CRC-32). These
tests drive the real ``stage_local_zips`` and key what it returns.
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

from services.acquisition_transport import acquisition_cache_key
from services.remote_staging import stage_local_zips

SCHEMA = "well:channel:fov"
SEP = "_"
OLD = 1_000_000_000          # 2001: a source mtime no staging run could write


def _plate(inp: Path, *, seed: int = 0) -> None:
    """Two well archives of three equal-sized channels, stored uncompressed so
    the GFP and RFP members have exactly the same size."""
    inp.mkdir(parents=True, exist_ok=True)
    rng = __import__("random").Random(seed)
    for w in ("A01", "A02"):
        path = inp / f"{w}.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
            for ch in ("DAPI", "GFP", "RFP"):
                zf.writestr(f"{w}_{ch}_F1.tif", bytes(rng.randrange(256) for _ in range(4000)))
        os.utime(path, (OLD, OLD))


def _stage(inp: Path, fluor: str):
    zipped = stage_local_zips(inp, filename_schema=SCHEMA, filename_sep=SEP,
                              fluor_tokens=[fluor], nuclear_token="DAPI")
    key = acquisition_cache_key(zipped)
    staged = {p.name: (p.read_bytes(), int(p.stat().st_mtime)) for p in zipped.well_zips}
    assert all(p.parent != inp for p in zipped.well_zips), "nothing was rewritten"
    zipped.cleanup()
    return key, staged


def test_an_unchanged_plate_keys_the_same_on_every_attempt(tmp_path):
    inp = tmp_path / "in"
    _plate(inp)
    key1, staged1 = _stage(inp, "GFP")
    key2, staged2 = _stage(inp, "GFP")
    # The mechanism, measured directly so the test cannot pass by both
    # attempts landing in the same wall-clock second: each staged copy carries
    # its source's mtime, and its bytes are the same on every attempt.
    assert {n: m for n, (_, m) in staged1.items()} == {"A01.zip": OLD, "A02.zip": OLD}
    assert staged1 == staged2
    assert key1 == key2


def test_a_replaced_image_keys_differently_even_at_the_same_size_and_mtime(tmp_path):
    inp = tmp_path / "in"
    _plate(inp, seed=0)
    key1, _ = _stage(inp, "GFP")
    # Re-export A01's GFP frame with other pixels, keeping its size and the
    # archive's timestamp — what a copy that preserves times produces.
    path = inp / "A01.zip"
    with zipfile.ZipFile(path) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    members["A01_GFP_F1.tif"] = bytes(reversed(members["A01_GFP_F1.tif"]))
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        for n, data in members.items():
            zf.writestr(n, data)
    os.utime(path, (OLD, OLD))
    key2, _ = _stage(inp, "GFP")
    assert key1 != key2


def test_two_channel_selections_of_one_plate_key_differently(tmp_path):
    """GFP and RFP frames (and names) are the same size here, so the staged
    archives have the same names, sizes and (stamped) mtimes; only the members
    differ."""
    inp = tmp_path / "in"
    _plate(inp)
    key_gfp, staged_gfp = _stage(inp, "GFP")
    key_rfp, staged_rfp = _stage(inp, "RFP")
    assert ({n: (len(b), m) for n, (b, m) in staged_gfp.items()}
            == {n: (len(b), m) for n, (b, m) in staged_rfp.items()})
    assert key_gfp != key_rfp
