"""What the window shows in phone layout, compared with a recorded baseline.

PHONE_FORM_TRACKER.md P0.1. The phone-form migration changes how widgets are
found and given their phone form; it must not change what a person sees. This
puts a window with the mini dataset loaded into phone layout, snapshots every
page of every mode (and a few sideways and at the large text size), and
compares each snapshot with ``tests/fixtures/phone_snapshot_baseline.json``.

A difference is read, not regenerated away: a row of the tracker is not done
while this shows a difference it cannot explain. To take a new baseline on
purpose — a row whose change is meant to move a widget — run

    ALLWELL_WRITE_PHONE_SNAPSHOT=1 scripts/test.sh tests/test_phone_layout_snapshot.py -m ""

and say in the row what moved and why.

The numbers depend on the Qt version, the application font and the screen, so
the baseline records those and the test skips, saying so, on a machine whose
fingerprint differs. CI never runs this file (it is a full-app test, marked
``slow``), so the local run is its only gate.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests import phone_snapshot

pytestmark = pytest.mark.slow

_BASELINE = Path(__file__).resolve().parents[1] / phone_snapshot.BASELINE


@pytest.fixture(scope="module")
def scenarios(qapp, tmp_path_factory):
    from allwell import AllWellApp
    from core import phone_layout
    from tests.fixtures.mini_dataset import build_mini_dataset
    from ui import theme

    dataset = build_mini_dataset(tmp_path_factory.mktemp("phone_snapshot") / "mini")
    win = AllWellApp(None)
    win.show()
    phone_snapshot._pump(qapp)
    if win._mode_seg.currentIndex() != 0:
        win._on_titlebar_mode_seg(0)
    win._review._load_path(dataset)
    phone_snapshot._pump(qapp, 40)
    try:
        yield phone_snapshot.collect(win, qapp)
    finally:
        phone_layout.set_on(False)
        theme.apply_ui_scale(1.0)
        win.close()


def test_the_snapshot_covers_every_mode_and_is_not_empty(scenarios):
    """The comparison below is only evidence if it compared something
    (CLAUDE.md §7.3 #1): several pages of each mode, each with widgets."""
    assert len(scenarios) >= 30, sorted(scenarios)
    for prefix in ("review/Home/", "review/Plotting/", "datasets/", "analyze/",
                   "sideways/analyze/", "sideways/review/", "large/review/"):
        assert any(name.startswith(prefix) for name in scenarios), prefix
    assert all(len(snap) > 20 for snap in scenarios.values()), \
        [name for name, snap in scenarios.items() if len(snap) <= 20]


def test_phone_layout_shows_what_the_baseline_recorded(qapp, scenarios):
    here = phone_snapshot.fingerprint(qapp)
    if os.environ.get("ALLWELL_WRITE_PHONE_SNAPSHOT"):
        _BASELINE.parent.mkdir(parents=True, exist_ok=True)
        _BASELINE.write_text(json.dumps({"fingerprint": here, "scenarios": scenarios},
                                        sort_keys=True, separators=(",", ":")) + "\n",
                             encoding="utf-8")
        pytest.skip(f"wrote {_BASELINE} ({len(scenarios)} scenarios)")
    assert _BASELINE.exists(), (
        f"no baseline at {_BASELINE}; write one on the unchanged tree with "
        "ALLWELL_WRITE_PHONE_SNAPSHOT=1")
    recorded = json.loads(_BASELINE.read_text(encoding="utf-8"))
    if recorded["fingerprint"] != here:
        pytest.skip(f"baseline taken under {recorded['fingerprint']}, this machine is {here}: "
                    "its geometry is not comparable")
    base = recorded["scenarios"]
    assert sorted(base) == sorted(scenarios), (
        f"scenarios only in the baseline: {sorted(base.keys() - scenarios.keys())}; "
        f"only now: {sorted(scenarios.keys() - base.keys())}")
    differences = []
    for name in sorted(base):
        lines = phone_snapshot.diff(base[name], scenarios[name], limit=6)
        differences.extend(f"[{name}] {line}" for line in lines)
    assert not differences, "phone layout changed:\n  " + "\n  ".join(differences)
