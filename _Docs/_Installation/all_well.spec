# all_well.spec
# -------------
# PyInstaller spec file for building AllWell.app on macOS.
#
# Usage (run from the directory containing this file):
#   pyinstaller all_well.spec
#
# Output:
#   dist/AllWell-<version>[-<profile>].app   — double-clickable macOS app bundle
#   (e.g. dist/AllWell-8.8.8.app, or dist/AllWell-8.8.8-thin.app for a subset)
#
# Requires: pyinstaller >= 6.0, all packages in _Docs/requirements.txt
# installed in the active Python environment.

import sys
import os
import tempfile as _tempfile
from pathlib import Path
import importlib.util

# Raise the interpreter recursion limit for the build. PyInstaller walks the
# module-dependency graph recursively during Analysis; on a tree this large and
# densely cross-linked (TensorFlow, torch, scipy, …) it overflows Python's
# default limit of 1000 with "maximum recursion depth exceeded". Bumping it here
# only affects the build process, not the frozen app. This is PyInstaller's
# documented fix for the error.
sys.setrecursionlimit(max(sys.getrecursionlimit(), 5000))


# ---------------------------------------------------------------------------
# Helper: locate a package's data directory
# ---------------------------------------------------------------------------

def pkg_dir(name: str) -> Path:
    spec = importlib.util.find_spec(name)
    if spec is None:
        raise RuntimeError(f"Package {name!r} not found — install _Docs/requirements.txt first")
    return Path(spec.origin).parent


# ---------------------------------------------------------------------------
# Sibling scripts bundled as data files
# PyInstaller cannot follow exec() calls in allwell.py, so these scripts
# are bundled explicitly and extracted to sys._MEIPASS at runtime.
# ---------------------------------------------------------------------------

_here = Path(SPECPATH)               # _Docs/_Installation/ — directory of this .spec  # noqa: F821
_parent = _here.parent.parent        # repository root (two levels up)

# Single source of truth for the app version (repo-root version.py). Loaded by
# exec so the .spec doesn't depend on the repo root being importable at build
# time. Bump version.py on a release; the bundle version tracks it automatically.
# Read with an explicit codec: both this file and the profile manifests below
# contain UTF-8 punctuation, and ``read_text()`` with no encoding decodes with
# the build machine's locale — the ANSI codepage on Windows, which mojibakes
# those bytes and raises outright on any the codepage has no character for.
_ver_ns: dict = {}
exec((_parent / "version.py").read_text(encoding="utf-8"), _ver_ns)
APP_VERSION = _ver_ns["__version__"]

# ---------------------------------------------------------------------------
# Build profile — select which features go in THIS binary
# ---------------------------------------------------------------------------
# `ALLWELL_PROFILE=<name> scripts/build_executable.sh` builds a binary that ships
# only the profile's features (profiles/<name>.toml). Unset / "full" = the full
# app. Excluded features' source dirs are omitted from the bundled `well_viewer`
# tree, their unique heavy deps are excluded from the module graph, and the
# chosen profile is baked as `active_profile.txt` so the frozen app's registry
# filters to match the bundled code (a frozen binary has no env at run time).
try:  # noqa: E402  tomllib is stdlib on 3.11+; fall back to tomli on the 3.10 build env.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib   # type: ignore[no-redef]

# The pure profile→exclusions math lives in scripts/profile_build.py (stdlib-only,
# unit-tested), so this spec stays declarative and the "deps are gated into the
# binary by feature, automatically" contract is verifiable outside a real build.
sys.path.insert(0, str(_parent))
from scripts.profile_build import (  # noqa: E402
    compute_exclusions, load_catalog, path_is_excluded,
)
# Remote (SLURM/SSH) execution uploads exactly these files to the worker; the
# frozen app resolves them under _MEIPASS, so every one MUST ship regardless of
# which GUI features a profile bundles (a remote run may use any segmenter). This
# list is the bundling FLOOR applied to the per-file profile filter below, so a
# profile that excludes a feature owning a manifest file (e.g. Cellpose owns
# pipeline/cellpose.py) can never drop it and break remote runs. remote_runner
# only pulls stdlib + the Qt-free services.remote_config, so importing it here is
# build-safe.
from services.remote_runner import _REQUIRED_REMOTE_FILES  # noqa: E402

_PROFILE = os.environ.get("ALLWELL_PROFILE", "").strip()
_excluded_features: list = []
_excluded_repo_dirs: list = []      # repo-relative paths to omit from the bundle
_excluded_deps: list = []           # import-name heavy deps unique to excluded feats
_profile_active = bool(_PROFILE) and _PROFILE.lower() != "full"

if _profile_active:
    _prof = tomllib.loads(
        (_parent / "profiles" / f"{_PROFILE}.toml").read_text(encoding="utf-8"))
    _catalog = load_catalog(_parent / "profiles" / "full.toml")
    _ex = compute_exclusions(_catalog, _prof.get("features"),
                             _prof.get("subtabs") or {})
    # repo_paths spans well_viewer/ tabs AND non-Qt units (pipeline/cellpose.py);
    # the bundling loop filters every first-party package against this list.
    _excluded_features = _ex.features
    # Apply the remote-manifest bundling floor: never exclude a file the remote
    # pipeline needs, even if the feature that "owns" it is profiled out. Without
    # this, a Cellpose-excluded profile drops pipeline/cellpose.py and every
    # remote run aborts at RemoteRunner.sync_required_files' existence check.
    _floor = set(_REQUIRED_REMOTE_FILES)
    _excluded_repo_dirs = [d for d in _ex.repo_paths if d not in _floor]
    _excluded_deps = _ex.deps
    print(f"[all_well.spec] ALLWELL_PROFILE={_PROFILE!r} — excluding "
          f"{len(_excluded_features)} feature(s): {_excluded_features}")

# ---------------------------------------------------------------------------
# Output bundle name — carry the version and (for a feature subset) the profile
# so the produced artifact self-documents which build it is, e.g.
#   AllWell-8.8.8        (full app)
#   AllWell-8.8.8-thin   (ALLWELL_PROFILE=thin)
# The pure name math lives in scripts/profile_build.bundle_name (unit-tested) so
# the build scripts derive the identical path for their post-build steps. The
# executable INSIDE the bundle stays "AllWell" (CFBundleExecutable), so the
# re-exec / launcher paths that spawn sys.executable are unaffected.
# ---------------------------------------------------------------------------
from scripts.profile_build import bundle_name  # noqa: E402
_BUNDLE_BASENAME = bundle_name(APP_VERSION, _PROFILE)
_DISPLAY_NAME = f"All Well {APP_VERSION}" + (f" ({_PROFILE})" if _profile_active else "")


def _pkg_datas(pkg_root: Path, dest: str, exclude_repo_dirs: list) -> list:
    """Per-file data tuples for *pkg_root* → *dest*, omitting excluded dirs.

    Excludes are repo-relative (e.g. ``well_viewer/features/stats``); any file
    under one is dropped, so a profiled-out feature's source never lands in the
    bundle. __pycache__ is always skipped."""
    out: list = []
    for f in pkg_root.rglob("*"):
        if not f.is_file() or "__pycache__" in f.parts:
            continue
        repo_rel = f"{dest}/{f.relative_to(pkg_root).as_posix()}"
        if path_is_excluded(repo_rel, exclude_repo_dirs):
            continue
        out.append((str(f), str(Path(dest) / f.relative_to(pkg_root).parent)))
    return out


# Every first-party module at the repository root, bundled as source beside
# the bytecode PyInstaller's own graph collects.
#
# The rule is "all of them", deliberately, rather than "the ones the graph
# might miss". Whether a given module is reachable by static analysis is a
# property of today's import statements, not of the module: one `importlib`
# call added inside a package turns a working build into one that fails only
# when a user reaches that screen, and nothing in this repository would catch
# it — the frozen build is not exercised by any test. Shipping a small .py
# twice costs kilobytes; deciding per module costs a released binary.
#
# `all_well_launcher.py` is absent because it is the Analysis entry script
# below, not a data file. `tests/test_spec_ships_every_first_party_module.py`
# holds this list against the tree.
sibling_scripts = [
    (str(_parent / "allwell.py"),               "."),
    (str(_parent / "process_microscopy.py"),    "."),
    (str(_parent / "WellPlateZipper.py"),       "."),
    (str(_parent / "version.py"),               "."),
    # Remote (SLURM/SSH) execution uploads the files in
    # services.remote_runner._REQUIRED_REMOTE_FILES to the worker; that list
    # includes _Docs/requirements-remote.txt (the headless pip install). The
    # frozen app resolves those paths under _MEIPASS, so the file must ship at
    # _MEIPASS/_Docs/requirements-remote.txt or remote runs abort with
    # "missing local file". (The GUI-superset requirements.txt is NOT bundled —
    # only the remote install manifest is needed at runtime.)
    (str(_parent / "_Docs" / "requirements-remote.txt"), "_Docs"),
    # The Analyze → Remotes dialog provisions a remote host by spawning this
    # bootstrap module as a subprocess (re-exec'd via the ``--run-bootstrap``
    # launcher sentinel — sys.executable is the .app binary, not python). It's
    # loaded by file path at _MEIPASS/scripts/_bootstrap_lib.py and its
    # ``REPO_ROOT = __file__.parent.parent`` resolves to _MEIPASS, where the
    # files it syncs to the remote (process_microscopy.py, pipeline/, util/,
    # _Docs/requirements-remote.txt) are already bundled. Keep the ``scripts/``
    # dest so that .parent.parent lands on the bundle root, not above it.
    (str(_parent / "scripts" / "_bootstrap_lib.py"), "scripts"),
]

# Bundle every first-party package so module imports resolve at runtime inside
# the PyInstaller _MEIPASS root. Under an active profile every package is
# filtered per-file against ``_excluded_repo_dirs`` (excluded feature dirs +
# non-Qt units like pipeline/cellpose.py dropped); the full build ships them
# whole.
for _pkg in ("well_viewer", "widgets", "ui", "services", "pipeline", "util",
             "startup",
             "analyze", "datasets"):
    _pkg_path = _parent / _pkg
    if not _pkg_path.is_dir():
        continue
    if _profile_active:
        sibling_scripts += _pkg_datas(_pkg_path, _pkg, _excluded_repo_dirs)
    else:
        sibling_scripts.append((str(_pkg_path), _pkg))

# matplotlib ships fonts, styles, and mpl-data at runtime
mpl_data = (str(pkg_dir("matplotlib") / "mpl-data"), "matplotlib/mpl-data")

# stardist ships .json config and .npz weight files inside the package
stardist_data = (str(pkg_dir("stardist")), "stardist")

# csbdeep ships no data files but include the package dir for safety
csbdeep_data = (str(pkg_dir("csbdeep")), "csbdeep")

# Bundled reference manual rendered inside the help drawer (see
# ``AllWellApp._toggle_help_drawer`` → README section). The whole
# ``Markdowns/`` directory ships so relative image links inside the
# README resolve correctly at runtime; missing folder silently hides the
# section in the help drawer (trimmed builds), which is fine.
#
# Under an active (thin) profile, ship a README trimmed to the profile's
# features so the in-app help documents only what this binary contains — the
# pure trimming math lives in scripts/profile_build.trim_readme_for_profile and
# is unit-tested. We copy Markdowns/ to a temp dir (so image links still
# resolve) and overwrite the README there with the trimmed text.
_markdowns_dir = _parent / "_Docs" / "Markdowns"
extra_data: list = []
if _markdowns_dir.is_dir():
    if _profile_active:
        import shutil as _shutil
        _tmp_md = Path(_tempfile.mkdtemp()) / "Markdowns"
        _shutil.copytree(str(_markdowns_dir), str(_tmp_md))
        _readme = _tmp_md / "README_WellViewer.md"
        if _readme.exists():
            from scripts.profile_build import trim_readme_for_profile
            _readme.write_text(
                trim_readme_for_profile(
                    _readme.read_text(encoding="utf-8"),
                    _prof.get("features"), _prof.get("subtabs")),
                encoding="utf-8")
        extra_data.append((str(_tmp_md), "Markdowns"))
    else:
        extra_data.append((str(_markdowns_dir), "Markdowns"))

# Bundled UI fonts (Inter + JetBrains Mono). Loaded at startup by
# well_viewer.core.app_fonts, which resolves ``_MEIPASS/assets/fonts`` in a
# frozen build — so without this the app silently falls back to the platform
# font. Ship them under the same ``assets/fonts`` layout the loader expects.
_fonts_dir = _parent / "assets" / "fonts"
if _fonts_dir.is_dir():
    extra_data.append((str(_fonts_dir), "assets/fonts"))

# Bundled UI icons (the dropdown chevron referenced by theme.qss()'s combo /
# menu-button caret rules, resolved via ``_MEIPASS/assets/icons`` when frozen).
_icons_dir = _parent / "assets" / "icons"
if _icons_dir.is_dir():
    extra_data.append((str(_icons_dir), "assets/icons"))

# Build-profile manifests + baked marker. Ship profiles/ so the frozen app's
# ``well_viewer.core.profile.load_profile`` resolves the active manifest, and
# write ``active_profile.txt`` at the bundle root so ``active_profile()`` picks
# the profile with no run-time env var. Absent (full build) → the full app.
_profiles_dir = _parent / "profiles"
if _profiles_dir.is_dir():
    extra_data.append((str(_profiles_dir), "profiles"))
if _profile_active:
    _marker_path = Path(_tempfile.mkdtemp()) / "active_profile.txt"
    _marker_path.write_text(_PROFILE, encoding="utf-8")
    extra_data.append((str(_marker_path), "."))

# Build-time THEME selection (soft): ``ALLWELL_THEME=<name> scripts/build_executable.sh``
# bakes ``allwell_theme.txt`` at the bundle root; all_well_launcher._apply_baked_theme
# reads it and setdefault()s ALLWELL_THEME, so the frozen app starts on that theme
# while a runtime ALLWELL_THEME / --theme still overrides. Unknown names are ignored
# (the build then starts on theme.DEFAULT_THEME). Mirrors the profile marker above.
_BAKED_THEME = os.environ.get("ALLWELL_THEME", "").strip()
if _BAKED_THEME:
    import theme as _thememod  # Qt-free; safe at build time
    if _BAKED_THEME in _thememod.available_themes():
        _theme_marker = Path(_tempfile.mkdtemp()) / "allwell_theme.txt"
        _theme_marker.write_text(_BAKED_THEME, encoding="utf-8")
        extra_data.append((str(_theme_marker), "."))
        print(f"[all_well.spec] ALLWELL_THEME={_BAKED_THEME!r} — baking as the default theme")
    else:
        print(f"[all_well.spec] WARNING: ALLWELL_THEME={_BAKED_THEME!r} is not a known theme "
              f"({_thememod.available_themes()}); not baking — the build starts on "
              f"{_thememod.DEFAULT_THEME!r} like any other.")

datas = sibling_scripts + [mpl_data, stardist_data, csbdeep_data] + extra_data


# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------

hiddenimports = [
    # First-party repo-root pipeline module imported lazily inside
    # process_microscopy.compute_shape_features (function-level imports are
    # missed by PyInstaller's static analysis, unlike the top-level
    # pipeline.auto_threshold / util.well_token).
    "pipeline.shape_features",
    # Qt-free flat-field estimators shared by pipeline + viewer (imported lazily
    # inside the review-image controller, so static analysis can miss it).
    "pipeline.flatfield",
    # numpy internals
    "numpy.core._multiarray_umath",
    "numpy.core._multiarray_tests",
    "numpy.core.multiarray",
    "numpy.random",
    "numpy.random._common",
    "numpy.random._bounded_integers",
    "numpy.random._generator",
    "numpy.random.mtrand",

    # scipy — stats and ndimage used by well_viewer + process_microscopy
    "scipy.stats",
    "scipy.stats._stats_py",
    "scipy.stats._continuous_distns",
    "scipy.stats._discrete_distns",
    "scipy.stats._kde",              # gaussian_kde moved here in scipy 1.11
    "scipy.ndimage",
    "scipy.ndimage._morphology",
    "scipy.ndimage._filters",
    "scipy.ndimage._interpolation",
    "scipy.special",
    "scipy.special._ufuncs",         # _cython_special renamed in scipy 1.11
    "scipy.linalg",
    "scipy.linalg.blas",
    "scipy.linalg.lapack",
    "scipy._lib.messagestream",
    "scipy._lib._util",

    # scikit-image — verified against skimage 0.21.0 arm64 wheel contents
    "skimage",
    "skimage.morphology",
    "skimage.morphology._grayreconstruct",   # correct spelling (no 'e' in gray)
    "skimage.morphology._convex_hull",
    "skimage.morphology._flood_fill",
    "skimage.morphology.binary",
    "skimage.morphology.gray",
    "skimage.morphology.footprints",
    "skimage.segmentation",
    "skimage.segmentation.boundaries",       # no leading underscore in skimage 0.21
    "skimage.segmentation._watershed",
    "skimage.segmentation._slic",
    "skimage._shared",
    "skimage._shared.utils",
    "skimage._shared._geometry",
    "skimage.filters",
    "skimage.filters.rank",
    "skimage.filters.thresholding",
    "skimage.measure",
    "skimage.measure._regionprops",
    "skimage.util",
    "skimage.util.dtype",

    # matplotlib backends — Qt is the runtime backend; keep agg as the
    # in-memory rasteriser for SVG/PDF/PNG savefig paths.
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qt",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends.backend_svg",
    "matplotlib.backends.backend_pdf",
    "matplotlib.figure",
    "matplotlib.patches",
    "matplotlib.lines",
    "matplotlib.collections",
    "matplotlib.axes._axes",
    "matplotlib.axis",

    # PySide6 — the v2 UI runs on Qt6. PyInstaller picks up the visible
    # widgets via static analysis but the deferred-import paths in
    # widgets/* and well_viewer/* need explicit hints.
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtSvg",
    "shiboken6",

    # pyobjc — Copy SVG writes NSPasteboard natively so Illustrator pastes
    # true vector. Imported lazily inside figure_export_editor; PyInstaller's
    # static analysis cannot see it, so name the frameworks explicitly.
    "AppKit",
    "Foundation",
    "objc",

    # pandas — the Review tab and gating pipeline run on DataFrames.
    "pandas",
    "pandas.io",
    "pandas.io.formats",
    "pandas.io.formats.style",

    # pyarrow — the per-well Parquet frame cache (well_viewer/data/parquet_cache.py).
    # Imported lazily inside that module, so PyInstaller's module graph cannot
    # see it and it has to be named here. Absent, the app parses CSVs instead of
    # failing, but a shipped build should carry it: it is a 60x read.
    "pyarrow",
    "pyarrow.parquet",

    # PIL / Pillow — Qt6 path uses ImageQt instead of ImageTk.
    "PIL",
    "PIL.Image",
    "PIL.ImageQt",
    "PIL.ImageOps",
    "PIL.TiffImagePlugin",
    "PIL.PngImagePlugin",
    # JPEG raw/cell images. PIL registers decoders per-plugin and the frozen
    # build ships only the plugins named here; without this, ``PIL.Image.open``
    # on a ``.jpg``/``.jpeg`` (an accepted input ext — image_discovery._IMAGE_EXTS)
    # raises "cannot identify image file". The scatter Cell Viewer reads *raw*
    # inputs (unlike the Image Table, which shows the pipeline's uncompressed TIFF
    # outputs), so a JPEG input decodes fine from source but silently blanks in the
    # bundle. (JpegImagePlugin pulls PIL.JpegPresets, which PyInstaller follows.)
    "PIL.JpegImagePlugin",

    # tifffile + imageio
    "tifffile",
    "imageio",
    "imageio.plugins.tifffile",
    # imageio loads format plugins lazily by name. The pipeline writes PNG
    # overlays via imageio v3 (pipeline/io.py::_safe_imwrite -> iio.imwrite for
    # ".png"), which dispatches to the pillow plugin — absent it, PNG writes fail
    # in the frozen build with a "could not find a backend" error.
    "imageio.plugins.pillow",

    # imagecodecs — the top-level package only; compiled per-codec extensions
    # (lzw, jpeg, zstd, etc.) are collected via collect_all() below so their
    # .so binaries are bundled. Listing only "imagecodecs" here would bundle
    # the Python shim but leave the compiled codec extensions out, producing
    # "cannot import name lzw_decode from imagecodecs" at runtime.
    "imagecodecs",

    # standard library PyInstaller sometimes misses
    "zipfile",
    "csv",
    "json",
    "statistics",
    "copy",
    "queue",
    "threading",
    "subprocess",
    "shutil",
    "importlib.util",
    "html",
    "html.parser",
    "http",
    "http.client",
    "pyparsing",
    "pyparsing.helpers",
    "pyparsing.core",
    "pyparsing.common",
    "pyparsing.results",

    # stardist — compiled C extension; PyInstaller misses submodules entirely
    "stardist",
    "stardist.models",
    "stardist.models.model2d",
    "stardist.models.base",
    "stardist.geometry",
    "stardist.geometry.geom2d",
    "stardist.nms",
    "stardist.rays3d",
    "stardist.sample_patches",
    "stardist.utils",
    "stardist.matching",
    "stardist.plot",
    "stardist.data",

    # csbdeep — pure Python but uses dynamic plugin loading
    "csbdeep",
    "csbdeep.utils",
    "csbdeep.utils.utils",
    "csbdeep.utils.tf",
    "csbdeep.data",
    "csbdeep.data.transform",
    "csbdeep.data.generate",
    "csbdeep.models",
    "csbdeep.models.base_model",
    "csbdeep.models.care_standard",
    "csbdeep.internals",
    "csbdeep.internals.blocks",
    "csbdeep.internals.predict",
    "csbdeep.internals.train",

    # numba — stardist's NMS uses numba JIT; needs its full internal stack
    "numba",
    "numba.core",
    "numba.core.types",
    "numba.core.typing",
    "numba.core.compiler",
    "numba.core.dispatcher",
    "numba.np",
    "numba.np.ufunc",
    "numba.np.numpy_support",
    "numba.typed",
    "numba.typed.typeddict",
    "numba.typed.typedlist",
    "numba.cpython",
    "numba.cuda",
    "llvmlite",
    "llvmlite.binding",

    # tensorflow internals — needed when TF is imported inside worker processes
    "tensorflow",
    "tensorflow.python",
    "tensorflow.python.keras",
    "tensorflow.python.saved_model",
    "tensorflow.python.framework",
    "tensorflow.python.ops",
    "tensorflow.python.platform",
    "tensorflow.python.util",
    "tensorflow.python.eager",
    "keras",

    # h5py — used by keras/csbdeep to load model weights
    "h5py",
    "h5py._hl",
    "h5py._hl.files",
    "h5py._hl.dataset",
    "h5py._hl.group",
    "h5py.h5",
    "h5py.h5f",
    "h5py.h5d",
    "h5py.h5g",
    "h5py.h5s",
    "h5py.h5t",
    "h5py.h5z",

    # six — csbdeep dependency
    "six",

    # packaging — csbdeep uses it for version checks
    "packaging",
    "packaging.version",
    "packaging.requirements",

    # tqdm — csbdeep uses for progress bars
    "tqdm",
    "tqdm.auto",
    "tqdm.std",

    # charset_normalizer — requests (pulled in as a transitive dependency)
    # raises "unable to find acceptable character detection dependency" from
    # requests/__init__.py when neither charset_normalizer nor chardet can be
    # imported inside the frozen bundle.
    #
    # collect_all("charset_normalizer") below handles mypyc-compiled binary
    # extensions (md__mypyc / cd__mypyc .so files). Do NOT list those here
    # explicitly — they do not exist in all build environments and PyInstaller
    # emits ERROR: Hidden import '...' not found for any entry that is absent.
    "charset_normalizer",
    "charset_normalizer.api",
    "charset_normalizer.cd",
    "charset_normalizer.constant",
    "charset_normalizer.legacy",
    "charset_normalizer.md",
    "charset_normalizer.models",
    "charset_normalizer.utils",
    "charset_normalizer.version",

    # requests — imported transitively; PyInstaller's static analyser misses
    # it because the import lives inside try/except blocks in dependencies.
    # Do NOT list chardet here — it is not installed in the build environment
    # and causes ERROR: Hidden import not found during Analysis.
    "requests",
    "requests.adapters",
    "requests.auth",
    "requests.compat",
    "requests.cookies",
    "requests.exceptions",
    "requests.hooks",
    "requests.models",
    "requests.sessions",
    "requests.structures",
    "requests.utils",
    "urllib3",
    "urllib3.util",
    "urllib3.util.retry",
    "urllib3.util.timeout",
    "urllib3.util.url",
    "urllib3.contrib",
    "urllib3.contrib.pyopenssl",
    # urllib3.contrib.securetransport is macOS-only and not present in all
    # build environments — omit to avoid ERROR: Hidden import not found.
]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

# Collect all submodules of packages that use dynamic imports.
# NOTE: stardist, csbdeep, and pkg_resources are handled by custom hooks
# in hooks/ — do NOT call collect_submodules() on them here because that
# requires importing them, which fails when pkg_resources is not yet visible.
from PyInstaller.utils.hooks import collect_submodules, collect_data_files  # noqa: E402

_numba_excluded = {"numba.tests", "numba.pycc"}
_numba_mods = collect_submodules("numba")
hiddenimports += [m for m in _numba_mods
                  if not any(m == e or m.startswith(e + ".") for e in _numba_excluded)]
hiddenimports += collect_submodules("llvmlite")
hiddenimports += collect_submodules("h5py")

# scipy — vendors array_api_compat under scipy/_lib/ since 1.13. The
# array-api shim's numpy/__init__.py pulls in .fft/.linalg/_aliases via
# dynamic-looking imports that PyInstaller's static analyzer misses,
# producing a ModuleNotFoundError on the very first `from scipy import
# ndimage` at runtime. Collecting every scipy submodule is the most
# durable fix as scipy continues to refactor the array-api layer.
hiddenimports += collect_submodules("scipy")
datas       += collect_data_files("scipy")

# Heavy per-feature deps are gated OUT of the binary automatically from the
# catalog. ``_excluded_deps`` is the union of every profiled-out feature's
# ``deps`` (full.toml); a package is bundled only when it's NOT in that set.
# This is the single source of truth — adding a new dep-bearing feature to the
# catalog needs no edit here. NOTE: adding a package to PyInstaller ``excludes``
# (below) only prunes the *module graph*; it does NOT drop the ``datas`` /
# ``binaries`` that ``collect_all`` / ``collect_*`` emit. So skipping the collect
# is what actually keeps the package's .so/.dylib payload out of the bundle.
def _dep_included(*import_names) -> bool:
    """True when NONE of *import_names* were excluded by the active profile."""
    return not any(n in _excluded_deps for n in import_names)

# scikit-learn + umap-learn — the Multivariate tab's UMAP / t-SNE projections.
# Both have submodules loaded lazily (sklearn's manifold/decomposition, umap's
# numba-compiled layout functions, pynndescent) that the static analyzer misses,
# plus sklearn ships data files. numba/llvmlite/scipy are already collected
# above (umap/pynndescent depend on them). Skipped when Multivariate's deps are
# profiled out (they're added to `excludes` below too).
if _dep_included("sklearn"):
    hiddenimports += collect_submodules("sklearn")
    datas       += collect_data_files("sklearn")
if _dep_included("umap"):
    hiddenimports += collect_submodules("umap")
if _dep_included("pynndescent"):
    hiddenimports += collect_submodules("pynndescent")

# setuptools — TF's eager import of tensorflow._api.v2.compat.v1.lite
# walks through tensorflow.lite.python.convert, which pulls in
# setuptools._distutils.spawn and setuptools._distutils.errors. In
# setuptools >= 75 the latter does `from .compilers.C.errors import ...`,
# so the bundle must include the setuptools._distutils.compilers
# subpackage. collect_submodules alone under-collects this tree (the
# vendored _distutils package's __init__.py files get skipped, leaving
# phantom namespace packages that crash with
# KeyError: 'setuptools._distutils.compilers'). collect_all bundles
# every .py file as a data file, guaranteeing __init__.py is present.
from PyInstaller.utils.hooks import collect_all  # noqa: E402
_setuptools_datas, _setuptools_binaries, _setuptools_hidden = collect_all("setuptools")
datas         += _setuptools_datas
hiddenimports += _setuptools_hidden
hiddenimports += [
    "setuptools._distutils.compilers",
    "setuptools._distutils.compilers.C",
    "setuptools._distutils.compilers.C.errors",
    "setuptools._distutils.compilers.C.base",
    "setuptools._distutils.compilers.C.cygwin",
    "setuptools._distutils.compilers.C.msvc",
    "setuptools._distutils.compilers.C.unix",
    "setuptools._distutils.compilers.C.zos",
]

# Filter out dead/moved skimage submodules before collect_submodules even
# tries to import them. ``skimage.future.graph`` was moved to
# ``skimage.graph`` in scikit-image 0.20 and importing it raises
# ModuleNotFoundError, which PyInstaller's hook surfaces as a noisy
# "Failed to collect submodules for 'skimage.future.graph'" warning.
# Using the ``filter`` kwarg keeps PyInstaller from probing it at all.
def _skimage_filter(name: str) -> bool:
    return not name.startswith("skimage.future")

hiddenimports += collect_submodules("skimage", filter=_skimage_filter)

# charset_normalizer — use collect_all (not collect_submodules) so that
# mypyc-compiled binary extensions (e.g. md__mypyc.cpython-310-darwin.so,
# cd__mypyc.cpython-310-darwin.so) are collected as binaries, not just the
# wrapper .py files. collect_submodules() only adds module names to
# hiddenimports; it does not guarantee that compiled .so files with the
# __mypyc suffix are actually bundled.
_csn_datas, _csn_binaries, _csn_hidden = collect_all("charset_normalizer")
datas         += _csn_datas
hiddenimports += _csn_hidden

# requests — collect_all ensures urllib3 and other bundled extensions land
# as proper binaries rather than just hidden-import names.
_req_datas, _req_binaries, _req_hidden = collect_all("requests")
datas         += _req_datas
hiddenimports += _req_hidden

# imagecodecs — each codec (lzw, jpeg, zstd, …) is a separate compiled C
# extension. collect_all bundles both the Python shims and the .so binaries
# so that tifffile can load any codec present in the build environment.
_icd_datas, _icd_binaries, _icd_hidden = collect_all("imagecodecs")
datas         += _icd_datas
hiddenimports += _icd_hidden

# Cellpose is the only Torch-backed segmenter; StarDist (TF) is the kept default.
# torch / torchvision / cellpose are the Cellpose feature's catalog ``deps``, so
# profiling Cellpose out drops them from the bundle automatically — ``plan_ml``
# returns them only when they're NOT in ``_excluded_deps`` (unit-tested in
# tests/test_profile_build.py). collect_all grabs the Python shims, the .so/.dylib
# binaries, and data files so the frozen app runs without a Python toolchain. The
# macOS/Windows wheels are CPU/MPS (no CUDA); cellpose downloads model weights at
# runtime, so those are not bundled. The single biggest size lever after Qt.
from scripts.profile_build import plan_ml  # noqa: E402
_ml_packages = plan_ml(_excluded_deps)

_ml_binaries = []
for _pkg in _ml_packages:
    _ml_d, _ml_b, _ml_h = collect_all(_pkg)
    datas         += _ml_d
    _ml_binaries  += _ml_b
    hiddenimports += _ml_h

# torch subsystems unused by Cellpose *inference*. collect_all("torch") pulls
# the whole package, including torch.distributed (multi-node / multi-GPU
# TRAINING) — many of whose submodules do not cleanly import on macOS, producing
# a wall of "Hidden import 'torch.distributed...' not found" ERRORs during
# Analysis and needlessly enlarging/slowing the build. Excluding them here drops
# those modules from the graph (excludes win over the collect_all hiddenimports),
# silencing the errors. `import torch` does not import torch.distributed eagerly,
# and label-free inference never touches distributed/testing/tensorboard paths.
_torch_excludes = [
    "torch.distributed", "torch.testing",
    "torch.utils.tensorboard", "torch.utils.benchmark",
]

# Heavy training/serving packages the app never imports — kept out of the bundle
# even if a polluted build env has them installed or something pulls them in
# transitively. (These were the old multi-frame-tracking training stack, which no
# longer exists in the app; the static exclude is cheap hygiene against a large,
# unused payload.)
_dead_ml_excludes = [
    "btrack", "trackastra", "kornia",
    "lightning", "lightning_fabric", "lightning_cloud", "pytorch_lightning",
    "torchmetrics", "wandb", "tensorboard", "tensorboard_data_server",
    "cvxopt", "fastapi", "uvicorn", "starlette", "sentry_sdk",
]

# Qt/PySide6 module trimming — always on. The app imports only QtCore, QtGui,
# QtWidgets and QtSvg (verified across the tree), so the entire pyside6-addons
# payload (QtWebEngine, Qt3D, QtQuick3D, QtMultimedia, QtPdf, QtCharts,
# QtDataVisualization, QtVirtualKeyboard, QtDesigner, …) and the QML/Quick
# runtime are dead weight — hundreds of MB. PyInstaller's PySide6 hook drops the
# matching Qt shared libraries and QML plugins for every excluded module.
_qt_excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2", "PySide6.QtQml", "PySide6.QtQmlModels",
    "PySide6.QtQmlWorkerScript",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtBluetooth",
    "PySide6.QtNfc", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
    "PySide6.QtVirtualKeyboard", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtNetworkAuth", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
]

# Collect data files
datas += collect_data_files("numba")
datas += collect_data_files("skimage")

_extra_binaries = _csn_binaries + _req_binaries + _icd_binaries + _ml_binaries

a = Analysis(
    [str(_parent / "all_well_launcher.py")],
    pathex=[str(_parent)],
    binaries=_extra_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(_here / "hooks")],   # custom hooks for stardist/csbdeep/pkg_resources
    hooksconfig={},
    runtime_hooks=[str(_here / "hooks" / "rthook-charset_normalizer.py")],
    excludes=[
        # Heavy third-party packages not used by this app
        "IPython",
        "jupyter",
        "notebook",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "wx",
        "gtk",
        # v2 UI uses Qt; the tkinter integration is gone.
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "matplotlib.backends.backend_tkagg",
        "matplotlib.backends._backend_tk",
        # Dead/moved skimage submodules — raise ModuleNotFoundError on import
        # in scikit-image >= 0.20 (the whole skimage.future namespace was
        # decommissioned).
        "skimage.future",
        "skimage.future.graph",
        # keras benchmark module has syntax errors and is never used at runtime
        "keras.src.benchmarks",
        # NOTE: ssl intentionally NOT excluded — process_microscopy patches
        # ssl._create_default_https_context for StarDist model download
    ] + _qt_excludes + _dead_ml_excludes + _torch_excludes + _excluded_deps,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data)   # noqa: F821


# ---------------------------------------------------------------------------
# Executable
# ---------------------------------------------------------------------------

# Strip native symbols from the bundled binaries — a real size win on the large
# .dylib/.so payloads (TF, torch, …). Enabled on macOS and Linux, where the
# `strip` utility is present. NOT on Windows: a stock conda/Miniforge env has no
# `strip` binary, so PyInstaller would warn and skip it (or error on some
# setups). Unlike UPX, strip does not break macOS code-signing.
_STRIP = sys.platform != "win32"

exe = EXE(   # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AllWell",
    debug=False,
    bootloader_ignore_signals=False,
    strip=_STRIP,        # see _STRIP note above (on: macOS/Linux, off: Windows)
    upx=False,           # UPX breaks macOS code-signing
    console=False,       # GUI app — no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    # macOS: target Apple Silicon (arm64) explicitly. The dependency wheels are
    # arm64 (tensorflow-macos requires it), and many others (PySide6, …) ship as
    # universal2 fat binaries — target_arch="arm64" makes PyInstaller thin those
    # to the arm64 slice, dropping the dead x86_64 halves. Must be built on an
    # Apple-Silicon Mac. Off macOS this is None (Linux/Windows are single-arch:
    # ELF/PE aren't fat, so an arm64 Linux build is just built on an aarch64 host).
    target_arch=("arm64" if sys.platform == "darwin" else None),
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(   # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=_STRIP,        # match EXE — strip the collected binaries too
    upx=False,
    upx_exclude=[],
    name=_BUNDLE_BASENAME,   # dist/AllWell-<ver>[-<profile>]/ (the onedir bundle)
)


# ---------------------------------------------------------------------------
# macOS .app bundle
# ---------------------------------------------------------------------------

app = BUNDLE(   # noqa: F821
    coll,
    name=f"{_BUNDLE_BASENAME}.app",   # dist/AllWell-<ver>[-<profile>].app
    icon=None,           # replace with "all_well_icon.icns" if you have one
    bundle_identifier="com.allwell.app",
    version=APP_VERSION,
    info_plist={
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription":
            "AllWell needs access to open microscopy data files.",
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "CFBundleName": "AllWell",
        # Finder / Get Info display name carries the version and (thin) profile so
        # the build is identifiable even though CFBundleName stays short.
        "CFBundleDisplayName": _DISPLAY_NAME,
        "CFBundleExecutable": "AllWell",
        # PySide6 / Qt6 requires macOS 10.15+.
        "LSMinimumSystemVersion": "10.15",
        "NSPrincipalClass": "NSApplication",
        "NSRequiresAquaSystemAppearance": False,  # support dark mode
    },
)
