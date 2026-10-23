"""Test detection's analysis, run in a child process so a segmenter that crashes cannot take the app with it.

Counting the cells in a nanowell loads Cellpose, and Cellpose loads Torch. On a Windows install whose Torch DLLs
clash with what the app has already loaded, ``import torch`` does not raise: the process dies with a native fault
(``0xC0000138``, *ordinal not found*) that Python cannot catch. Run in the app's own process that is the whole
app gone, with the boot log as the only trace. A plate-wide run never had the problem, because the pipeline is
already a subprocess — Test detection was the one place the GUI process measured a nanowell itself.

So :func:`run_preview` does what a run does: it hands the job to a fresh interpreter and reads the result back.

* Source tree: ``[sys.executable, "<this file>", job, out]``. Spawned by path, so the repository root is put on
  ``sys.path`` below (Python seeds ``sys.path[0]`` with ``services/``, one level too deep for ``pipeline``).
* Frozen bundle: ``sys.executable`` is the app, so ``[sys.executable, "--run-nanowell-preview", job, out]`` and
  ``all_well_launcher`` routes it to :func:`main`, as it does ``--run-pipeline``.

The job and the result cross as pickles in a private temporary directory. When the child dies without writing a
result, it is run once more with a segmenter that refuses to load
(:class:`pipeline.cellpose.CellposeUnavailable`). Finding the nanowells needs no segmenter, so the second run
draws the array uncounted and says why — naming the exit code — instead of drawing nothing. The only difference
between the two runs is whether the segmenter loaded, so a second run that succeeds pins the crash on it; one that
crashes too is reported as the failure it is.

Qt-free: it runs in the child, which must not load the Qt libraries the crash may be clashing with.
"""

from __future__ import annotations

import os
import sys

# Spawned by path from a source tree: put the repository root first, as pipeline/well_plate_zipper.py does.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import logging  # noqa: E402
import pickle  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

log = logging.getLogger(__name__)

#: The sentinel ``all_well_launcher`` dispatches on in a frozen bundle.
FROZEN_SENTINEL = "--run-nanowell-preview"
#: How much of a crashed child's stderr (its faulthandler dump) is kept in the log.
STDERR_TAIL = 4000

#: Windows NTSTATUS codes a crashed child is most likely to end with, by name, so the note says what happened.
_NT_STATUS = {0xC0000138: "STATUS_ORDINAL_NOT_FOUND: a library it loaded did not match another",
              0xC0000139: "STATUS_ENTRYPOINT_NOT_FOUND: a library it loaded did not match another",
              0xC0000135: "STATUS_DLL_NOT_FOUND: a library it needs is missing",
              0xC0000005: "STATUS_ACCESS_VIOLATION",
              0xC00000FD: "STATUS_STACK_OVERFLOW",
              0xC0000409: "STATUS_STACK_BUFFER_OVERRUN"}


class PreviewFailed(RuntimeError):
    """The preview raised in the child; the message is already ``"Type: message"``."""


class PreviewCrashed(PreviewFailed):
    """The child process died without a result, twice; the message names how."""


def describe_exit(code: int | None) -> str:
    """A child's exit status as text: a signal on POSIX, a hex NTSTATUS (named where known) for a Windows fault."""
    if code is None:
        return "no exit status"
    if code < 0:
        return f"killed by signal {-code}"
    if code > 0xFFFF:                                      # a Windows exception code, reported unsigned
        name = _NT_STATUS.get(code)
        return f"exit code {code:#010x}" + (f", {name}" if name else "")
    return f"exit code {code}"


def child_command(job_path: Path, out_path: Path) -> list[str]:
    """The argv that runs :func:`main` in a fresh interpreter, frozen or from source."""
    if getattr(sys, "frozen", False):
        return [sys.executable, FROZEN_SENTINEL, str(job_path), str(out_path)]
    return [sys.executable, str(Path(__file__).resolve()), str(job_path), str(out_path)]


def _refusing_segmenter(reason: str):
    """A segmenter that refuses to load, as Cellpose does when it is not installed: the preview then measures
    without counting and names *reason*."""
    def segment(*_args, **_kwargs):
        from pipeline.cellpose import CellposeUnavailable

        raise CellposeUnavailable(reason)
    return segment


def compute(job: dict, segmenter=None):
    """The preview itself, in whichever process calls it. ``PreviewResult`` on success; raises on failure."""
    import process_microscopy as pm

    from pipeline.nanowell_preview import preview_timepoint
    from util.schema import parse_schema

    with tempfile.TemporaryDirectory(prefix="allwell-preview-") as scratch:
        return preview_timepoint(
            job["zip"], job["well"], job["timepoint"], job["settings"], grouper=pm.find_image_groups_in_zip,
            parse=pm.parse_filename, schema=parse_schema(job["schema"]), sep=job["sep"],
            phase_token=job["phase_token"], scratch=Path(scratch), pattern=job.get("pattern"), segmenter=segmenter)


def serve(job_path: Path, out_path: Path) -> None:
    """Read the job, run it, write ``("ok", result)`` or ``("error", "Type: message")`` — whole or not at all."""
    with open(job_path, "rb") as fh:
        job, refuse = pickle.load(fh)
    segmenter = _refusing_segmenter(refuse) if refuse else None
    try:
        outcome = ("ok", compute(job, segmenter))
    except Exception as exc:
        log.warning("Test detection failed", exc_info=True)
        outcome = ("error", f"{type(exc).__name__}: {exc}")
    tmp = out_path.with_suffix(".partial")
    with open(tmp, "wb") as fh:
        pickle.dump(outcome, fh, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, out_path)                              # a crash mid-write leaves no result, not half of one


def main(argv: list[str] | None = None) -> int:
    """Child entry (``<this file> JOB OUT``, or the launcher's sentinel)."""
    import faulthandler

    argv = sys.argv[1:] if argv is None else argv
    if sys.stderr is not None:
        faulthandler.enable(sys.stderr)                    # a native crash leaves its stack for the parent's log
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    serve(Path(argv[0]), Path(argv[1]))
    return 0


def _run_child(job: dict, refuse: str | None, folder: Path, attempt: int, runner=subprocess.run):
    """One child run. ``(outcome, None)`` when it wrote a result, ``(None, exit description)`` when it died."""
    job_path, out_path = folder / f"job{attempt}.pkl", folder / f"result{attempt}.pkl"
    with open(job_path, "wb") as fh:
        pickle.dump((job, refuse), fh, protocol=pickle.HIGHEST_PROTOCOL)
    kwargs = {}
    if os.name == "nt":                                   # no console window flashing up from a windowed app
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = runner(child_command(job_path, out_path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.PIPE, check=False, **kwargs)
    if out_path.exists():
        with open(out_path, "rb") as fh:
            return pickle.load(fh), None
    died = describe_exit(proc.returncode)
    tail = (proc.stderr or b"")[-STDERR_TAIL:].decode("utf-8", "replace")
    log.warning("Test detection's child process died (%s) without a result. Its last output:\n%s", died, tail)
    return None, died


def run_preview(job: dict, *, runner=subprocess.run):
    """Run the preview in a child process; the parent never loads a segmenter.

    Returns the ``PreviewResult``. Raises :class:`PreviewFailed` with the child's own ``"Type: message"`` when the
    preview failed, and :class:`PreviewCrashed` when the child died both with and without a segmenter.
    """
    with tempfile.TemporaryDirectory(prefix="allwell-preview-job-") as folder:
        folder = Path(folder)
        outcome, died = _run_child(job, None, folder, 1, runner)
        if outcome is None:
            reason = (f"the process that loaded it crashed ({died}); the app was not affected. "
                      "Cellpose or Torch is broken in this environment")
            outcome, died_again = _run_child(job, reason, folder, 2, runner)
            if outcome is None:
                raise PreviewCrashed(f"Test detection's process crashed ({died}), and again without the segmenter "
                                     f"({died_again}); the crash is in registration or detection, not Cellpose. "
                                     "The app's log holds the child's last output.")
    status, value = outcome
    if status != "ok":
        raise PreviewFailed(value)
    return value


if __name__ == "__main__":
    sys.exit(main())
