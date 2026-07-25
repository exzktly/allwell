"""Build profiles — the single declaration of *which* features a build ships.

A **profile** names a subset of the feature registry (top-level features + an
optional per-parent sub-tab allowlist). It is the seam that lets one codebase run
as the full app *or* any thin subset without a fork:

* ``build_feature_registry(profile)`` keeps only the descriptors the profile
  admits; the existing removability contract (ARCHITECTURE §5.3) drops each
  excluded feature's rail entry, dispatch, state slice, and persistence for free.
* The active profile is chosen by the ``ALLWELL_PROFILE`` env var. Unset (the
  default) is the **full** app — every declared feature. Set it to a name and the
  matching ``profiles/<name>.toml`` at the repo root is loaded, so
  ``ALLWELL_PROFILE=thin python -m allwell`` runs the thin subset off the *full*
  tree, no code deleted.

Profiles are also what the offline generator (``scripts/derive_profile.py``)
consumes to produce a physically-stripped tree for distribution.

Manifest shape (TOML)::

    name = "thin"
    features = ["Plotting", "Sample Definitions", "Image Browser"]

    [subtabs]                 # optional; omit a parent to keep all its sub-tabs
    "Image Browser" = ["Segmentation"]

Stdlib-only (``tomllib`` on 3.11+, ``tomli`` on 3.10 — the project's pin);
no Qt / feature imports, so importing this is cheap and safe from the pipeline
side too. This module is imported at app startup (the registry reads the active
profile), so the TOML backend must resolve on Python 3.10 — a bare
``import tomllib`` would raise ModuleNotFoundError before the app can load.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:  # tomllib is stdlib on Python 3.11+; fall back to tomli on 3.10 (project pin).
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on interpreter version
    import tomli as tomllib  # type: ignore[no-redef]

_ENV_VAR = "ALLWELL_PROFILE"


def _repo_root() -> Path:
    # In a PyInstaller bundle the baked ``active_profile.txt`` marker and the
    # ``profiles/`` dir live at the bundle root (``sys._MEIPASS``), which is NOT
    # ``parents[2]`` of this module's ``__file__`` on every layout — a macOS
    # ``.app`` splits code from data, so the marker written to the bundle root
    # would be missed and ``active_profile()`` would silently fall back to the
    # full app over a thin (stripped) bundle, crashing at startup. Prefer
    # ``_MEIPASS`` when frozen (as ``core/app_fonts.py`` and
    # ``all_well_launcher.py`` do); fall back to the source tree in a dev
    # checkout, where ``well_viewer/core/profile.py`` -> repo root is two
    # parents up from the package.
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parents[2]


def profiles_dir() -> Path:
    return _repo_root() / "profiles"


@dataclass(frozen=True)
class Profile:
    """A resolved build profile.

    ``features`` is ``None`` for the full app (admit everything) or the frozenset
    of top-level feature names to keep. ``subtabs`` optionally restricts a
    composite parent's sub-tabs; a parent absent from the map keeps all of them.
    """

    name: str = "full"
    features: frozenset | None = None
    subtabs: dict = field(default_factory=dict)

    def includes(self, descriptor) -> bool:
        """Whether *descriptor* (a ``FeatureDescriptor``) survives this profile."""
        if self.features is None:
            return True
        if descriptor.parent:
            if descriptor.parent not in self.features:
                return False
            allowed = self.subtabs.get(descriptor.parent)
            return allowed is None or descriptor.name in allowed
        return descriptor.name in self.features

    def includes_subtab(self, parent: str, subtab: str) -> bool:
        """Whether a composite *parent*'s *subtab* leaf survives this profile.

        Used by composite containers (Image Browser) that enumerate their own
        sub-tabs outside the descriptor list.
        """
        if self.features is not None and parent not in self.features:
            return False
        allowed = self.subtabs.get(parent)
        return allowed is None or subtab in allowed


#: The full app — admit every declared feature. Explicit sentinel so call sites
#: and tests can ask for "no filtering" without reaching for ``None`` semantics.
FULL = Profile(name="full", features=None, subtabs={})


def load_profile(source) -> Profile:
    """Load a profile from a name (``profiles/<name>.toml``) or a manifest path."""
    p = Path(source)
    if not p.suffix:
        p = profiles_dir() / f"{source}.toml"
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    name = str(data.get("name") or p.stem)
    feats = data.get("features")
    features = None if feats is None else frozenset(str(f) for f in feats)
    raw_sub = data.get("subtabs") or {}
    subtabs = {str(k): frozenset(str(x) for x in v) for k, v in raw_sub.items()}
    return Profile(name=name, features=features, subtabs=subtabs)


#: Marker file the PyInstaller build writes into the bundle root to bake the
#: chosen profile, since a frozen binary has no ``ALLWELL_PROFILE`` env at run
#: time (see ``all_well.spec``). Absent in a dev checkout → the full app.
_BAKED_MARKER = "active_profile.txt"


def active_profile() -> Profile:
    """Resolve the active profile.

    Precedence: the ``ALLWELL_PROFILE`` env var (dev / explicit override), then a
    baked ``active_profile.txt`` marker in the bundle root (set at build time),
    then the full feature set.
    """
    name = os.environ.get(_ENV_VAR, "").strip()
    if not name:
        marker = _repo_root() / _BAKED_MARKER
        try:
            if marker.exists():
                name = marker.read_text(encoding="utf-8").strip()
        except OSError:
            name = ""
    if not name or name.lower() == "full":
        return FULL
    return load_profile(name)
