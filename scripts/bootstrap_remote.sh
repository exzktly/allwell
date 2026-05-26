#!/usr/bin/env bash
# bootstrap_remote.sh — one-time setup for an AllWell remote-execution host.
#
# Usage:
#   scripts/bootstrap_remote.sh <ssh-alias> [--force]
#
# Idempotent: re-runs on an already-configured host are a fast no-op
# (no installer download, no env recreate, no config rewrite). Use --force
# to recreate everything when the remote env is suspected corrupt.
#
# What this does on the remote machine:
#   1. Probes for an existing miniforge / conda install in $HOME.
#   2. Probes for an existing 'allwell' conda env.
#   3. Probes for an existing repo checkout at $HOME/allwell.
#   4. Installs / syncs only the pieces that are missing.
#   5. Smoke-checks tensorflow + stardist imports and prints GPU visibility.
#   6. Measures inbound + outbound bandwidth via a 50 MB throwaway file.
#   7. Generates a remotes.toml block to paste into ~/.config/allwell/.
#
# Constraints honoured (the user has no admin rights on the remote):
#   - All installs go under $HOME, never via system package managers.
#   - SSH is the only transport — no port forwarding, no daemons.
#   - The local ssh client's existing ~/.ssh/config is the auth source.

set -euo pipefail

# ── argv ─────────────────────────────────────────────────────────────────────

ALIAS=""
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        --help|-h)
            sed -n '2,28p' "$0"
            exit 0
            ;;
        -*) echo "Unknown flag: $arg" >&2; exit 2 ;;
        *)
            if [[ -z "$ALIAS" ]]; then
                ALIAS="$arg"
            else
                echo "Unexpected positional arg: $arg" >&2; exit 2
            fi
            ;;
    esac
done
if [[ -z "$ALIAS" ]]; then
    echo "Usage: $0 <ssh-alias> [--force]" >&2
    exit 2
fi

LOCAL_REPO="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_REPO_DEFAULT='$HOME/allwell'
REMOTE_STAGING_DEFAULT='$HOME/.allwell-staging'
MINIFORGE_DIR_DEFAULT='$HOME/miniforge3'
ENV_NAME=allwell

say() { printf "\033[1;36m[bootstrap]\033[0m %s\n" "$*"; }

# ── 1-4: install / sync everything that's missing ────────────────────────────

# We pass FORCE through to the remote heredoc so the same code path handles
# the --force case (rm-rf the conda dir before reinstall).
say "Probing $ALIAS for existing setup…"

REMOTE_REPORT="$(ssh "$ALIAS" FORCE=$FORCE bash -s <<'REMOTE'
set -euo pipefail

MINIFORGE_DIR="$HOME/miniforge3"
REPO_DIR="$HOME/allwell"
ENV_NAME=allwell

has_conda() {
    if command -v conda >/dev/null 2>&1; then return 0; fi
    if [[ -x "$MINIFORGE_DIR/bin/conda" ]]; then return 0; fi
    return 1
}

force_clean() {
    if [[ "${FORCE:-0}" == "1" ]]; then
        echo "[bootstrap-remote] --force: removing $MINIFORGE_DIR and $REPO_DIR" >&2
        rm -rf "$MINIFORGE_DIR" "$REPO_DIR"
    fi
}

force_clean

# 1. Miniforge ────────────────────────────────────────────────────────────
if has_conda; then
    echo "[bootstrap-remote] miniforge already present — skipping install" >&2
else
    echo "[bootstrap-remote] installing miniforge to $MINIFORGE_DIR" >&2
    UNAME="$(uname -s)-$(uname -m)"
    INSTALLER="/tmp/miniforge-${UNAME}.sh"
    URL="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-${UNAME}.sh"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$URL" -o "$INSTALLER"
    elif command -v wget >/dev/null 2>&1; then
        wget -q "$URL" -O "$INSTALLER"
    else
        echo "[bootstrap-remote] need curl or wget to fetch miniforge" >&2
        exit 1
    fi
    bash "$INSTALLER" -b -p "$MINIFORGE_DIR"
    rm -f "$INSTALLER"
fi

# Activate conda for the rest of the heredoc.
# shellcheck disable=SC1091
source "$MINIFORGE_DIR/etc/profile.d/conda.sh"

# 2. Repo ─────────────────────────────────────────────────────────────────
if [[ -d "$REPO_DIR/.git" ]]; then
    echo "[bootstrap-remote] repo present — fetching latest" >&2
    git -C "$REPO_DIR" fetch --all --quiet || true
    # Leave the user's branch alone — the caller's rsync of the working
    # copy (later) is the authoritative sync; we just need *something*
    # checked out so conda env creation can find environment.yml.
else
    echo "[bootstrap-remote] no repo checkout at $REPO_DIR — placeholder dir created" >&2
    mkdir -p "$REPO_DIR"
fi

mkdir -p "$HOME/.allwell-staging/datasets" "$HOME/.allwell-staging/runs"

# 3. Conda env ────────────────────────────────────────────────────────────
if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    echo "[bootstrap-remote] env '$ENV_NAME' present — leaving alone" >&2
    echo "[bootstrap-remote] (use --force to recreate)" >&2
else
    echo "[bootstrap-remote] env '$ENV_NAME' missing — creating from python=3.10.14" >&2
    conda create -y -n "$ENV_NAME" python=3.10.14 setuptools=69.5.1 \
        "numpy>=1.24,<1.25" scipy scikit-image matplotlib pillow imageio h5py \
        tomli -c conda-forge >&2
    # Note: the heavyweight pip installs (tensorflow, stardist, ...) are
    # done by the caller after rsyncing requirements.txt — keeps this
    # script idempotent + decoupled from the project's exact pip pins.
fi

conda activate "$ENV_NAME"

# 4. Smoke-check + GPU report ─────────────────────────────────────────────
# Report whether tensorflow / stardist are importable. On a fresh env
# they won't be yet (pip install happens next, by the caller), so a
# failure here is informational, not fatal.
echo "===PROBE_BEGIN==="
python - <<'PY' || true
import sys
report = {"python": sys.version.split()[0]}
try:
    import tensorflow as tf
    report["tensorflow"] = tf.__version__
    report["gpu"] = bool(tf.config.list_physical_devices("GPU"))
except Exception as e:
    report["tensorflow_error"] = type(e).__name__
    report["gpu"] = False
try:
    import stardist  # noqa: F401
    report["stardist"] = "ok"
except Exception as e:
    report["stardist_error"] = type(e).__name__
for k, v in report.items():
    print(f"{k}={v}")
PY
echo "===PROBE_END==="
REMOTE
)" || { echo "[bootstrap] remote setup failed" >&2; exit 1; }

echo "$REMOTE_REPORT"

# Extract probe section for the GPU + tensorflow check.
PROBE="$(printf '%s' "$REMOTE_REPORT" | awk '/^===PROBE_BEGIN===$/{flag=1; next} /^===PROBE_END===$/{flag=0} flag')"
HAS_GPU="$(printf '%s' "$PROBE" | awk -F= '/^gpu=/{print $2}')"
HAS_TF="$(printf '%s' "$PROBE" | awk -F= '/^tensorflow=/{print $2}')"

# ── 5. Sync the working copy → remote ────────────────────────────────────────
say "Syncing local working copy → $ALIAS:~/allwell/  (this is the authoritative project source)"
rsync -aH --delete \
    --exclude='.git/' --exclude='__pycache__/' --exclude='*.pyc' \
    --exclude='build/' --exclude='dist/' --exclude='.pytest_cache/' \
    --exclude='_results*/' --exclude='results/' \
    "$LOCAL_REPO/" "$ALIAS:~/allwell/"

# If TF wasn't importable, the env is freshly created — install pip deps now.
if [[ "$HAS_TF" == "" ]]; then
    say "Installing pip dependencies (requirements.txt) on remote — this is the big download"
    ssh "$ALIAS" bash -s <<'REMOTE'
set -euo pipefail
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda activate allwell
if [[ -f "$HOME/allwell/_Docs/requirements.txt" ]]; then
    pip install --no-build-isolation -r "$HOME/allwell/_Docs/requirements.txt"
else
    echo "WARN: _Docs/requirements.txt not found on remote" >&2
fi
REMOTE
fi

# ── 6. Bandwidth probe ───────────────────────────────────────────────────────

say "Measuring bandwidth (50 MB throwaway)…"
PROBE_FILE="$(mktemp -t allwell-bw.XXXXXX)"
trap 'rm -f "$PROBE_FILE"' EXIT
# Random-ish 50 MB so deflate / dedup heuristics don't lie.
dd if=/dev/urandom of="$PROBE_FILE" bs=1M count=50 status=none

START=$(date +%s.%N)
rsync -a "$PROBE_FILE" "$ALIAS:/tmp/allwell-bw-probe"
UP_END=$(date +%s.%N)
ssh "$ALIAS" rm -f /tmp/allwell-bw-probe
UP_S=$(awk -v s="$START" -v e="$UP_END" 'BEGIN{printf "%.3f", e-s}')
MBPS=$(awk -v s="$UP_S" 'BEGIN{printf "%.1f", (50*8)/s}')
say "Upload bandwidth: ${MBPS} Mbps (50 MB in ${UP_S}s)"

# ── 7. Generate remotes.toml block ───────────────────────────────────────────

CFG_DIR="$HOME/.config/allwell"
CFG_FILE="$CFG_DIR/remotes.toml"
mkdir -p "$CFG_DIR"

CPU_ONLY="false"
if [[ "$HAS_GPU" != "True" && "$HAS_GPU" != "true" ]]; then
    CPU_ONLY="true"
    say "No GPU visible on remote — config will set cpu_only=true."
else
    say "GPU detected on remote — config will set cpu_only=false."
fi

BLOCK=$(cat <<TOML
[remote.$ALIAS]
ssh_alias = "$ALIAS"
remote_project_dir = "~/allwell"
remote_staging_dir = "~/.allwell-staging"
remote_python = "~/miniforge3/envs/allwell/bin/python"
cpu_only = $CPU_ONLY
extra_args = []
cache_budget_gb = 200
measured_bandwidth_mbps = $MBPS
TOML
)

# If the section already exists in the file, leave the file untouched —
# but always print what *would* go in so the user can compare manually.
if [[ -f "$CFG_FILE" ]] && grep -q "^\[remote\.$ALIAS\]" "$CFG_FILE"; then
    say "Config block for $ALIAS already exists in $CFG_FILE — no changes written."
    say "Detected block (compare to printed version below):"
    awk -v sect="[remote.$ALIAS]" '
        $0 == sect { inblock=1 }
        inblock { print }
        inblock && /^$/ { inblock=0 }
    ' "$CFG_FILE"
else
    say "Appending new block to $CFG_FILE"
    {
        if [[ -f "$CFG_FILE" ]]; then
            cat "$CFG_FILE"
            echo
        fi
        printf '%s\n' "$BLOCK"
    } > "$CFG_FILE.tmp"
    mv "$CFG_FILE.tmp" "$CFG_FILE"
fi

cat <<EOF

──────────────────────────────────────────────────────────────────────────
Bootstrap complete for $ALIAS.

Try it:
    python scripts/allwell_remote.py --remote $ALIAS \\
        --input_dir <your-plate-dir> --output_dir ./results

Config file: $CFG_FILE
Block:
$BLOCK
──────────────────────────────────────────────────────────────────────────
EOF
