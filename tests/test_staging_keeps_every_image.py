"""Remote staging never destroys an image the user had — the channel filter only
ever changes what is uploaded.

A remote run turns the user's input into per-well archives before anything is
sent. For a plate of loose images or well folders that conversion happens in the
user's own input directory: each well folder is packed into ``<well>.zip`` and
its images are deleted as soon as the archive is published, so that archive *is*
the user's only copy of the well from then on. Three defects destroyed images
along that path (remote review S1–S3):

* **S1** — the channel filter then rewrote those archives *in place*, keeping
  only the nuclear and selected channels. A DAPI/GFP/BF plate run with GFP
  selected — which the Analyze tab does on every run, because it always passes
  ``selective_channels=True`` — lost every BF image before a byte was uploaded,
  whether or not the run then succeeded.
* **S2** — a well folder holding no ``.tif`` (PNG images, metadata only) was not
  zipped, not uploaded, not named in the log, and then ``rmtree``'d by the
  success hook; PNGs beside TIFs were silently left out of the upload; and
  AppleDouble ``._*.tif`` sidecars were packed as images.
* **S3** — the success hooks unlinked every top-level image snapshotted before
  grouping, archived or not, so a ``flatfield_reference_DAPI.tif`` beside the
  plate was deleted after a successful run. Both hooks: the staging one in
  ``services/remote_staging.py`` and the ``resolve_input_output`` one in
  ``services/input_resolution_service.py``.

Every test drives the real entry points in the order the app calls them
(``services.pipeline_runner``): ``resolve_input_output`` (for the Analyze
flow), ``stage_local_zips``, then — on success — ``zipped.on_pipeline_success``,
the resolve hook, and finally ``zipped.cleanup``; on failure only the cleanup.
The fixtures are random bytes, so a "copy" that is not byte-identical to the
original does not count, and the transit directory holding the upload's
trimmed copies is excluded from the census, so the staged copies cannot stand
in for the user's own.
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from services.input_resolution_service import (
    _delete_sources_that_landed,
    resolve_input_output,
)
from services.remote_scratch import TRANSIT_SUBDIR
from services.remote_staging import stage_local_zips
from util.image_exts import is_run_input_member

SCHEMA = "well:channel:fov"
SEP = "_"
WELLS = ("A01", "A02")
CHANNELS = ("DAPI", "GFP", "BF")
# Images the zipper places in no well: the S3 case. Neither may ever be deleted.
NOT_A_WELL = ("flatfield_reference_DAPI.tif", "plate_overview.tif")
SELECTED = {"DAPI", "GFP"}          # nuclear DAPI + fluor GFP; BF not selected


def _plate() -> dict[str, bytes]:
    """Every plate image by name: two wells x three channels x two fields."""
    rng = __import__("random").Random(7)
    return {f"{w}_{ch}_F{f}.tif": bytes(rng.randrange(256) for _ in range(3000))
            for w in WELLS for ch in CHANNELS for f in (1, 2)}


def _census(root: Path) -> dict[str, list[bytes]]:
    """Every image under *root*, loose or archived, as ``name -> [bytes, …]``.

    The transit directory is skipped: what is in it is the upload's trimmed
    copy, which cleanup removes, and counting it would let the staged copy
    stand in for the user's own.
    """
    found: dict[str, list[bytes]] = {}
    for path in root.rglob("*"):
        if TRANSIT_SUBDIR in path.parts or not path.is_file():
            continue
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    found.setdefault(Path(name).name, []).append(zf.read(name))
        elif is_run_input_member(path.name):
            found.setdefault(path.name, []).append(path.read_bytes())
    return found


def _missing(originals: dict[str, bytes], root: Path) -> list[str]:
    census = _census(root)
    return sorted(n for n, data in originals.items() if data not in census.get(n, []))


def _uploaded_members(zipped) -> set[str]:
    out: set[str] = set()
    for z in zipped.well_zips:
        with zipfile.ZipFile(z) as zf:
            out.update(zf.namelist())
    return out


def _write(folder: Path, files: dict[str, bytes]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)


def _layout(kind: str, root: Path, plate: dict[str, bytes]) -> Path:
    """Lay *plate* out as *kind*; return the directory the app stages from."""
    extras = {n: os.urandom(500) for n in NOT_A_WELL}
    if kind.startswith("analyze"):
        raw = root / "raw"
        _write(raw, {**plate, **extras})
        return raw
    inp = root / "in"
    if kind.startswith("flat"):
        _write(inp, {**plate, **extras})
    elif kind == "folder":
        for w in WELLS:
            _write(inp / w, {n: d for n, d in plate.items() if n.startswith(w)})
    elif kind == "zip":
        inp.mkdir(parents=True)
        for w in WELLS:
            with zipfile.ZipFile(inp / f"{w}.zip", "w") as zf:
                for n, d in plate.items():
                    if n.startswith(w):
                        zf.writestr(n, d)
    return inp


KINDS = ["analyze-copy", "analyze-move", "folder", "flat-copy", "flat-move", "zip"]


def _run(kind: str, start: Path, outcome: str, logs: list[str]):
    """Stage *start* as the app does, then end the run as *outcome* says."""
    move = kind.endswith("-move")
    resolve_hook = None
    stage_dir = start
    if kind.startswith("analyze"):
        stage_dir, _out, resolve_hook = resolve_input_output(
            start, filename_schema=SCHEMA, filename_sep=SEP,
            move_source_tifs=move, log_fn=logs.append)
    zipped = stage_local_zips(
        stage_dir, filename_schema=SCHEMA, filename_sep=SEP,
        fluor_tokens=["GFP"], nuclear_token="DAPI",
        move_source_tifs=move, log_fn=logs.append)
    uploaded = _uploaded_members(zipped)
    if outcome == "succeeded":
        if zipped.on_pipeline_success is not None:
            zipped.on_pipeline_success()
        if resolve_hook is not None:
            resolve_hook()
    if outcome != "staged" and zipped.cleanup is not None:
        zipped.cleanup()
    return zipped, uploaded


@pytest.mark.parametrize("outcome", ["staged", "failed", "succeeded"])
@pytest.mark.parametrize("kind", KINDS)
def test_every_image_survives_staging_and_the_run_s_end(tmp_path, kind, outcome):
    plate = _plate()
    start = _layout(kind, tmp_path, plate)
    logs: list[str] = []
    zipped, uploaded = _run(kind, start, outcome, logs)

    # The invariant: every image the user had is still under their input, byte
    # for byte — archived or loose — whatever the run did.
    lost = _missing(plate, start)
    assert lost == [], f"images gone from the user's input: {lost}"
    # And the filter still did its job on what was *uploaded*: exactly the
    # selected channels of every well, no BF.
    assert uploaded == {n for n in plate if n.split(SEP)[1] in SELECTED}


@pytest.mark.parametrize("kind", ["analyze-copy", "flat-copy", "flat-move", "analyze-move"])
def test_the_success_hook_removes_what_is_archived_and_nothing_else(tmp_path, kind):
    """The other half of the invariant, so "never delete anything" cannot pass:
    after a successful run the loose plate images that are now inside an
    archive are gone, and the images the zipper placed in no well are not."""
    plate = _plate()
    start = _layout(kind, tmp_path, plate)
    _run(kind, start, "succeeded", [])

    loose = sorted(p.name for p in start.iterdir()
                   if p.is_file() and is_run_input_member(p.name))
    assert loose == sorted(NOT_A_WELL)


def test_a_folder_plate_s_odd_wells_are_uploaded_or_named_never_deleted(tmp_path):
    """S2: PNG wells, metadata-only folders, AppleDouble sidecars, subfolders."""
    inp = tmp_path / "in"
    rnd = os.urandom
    _write(inp / "A01", {"A01_DAPI_F1.tif": rnd(800), "A01_GFP_F1.tif": rnd(800),
                         "._A01_DAPI_F1.tif": rnd(400)})
    _write(inp / "A01" / "masks", {"A01_DAPI_F1_mask.png": rnd(300)})
    _write(inp / "A02", {"A02_DAPI_F1.png": rnd(800), "A02_GFP_F1.png": rnd(800)})
    _write(inp / "A03", {"A03_acquisition.xml": b"<meta/>"})
    _write(inp / "A04", {"A04_DAPI_F1.tif": rnd(800), "A04_GFP_F1.png": rnd(800)})
    logs: list[str] = []

    zipped = stage_local_zips(inp, filename_schema=SCHEMA, filename_sep=SEP,
                              log_fn=logs.append)
    members = {}
    for z in zipped.well_zips:
        with zipfile.ZipFile(z) as zf:
            members[z.stem] = sorted(zf.namelist())
    # Every image is uploaded, PNGs included; the sidecar and the subfolder's
    # file are not images of the well, and are not packed.
    assert members == {
        "A01": ["A01_DAPI_F1.tif", "A01_GFP_F1.tif"],
        "A02": ["A02_DAPI_F1.png", "A02_GFP_F1.png"],
        "A04": ["A04_DAPI_F1.tif", "A04_GFP_F1.png"],
    }
    # The well with nothing to upload is named, as not uploaded.
    blob = "".join(logs)
    assert "A03: the folder holds no images — well not uploaded" in blob
    assert "A03_acquisition.xml" in blob

    zipped.on_pipeline_success()
    # Nothing the archives do not hold was removed by the success hook.
    assert (inp / "A03" / "A03_acquisition.xml").read_bytes() == b"<meta/>"
    assert (inp / "A01" / "._A01_DAPI_F1.tif").is_file()
    assert (inp / "A01" / "masks" / "A01_DAPI_F1_mask.png").is_file()
    assert not (inp / "A02").exists()       # fully archived, so consumed
    assert not (inp / "A04").exists()


def test_flat_images_in_no_well_are_named_in_the_log(tmp_path):
    plate = _plate()
    inp = _layout("flat-copy", tmp_path, plate)
    logs: list[str] = []
    stage_local_zips(inp, filename_schema=SCHEMA, filename_sep=SEP, log_fn=logs.append)
    lines = [ln for ln in logs if "in no well under the schema" in ln]
    assert len(lines) == 1, "".join(logs)
    assert "not uploaded" in lines[0]
    for name in NOT_A_WELL:
        assert name in lines[0]


def test_a_loose_image_is_deleted_only_when_its_bytes_are_archived(tmp_path):
    """S3's sibling in ``input_resolution_service``: the well having landed is
    not proof that *this file* did. A same-named, same-sized file with other
    pixels — a re-export, or the loser of a name collision the zipper left
    behind — is kept."""
    grouped = tmp_path / "in"
    grouped.mkdir()
    archived_gfp = os.urandom(1000)
    with zipfile.ZipFile(grouped / "A01.zip", "w") as zf:
        zf.writestr("A01_DAPI_F1.tif", os.urandom(1000))
        zf.writestr("A01_GFP_F1.tif", archived_gfp)
    dapi = tmp_path / "A01_DAPI_F1.tif"
    dapi.write_bytes(os.urandom(1000))          # same name and size, other bytes
    gfp = tmp_path / "A01_GFP_F1.tif"
    gfp.write_bytes(archived_gfp)               # really is the archived one

    kept = _delete_sources_that_landed([dapi, gfp], grouped,
                                       filename_schema=SCHEMA, filename_sep=SEP)
    assert dapi.is_file(), "a file whose bytes are in no archive was deleted"
    assert not gfp.exists()
    assert kept == [dapi]
