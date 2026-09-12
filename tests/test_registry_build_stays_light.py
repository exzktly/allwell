"""Building the feature registry imports no feature and no data layer.

``core/feature_registry.py`` states the contract in its own docstring: every
hook callable is lazy, "so importing the registry does not eagerly pull heavy
feature deps". Assembling the descriptors is meant to be cheap enough that a
caller who only wants to *read* them — a guard counting service keys, a profile
tool asking which features a build would keep — pays nothing for the features
themselves.

Wiring the row-cache projectors into ``build_feature_registry`` broke that
quietly. Registering the resolved ``apply_qc_to_cache`` meant importing
``features/qc/controller``, and through ``data/curation`` the whole data layer
and pandas with it: measured at 15ms before and 6.6s after, in a process that
had not already imported them. The app never noticed, because ``runtime_app``
imports ``data_loading`` at module scope regardless — which is exactly why a
test that measured this from inside the suite would have seen nothing.

So it runs in a subprocess that imports the registry and nothing else. The
projectors are registered as lazy wrappers now, resolving on first call the way
every hook does.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Modules whose presence proves the build resolved a projector eagerly. The
#: data layer is the tell for curation, and the two controllers for QC / crop.
_MUST_NOT_LOAD = (
    "pandas",
    "review.data.curation",
    "review.data.data_loading",
    "review.features.qc.controller",
    "review.features.crop.controller",
)

_PROBE = textwrap.dedent(
    """
    import sys
    from review.features.registry import build_feature_registry

    registry = build_feature_registry()
    assert registry.all(), "the probe built an empty registry"
    print(",".join(m for m in {names!r} if m in sys.modules))
    """
)


def _modules_loaded_by_a_registry_build() -> list[str]:
    pytest.importorskip("PySide6")
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE.format(names=_MUST_NOT_LOAD)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, (
        f"the probe process failed, so it measured nothing:\n{proc.stderr}")
    return [m for m in proc.stdout.strip().split(",") if m]


def test_building_the_registry_pulls_in_no_feature_or_data_module():
    loaded = _modules_loaded_by_a_registry_build()
    assert not loaded, (
        "building the feature registry imported "
        + ", ".join(loaded)
        + " — a descriptor or a projector is being resolved at build time "
        "instead of on first call. Register a lazy wrapper (see "
        "features/registry._lazy_projector) rather than the function itself."
    )


def test_the_probe_would_notice_an_eager_import():
    """The guard is only worth having if its probe can come back non-empty.

    Asserted by importing one of the watched modules in the same shape the
    probe measures, rather than by trusting that an empty result means the
    contract holds — an empty result is also what a broken probe returns.
    """
    pytest.importorskip("PySide6")
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys, pandas\n"
         "from review.features.registry import build_feature_registry\n"
         "build_feature_registry()\n"
         f"print(','.join(m for m in {_MUST_NOT_LOAD!r} if m in sys.modules))"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    assert "pandas" in proc.stdout, (
        "the probe reported nothing loaded in a process that had just imported "
        "pandas — it is not measuring what it claims to")
