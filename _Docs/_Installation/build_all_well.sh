#!/usr/bin/env bash
# build_all_well.sh
# -----------------
# Build AllWell.app on macOS using the allwell mamba environment.
#
# SETUP (one time):
#   mamba create -n allwell python=3.10.14 -c conda-forge -y
#   mamba activate allwell
#   pip install -r _Docs/requirements.txt
#   xcode-select --install
#
# BUILD (run from the repository root):
#   mamba activate allwell
#   chmod +x _Docs/_Installation/build_all_well.sh
#   _Docs/_Installation/build_all_well.sh
#
# UNIVERSAL BINARY (Intel + Apple Silicon):
#   TARGET_ARCH=universal2 _Docs/_Installation/build_all_well.sh

set -euo pipefail

TARGET_ARCH=${TARGET_ARCH:-}
DIST_DIR="dist"
APP_NAME="AllWell"

echo "=== AllWell macOS build ==="
echo "Python : $(python --version)"
echo "Which  : $(which python)"
echo "Arch   : ${TARGET_ARCH:-native}"
echo ""

# ── 1. Confirm allwell mamba environment is active ─────────────────────────
PY_PATH=$(python -c "import sys; print(sys.executable)")
if [[ "$PY_PATH" != *"/allwell/"* ]]; then
    echo "ERROR: allwell mamba environment does not appear to be active."
    echo "  Current Python: $PY_PATH"
    echo "  Run: mamba activate allwell"
    exit 1
fi

# Ensure pip-visible setuptools is installed (mamba's conda version is
# sometimes not visible to pip/PyInstaller, causing pkg_resources errors).
# We force-reinstall to guarantee the pip-managed version is present.
echo "Ensuring pip-managed setuptools (provides pkg_resources) ..."
pip install -q --force-reinstall "setuptools>=67.0"
echo ""

# ── 2. Detect architecture ─────────────────────────────────────────────────
MACHINE=$(uname -m)
if [ "$MACHINE" = "arm64" ]; then
    echo "Detected: Apple Silicon (arm64)"
else
    echo "Detected: Intel Mac (x86_64)"
fi
echo ""

# ── 3. Sanity checks ────────────────────────────────────────────────────────
echo "Checking dependencies ..."

python - << 'CHECKS'
import sys, importlib, platform

checks = [
    ("PySide6",     "PySide6"),
    ("shiboken6",   "shiboken6"),
    ("matplotlib",  "matplotlib"),
    ("numpy",       "numpy"),
    ("pandas",      "pandas"),
    ("scipy",       "scipy"),
    ("tifffile",    "tifffile"),
    ("imageio",     "imageio"),
    ("PIL",         "Pillow"),
    ("skimage",     "scikit-image"),
    ("csbdeep",     "csbdeep"),
    ("stardist",    "stardist"),
]

ok = True
WARN_ONLY = {"stardist", "csbdeep"}  # these import numba which may fail in check context
for mod, pkg in checks:
    try:
        m = importlib.import_module(mod)
        ver = getattr(m, "__version__", "?")
        print(f"  ✓ {pkg}  {ver}")
    except ImportError as e:
        if mod in WARN_ONLY:
            print(f"  ! {pkg} import warning (will still be bundled): {e}")
        else:
            print(f"  ✗ {pkg} MISSING — {e}")
            ok = False

# TensorFlow check — installed as tensorflow-macos on Apple Silicon
# but always imported as 'tensorflow'
try:
    import tensorflow as tf
    print(f"  ✓ tensorflow  {tf.__version__}")
    if platform.machine() == "arm64":
        gpus = tf.config.list_physical_devices("GPU")
        if gpus:
            print(f"  ✓ Metal GPU: {gpus[0].name}")
        else:
            print("  ! No Metal GPU — tensorflow-metal may not be installed")
            print("    pip install tensorflow-metal==1.1.0")
except ImportError:
    print("  ✗ tensorflow MISSING")
    if platform.machine() == "arm64":
        print("    pip install tensorflow-macos==2.13.0 tensorflow-metal==1.1.0")
    else:
        print("    pip install tensorflow>=2.12,<2.16")
    ok = False

# PySide6 functional test — create + destroy a QApplication to verify
# the Qt6 plugins are reachable. matplotlib's QtAgg backend depends on
# the same plugins working at runtime.
try:
    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(sys.argv[:1])
    print("  ✓ PySide6 functional")
except Exception as e:
    print(f"  ✗ PySide6 failed: {e}")
    ok = False

if not ok:
    sys.exit(1)
CHECKS

echo ""

# ── 4. Verify all source files are present ──────────────────────────────────
echo "Checking source files ..."

# Determine script directory and repository root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# Script lives at _Docs/_Installation/build_all_well.sh — repo root is two
# directory levels up, not one. Going only ``dirname`` deep made
# REPO_ROOT == .../_Docs and PyInstaller looked for allwell.py inside
# _Docs/ instead of the project root.
REPO_ROOT="$( dirname "$( dirname "$SCRIPT_DIR" )" )"

# Operate from the actual repo root so all relative paths below resolve.
cd "$REPO_ROOT" || exit 1

# Versioned + profile-tagged bundle name — must match all_well.spec's output
# (COLLECT / BUNDLE name) so the post-build codesign / xattr / zip steps below
# find the produced app. Derived from the same pure helper the spec uses, so the
# two can never drift. Overrides the "AllWell" default set above.
APP_NAME=$(python - << 'PYNAME'
import os, sys
sys.path.insert(0, "scripts")
from profile_build import bundle_name
ns = {}
exec(open("version.py").read(), ns)
print(bundle_name(ns["__version__"], os.environ.get("ALLWELL_PROFILE", "")))
PYNAME
)
echo "Bundle name: $APP_NAME"

REQUIRED=(
    "allwell.py"
    "all_well_launcher.py"
    "process_microscopy.py"
    "pipeline/__init__.py"
    "pipeline/well_plate_zipper.py"
    "util/__init__.py"
    "analyze/__init__.py"
    "datasets/__init__.py"
    "review/__init__.py"
    "widgets/__init__.py"
    "ui/__init__.py"
    "ui/theme.py"
    "startup/__init__.py"
    "core/__init__.py"
    "domain/__init__.py"
    "services/__init__.py"
    "_Docs/_Installation/all_well.spec"
    "_Docs/_Installation/hooks/hook-stardist.py"
    "_Docs/_Installation/hooks/hook-csbdeep.py"
    "_Docs/_Installation/hooks/hook-pkg_resources.py"
    "_Docs/_Installation/hooks/rthook-pkg_resources.py"
    "_Docs/requirements.txt"
)
MISSING=0
for f in "${REQUIRED[@]}"; do
    if [ -f "$f" ]; then
        echo "  ✓ $f"
    else
        echo "  ✗ $f MISSING"
        MISSING=$((MISSING + 1))
    fi
done
if [ "$MISSING" -gt 0 ]; then
    echo ""
    echo "ERROR: $MISSING required file(s) missing. Aborting."
    echo ""
    echo "Repository root: $REPO_ROOT"
    echo ""
    echo "Ensure you:"
    echo "  1. Have the entry points at the repository root: allwell.py, all_well_launcher.py, process_microscopy.py"
    echo "  2. Have _Docs/_Installation/hooks/ subdirectory with PyInstaller hooks"
    echo "  3. Have _Docs/requirements.txt with pip dependencies"
    exit 1
fi
echo ""

# ── 5. Run PyInstaller ───────────────────────────────────────────────────────
echo "Running PyInstaller ..."
echo "  Repository root: $REPO_ROOT"
rm -rf build "$DIST_DIR/$APP_NAME" "$DIST_DIR/$APP_NAME.app"

SPEC_FILE="_Docs/_Installation/all_well.spec"
if [ -n "$TARGET_ARCH" ]; then
    echo "  Patching spec for target_arch='$TARGET_ARCH' ..."
    sed -i '' "s/target_arch=None/target_arch='$TARGET_ARCH'/" "$SPEC_FILE"
fi

pyinstaller "$SPEC_FILE" \
    --noconfirm \
    --log-level WARN

if [ -n "$TARGET_ARCH" ]; then
    sed -i '' "s/target_arch='$TARGET_ARCH'/target_arch=None/" "$SPEC_FILE"
fi

echo ""

# ── 6. Verify the bundle ────────────────────────────────────────────────────
if [ -d "$DIST_DIR/$APP_NAME.app" ]; then
    SIZE=$(du -sh "$DIST_DIR/$APP_NAME.app" | cut -f1)
    echo "✓ Build successful: $DIST_DIR/$APP_NAME.app  ($SIZE)"
else
    echo "ERROR: Build failed — $APP_NAME.app not found in $DIST_DIR"
    exit 1
fi

# ── 7. Ad-hoc code sign ─────────────────────────────────────────────────────
echo ""
echo "Ad-hoc code signing ..."
# Remove all extended attributes first — quarantine bits block ad-hoc signing
xattr -cr "$DIST_DIR/$APP_NAME.app" 2>/dev/null || true
codesign --force --deep --sign - "$DIST_DIR/$APP_NAME.app" 2>/dev/null \
    && echo "✓ Signed" \
    || echo "  WARNING: codesign failed — Gatekeeper may block first launch"

# ── 8. Remove quarantine flag ───────────────────────────────────────────────
xattr -d com.apple.quarantine "$DIST_DIR/$APP_NAME.app" 2>/dev/null || true
# Also clear any quarantine on contents
find "$DIST_DIR/$APP_NAME.app" -name "*.dylib" -o -name "*.so" | \
    xargs xattr -d com.apple.quarantine 2>/dev/null || true

echo ""
echo "=== Done ==="
echo ""
echo "To run:"
echo "    open $DIST_DIR/$APP_NAME.app"
echo ""
echo "To distribute:"
echo "    cd $DIST_DIR && zip -r $APP_NAME-mac.zip $APP_NAME.app"
echo ""
if [ -z "$TARGET_ARCH" ]; then
    echo "For a universal binary (Intel + Apple Silicon):"
    echo "    TARGET_ARCH=universal2 ./build_all_well.sh"
    echo ""
fi
