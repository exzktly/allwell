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

import re
from collections.abc import Callable
from dataclasses import dataclass

#: Directory, under ``<staging>/datasets/``, holding one stamp per dataset key.
IN_USE_STAMP_DIR = ".aw_last_used"
#: A dataset stamped more recently than this is in use and is never evicted.
IN_USE_WINDOW_S = 7 * 24 * 3600


def in_use_stamp(staging_dir: str, key: str) -> str:
    """The stamp a run touches when it starts using ``datasets/<key>``."""
    return f"{staging_dir}/datasets/{IN_USE_STAMP_DIR}/{key}"


#: Beside the datasets, the per-dataset expiry the Storage browser writes
#: (``services.remote_storage``); a dataset whose file reads ``keep`` is never evicted.
USER_EXPIRY_DIR = ".aw_expiry"
#: Beside the datasets, each dataset's label (``services.remote_storage``).
LABEL_DIR = ".aw_labels"
#: The only names a dataset folder has: a hex content fingerprint. A listed name
#: of any other shape is not a dataset this module may evict.
_KEY_RE = re.compile(r"^[0-9a-f]{16,128}$")


def listing_script(staging_dir: str) -> str:
    """Remote bash printing ``<bytes>\\t<last used, epoch s>\\t<key>[\\tkeep]`` per
    dataset, then ``now\\t<epoch s>``. A dataset never stamped (uploaded
    before stamps existed) reports its directory's own mtime; one the user
    marked *Keep forever* in the Storage browser carries a fourth ``keep`` field."""
    return (
        f"cd {staging_dir}/datasets 2>/dev/null || {{ echo 'now\t'\"$(date +%s)\"; exit 0; }}\n"
        "for d in */; do\n"
        "  d=${d%/}; [ -d \"$d\" ] || continue\n"
        "  size=$(du -sb -- \"$d\" | cut -f1) || exit 3\n"
        f"  stamp={IN_USE_STAMP_DIR}/\"$d\"\n"
        "  if [ -e \"$stamp\" ]; then used=$(stat -c %Y -- \"$stamp\"); "
        "else used=$(stat -c %Y -- \"$d\"); fi || exit 3\n"
        f"  k=''; [ -f {USER_EXPIRY_DIR}/\"$d\" ] && [ \"$(head -c 4 {USER_EXPIRY_DIR}/\"$d\")\" = keep ] && k='\\tkeep'\n"
        "  printf '%s\\t%s\\t%s%b\\n' \"$size\" \"$used\" \"$d\" \"$k\"\n"
        "done\n"
        "echo 'now\t'\"$(date +%s)\"\n"
    )


@dataclass(frozen=True)
class Dataset:
    key: str
    size: int
    last_used: int
    kept: bool = False          # marked Keep forever in the Storage browser


def parse_listing(text: str) -> tuple[list[Dataset], int] | None:
    """The datasets and the remote's clock from :func:`listing_script`'s
    output, or ``None`` when it is not a complete listing (no clock line)."""
    datasets: list[Dataset] = []
    now: int | None = None
    for line in (text or "").splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 2 and parts[0] == "now" and parts[1].strip().isdigit():
            now = int(parts[1])
        # A name that is not a dataset key is skipped, and so is a fourth field that
        # is not exactly ``keep``: a folder named ``victim<TAB>x`` must not parse as
        # dataset ``victim`` with another folder's age.
        elif (len(parts) in (3, 4) and parts[0].isdigit() and parts[1].isdigit()
              and _KEY_RE.match(parts[2]) and (len(parts) == 3 or parts[3] == "keep")):
            datasets.append(Dataset(parts[2], int(parts[0]), int(parts[1]), kept=len(parts) == 4))
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
        if d.kept:
            continue                    # the user asked to keep it; it still counts towards the budget
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
    if evict:
        # Through the Storage module's deletion, so an eviction gets every guard a
        # person's Delete… gets: a real, unlinked folder of yours directly in
        # ``datasets/``, nothing mounted in it, not in use at that moment, removed
        # on one filesystem — and its stamp, label and expiry with it, through
        # checks that refuse a linked bookkeeping folder. (Imported here: that
        # module imports this one's constants.)
        from services import remote_storage
        items = [remote_storage.StorageItem("dataset", d.key, 0, d.last_used) for d in evict]
        outcomes, error = remote_storage.delete_items(cache, items)
        for d in evict:
            outcome = outcomes.get(("dataset", d.key), "unknown")
            if remote_storage.status_of(outcome) == "gone":
                log(f"[remote] gc: evicted dataset {d.key} ({d.size // (1024**2)} MB)\n")
            else:
                log(f"[remote] gc: kept dataset {d.key} over budget: {outcome}"
                    f"{'; ' + error if error else ''}\n")
    if kept:
        log(f"[remote] gc: kept {len(kept)} dataset(s) over budget because a run used "
            f"them in the last {IN_USE_WINDOW_S // 86400} days: "
            f"{', '.join(d.key for d in kept)}\n")
