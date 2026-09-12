"""The smFISH tab's placeholder reason, and which state it reads it from.

Reported against a dataset whose sidecar carries ``"smfish_tokens": ["w4Cy5"]``,
whose CSVs already hold spot counts, and whose other tabs plot smFISH happily:
opening the tab showed *"No smFISH data was generated during the processing
pipeline."* Leaving the tab and coming back, it worked.

The reason was decided from ``ctx.runtime.smfish.tokens`` — this tab's own
scratch list, not the sidecar. On the first open of a lazily-built tab the order
is: the builder sets that list to ``[]``, the shared refresh computes the reason
and paints it, and only then does ``smfish_sync_from_app`` fill the tokens in.
So the message was produced at the one moment the list is guaranteed empty, and
any later refresh cleared it — exactly the transience reported.

Reading the sidecar the loader already parsed makes the answer independent of
build-versus-sync order. It also splits one sentence into the two different
claims it was making: "the run had no smFISH channel" is about the analysis,
"the sidecar could not be read" is about a file, and printing the first for both
sent users to re-run an analysis that was fine.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from review.features.smfish.tab_view import _smfish_empty_reason


def _ctx(pipeline_info: dict, *, runtime_tokens=(), wells=("A01",)):
    return SimpleNamespace(
        state=SimpleNamespace(
            dataset=SimpleNamespace(pipeline_info=pipeline_info,
                                    well_paths={w: "/tmp" for w in wells}),
            selection=SimpleNamespace(selected_wells=set(wells), selections={}),
        ),
        runtime=SimpleNamespace(smfish=SimpleNamespace(tokens=list(runtime_tokens))),
    )


def test_the_sidecar_wins_over_the_unfilled_scratch_list():
    """The reported bug, in the exact state the first open passes through."""
    reason = _smfish_empty_reason(
        _ctx({"smfish_tokens": ["w4cy5"]}, runtime_tokens=()))
    assert reason is None or "pipeline" not in (reason.title or "").lower(), (
        f"the tab claimed the run generated no smFISH data: {reason}")


def test_a_run_with_no_smfish_channel_still_says_so():
    """The counter-test: the message must survive for the case it is true of."""
    reason = _smfish_empty_reason(_ctx({"smfish_tokens": [], "separator": "_"}))
    assert reason is not None
    assert "No smFISH data was generated" in reason.title


def test_an_unreadable_sidecar_says_that_instead():
    """An empty parsed block means the file was missing or unparseable.

    ``read_pipeline_info`` returns ``{}`` for both, and neither is a statement
    about what the pipeline produced.
    """
    reason = _smfish_empty_reason(_ctx({}))
    assert reason is not None
    assert "pipeline_info.json" in reason.title
    assert "No smFISH data was generated" not in reason.title


def test_the_well_prompt_still_ranks_below_both():
    reason = _smfish_empty_reason(
        _ctx({"smfish_tokens": ["w4cy5"]}, wells=()))
    assert reason is not None
    assert reason.title == "No well selected."


@pytest.mark.parametrize("tokens", [["w4cy5"], ["gfp", "mcherry"]])
def test_a_selected_well_on_an_smfish_run_shows_the_tab(tokens):
    assert _smfish_empty_reason(_ctx({"smfish_tokens": tokens})) is None


# ── the same thing on a real app, through the first activation ───────────────

def test_the_tab_does_not_deny_its_own_data_on_first_open(loaded_app_smfish):
    """Open the smFISH tab once, on a dataset that has smFISH, and read it.

    The unit tests above pin the reason function; this pins the path the
    reporter actually walked — a lazily-built tab activated for the first time,
    with no intervening selection change to refresh the placeholder behind it.
    """
    from PySide6.QtWidgets import QLabel

    app = loaded_app_smfish
    app._notebook.setCurrentByName("smFISH")
    app._drain_pending_centre_builders()

    shown: list[str] = []
    for stack in (getattr(app, "_plot_empty_stacks", None) or []):
        if stack.currentIndex() != 0:
            continue
        shown.extend(lbl.text() for lbl in stack.widget(0).findChildren(QLabel)
                     if lbl.text())
    assert not any("No smFISH data was generated" in t for t in shown), (
        f"the tab denies data the dataset has — placeholders showing {shown}")
