"""A dataset destination named ``in`` is refused before anything is written.

Every Datasets tool that writes a dataset writes ``<destination>/in/<WELL>.zip``.
A destination that is itself called ``in`` therefore produced ``in/in/``, and a
queued Analyze chained after the conversion was handed that ``in`` folder,
treated it as the input itself (``plan_input_layout``'s named-``in`` branch),
found no per-well zips in it and failed — after the conversion had already
succeeded and written the whole plate somewhere Analyze could not see it.
"""

from __future__ import annotations

import zipfile

import pytest

from datasets.core import convert_marker
from datasets.core.dataset_fields import is_free_destination
from datasets.core.dataset_paths import named_in_problem
from services.input_resolution_service import plan_input_layout


@pytest.mark.parametrize("name", ["in", "IN", "In"])
def test_a_folder_named_in_is_refused(tmp_path, name):
    message = named_in_problem(tmp_path / name)
    assert message.startswith("Named “in”")
    assert "in/in/" in message


@pytest.mark.parametrize("name", ["plate", "inputs", "in_vitro", "bin"])
def test_other_names_are_not(tmp_path, name):
    assert named_in_problem(tmp_path / name) == ""


def test_the_layout_it_would_produce_is_one_analyze_cannot_run(tmp_path):
    """Why the rule exists, measured: the ``in/in/`` a conversion into ``in``
    writes is unrunnable from the folder the queue hands Analyze."""
    dest = tmp_path / "in"
    (dest / "in").mkdir(parents=True)
    with zipfile.ZipFile(dest / "in" / "A01.zip", "w") as z:
        z.writestr("exp_DAPI_A01_f00_T0.tif", b"")
    plan = plan_input_layout(dest)
    assert not plan.runnable
    assert "No per-well zips" in plan.message


def test_the_converters_shared_rule_refuses_it(tmp_path):
    assert convert_marker.destination_problem(
        tmp_path / "in", ".aw_x_in_progress") == named_in_problem(tmp_path / "in")
    assert convert_marker.destination_problem(
        tmp_path / "plate", ".aw_x_in_progress") == ""


def test_the_copying_tools_shared_rule_refuses_it(tmp_path):
    never = lambda _dest: False  # noqa: E731
    assert is_free_destination(tmp_path / "in", is_complete=never,
                               has_unrecognized=never).startswith("Named “in”")
    assert is_free_destination(tmp_path / "plate", is_complete=never,
                               has_unrecognized=never) == ""


@pytest.mark.parametrize("tool, params", [
    ("czi_import", {}),
    ("ome_import", {"grid_rows": 1, "grid_cols": 1}),
    ("nd2_import", {}),
])
def test_each_converter_refuses_it_at_run_time(tmp_path, tool, params):
    """At run time and not only in the page's field hint: a queued conversion
    reaches ``convert`` through ``start_from_queue`` without the field ever
    being consulted. Nothing may be written — the refusal comes before the
    source is even read (the source here does not exist)."""
    import importlib

    apply = importlib.import_module(f"datasets.{tool}.apply")
    compute = importlib.import_module(f"datasets.{tool}.{tool}_compute")
    dest = tmp_path / "in"
    with pytest.raises(Exception) as excinfo:
        apply.convert(tmp_path / "missing", dest, compute.ConversionParams(**params))
    assert "Named “in”" in str(excinfo.value), excinfo.value
    assert not (dest / "in").exists()
