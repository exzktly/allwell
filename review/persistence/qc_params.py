"""QC-layer threshold persistence — ``qc_params`` section of ``persistence.json``.

Stores the live debris-gating thresholds edited in the QC sidebar view so the
curation survives between sessions. When no persisted edits exist, this loader
clears ``app._app_state.qc.params`` and the QC controller's accessor lazily seeds it from
the dataset's ``pipeline_info.json`` (the thresholds used at run time) — so a
freshly-processed dataset opens with the run's QC settings already in place,
without this data-only module importing feature code.

Lives in the consolidated ``persistence.json`` document (like ``ratios`` /
``heatmap_layouts`` / ``line_order`` / ``view_state``), not a standalone sidecar.
A legacy standalone ``qc_params.json`` is absorbed into the section on first load
and then deleted.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from review.domain.qc_params import QCParams
from review.persistence import _doc

_logger = logging.getLogger("review")

_SECTION = "qc_params"
_LEGACY_FILENAME = "qc_params.json"


def run_recorded_cut(ctx) -> float | None:
    """The focus cut this dataset's own run wrote into its sidecar.

    Read through :meth:`QCParams.from_pipeline_info` rather than by reaching
    into the block, so the sidecar is parsed in exactly one place and a
    recorded ``0.0`` — which is how the pipeline spells "the run stated no
    cut" — means the same thing here as it does at seeding time.
    """
    return QCParams.from_pipeline_info(ctx.state.dataset.pipeline_info).focus_min


def save_to_data_dir(ctx) -> None:
    app = ctx.window  # transitional bridge (B4)
    if not app._app_state.dataset.data_dir:
        return
    params = app._app_state.qc.params
    if params is None:
        return
    payload = params.to_dict()
    # **The focus cut is stored only when the user's number differs from the
    # run's.** Every other threshold on this panel is the user's own from the
    # moment they touch anything; the focus cut is the one that also has an
    # authoritative source, because the run records it and re-recording it is
    # deliberately cheap (``focus_min`` is outside the reuse key, so a re-run
    # that changes only the cut skips every well and still rewrites the
    # sidecar).
    #
    # Storing it unconditionally made that cheap path silently wrong. This
    # section is written whenever ANY control on the panel moves -- the debris
    # checkbox, a bound, the field-quality box -- so merely opening the panel
    # once froze whatever cut was seeded. A later re-run recording 0.12 then
    # left Analyze showing 0.12 and Review showing 0.08, one dataset with two
    # numbers and nothing anywhere saying so.
    #
    # Omitting the key when it agrees with the run means "I have not overridden
    # this", and the loader re-seeds from the sidecar. A user who types the
    # run's own number loses nothing by it: the value is identical until the
    # run's number changes, and at that point adopting the new one is what
    # they would have wanted anyway.
    if payload.get("focus_min") == run_recorded_cut(ctx):
        payload.pop("focus_min", None)
    _doc.set_section(ctx, _SECTION, payload)


def load_from_data_dir(ctx) -> None:
    app = ctx.window  # transitional bridge (B4)
    if not app._app_state.dataset.data_dir:
        return
    data = _doc.get_section(ctx, _SECTION)
    if data is None:
        data = _absorb_legacy_sidecar(app)
    if data is None:
        # No persisted edits — clear any stale params from a previously open
        # dataset. The QC controller's accessor (``get_qc_params``) lazily
        # re-seeds from this dataset's ``pipeline_info`` thresholds on first
        # read (the very next step in the load sequence is ``apply_qc_to_cache``,
        # which reads through that accessor). Keeping the seed there — rather
        # than calling a *feature* function from this persistence module —
        # is what lets ``persistence`` stay data-only (see test_layering).
        app._app_state.qc.params = None
        return
    params = QCParams.from_dict(data)
    if "focus_min" not in data:
        # Not overridden (see ``save_to_data_dir``) -- so the run's own
        # recorded cut is the answer, including when a later run changed it.
        params = params.with_updates(focus_min=run_recorded_cut(ctx))
    app._app_state.qc.params = params


def _legacy_path(app) -> Path | None:
    d = app._app_state.dataset.data_dir
    return Path(d) / _LEGACY_FILENAME if d else None


def _absorb_legacy_sidecar(app) -> dict | None:
    """Migrate a legacy standalone ``qc_params.json`` into the persistence doc.

    The old sidecar wrapped the params as ``{"schema_version": …, "qc_params":
    {…}}``, so (unlike the raw-format sidecars handled by ``_doc``'s generic
    migrator) it must be unwrapped. Write the inner dict into the ``qc_params``
    section, then delete the legacy file — but only after the section is written,
    so a crash mid-migration leaves it for a retry. Returns the inner dict, or
    None when the legacy file is absent / unreadable.
    """
    legacy = _legacy_path(app)
    if legacy is None or not legacy.exists():
        return None
    try:
        inner = (json.loads(legacy.read_text()) or {}).get("qc_params")
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Legacy %s unreadable during migration: %s", legacy, exc)
        return None
    if not isinstance(inner, dict):
        return None
    _doc.set_section(app._ctx, _SECTION, inner)      # absorb into persistence.json
    try:
        legacy.unlink()
    except OSError as exc:
        _logger.warning("Could not remove legacy %s: %s", legacy, exc)
    else:
        _logger.info("Migrated legacy %s into persistence.json", legacy.name)
    return inner
