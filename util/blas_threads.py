"""Bound the BLAS thread pools of the GUI process, and keep that bound out of the pipeline.

numpy and scipy each ship their own copy of OpenBLAS, and each copy starts one
thread per logical core. Every one of those threads owns a private work buffer,
and on Windows OpenBLAS allocates that buffer with ``VirtualAlloc(MEM_RESERVE |
MEM_COMMIT)``: the whole buffer counts against the system commit limit — backed
by the page file — from the moment the thread exists, whether or not a single
page of it is ever written. Nothing frees it before the process exits.

Measured from source on a 4-core Linux container (numpy 1.24, scipy, pandas and
matplotlib imported): the process mapped 639 MB with the default thread count
and 394 MB with ``OPENBLAS_NUM_THREADS=1``, at the same resident size — about
82 MB of untouched buffer per extra thread across the two libraries. On a
24-thread Windows workstation that is roughly 1.9 GB of page file the app
claims at start-up and never uses, which is what users saw: a large and
constant commit charge, only released by quitting.

The GUI's own linear algebra is small — a PCA over sampled cells, a curve fit,
a correlation matrix — and gains little past a few threads, so the window caps
the pools at :data:`GUI_BLAS_THREADS`. The cap must be in the environment
before numpy is first imported, because OpenBLAS reads it once, when it loads;
:func:`cap_for_gui` is therefore called at the very top of the two GUI entry
points. A value the user has already set is respected.

The pipeline is the opposite case: it sizes its own thread pools per worker
(``process_microscopy`` sets ``OPENBLAS_NUM_THREADS`` and friends from the
parallel plan) and a run inheriting the window's cap would be slower for it
wherever it does not override them. :func:`uncapped_env` removes exactly the
variables :func:`cap_for_gui` added, named in :data:`MARKER_ENV`, so a local run
starts with the environment the user launched the app with.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping

#: Threads each BLAS / OpenMP pool in the GUI process may use.
GUI_BLAS_THREADS = 4

#: The thread-count variables the cap sets. ``OPENBLAS_NUM_THREADS`` is the one
#: that bounds the committed buffers; the others keep an MKL or OpenMP build of
#: the same libraries from doing the same thing under another name.
THREAD_VARS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")

#: Comma-separated names of the variables :func:`cap_for_gui` set (and the user
#: had not), so :func:`uncapped_env` removes those and nothing else.
MARKER_ENV = "ALLWELL_GUI_BLAS_CAPPED"


def cap_for_gui(environ: MutableMapping[str, str] | None = None, *, cpu_count: int | None = None) -> list[str]:
    """Set each unset variable of :data:`THREAD_VARS` to the GUI's cap; return the names set.

    Idempotent: both GUI entry points call it, and the second call finds every
    variable already present and leaves the marker as the first call wrote it.
    """
    env = os.environ if environ is None else environ
    cores = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    value = str(max(1, min(GUI_BLAS_THREADS, cores)))
    added = [name for name in THREAD_VARS if not env.get(name)]
    for name in added:
        env[name] = value
    if added:
        previous = [n for n in env.get(MARKER_ENV, "").split(",") if n]
        env[MARKER_ENV] = ",".join(previous + [n for n in added if n not in previous])
    return added


def uncapped_env(environ: Mapping[str, str]) -> dict[str, str]:
    """A copy of *environ* without the variables :func:`cap_for_gui` added, or its marker."""
    env = dict(environ)
    for name in env.pop(MARKER_ENV, "").split(","):
        if name in THREAD_VARS:
            env.pop(name, None)
    return env
