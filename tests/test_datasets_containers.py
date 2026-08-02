"""Tests for the shared Datasets container copier.

``datasets._common.containers.copy_container_members`` is the one path every
Datasets tool moves image bytes through, and it moves them without decoding
them (see that module's docstring for the three tiers). These tests hold it to
the two things that buys and the one thing it must never cost:

* the fast tiers are actually taken — a whole-file copy never opens a member,
  and a passthrough member keeps its source's compressed size and method, which
  it could not do if it had been re-deflated;
* every member that lands decompresses to exactly the bytes it had in the
  source, whatever its name, encoding, extra field, or size;
* a source the copier cannot honour fails loudly rather than publishing a
  destination that is quietly missing data.

Fixture archives deliberately carry the junk real ones do — ``.DS_Store``, a
directory entry, an AppleDouble sidecar, a name that parses to nothing. A
corpus of well-formed TIFs validates the happy path and hides everything else.
"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import pytest

from datasets._common.containers import copy_container_members
from datasets._common.zip_passthrough import (
    _UNICODE_PATH_EXTRA_ID,
    _ZIP64_EXTRA_ID,
    RawCopyUnsupported,
    _filtered_extra,
    copy_member_raw,
    member_is_raw_copyable,
)

# Poorly compressible, like a real 16-bit frame: a member that deflates to
# nothing would hide a size change between the source's method and level 1's.
_PAYLOAD = bytes(range(256)) * 40


def _keep_all(_container: Path, name: str) -> str:
    return name


def _junk_members() -> list[tuple[str, bytes]]:
    """The debris a real well archive picks up on a Mac or a network share."""
    return [
        (".DS_Store", b"\x00\x01junk"),
        ("__MACOSX/._exp_GFP_A01_F001_24h.tif", b"appledouble"),
        ("notes.txt", b"free text, parses to nothing"),
    ]


def _build_zip(
    path: Path, names: list[str], *,
    compression: int = zipfile.ZIP_DEFLATED,
    payload=None,
    junk: bool = True,
    directory_entry: bool = True,
) -> dict[str, bytes]:
    """Write an archive and return the member bytes it should read back as."""
    expected: dict[str, bytes] = {}
    with zipfile.ZipFile(path, "w", compression) as archive:
        if directory_entry:
            archive.writestr(zipfile.ZipInfo("frames/"), b"")
        for index, name in enumerate(names):
            data = payload(index) if callable(payload) else _PAYLOAD + bytes([index])
            archive.writestr(name, data)
            expected[name] = data
        if junk:
            for name, data in _junk_members():
                archive.writestr(name, data)
                expected[name] = data
    return expected


def _read_all(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        return {
            info.filename: archive.read(info.filename)
            for info in archive.infolist() if not info.is_dir()
        }


def _infos(path: Path) -> dict[str, zipfile.ZipInfo]:
    with zipfile.ZipFile(path) as archive:
        return {info.filename: info for info in archive.infolist()}


_NAMES = [f"exp_GFP_A01_F001_{tp}h.tif" for tp in (24, 48, 72, 96)]


# ── tier 1: the whole-file copy ──────────────────────────────────────────────
def test_identity_copy_reproduces_the_source_file_byte_for_byte(tmp_path):
    src = tmp_path / "A01.zip"
    _build_zip(src, _NAMES)
    dest = tmp_path / "out" / "A01.zip"

    written, dropped = copy_container_members(dest, [src], _keep_all)

    assert dest.read_bytes() == src.read_bytes()
    assert not dropped
    # Directory entries carry no data and are not "written"; everything else is.
    assert written == set(_read_all(src))


def test_identity_copy_never_reads_a_member(tmp_path, monkeypatch):
    """The point of tier 1: the archive is moved, not opened.

    Guards the tier against silently regressing into tier 2, which would still
    produce a correct destination — just at inflate+deflate speed, which is the
    entire bug this path exists to avoid.
    """
    src = tmp_path / "A01.zip"
    _build_zip(src, _NAMES)

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("a whole-file copy must not decompress a member")

    monkeypatch.setattr(zipfile.ZipFile, "read", _forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "open", _forbidden)

    copy_container_members(tmp_path / "dest.zip", [src], _keep_all)


def test_identity_copy_is_not_taken_when_a_member_is_dropped(tmp_path):
    src = tmp_path / "A01.zip"
    expected = _build_zip(src, _NAMES)
    dest = tmp_path / "dest.zip"

    def _drop_first(_container: Path, name: str) -> str | None:
        return None if name == _NAMES[0] else name

    written, _dropped = copy_container_members(dest, [src], _drop_first)

    assert dest.read_bytes() != src.read_bytes()
    assert _NAMES[0] not in written
    assert _read_all(dest) == {k: v for k, v in expected.items() if k != _NAMES[0]}


# ── tier 2: raw member passthrough ───────────────────────────────────────────
@pytest.mark.parametrize("compression", [zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED])
def test_kept_members_round_trip_byte_identically(tmp_path, compression):
    src = tmp_path / "A01.zip"
    expected = _build_zip(src, _NAMES, compression=compression)
    dest = tmp_path / "dest.zip"

    def _drop_last(_container: Path, name: str) -> str | None:
        return None if name == _NAMES[-1] else name

    copy_container_members(dest, [src], _drop_last)

    assert _read_all(dest) == {k: v for k, v in expected.items() if k != _NAMES[-1]}


def test_passthrough_keeps_the_source_compression_untouched(tmp_path):
    """A member that had been re-deflated at level 1 would not keep its size.

    The source is written at the stdlib default (level 6) and the destination
    archive is opened at level 1, so an identical ``compress_size`` is only
    possible if the compressed bytes were moved rather than rebuilt.
    """
    src = tmp_path / "A01.zip"
    _build_zip(src, _NAMES, compression=zipfile.ZIP_DEFLATED)
    dest = tmp_path / "dest.zip"

    def _drop_last(_container: Path, name: str) -> str | None:
        return None if name == _NAMES[-1] else name

    copy_container_members(dest, [src], _drop_last)

    src_infos, dest_infos = _infos(src), _infos(dest)
    for name in dest_infos:
        assert dest_infos[name].compress_type == src_infos[name].compress_type
        assert dest_infos[name].compress_size == src_infos[name].compress_size
        assert dest_infos[name].CRC == src_infos[name].CRC
        assert dest_infos[name].file_size == src_infos[name].file_size


def test_renaming_a_member_does_not_touch_its_bytes(tmp_path):
    src = tmp_path / "A01.zip"
    expected = _build_zip(src, _NAMES, junk=False, directory_entry=False)
    dest = tmp_path / "dest.zip"

    def _rename(_container: Path, name: str) -> str:
        return name.replace("A01", "B07")

    written, _dropped = copy_container_members(dest, [src], _rename)

    assert written == {n.replace("A01", "B07") for n in expected}
    assert _read_all(dest) == {k.replace("A01", "B07"): v for k, v in expected.items()}
    src_infos, dest_infos = _infos(src), _infos(dest)
    for name, data in expected.items():
        assert dest_infos[name.replace("A01", "B07")].compress_size == src_infos[name].compress_size
        assert data == _PAYLOAD + bytes([_NAMES.index(name)])


def test_a_repacked_archive_carries_no_directory_entries(tmp_path):
    """Scoped to the re-pack path on purpose — a whole-file copy keeps them.

    A tier-1 copy reproduces the source file byte for byte, directory entries
    included, so the two tiers genuinely differ here. Nothing downstream reads
    them (``find_image_groups_in_zip``, ``zip_members``, Validate and
    ``incremental_keys`` all filter by basename/extension or on a trailing
    slash), which is why the difference is allowed to stand rather than tier 1
    being made to strip them and stop being a file copy.
    """
    src = tmp_path / "A01.zip"
    _build_zip(src, _NAMES, directory_entry=True)

    def _drop_last(_container: Path, name: str) -> str | None:
        return None if name == _NAMES[-1] else name

    repacked = tmp_path / "repacked.zip"
    copy_container_members(repacked, [src], _drop_last)
    with zipfile.ZipFile(repacked) as archive:
        assert not [info for info in archive.infolist() if info.is_dir()]

    whole = tmp_path / "whole.zip"
    copy_container_members(whole, [src], _keep_all)
    with zipfile.ZipFile(whole) as archive:
        assert [info.filename for info in archive.infolist() if info.is_dir()] == ["frames/"]


def test_non_ascii_names_and_extra_fields_round_trip(tmp_path):
    src = tmp_path / "A01.zip"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("frøkost_µm_∆.tif", (2024, 5, 1, 12, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        # A UT-timestamp-shaped extra record: not ZIP64, so it must survive.
        info.extra = b"\x55\x54\x05\x00\x01\x00\x00\x00\x00"
        info.external_attr = 0o644 << 16
        archive.writestr(info, _PAYLOAD)
        archive.writestr("plain.tif", b"plain")
    dest = tmp_path / "dest.zip"

    def _drop_plain(_container: Path, name: str) -> str | None:
        return None if name == "plain.tif" else name

    copy_container_members(dest, [src], _drop_plain)

    assert _read_all(dest) == {"frøkost_µm_∆.tif": _PAYLOAD}
    copied = _infos(dest)["frøkost_µm_∆.tif"]
    assert copied.date_time == (2024, 5, 1, 12, 0, 0)
    assert b"\x55\x54" in copied.extra
    assert copied.external_attr == 0o644 << 16


def test_a_data_descriptor_source_round_trips(tmp_path):
    """A zip written to a non-seekable stream stores sizes in a trailer.

    The copied member carries them inline instead, so its descriptor flag must
    come off — left on, a reader would look for a trailer that isn't there.
    """
    class _Unseekable(io.RawIOBase):
        def __init__(self, handle):
            self._handle = handle

        def writable(self):
            return True

        def write(self, data):
            return self._handle.write(data)

        def seekable(self):
            return False

    src = tmp_path / "A01.zip"
    with src.open("wb") as raw, _Unseekable(raw) as stream:
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("exp_GFP_A01_F001_24h.tif", _PAYLOAD)
            archive.writestr("drop.tif", b"drop")

    assert _infos(src)["exp_GFP_A01_F001_24h.tif"].flag_bits & 0x8
    dest = tmp_path / "dest.zip"
    copy_container_members(
        dest, [src], lambda _c, n: None if n == "drop.tif" else n,
    )

    assert _read_all(dest) == {"exp_GFP_A01_F001_24h.tif": _PAYLOAD}
    assert not _infos(dest)["exp_GFP_A01_F001_24h.tif"].flag_bits & 0x8


def test_a_zip64_member_round_trips(tmp_path, monkeypatch):
    """Forced with a lowered limit rather than a 4 GB fixture."""
    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 64)
    src = tmp_path / "A01.zip"
    expected = _build_zip(
        src, _NAMES, compression=zipfile.ZIP_STORED, junk=False, directory_entry=False,
    )
    dest = tmp_path / "dest.zip"

    def _drop_last(_container: Path, name: str) -> str | None:
        return None if name == _NAMES[-1] else name

    copy_container_members(dest, [src], _drop_last)

    assert _read_all(dest) == {k: v for k, v in expected.items() if k != _NAMES[-1]}


# ── tier 3: fallback ─────────────────────────────────────────────────────────
def test_a_folder_source_is_copied_through_the_ordinary_path(tmp_path):
    folder = tmp_path / "A01"
    (folder / "nested").mkdir(parents=True)
    (folder / "exp_GFP_A01_F001_24h.tif").write_bytes(_PAYLOAD)
    (folder / "nested" / "exp_GFP_A01_F001_48h.tif").write_bytes(b"nested")
    (folder / ".DS_Store").write_bytes(b"junk")
    dest = tmp_path / "dest.zip"

    written, dropped = copy_container_members(
        dest, [folder], lambda _c, n: None if n == ".DS_Store" else n,
    )

    assert not dropped
    assert written == {"exp_GFP_A01_F001_24h.tif", "nested/exp_GFP_A01_F001_48h.tif"}
    assert _read_all(dest) == {
        "exp_GFP_A01_F001_24h.tif": _PAYLOAD,
        "nested/exp_GFP_A01_F001_48h.tif": b"nested",
    }


def test_a_member_the_passthrough_declines_still_lands(tmp_path, monkeypatch):
    src = tmp_path / "A01.zip"
    expected = _build_zip(src, _NAMES, junk=False, directory_entry=False)
    dest = tmp_path / "dest.zip"

    monkeypatch.setattr(
        "datasets._common.containers.member_is_raw_copyable", lambda _info: False,
    )
    copy_container_members(
        dest, [src], lambda _c, n: None if n == _NAMES[-1] else n,
    )

    assert _read_all(dest) == {k: v for k, v in expected.items() if k != _NAMES[-1]}


def test_encrypted_and_directory_members_are_declined(tmp_path):
    directory = zipfile.ZipInfo("frames/")
    assert not member_is_raw_copyable(directory)
    encrypted = zipfile.ZipInfo("secret.tif")
    encrypted.flag_bits |= 0x1
    assert not member_is_raw_copyable(encrypted)
    assert member_is_raw_copyable(zipfile.ZipInfo("plain.tif"))


# ── contract: collisions, empties, progress, corruption ──────────────────────
def test_the_first_source_wins_a_name_collision(tmp_path):
    first = tmp_path / "one.zip"
    second = tmp_path / "two.zip"
    _build_zip(first, ["shared.tif"], payload=lambda _i: b"from-first", junk=False,
               directory_entry=False)
    _build_zip(second, ["shared.tif", "only-second.tif"],
               payload=lambda _i: b"from-second", junk=False, directory_entry=False)
    dest = tmp_path / "dest.zip"

    written, dropped = copy_container_members(dest, [first, second], _keep_all)

    assert written == {"shared.tif", "only-second.tif"}
    assert dropped == {"shared.tif"}
    assert _read_all(dest)["shared.tif"] == b"from-first"


def test_a_filtered_out_member_is_not_reported_as_dropped(tmp_path):
    """*dropped* means data loss, so a member nobody asked for must not appear.

    Concatenate reads a non-empty *dropped* as a failed merge; folding the
    filter's own exclusions into it would fail every filtered run.
    """
    src = tmp_path / "A01.zip"
    _build_zip(src, _NAMES, junk=False, directory_entry=False)

    _written, dropped = copy_container_members(
        tmp_path / "dest.zip", [src], lambda _c, n: None if n == _NAMES[0] else n,
    )

    assert dropped == frozenset()


def test_nothing_kept_leaves_no_archive_behind(tmp_path):
    """A zero-member zip is valid but macOS refuses to expand it, and All-Well
    reads it as a well whose images have vanished."""
    src = tmp_path / "A01.zip"
    _build_zip(src, _NAMES)
    dest = tmp_path / "dest.zip"

    written, dropped = copy_container_members(dest, [src], lambda _c, _n: None)

    assert not dest.exists()
    assert not written and not dropped


def test_every_tier_closes_out_its_progress_slice(tmp_path):
    """One container is one slice, whichever tier serves it — otherwise a well
    that genuinely finished leaves its dashboard dial short."""
    src = tmp_path / "A01.zip"
    _build_zip(src, _NAMES)
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "a.tif").write_bytes(b"a")

    for label, sources, rename in [
        ("whole-file", [src], _keep_all),
        ("passthrough", [src], lambda _c, n: None if n == _NAMES[0] else n),
        ("nothing kept", [src], lambda _c, _n: None),
        ("folder", [folder], _keep_all),
    ]:
        seen: list[tuple[int, int]] = []
        copy_container_members(
            tmp_path / f"{label}.zip", sources, rename,
            progress=lambda done, total: seen.append((done, total)),
        )
        assert seen, f"{label} reported no progress at all"
        done, total = seen[-1]
        assert done == total > 0, f"{label} left its slice at {done}/{total}"


def test_a_source_whose_headers_disagree_raises_rather_than_writing_less(tmp_path):
    """A central directory that points at no local header is a corrupt source.

    It is raised on rather than skipped: the ordinary decompress path would
    fail on the same member for the same reason, and an archive that quietly
    holds fewer images than it was asked for is the one outcome this code must
    never produce.
    """
    src = tmp_path / "A01.zip"
    _build_zip(src, _NAMES, compression=zipfile.ZIP_STORED, junk=False,
               directory_entry=False)
    raw = bytearray(src.read_bytes())
    second = raw.find(b"PK\x03\x04", raw.find(b"PK\x03\x04") + 1)
    raw[second:second + 4] = b"XXXX"
    src.write_bytes(bytes(raw))

    dest = tmp_path / "dest.zip"
    # A dropped member keeps this off the whole-file-copy tier, which would
    # never look at a local header at all.
    with pytest.raises(RawCopyUnsupported):
        copy_container_members(
            dest, [src], lambda _c, n: None if n == _NAMES[-1] else n,
        )


def test_a_failed_member_leaves_the_destination_where_it_was(tmp_path):
    src = tmp_path / "A01.zip"
    _build_zip(src, _NAMES, junk=False, directory_entry=False)

    with zipfile.ZipFile(src) as source, zipfile.ZipFile(
        tmp_path / "dest.zip", "w", zipfile.ZIP_DEFLATED,
    ) as dest:
        first = source.infolist()[0]
        copy_member_raw(source, first, dest, first.filename)
        resume = dest.fp.tell()

        broken = zipfile.ZipInfo(source.infolist()[1].filename)
        broken.header_offset = 10 ** 9  # nowhere near a local header
        with pytest.raises(RawCopyUnsupported):
            copy_member_raw(source, broken, dest, broken.filename)
        assert dest.fp.tell() == resume

    assert set(_read_all(tmp_path / "dest.zip")) == {_NAMES[0]}


# ── extra-field surgery ──────────────────────────────────────────────────────
def test_filtered_extra_keeps_every_record_it_was_not_asked_to_drop():
    zip64 = b"\x01\x00\x08\x00" + b"\x00" * 8
    timestamp = b"\x55\x54\x05\x00\x01\x00\x00\x00\x00"
    unix = b"\x75\x78\x04\x00\xe8\x03\xe8\x03"
    drop = frozenset({_ZIP64_EXTRA_ID})

    assert _filtered_extra(timestamp + zip64 + unix, drop) == timestamp + unix
    assert _filtered_extra(zip64, drop) == b""
    assert _filtered_extra(b"", drop) == b""
    # A record whose declared length runs past the buffer is dropped, not
    # emitted as a header a reader would misparse.
    assert _filtered_extra(timestamp + b"\x99\x99\xff\xff\x00", drop) == timestamp


# ── a failure must not leave a complete-looking archive ──────────────────────
def test_a_member_failing_mid_archive_publishes_nothing(tmp_path, monkeypatch):
    """The destination must not exist at all rather than exist and read clean.

    A plain ``with ZipFile(dest, "w")`` writes a central directory for whatever
    landed before the exception, so a well that failed half way through left a
    structurally valid archive holding fewer images than it was asked for, with
    nothing on it saying so. The tools do write an in-progress marker and clear
    the destination on the next attempt, but nothing stops a user opening that
    half-written folder in Review first.
    """
    src = tmp_path / "A01.zip"
    _build_zip(src, _NAMES, junk=False, directory_entry=False)
    dest = tmp_path / "dest.zip"

    real = copy_member_raw
    calls = {"n": 0}

    def _fail_on_third(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RawCopyUnsupported("simulated mid-archive failure")
        return real(*args, **kwargs)

    monkeypatch.setattr("datasets._common.containers.copy_member_raw", _fail_on_third)
    with pytest.raises(RawCopyUnsupported):
        copy_container_members(
            dest, [src], lambda _c, n: None if n == _NAMES[-1] else n,
        )

    assert not dest.exists()
    assert not list(tmp_path.glob("__aw_tmp_*")), "staging file was left behind"


def test_a_rename_drops_the_records_that_carry_the_old_name(tmp_path):
    """Info-ZIP's Unicode Path record embeds its own copy of the member name.

    Carried across a rename it is a second, stale name for the same member: a
    reader that honours it and skips its name-CRC restores the file under its
    pre-rename name, silently undoing the well remap or timepoint offset the
    copy existed to perform.
    """
    unicode_path = (
        struct.pack("<HH", _UNICODE_PATH_EXTRA_ID, 5 + len(b"A01_old.tif"))
        + b"\x01" + struct.pack("<I", 0) + b"A01_old.tif"
    )
    timestamp = b"\x55\x54\x05\x00\x01\x00\x00\x00\x00"

    src = tmp_path / "A01.zip"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("A01_old.tif", (2024, 5, 1, 12, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.extra = timestamp + unicode_path
        archive.writestr(info, _PAYLOAD)
        archive.writestr("keep_me.tif", b"keep")

    renamed = tmp_path / "renamed.zip"
    copy_container_members(
        renamed, [src],
        lambda _c, n: "B07_new.tif" if n == "A01_old.tif" else None,
    )
    copied = _infos(renamed)["B07_new.tif"]
    assert b"A01_old.tif" not in copied.extra, "the old name survived the rename"
    assert b"\x55\x54" in copied.extra, "unrelated records must still be carried"
    assert _read_all(renamed) == {"B07_new.tif": _PAYLOAD}

    # A member keeping its own name keeps its records — they still describe it.
    kept = tmp_path / "kept.zip"
    copy_container_members(
        kept, [src], lambda _c, n: n if n == "A01_old.tif" else None,
    )
    assert b"A01_old.tif" in _infos(kept)["A01_old.tif"].extra
