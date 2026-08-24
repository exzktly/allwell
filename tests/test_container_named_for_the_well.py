"""A container copied whole lands under the well's name, not the source file's.

A dataset is read back by well token — ``in/<WELL>.zip`` for a zipped well,
``in/<WELL>/`` for a folder-mode one — and every path that *rewrites* a
container builds that name from the well. The two paths that copy a container
byte-for-byte built it from ``src.name`` instead, so a source plate written
with unpadded tokens produced ``in/A1.zip`` beside an ``out/A01_out.zip`` from
the same run, and nothing downstream matched the two up.

Merge Plates was half-swept within one file: its QC branch already named the
destination for the well (``f"{dest_well}_out.zip"``) while its input branch,
twenty lines above, did not — so the two halves of the same well disagreed
about what that well is called.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from datasets._common.containers import container_dest  # noqa: E402


def _zip(path: Path, names=("img_t1.tif",)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for n in names:
            zf.writestr(n, b"x")
    return path


def test_a_zip_takes_the_well_token_and_the_zip_suffix(tmp_path):
    src = _zip(tmp_path / "src" / "A1.zip")
    assert container_dest(src, tmp_path / "dest", "A01") == tmp_path / "dest" / "A01.zip"


def test_a_folder_container_stays_a_folder(tmp_path):
    src = tmp_path / "src" / "a1"
    src.mkdir(parents=True)
    (src / "img_t1.tif").write_bytes(b"x")
    assert container_dest(src, tmp_path / "dest", "A01") == tmp_path / "dest" / "A01"


def test_concatenate_copies_an_unpadded_well_under_its_canonical_name(tmp_path):
    from datasets.concatenate import apply as ccat

    dest_in = tmp_path / "dest" / "in"
    src = _zip(tmp_path / "src" / "A1.zip")
    ccat._copy_container_as_is(src, dest_in, "A01")

    assert (dest_in / "A01.zip").exists(), sorted(p.name for p in dest_in.iterdir())
    assert not (dest_in / "A1.zip").exists()


def test_merge_plates_copies_an_unpadded_well_under_its_canonical_name(tmp_path):
    from datasets.merge_plates import apply as mp

    dest_in = tmp_path / "dest" / "in"
    src = _zip(tmp_path / "src" / "A1.zip")
    mp._copy_container_as_is(src, dest_in, "A01")

    assert (dest_in / "A01.zip").exists(), sorted(p.name for p in dest_in.iterdir())
    assert not (dest_in / "A1.zip").exists()


@pytest.mark.parametrize("module,fn", [
    ("datasets.concatenate.apply", "_copy_container_as_is"),
    ("datasets.merge_plates.apply", "_copy_container_as_is"),
])
def test_neither_copy_path_builds_its_destination_from_the_source_name(module, fn):
    """The guard: ``dest_dir / src.name`` is the shape that caused this."""
    import ast
    import importlib
    import inspect
    import textwrap

    mod = importlib.import_module(module)
    src = textwrap.dedent(inspect.getsource(getattr(mod, fn)))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "name"
                and isinstance(node.value, ast.Name) and node.value.id == "src"):
            raise AssertionError(
                f"{module}.{fn} names the destination after the source file "
                "again; it has to come from the well token"
            )
