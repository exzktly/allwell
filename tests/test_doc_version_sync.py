"""The docs' *current-release* claims must match ``version.__version__``.

`version.py` is the single source of truth, but two docs also state the
version in prose, and both had silently drifted a release behind by the time
anyone noticed (8.8.22 in the docs while the app shipped 8.8.23):

* ``ARCHITECTURE.md`` — "Reflects All-Well vX.Y.Z", the "v8.4 → vX.Y.Z line"
  range, and the ``version.py`` entry in the directory map.
* ``README_WellViewer.md`` — the ``AllWell-X.Y.Z.app`` build-output names.

This test pins exactly those sites. It deliberately does NOT touch the inline
``(vX.Y.Z)`` feature markers scattered through ARCHITECTURE.md: those record
*when a feature landed* and must keep their historical values.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from version import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHITECTURE = REPO_ROOT / "_Docs" / "Markdowns" / "ARCHITECTURE.md"
README = REPO_ROOT / "_Docs" / "Markdowns" / "README_WellViewer.md"

_VER = r"(\d+\.\d+\.\d+)"

# (file, regex, human description). Each regex must capture the version.
CURRENT_RELEASE_SITES = [
    (ARCHITECTURE, rf"\*\*Reflects All-Well v{_VER}\.\*\*",
     'the "Reflects All-Well vX.Y.Z" header'),
    (ARCHITECTURE, rf"v8\.4 → v{_VER} line",
     'the "v8.4 → vX.Y.Z line" marker range'),
    (ARCHITECTURE, rf"single source of truth for the app version \(v{_VER};",
     "the version.py entry in the directory map"),
    (README, rf"AllWell-{_VER}(?:-|\.app)",
     "the AllWell-X.Y.Z.app build-output names"),
]


@pytest.mark.parametrize(
    "path,pattern,what",
    CURRENT_RELEASE_SITES,
    ids=[w for _, _, w in CURRENT_RELEASE_SITES],
)
def test_doc_current_release_matches_version_py(path: Path, pattern: str, what: str):
    text = path.read_text(encoding="utf-8")
    found = re.findall(pattern, text)
    assert found, (
        f"{path.name}: could not find {what}. If the wording changed, update "
        f"the pattern in this test rather than dropping the check."
    )
    stale = sorted({v for v in found if v != __version__})
    assert not stale, (
        f"{path.name}: {what} says {', '.join(stale)} but version.py says "
        f"{__version__}. Bumping the version means updating these too — they "
        f"state the CURRENT release, not the release a feature landed in."
    )


def test_historical_feature_markers_are_left_alone():
    """Guard the guard: the inline (vX.Y.Z) markers are history, not the current
    release, so this file must never be 'fixed' by rewriting them all."""
    text = ARCHITECTURE.read_text(encoding="utf-8")
    markers = set(re.findall(r"\(v(\d+\.\d+\.\d+)[);,]", text))
    older = {m for m in markers if m != __version__}
    assert older, (
        "Expected ARCHITECTURE.md to retain inline (vX.Y.Z) markers for "
        "releases older than the current one. If every marker now equals "
        f"{__version__}, they were mass-rewritten and the feature history is "
        "lost."
    )
