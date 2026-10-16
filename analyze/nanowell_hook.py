"""Where the Analyze form meets the removable Nanowells feature.

The form's nanowell section (``analyze/nanowell_section.py`` with its preview, pre-flight and field rules) ships
with the Nanowells feature, like Review's page: ``profiles/full.toml`` lists its files under that feature, so a
profile that leaves Nanowells out builds without them and a build may delete them. The form is assembled from
mixins, so the section joins it as one of its bases — chosen here, once, when the form's module is imported:

* the real :class:`~analyze.nanowell_section.AnalyzeNanowellSectionMixin` when the active profile includes
  Nanowells and its module can be imported;
* otherwise :class:`NoNanowellSection`, which builds nothing, contributes no run options, validates nothing and
  restores nothing. Every reader downstream already reads an absent ``nanowell_format`` as the mode off
  (``services.pipeline_service._emit_nanowell_flags``, ``analyze.domain.nanowell_fields``), so a build without the
  section emits exactly the argv it did before the section existed.

These four methods are the whole interface the rest of the form calls; ``tests/test_nanowell_removable.py`` holds
the stand-in to the real section's signatures, and builds the form both ways.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: The feature's name in the registry and the profiles.
FEATURE = "Nanowells"


class NoNanowellSection:
    """The form's nanowell section in a build without the Nanowells feature: nothing at all."""

    def _build_nanowell_section(self, parent) -> None:
        return None

    def _nanowell_option_values(self) -> dict:
        return {}

    def _nanowell_validation_error(self, *, check_input: bool, for_run: bool = True) -> str | None:
        return None

    def _apply_nanowell_settings(self, d: dict) -> None:
        return None


def nanowells_in_build() -> bool:
    """Whether the active profile ships the Nanowells feature."""
    from core.profile import active_profile

    features = active_profile().features
    return features is None or FEATURE in features


def nanowell_section_mixin() -> type:
    """The section mixin the form is built with: the real one, or :class:`NoNanowellSection`."""
    if not nanowells_in_build():
        return NoNanowellSection
    try:
        from analyze.nanowell_section import AnalyzeNanowellSectionMixin
    except ModuleNotFoundError as exc:
        # Profiled in but deleted from disk: a build is allowed to delete a feature's files, and the form must
        # still open. Logged, because a full build that lost the module is a packaging fault worth seeing.
        log.warning("The Nanowells section is not in this build (%s); the Analyze form opens without it", exc)
        return NoNanowellSection
    return AnalyzeNanowellSectionMixin
