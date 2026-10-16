"""The Analyze form's nanowell section is part of the removable Nanowells feature (tracker X33).

``profiles/full.toml`` lists the section's files under Nanowells, so a profile without it builds without them and a
build may delete them. The form reaches the section only through ``analyze.nanowell_hook``: the real mixin, or a
stand-in that is nothing. Held here: the stand-in offers exactly the methods the form calls, with the real one's
signatures; the hook picks it for a profile without Nanowells and for deleted files; and the real ``AnalyzeTab``,
built that way in a fresh interpreter (the base class is chosen when the form's module is imported, so it cannot
be re-chosen in this one), opens, validates and builds the argv it built before the section existed.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import textwrap

import pytest

from analyze import nanowell_hook
from tests._repo_scan import REPO_ROOT

#: The methods of the section the rest of the form calls (form.py, run_spec.py, pinfo.py).
_INTERFACE = ("_build_nanowell_section", "_nanowell_option_values", "_nanowell_validation_error",
              "_apply_nanowell_settings")


def test_the_stand_in_offers_what_the_form_calls_with_the_real_signatures():
    from analyze.nanowell_section import AnalyzeNanowellSectionMixin

    for name in _INTERFACE:
        real = inspect.signature(getattr(AnalyzeNanowellSectionMixin, name))
        stand_in = inspect.signature(getattr(nanowell_hook.NoNanowellSection, name))
        assert list(stand_in.parameters) == list(real.parameters), name


#: The section's modules, which ship with the feature (``profiles/full.toml``).
_REMOVABLE = ("analyze.nanowell_section", "analyze.nanowell_preview", "analyze.nanowell_preflight",
              "analyze.domain.nanowell_fields")
#: Who may import them: each other, and the two guarded sites that stand in nothing when they are gone.
_ALLOWED_IMPORTERS = {"analyze/nanowell_hook.py", "analyze/domain/fields.py"}


def _imported_modules(tree) -> set[str]:
    import ast

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def test_the_form_reaches_the_section_only_through_the_hook():
    """A direct import anywhere else would break a build that deleted the files."""
    from tests._repo_scan import parsed_sources, rel

    offenders = []
    for path, tree in parsed_sources():
        name = rel(path)
        if name in _ALLOWED_IMPORTERS or name[:-3].replace("/", ".") in _REMOVABLE:
            continue
        offenders += [f"{name}: {mod}" for mod in sorted(_imported_modules(tree) & set(_REMOVABLE))]
    assert offenders == []


@pytest.mark.parametrize("profile, blocked, expected", [
    ("full", False, "AnalyzeNanowellSectionMixin"),
    ("thin", False, "NoNanowellSection"),           # a profile without Nanowells
    ("full", True, "NoNanowellSection"),            # the files deleted from a build
])
def test_the_hook_picks_the_section_by_profile_and_presence(monkeypatch, profile, blocked, expected):
    monkeypatch.setenv("ALLWELL_PROFILE", profile)
    if blocked:
        class _Blocker:
            def find_spec(self, name, path=None, target=None):
                if name == "analyze.nanowell_section":
                    raise ModuleNotFoundError(f"blocked (simulated deletion): {name}")
                return None

        monkeypatch.delitem(sys.modules, "analyze.nanowell_section", raising=False)
        monkeypatch.setattr(sys, "meta_path", [_Blocker(), *sys.meta_path])
    assert nanowell_hook.nanowell_section_mixin().__name__ == expected


_STRIPPED_FORM = textwrap.dedent("""
    import sys
    from pathlib import Path

    REMOVED = ("analyze.nanowell_section", "analyze.nanowell_preview", "analyze.nanowell_preflight",
               "analyze.domain.nanowell_fields", "review.features.nanowell")

    class Blocker:
        def find_spec(self, name, path=None, target=None):
            if any(name == m or name.startswith(m + ".") for m in REMOVED):
                raise ModuleNotFoundError("deleted: " + name)
            return None

    sys.meta_path.insert(0, Blocker())
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    import analyze.form
    analyze.form.load_remotes = lambda: {}
    from analyze.tab import AnalyzeTab
    from services.pipeline_service import build_pipeline_args

    tab = AnalyzeTab(parent=None)
    assert not hasattr(tab, "_nanowell_cb"), "the section was built"
    spec = tab._collect_run_spec()
    assert not any(k.startswith("nanowell_") for k in spec.opts), sorted(spec.opts)
    from analyze.domain.fields import _numeric_option_errors      # what the Run button's validation calls
    assert _numeric_option_errors(spec.opts) == [], _numeric_option_errors(spec.opts)
    argv = build_pipeline_args(Path("pm.py"), Path("in"), Path("out"), spec.opts)
    assert not any(a.startswith("--nanowell") for a in argv), argv
    tab._apply_pipeline_info({"execution_options": {"nanowell_format": "100um", "segmentation_method": "cellpose"}})
    assert not any(m.startswith(REMOVED) for m in sys.modules), [m for m in sys.modules if m.startswith(REMOVED)]
    print("STRIPPED FORM OK")
""")


def test_the_analyze_form_opens_and_runs_without_the_section(tmp_path):
    env = dict(os.environ, ALLWELL_PROFILE="thin", QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(REPO_ROOT))
    script = tmp_path / "stripped_form.py"
    script.write_text(_STRIPPED_FORM)
    done = subprocess.run([sys.executable, str(script)], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                          timeout=120)
    assert "STRIPPED FORM OK" in done.stdout, done.stdout[-2000:] + done.stderr[-4000:]
