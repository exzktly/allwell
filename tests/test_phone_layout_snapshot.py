"""What the window shows in phone layout, compared with a recorded baseline.

The phone-form migration changes how widgets are
found and given their phone form; it must not change what a person sees. This
puts a window with the mini dataset loaded into phone layout, snapshots every
page of every mode (and a few sideways and at the large text size), and
compares each snapshot with ``tests/fixtures/phone_snapshot_baseline.json``. It runs on the phone's screen,
432×936 (``tests/_screens.FakeScreen``), and sideways is that phone turned: Qt's own offscreen screen is a
800×800 square, which no phone is.

A difference is read, not regenerated away: a change to how phone layout finds
its widgets is not finished while this shows a difference it cannot explain. To take a new baseline on
purpose — a row whose change is meant to move a widget — run

    ALLWELL_WRITE_PHONE_SNAPSHOT=1 scripts/test.sh tests/test_phone_layout_snapshot.py -m ""

and say in the row what moved and why.

The numbers depend on the Qt version, the application font and the screen, so
the baseline records those and the test skips, saying so, on a machine whose
fingerprint differs. The PR gate never runs this file (it is a full-app test,
marked ``slow``); the slow lane does, and installs Inter, a fontconfig rule that
prefers it for sans-serif, and the face the baseline draws ⓘ from, so the runner
resolves the same fonts and compares rather than skipping. The dataset is built at
one fixed path (:data:`SNAPSHOT_DATASET`), because its path is drawn on some pages
and its length moves the lines that wrap.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from tests import phone_snapshot

pytestmark = pytest.mark.slow

_BASELINE = Path(__file__).resolve().parents[1] / phone_snapshot.BASELINE


@pytest.fixture(scope="module")
def phone_screen():
    from tests._screens import PHONE_UPRIGHT, FakeScreen
    with FakeScreen(PHONE_UPRIGHT) as screen:
        yield screen


#: Where the snapshot's dataset is built: the same path on every machine. The dataset's path reaches text the
#: pages draw (the Analyze form's folder fields, Home's cards), and its length decides where those lines wrap — so a
#: dataset under pytest's numbered temp directory (``/tmp/pytest-of-<user>/pytest-<n>/…``) measured differently on
#: every machine and every run count: the CI runner's ``pytest-of-runner`` path put a Home card 17 px lower than the
#: baseline, with identical fonts. Not under ``tempfile.gettempdir()``, which follows ``TMPDIR`` and so differs too.
SNAPSHOT_DATASET = Path("/tmp/allwell-phone-snapshot/mini")


@pytest.fixture(scope="module")
def scenarios(qapp, phone_screen):
    from allwell import AllWellApp
    from core import phone_layout
    from tests._screens import place
    from tests.fixtures.mini_dataset import build_mini_dataset
    from ui import theme

    shutil.rmtree(SNAPSHOT_DATASET.parent, ignore_errors=True)
    dataset = build_mini_dataset(SNAPSHOT_DATASET)
    win = AllWellApp(None)
    win.show()
    place(win, phone_screen.rect, qapp)
    if win._mode_seg.currentIndex() != 0:
        win._on_titlebar_mode_seg(0)
    win._review._load_path(dataset)
    phone_snapshot._pump(qapp, 40)
    try:
        yield phone_snapshot.collect(win, qapp, phone_screen)
    finally:
        phone_layout.set_on(False)
        theme.apply_ui_scale(1.0)
        shutil.rmtree(SNAPSHOT_DATASET.parent, ignore_errors=True)
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


def test_phone_layout_shows_what_the_baseline_recorded(qapp, scenarios, phone_screen):
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
