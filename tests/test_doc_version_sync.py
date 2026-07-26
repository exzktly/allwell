"""The docs must not state the app version at all — ``version.py`` owns it.

The two live docs used to repeat the current release in three places
(``ARCHITECTURE.md``'s "Reflects All-Well vX.Y.Z" header and its ``version.py``
directory-map entry, plus ``README_WellViewer.md``'s ``AllWell-X.Y.Z.app`` build
names), and a test pinned all three to ``version.__version__``. That made every
release a four-file edit, and it had two costs that showed up repeatedly:

* the numbers drifted anyway when someone edited one site and not the others
  (8.8.22 in the docs while the app shipped 8.8.23; an ``AllWell-8.8.9-thin``
  example that outlived several releases because no pattern covered it);
* with more than one branch open at a time — the normal state here — every
  release bump collided in *four* files instead of one, and resolving those
  conflicts is itself a way to lose unrelated prose from the same file.

So the docs now describe the version's *home* rather than its value, and this
module enforces that: no three-part version literal outside a short allowlist of
third-party pins. Bumping a release is a one-line edit to ``version.py``, and the
only merge conflict a concurrent bump can cause is on that line.

``ARCHITECTURE.md`` is a running description of how the app is structured
*today*, not a changelog: it must not record which release a feature landed in
either. ``test_no_introduced_in_version_markers`` holds that line.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHITECTURE = REPO_ROOT / "_Docs" / "Markdowns" / "ARCHITECTURE.md"
README = REPO_ROOT / "_Docs" / "Markdowns" / "README_WellViewer.md"

_VER_RE = re.compile(r"\b\d+\.\d+\.\d+\b")

# Third-party pins the install/build sections legitimately state. These are
# versions of *other* projects, so they neither drift with our releases nor
# collide when two branches bump All-Well. Matched as substrings of the line.
_THIRD_PARTY_PINS = (
    "tensorflow", "torch", "Torch", "cellpose", "trackastra", "btrack",
    "scikit-learn", "umap-learn", "typing-extensions", "numpy", "pandas",
    "PySide6", "matplotlib", "scipy", "python", "Python", "ruff", "pytest",
    "protobuf", "keras", "csbdeep", "stardist", "tifffile", "Pillow",
    "shiboken6", "mamba create",
)


@pytest.mark.parametrize("path", [ARCHITECTURE, README], ids=lambda p: p.name)
def test_docs_state_no_app_version(path: Path):
    """No hardcoded All-Well version anywhere in the live docs.

    Write ``<version>`` (or name ``version.py``) instead of a literal. A number
    here is a fourth place to update on every release and a fourth file to
    conflict in when two branches bump at once — and it goes stale silently,
    because nothing renders the docs against the app.
    """
    offenders = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not _VER_RE.search(line):
            continue
        if any(pin in line for pin in _THIRD_PARTY_PINS):
            continue
        offenders.append(f"  {lineno}: {line.strip()[:110]}")
    assert not offenders, (
        f"{path.name} states a version literal. version.py is the single source "
        "of truth — write `<version>` and point at it, or (if this is a "
        "third-party pin) add the package to _THIRD_PARTY_PINS:\n"
        + "\n".join(offenders)
    )


def test_no_introduced_in_version_markers():
    """ARCHITECTURE.md describes the app as it is now — never when a bit of it
    arrived. A ``vX.Y`` reference is an "introduced in" marker creeping back in;
    that history lives in ``git log`` / ``git blame`` and the PRs."""
    offenders = []
    for lineno, line in enumerate(ARCHITECTURE.read_text(encoding="utf-8").splitlines(), 1):
        if not re.search(r"v\d+\.\d+(\.\d+)?", line):
            continue
        if any(pin in line for pin in _THIRD_PARTY_PINS):
            continue
        offenders.append(f"  {lineno}: {line.strip()[:110]}")
    assert not offenders, (
        "ARCHITECTURE.md must not say when a feature landed — it is a running "
        "description of the current architecture, not a changelog. Drop the "
        "version reference:\n" + "\n".join(offenders)
    )
