"""Startup validation of the ``ALLWELL_*`` environment overrides.

Audit X12. The app honours a handful of environment variables
(:envvar:`ALLWELL_SCRATCH_DIR`, :envvar:`ALLWELL_REMOTES`,
:envvar:`ALLWELL_SEG_DEBUG`). Each is read lazily deep inside the code that
uses it, and a mistyped path silently falls back to a default — the scratch
override degrades to the OS temp dir (potentially filling a small system
drive), and a bad ``ALLWELL_REMOTES`` path yields an empty remotes list with
no hint as to why. That is invisible until a run misbehaves.

:func:`validate_env` is a **pure** function: it takes an environment mapping
(defaulting to :data:`os.environ`) and returns a list of human-readable
warning strings for any override that is set but unusable, plus a list of
informational lines describing the active overrides. The caller (``main``)
logs both once at startup so a misconfiguration is visible immediately rather
than at first failure. Nothing here raises or falls back — validation only
observes; the consuming code keeps its own fallback behaviour.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from collections.abc import Mapping

logger = logging.getLogger(__name__)


def _check_scratch_dir(value: str) -> str | None:
    """Return a warning if ``ALLWELL_SCRATCH_DIR`` is set but unusable.

    Mirrors :func:`services.remote_scratch._scratch_dir`: the override must be a
    directory we can create. We only *probe* here (create-if-missing, same as
    the real path) — we never delete, and we never fall back.
    """
    try:
        p = Path(value).expanduser()
        p.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return (
            f"ALLWELL_SCRATCH_DIR={value!r} is unusable ({exc}); remote-staging "
            "scratch will fall back to the OS temp dir, which may fill a small "
            "system drive with GB-sized transit copies."
        )
    if not p.is_dir():
        return (
            f"ALLWELL_SCRATCH_DIR={value!r} exists but is not a directory; "
            "remote-staging scratch will fall back to the OS temp dir."
        )
    return None


def _check_remotes_file(value: str) -> str | None:
    """Return a warning if ``ALLWELL_REMOTES`` points at a missing file.

    Mirrors :func:`services.remote_config.remotes_config_path`: the override is
    the path to ``remotes.toml``. A missing file means the remotes list loads
    empty and remote execution silently offers no hosts.
    """
    p = Path(value).expanduser()
    if not p.exists():
        return (
            f"ALLWELL_REMOTES={value!r} does not exist; the remote-host list "
            "will load empty and remote execution will offer no hosts."
        )
    if p.is_dir():
        return (
            f"ALLWELL_REMOTES={value!r} is a directory, not a remotes.toml "
            "file; the remote-host list will load empty."
        )
    return None


def validate_env(env: Mapping[str, str] | None = None) -> tuple[list[str], list[str]]:
    """Validate the ``ALLWELL_*`` overrides.

    Returns ``(warnings, active)`` where *warnings* are problems the user
    should fix (set-but-unusable overrides) and *active* is an informational
    list describing which overrides are in effect. Pure — no fallback, no
    mutation of the environment.
    """
    if env is None:
        env = os.environ

    warnings: list[str] = []
    active: list[str] = []

    scratch = env.get("ALLWELL_SCRATCH_DIR", "").strip()
    if scratch:
        active.append(f"ALLWELL_SCRATCH_DIR={scratch!r}")
        w = _check_scratch_dir(scratch)
        if w:
            warnings.append(w)

    remotes = env.get("ALLWELL_REMOTES", "").strip()
    if remotes:
        active.append(f"ALLWELL_REMOTES={remotes!r}")
        w = _check_remotes_file(remotes)
        if w:
            warnings.append(w)

    if env.get("ALLWELL_SEG_DEBUG", "").strip():
        active.append("ALLWELL_SEG_DEBUG=1 (StarDist deep-trace logging enabled)")

    return warnings, active


def log_env_validation(env: Mapping[str, str] | None = None) -> list[str]:
    """Run :func:`validate_env` and log the result. Returns the warnings.

    Called once from ``main`` at startup. Active overrides log at INFO so the
    launch log records the effective configuration; problems log at WARNING.
    Never raises — a diagnostics helper must not block startup.
    """
    try:
        warnings, active = validate_env(env)
    except Exception:  # diagnostics only; never block startup
        logger.debug("env validation failed", exc_info=True)
        return []
    for line in active:
        logger.info("Environment override active: %s", line)
    for line in warnings:
        logger.warning("%s", line)
    return warnings
