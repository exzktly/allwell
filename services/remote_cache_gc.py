"""Which dataset caches on a remote host may be evicted to keep it under budget.

``DatasetCache`` keeps each uploaded input under ``<staging>/datasets/<key>/``
so re-running the same plate is a metadata round trip. With ``auto_gc`` on,
the oldest of those are deleted when the cache exceeds ``cache_budget_gb`` —
and a dataset deleted while a run reads it takes that run down part way, on
whichever machine started it (a second window, a colleague's laptop).

So a dataset in use is never evicted. "In use" is a stamp: every run that
uploads to (or reuses) a dataset touches ``datasets/.aw_last_used/<key>`` as
it starts, and a dataset whose stamp is younger than :data:`IN_USE_WINDOW_S`
is kept whatever the budget says. The window is longer than any run a
scheduler grants (Slurm sites cap walltime at days), so a run that is still
going has stamped its dataset inside it; a run that crashed protects its
dataset for that long and no longer. The stamp lives beside the datasets, not
inside one, so a pipeline listing its input directory never sees it.

The listing is one script, timed by the remote's own clock, so a skewed client
clock cannot make a fresh stamp look old. A listing that fails evicts nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

#: Directory, under ``<staging>/datasets/``, holding one stamp per dataset key.
IN_USE_STAMP_DIR = ".aw_last_used"
#: A dataset stamped more recently than this is in use and is never evicted.
IN_USE_WINDOW_S = 7 * 24 * 3600


def in_use_stamp(staging_dir: str, key: str) -> str:
    """The stamp a run touches when it starts using ``datasets/<key>``."""
    return f"{staging_dir}/datasets/{IN_USE_STAMP_DIR}/{key}"


def listing_script(staging_dir: str) -> str:
    """Remote bash printing ``<bytes>\\t<last used, epoch s>\\t<key>`` per
    dataset, then ``now\\t<epoch s>``. A dataset never stamped (uploaded
    before stamps existed) reports its directory's own mtime."""
    return (
        f"cd {staging_dir}/datasets 2>/dev/null || {{ echo 'now\t'\"$(date +%s)\"; exit 0; }}\n"
        "for d in */; do\n"
        "  d=${d%/}; [ -d \"$d\" ] || continue\n"
        "  size=$(du -sb -- \"$d\" | cut -f1) || exit 3\n"
        f"  stamp={IN_USE_STAMP_DIR}/\"$d\"\n"
        "  if [ -e \"$stamp\" ]; then used=$(stat -c %Y -- \"$stamp\"); "
        "else used=$(stat -c %Y -- \"$d\"); fi || exit 3\n"
        "  printf '%s\\t%s\\t%s\\n' \"$size\" \"$used\" \"$d\"\n"
        "done\n"
        "echo 'now\t'\"$(date +%s)\"\n"
    )


@dataclass(frozen=True)
class Dataset:
    key: str
    size: int
    last_used: int


def parse_listing(text: str) -> tuple[list[Dataset], int] | None:
    """The datasets and the remote's clock from :func:`listing_script`'s
    output, or ``None`` when it is not a complete listing (no clock line)."""
    datasets: list[Dataset] = []
    now: int | None = None
    for line in (text or "").splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 2 and parts[0] == "now" and parts[1].strip().isdigit():
            now = int(parts[1])
        elif len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit() and parts[2]:
            datasets.append(Dataset(parts[2], int(parts[0]), int(parts[1])))
    return None if now is None else (datasets, now)


def plan_eviction(datasets: list[Dataset], now: int, max_bytes: int, *,
                  window_s: int = IN_USE_WINDOW_S) -> tuple[list[Dataset], list[Dataset]]:
    """``(evict, kept_in_use)``: the least recently used datasets to delete,
    oldest first, until the cache fits *max_bytes* — never one used within
    *window_s* — and the in-use ones that were kept although the cache is over
    budget. Evicting stops early when only in-use datasets remain."""
    total = sum(d.size for d in datasets)
    evict: list[Dataset] = []
    kept: list[Dataset] = []
    for d in sorted(datasets, key=lambda d: d.last_used):
        if total <= max_bytes:
            break
        if now - d.last_used < window_s:
            kept.append(d)
            continue
        evict.append(d)
        total -= d.size
    return evict, kept


def collect_garbage(cache, max_bytes: int, log: Callable[[str], None]) -> None:
    """Evict per :func:`plan_eviction` on *cache*'s host (a ``DatasetCache``)."""
    staging = cache.host.remote_staging_dir
    listing = cache._ssh_script(listing_script(staging), check=False)
    parsed = parse_listing(listing.stdout) if listing.returncode == 0 else None
    if parsed is None:
        log(f"[remote] gc: could not list the dataset cache (exit {listing.returncode}: "
            f"{(listing.stderr or '').strip() or 'no listing'}); nothing evicted.\n")
        return
    evict, kept = plan_eviction(*parsed, max_bytes)
    for d in evict:
        result = cache._ssh_run(["rm", "-rf", f"{staging}/datasets/{d.key}",
                                 in_use_stamp(staging, d.key)], check=False)
        if result.returncode != 0:
            log(f"[remote] gc: could not evict dataset {d.key} (exit {result.returncode}: "
                f"{(result.stderr or '').strip()})\n")
            continue
        log(f"[remote] gc: evicted dataset {d.key} ({d.size // (1024**2)} MB)\n")
    if kept:
        log(f"[remote] gc: kept {len(kept)} dataset(s) over budget because a run used "
            f"them in the last {IN_USE_WINDOW_S // 86400} days: "
            f"{', '.join(d.key for d in kept)}\n")
