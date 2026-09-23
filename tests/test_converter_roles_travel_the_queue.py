"""A converter's channel roles travel with its queued job to the Analyze job
chained after it.

Three hops, each tested where it happens: the converter's page writes its role
rows into the queued spec (and reads them back on an edit, before the rows
exist), the queue records them as the finished job's ``layout``, and
``materialise`` hands them to an Analyze successor — to no other kind.
"""

from __future__ import annotations

import pytest

from services.job_kinds import CONVERTER_KINDS, produced_layout
from services.job_queue import JobQueue, JobSpec, JobState


# ── produced_layout ──────────────────────────────────────────────────────────

def test_a_converter_states_its_own_naming_and_the_page_s_roles():
    layout = produced_layout("czi_import", {
        "channel_roles": [["DAPI", "fluor"], ["GFP", "nuclear"], ["BF", "phase"],
                          ["Cy5", "skip"]],
    })
    assert layout == {
        "schema": "experiment:channel:well:fov:timepoint", "separator": "_",
        "nuclear_token": "GFP", "fluor_tokens": ["DAPI", "BF"], "phase_token": "BF",
    }


def test_a_converter_with_no_roles_states_only_its_naming():
    assert produced_layout("nd2_import", {}) == {
        "schema": "experiment:channel:well:fov:timepoint", "separator": "_",
    }


@pytest.mark.parametrize("kind", ["analyze", "subset", "validate", "import_loose"])
def test_other_kinds_state_nothing(kind):
    assert produced_layout(kind, {"channel_roles": [["DAPI", "nuclear"]]}) is None


def test_every_converter_kind_is_one_the_queue_knows():
    from services.job_kinds import KIND_LABEL

    assert set(CONVERTER_KINDS) <= set(KIND_LABEL)


# ── the queue model ──────────────────────────────────────────────────────────

_LAYOUT = {"schema": "experiment:channel:well:fov:timepoint", "separator": "_",
           "nuclear_token": "GFP", "fluor_tokens": ["DAPI"], "phase_token": ""}


def _chain(tmp_path, successor_kind="analyze", target="opts.raw"):
    q = JobQueue()
    q.add(JobSpec(id="conv", kind="ome_import", label="c", spec={}))
    q.add(JobSpec(id="next", kind=successor_kind, label="n", spec={"opts": {}}))
    q.set_chain("next", "conv", chain_target=target, chain_kind="dataset_root")
    q.mark_running("conv")
    q.mark_finished("conv", ok=True, produced={
        "dataset_root": str(tmp_path), "layout": _LAYOUT})
    return q


def test_materialise_hands_an_analyze_successor_the_layout(tmp_path):
    spec = _chain(tmp_path).materialise("next")
    assert spec["opts"]["raw"] == str(tmp_path)
    assert spec["upstream_layout"] == _LAYOUT


def test_materialise_hands_it_to_no_other_kind(tmp_path):
    spec = _chain(tmp_path, successor_kind="validate", target="dataset").materialise("next")
    assert "upstream_layout" not in spec


def test_the_handed_layout_is_a_copy(tmp_path):
    q = _chain(tmp_path)
    q.materialise("next")["upstream_layout"]["nuclear_token"] = "changed"
    assert q.get("conv").layout["nuclear_token"] == "GFP"


def test_the_layout_survives_a_restart_and_retry_clears_it(tmp_path):
    q = _chain(tmp_path)
    again = JobSpec.from_json(q.get("conv").to_json())
    assert again.layout == _LAYOUT
    assert q.retry("conv")
    assert q.get("conv").layout is None
    assert q.get("conv").state == JobState.PENDING


def test_a_failed_converter_records_no_layout(tmp_path):
    q = JobQueue()
    q.add(JobSpec(id="conv", kind="ome_import", label="c", spec={}))
    q.mark_running("conv")
    q.mark_finished("conv", ok=False, message="boom",
                    produced={"layout": _LAYOUT})
    assert q.get("conv").layout is None


# ── the converter page ───────────────────────────────────────────────────────

@pytest.fixture
def converter(qtbot):
    from datasets.ome_import.controller import OmeImportController

    c = OmeImportController()
    qtbot.addWidget(c.page)
    yield c
    c.shutdown()


def _roles(page):
    return page.channel_role_pairs()


def test_collect_spec_carries_the_role_rows(converter):
    page = converter.page
    page._rebuild_channels(["DAPI", "GFP"])
    # Against the defaults, so a spec that merely re-derived them would differ.
    page.set_channel_roles([["DAPI", "fluor"], ["GFP", "nuclear"]])
    assert converter.collect_spec()["channel_roles"] == [["DAPI", "fluor"],
                                                         ["GFP", "nuclear"]]


def test_a_page_with_no_rows_writes_no_roles(converter):
    assert "channel_roles" not in converter.collect_spec()


def test_an_edit_applies_roles_once_the_rows_arrive(converter):
    """``show_job`` names the source; the rows come from reading it, later."""
    page = converter.page
    converter.show_job({"source": "", "destination": "",
                        "channel_roles": [["DAPI", "fluor"], ["GFP", "nuclear"]]})
    page._rebuild_channels(["DAPI", "GFP"])
    assert _roles(page) == [["DAPI", "fluor"], ["GFP", "nuclear"]]
    # Applied once: a later, different source keeps its own defaults.
    page._rebuild_channels(["Hoechst", "GFP"])
    assert _roles(page) == [["Hoechst", "nuclear"], ["GFP", "fluor"]]
