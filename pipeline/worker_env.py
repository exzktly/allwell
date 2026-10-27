"""Process-level settings a pipeline worker makes for itself before it reads an image.

Qt-free and import-light, so the per-well workers (``process_microscopy._worker_init``) and anything else that
starts one can share it.
"""

from __future__ import annotations

from util.aw_safe import swallowed


def raise_open_file_limit(cap: int = 4096) -> None:
    """Raise this process's soft open-file limit to what the kernel already permits (the hard limit, capped at
    *cap*), without needing root.

    macOS's default soft limit (256) is easily exceeded by TF + StarDist + tifffile in a well with many FOVs, and
    the EMFILE that results surfaces as a confusing OSError far downstream. ``resource`` is POSIX-only; where it is
    missing or the bump is not permitted, nothing changes.
    """
    try:
        import resource as _resource
        _soft, _hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
        _target = max(_soft, min(_hard, cap))
        if _target > _soft:
            _resource.setrlimit(_resource.RLIMIT_NOFILE, (_target, _hard))
    except (ImportError, ValueError, OSError):
        swallowed("could not raise the open-file limit")
