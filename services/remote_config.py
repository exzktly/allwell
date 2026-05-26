"""Per-host remote-execution config for the AllWell remote-runner.

Loads host definitions from ``~/.config/allwell/remotes.toml`` (or the path
in ``$ALLWELL_REMOTES``). Each entry describes how to reach one SSH host
and run ``process_microscopy.py`` on it.

The TOML schema is documented in the v5.0.0 release notes and the
``bootstrap_remote.sh`` script generates a starter file for the user.

Example::

    [remote.bigbox]
    ssh_alias = "bigbox"
    remote_project_dir = "~/allwell"
    remote_staging_dir = "~/.allwell-staging"
    remote_python = "~/miniforge3/envs/allwell/bin/python"
    cpu_only = false
    extra_args = ["--workers", "0"]
    cache_budget_gb = 200
    measured_bandwidth_mbps = 0.0
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# tomllib is stdlib on 3.11+; tomli is the 3.10 backport. The project pins
# 3.10.14, so tomli is the expected provider — but we accept either so the
# module is portable to newer interpreters as the project's Python pin
# moves forward.
try:  # pragma: no cover — exercised by whichever interpreter the user has.
    import tomllib as _toml
except ImportError:  # pragma: no cover
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Reading remotes.toml requires the 'tomli' package on Python "
            "<3.11. Install it with: pip install tomli"
        ) from exc


_DEFAULT_CONFIG_PATH = Path("~/.config/allwell/remotes.toml")


@dataclass(frozen=True)
class RemoteHost:
    """One configured remote-execution target.

    ``ssh_alias`` is the only required field — everything else has a
    sensible default. The alias is passed verbatim to ``ssh`` and ``rsync``
    so users can point at a fully-configured ``~/.ssh/config`` entry
    (recommended) or at ``user@host`` directly.
    """

    name: str
    ssh_alias: str
    remote_project_dir: str = "~/allwell"
    remote_staging_dir: str = "~/.allwell-staging"
    remote_python: str = "~/miniforge3/envs/allwell/bin/python"
    cpu_only: bool = False
    extra_args: tuple[str, ...] = field(default_factory=tuple)
    cache_budget_gb: int = 200
    measured_bandwidth_mbps: float = 0.0

    def ssh_argv(self, *, allocate_tty: bool = True) -> list[str]:
        """Build the leading ``ssh`` command for this host.

        ``-tt`` allocates a PTY so SIGHUP propagates to the remote process
        when the local ssh exits — that's how the existing pgid-based
        ``stop()`` reaches the remote pipeline. The ControlMaster/Persist
        pair lets follow-up ssh's (cleanup, kill) reuse the same TCP
        connection without re-authenticating.
        """
        argv = ["ssh"]
        if allocate_tty:
            argv.append("-tt")
        argv += [
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={_controlpath_template()}",
            "-o", "ControlPersist=60s",
            "-o", "ServerAliveInterval=30",
            self.ssh_alias,
        ]
        return argv


def _controlpath_template() -> str:
    """OpenSSH ControlPath template per-user, per-host, per-port."""
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or Path.home() / ".ssh")
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "allwell-cm-%r@%h:%p")


def default_config_path() -> Path:
    """Return the resolved path to the user's remotes.toml.

    Honors ``$ALLWELL_REMOTES`` for tests and ad-hoc overrides.
    """
    override = os.environ.get("ALLWELL_REMOTES")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_CONFIG_PATH.expanduser()


def load_remotes(path: Path | None = None) -> dict[str, RemoteHost]:
    """Parse the remotes.toml file. Returns an empty dict if missing.

    Missing-file is **not** an error: the user may simply have no remotes
    configured. Malformed-file *is* an error and surfaces as a
    ``ValueError`` so the GUI can show it cleanly.
    """
    p = path or default_config_path()
    if not p.is_file():
        return {}
    try:
        with open(p, "rb") as fh:
            doc: dict[str, Any] = _toml.load(fh)
    except _toml.TOMLDecodeError as exc:
        raise ValueError(f"Cannot parse {p}: {exc}") from exc

    remotes_table = doc.get("remote", {}) or {}
    if not isinstance(remotes_table, dict):
        raise ValueError(f"{p}: top-level 'remote' must be a table.")

    out: dict[str, RemoteHost] = {}
    for name, entry in remotes_table.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{p}: 'remote.{name}' must be a table.")
        try:
            out[name] = _parse_entry(name, entry)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{p}: 'remote.{name}': {exc}") from exc
    return out


def get_remote(name: str, path: Path | None = None) -> RemoteHost | None:
    """Convenience accessor: load + look up by name in one call."""
    return load_remotes(path).get(name)


def update_bandwidth(name: str, mbps: float, path: Path | None = None) -> None:
    """Rewrite ``measured_bandwidth_mbps`` for ``name`` in place.

    Used by the runner after each successful upload to keep the rolling
    bandwidth estimate fresh. The write is best-effort: a missing or
    unwritable file logs a warning to stderr but does not raise — the
    estimate is a hint, not a correctness requirement.
    """
    p = path or default_config_path()
    if not p.is_file():
        return
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return

    new_line = f"measured_bandwidth_mbps = {mbps:.2f}"
    in_section = False
    out_lines: list[str] = []
    written = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not written:
                out_lines.append(new_line)
                written = True
            in_section = stripped == f"[remote.{name}]"
        elif in_section and stripped.startswith("measured_bandwidth_mbps"):
            out_lines.append(new_line)
            written = True
            continue
        out_lines.append(line)
    if in_section and not written:
        out_lines.append(new_line)
        written = True

    if not written:
        # The host isn't in the file — leave it alone rather than appending
        # a half-written section.
        return

    try:
        p.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def _parse_entry(name: str, entry: dict[str, Any]) -> RemoteHost:
    alias = entry.get("ssh_alias")
    if not isinstance(alias, str) or not alias.strip():
        raise ValueError("'ssh_alias' is required and must be a non-empty string.")

    extra_args = entry.get("extra_args", [])
    if not isinstance(extra_args, list) or not all(
        isinstance(x, str) for x in extra_args
    ):
        raise ValueError("'extra_args' must be a list of strings.")

    defaults = RemoteHost(name=name, ssh_alias=alias.strip())
    return replace(
        defaults,
        remote_project_dir=str(entry.get("remote_project_dir", defaults.remote_project_dir)),
        remote_staging_dir=str(entry.get("remote_staging_dir", defaults.remote_staging_dir)),
        remote_python=str(entry.get("remote_python", defaults.remote_python)),
        cpu_only=bool(entry.get("cpu_only", defaults.cpu_only)),
        extra_args=tuple(extra_args),
        cache_budget_gb=int(entry.get("cache_budget_gb", defaults.cache_budget_gb)),
        measured_bandwidth_mbps=float(
            entry.get("measured_bandwidth_mbps", defaults.measured_bandwidth_mbps)
        ),
    )
