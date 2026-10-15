"""Remote-side CSV compression, and tidying the local twin of a fetched CSV.

Split out of ``services.remote_fetch`` (whose line budget only moves down). See
ARCHITECTURE §6.5b for the whole story: a finished well's CSV is ``.csv.gz``, a
run started before compression existed leaves plain ones on the remote, and a
reader prefers ``x.csv`` over ``x.csv.gz``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from util.aw_safe import swallow
from util.csv_gz import gz_path, is_gz_csv_name, plain_path


def compress_remote_csvs(host, remote_output_dir: str, well: str,
                         log: Callable[[str], None]) -> None:
    """gzip the well's plain CSVs on the remote, in place, before they move.

    A no-op for a remote pipeline that already compressed its output, which is
    the normal case; it matters for a run that was started before CSVs were
    compressed. Each file is compressed to a hidden ``.part`` sibling and
    renamed over its ``.gz`` before the original is removed, so a dropped
    connection leaves a complete plain file and never a lone partial. Failure is
    logged and the plain CSV is fetched as it is.
    """
    script = (
        f'for f in {remote_output_dir}/*_{well}.csv; do\n'
        f'  [ -f "$f" ] || continue\n'
        f'  if gzip -c -- "$f" > "$f.gz.part" && mv -f -- "$f.gz.part" "$f.gz"; then\n'
        f'    rm -f -- "$f"\n'
        f'  else\n'
        f'    rm -f -- "$f.gz.part"; echo "allwell-gzip-failed: $f"\n'
        f'  fi\n'
        f'done\n'
    )
    try:
        res = subprocess.run(
            host.ssh_argv(allocate_tty=False) + ["bash", "-s"],
            input=script.encode("utf-8"), capture_output=True, timeout=600)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(f"[remote] ↓ {well}: WARN could not compress its CSV(s) on the "
            f"remote ({exc}); fetching them as they are.\n")
        return
    out = res.stdout.decode("utf-8", errors="replace") if res.stdout else ""
    if "allwell-gzip-failed" in out:
        log(f"[remote] ↓ {well}: WARN gzip failed on the remote for some "
            f"CSV(s); fetching them uncompressed.\n")


def retire_superseded_csvs(remote_paths: list[str], dest_dir: Path) -> None:
    """Remove the other spelling of every CSV just pulled into *dest_dir*.

    A reader prefers ``x.csv`` over ``x.csv.gz`` when both exist, so a freshly
    fetched ``.csv.gz`` landing beside an older plain ``x.csv`` from a previous
    run would be shadowed by the stale file. Only a CSV that actually arrived
    retires its twin.
    """
    for rp in remote_paths:
        name = rp.rsplit("/", 1)[-1]
        landed = dest_dir / name
        if not landed.is_file():
            continue
        twin = (plain_path(landed) if is_gz_csv_name(name)
                else gz_path(landed))
        with swallow("remove superseded CSV spelling"):
            twin.unlink()


def remote_csv_globs(remote_output_dir: str, well: str) -> str:
    """The remote paths of a well's CSV in both spellings, space-separated for
    ``ls`` / ``rm`` (a run begun before compression leaves plain CSVs)."""
    return f"{remote_output_dir}/*_{well}.csv {remote_output_dir}/*_{well}.csv.gz"


def list_remote_csvs(host, remote_output_dir: str, well: str,
                     log: Callable[[str], None], *, compress: bool) -> list[str] | None:
    """The end-of-run retry's listing of a well's remote CSVs, compressing the
    plain ones first when *compress* (the well was never transferred). ``None``
    when the listing itself failed."""
    if compress:
        compress_remote_csvs(host, remote_output_dir, well, log)
    try:
        listing = subprocess.run(
            host.ssh_argv(allocate_tty=False) + ["bash", "-s"],
            # Bytes input: text mode would turn \n into \r\n on Windows and
            # trip the remote bash.
            input=f"ls -1 {remote_csv_globs(remote_output_dir, well)} 2>/dev/null || true".encode("utf-8"),
            capture_output=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(f"[remote] retry: listing {well} failed: {exc}\n")
        return None
    out = listing.stdout.decode("utf-8", errors="replace") if listing.stdout else ""
    return [ln for ln in out.splitlines() if ln.strip()]
