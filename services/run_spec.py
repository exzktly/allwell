"""The JSON-serialisable description of one Analyze run.

Splits an Analyze run into "what would be run" (this module) and "actually
run it" (``analyze_run_spec.py``'s ``AnalyzeRunMixin``), so the job queue can
persist a run's configuration and replay it later without depending on the
live Analyze form.

Two things are deliberately **not** persisted inside a :class:`RunSpec`:

* The resolved ``pipeline`` script path — re-resolved at dequeue via
  ``services.pipeline_service.find_pipeline_script()``, since a stored path
  to ``process_microscopy.py`` breaks across a frozen-bundle relaunch from a
  different location, which is exactly the lifetime a persisted queue has.
* The resolved ``RemoteHost`` object — only the remote's *name* is stored;
  :meth:`RunSpec.resolve_remote` re-resolves it through
  ``services.remote_config.get_remote`` at dequeue, because ``remotes.toml``
  can change between enqueue and run. A remote that's been renamed or
  deleted in the meantime fails the job with a clear message rather than
  silently falling back to a local run.
"""

from __future__ import annotations

# Names used only in annotations. Under ``from __future__ import
# annotations`` these are never evaluated, so importing them for real would
# add startup cost and, for several of these, an import cycle.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.remote_config import RemoteHost

from dataclasses import dataclass, field
from pathlib import Path

# opts["raw"] is the only Path-valued key AnalyzeTabFormMixin._collect_run_options
# produces (in analyze_tab_form.py) — everything else is already
# str/int/float/bool/list[str]/None. If a second Path-valued key is ever added
# to that dict, it must be listed here too, or to_json()/from_json() will
# silently mis-round-trip it.
_PATH_KEYS = ("raw",)


class RunSpecError(Exception):
    """A :class:`RunSpec` can't be resolved into something runnable. The
    message is user-facing."""


@dataclass(frozen=True)
class RunSpec:
    """Everything ``PipelineRunner.start`` needs, plus enough to resolve a
    remote and re-derive the pipeline script — the complete, JSON-round-
    trippable description of one Analyze run."""

    opts: dict = field(default_factory=dict)
    remote_name: str | None = None
    cleanup_remote: str = "none"          # "all" | "none"
    dry_run_cleanup: bool = False
    output_filter: str = "full"
    selective_channels: bool = True

    def label(self) -> str:
        raw = self.opts.get("raw")
        name = Path(raw).name if raw else "(no input)"
        return f"Analyze — {name}"

    def to_json(self) -> dict:
        opts = dict(self.opts)
        for key in _PATH_KEYS:
            if key in opts and opts[key] is not None:
                opts[key] = str(opts[key])
        return {
            "opts": opts,
            "remote_name": self.remote_name,
            "cleanup_remote": self.cleanup_remote,
            "dry_run_cleanup": self.dry_run_cleanup,
            "output_filter": self.output_filter,
            "selective_channels": self.selective_channels,
        }

    @classmethod
    def from_json(cls, data: dict) -> RunSpec:
        opts = dict(data.get("opts") or {})
        for key in _PATH_KEYS:
            if key in opts and opts[key] is not None:
                opts[key] = Path(opts[key])
        return cls(
            opts=opts,
            remote_name=data.get("remote_name"),
            cleanup_remote=data.get("cleanup_remote", "none"),
            dry_run_cleanup=bool(data.get("dry_run_cleanup", False)),
            output_filter=data.get("output_filter", "full"),
            selective_channels=bool(data.get("selective_channels", True)),
        )

    def resolve_remote(self) -> RemoteHost | None:
        """Return the live ``RemoteHost`` for :attr:`remote_name`, or
        ``None`` for a local run. Raises :class:`RunSpecError` when a named
        remote no longer resolves."""
        if self.remote_name is None:
            return None
        from services.remote_config import get_remote

        try:
            remote = get_remote(self.remote_name)
        except (ValueError, OSError) as exc:
            raise RunSpecError(f"Could not load remote {self.remote_name!r}: {exc}") from exc
        if remote is None:
            raise RunSpecError(f"Remote {self.remote_name!r} is no longer defined.")
        return remote

    def runner_opts(self) -> dict:
        """The ``opts`` dict as ``PipelineRunner.start`` / ``build_pipeline_args``
        expect it — ``opts["raw"]`` as a live ``Path``, not a string."""
        opts = dict(self.opts)
        raw = opts.get("raw")
        if raw is not None and not isinstance(raw, Path):
            opts["raw"] = Path(raw)
        return opts
