"""The docs' *current-release* claims must match ``version.__version__`` — and
must be the docs' ONLY version claims.

`version.py` is the single source of truth, but two docs also state the
version in prose, and both had silently drifted a release behind by the time
anyone noticed (8.8.22 in the docs while the app shipped 8.8.23):

* ``ARCHITECTURE.md`` — "Reflects All-Well vX.Y.Z" and the ``version.py`` entry
  in the directory map.
* ``README_WellViewer.md`` — the ``AllWell-X.Y.Z.app`` build-output names.

``ARCHITECTURE.md`` is a running description of how the app is structured
*today*, not a changelog: it must not record which release a feature landed in.
``test_no_introduced_in_version_markers`` holds that line — the inline
``(vX.Y.Z)`` markers it used to carry were removed, and new ones must not
appear. Feature history lives in ``git log`` / ``git blame`` and the PRs.
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


# The two sites above are the only sanctioned version mentions in ARCHITECTURE.md.
_ALLOWED_VERSION_LINES = (
    "**Reflects All-Well v",
    "├── version.py",
)


def test_no_introduced_in_version_markers():
    """ARCHITECTURE.md describes the app as it is now — never when a bit of it
    arrived. Any version reference outside the two current-release claims is an
    "introduced in" marker creeping back in."""
    offenders = []
    for lineno, line in enumerate(ARCHITECTURE.read_text(encoding="utf-8").splitlines(), 1):
        if not re.search(r"v\d+\.\d+(\.\d+)?", line):
            continue
        if any(allowed in line for allowed in _ALLOWED_VERSION_LINES):
            continue
        offenders.append(f"  {lineno}: {line.strip()[:110]}")
    assert not offenders, (
        "ARCHITECTURE.md must not say when a feature landed — it is a running "
        "description of the current architecture, not a changelog. Drop the "
        "version reference (git log / the PR carries that history):\n"
        + "\n".join(offenders)
    )
