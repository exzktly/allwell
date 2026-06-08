# all_well.spec
# -------------
# PyInstaller spec file for building AllWell.app on macOS.
#
# Usage (run from the directory containing this file):
#   pyinstaller all_well.spec
#
# Output:
#   dist/AllWell.app   — double-clickable macOS application bundle
#
# Requires: pyinstaller >= 6.0, all packages in _Docs/requirements.txt
# installed in the active Python environment.

import sys
import os
from pathlib import Path
import importlib.util


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
# PyInstaller cannot follow exec() calls in all_well.py, so these scripts
# are bundled explicitly and extracted to sys._MEIPASS at runtime.
# ---------------------------------------------------------------------------

_here = Path(SPECPATH)               # _Docs/_Installation/ — directory of this .spec  # noqa: F821
_parent = _here.parent.parent        # repository root (two levels up)

sibling_scripts = [
    (str(_parent / "all_well.py"),              "."),
    (str(_parent / "analyze_tab.py"),           "."),
    (str(_parent / "process_microscopy.py"),    "."),
    (str(_parent / "shape_features_core.py"),   "."),
    (str(_parent / "flatfield_core.py"),        "."),
    (str(_parent / "WellPlateZipper.py"),       "."),
    (str(_parent / "theme.py"),                 "."),
]

# Bundle every first-party package so module imports resolve at runtime
# inside the PyInstaller _MEIPASS root.
for _pkg in ("well_viewer", "widgets", "ui", "services"):
    _pkg_path = _parent / _pkg
    if _pkg_path.is_dir():
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
_markdowns_dir = _parent / "_Docs" / "Markdowns"
extra_data: list = []
if _markdowns_dir.is_dir():
    extra_data.append((str(_markdowns_dir), "Markdowns"))

datas = sibling_scripts + [mpl_data, stardist_data, csbdeep_data] + extra_data


# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------

hiddenimports = [
    # First-party repo-root pipeline module imported lazily inside
    # process_microscopy.compute_shape_features (function-level imports are
    # missed by PyInstaller's static analysis, unlike the top-level
    # auto_threshold_core / well_token).
    "shape_features_core",
    # Qt-free flat-field estimators shared by pipeline + viewer (imported lazily
    # inside the review-image controller, so static analysis can miss it).
    "flatfield_core",
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

    # PIL / Pillow — Qt6 path uses ImageQt instead of ImageTk.
    "PIL",
    "PIL.Image",
    "PIL.ImageQt",
    "PIL.ImageOps",
    "PIL.TiffImagePlugin",
    "PIL.PngImagePlugin",

    # tifffile + imageio
    "tifffile",
    "imageio",
    "imageio.plugins.tifffile",

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

# Label-free segmentation (cellpose) + cell tracking (btrack / trackastra).
# torch / torchvision carry large compiled extensions; cellpose, btrack,
# trackastra, kornia and lightning each have submodules and (trackastra) bundled
# data (pretrained-model configs). collect_all grabs the Python shims, the
# .so/.dylib binaries, and data files so the frozen app can segment and track
# without a Python toolchain present. The macOS/Windows build wheels are
# CPU/MPS (no CUDA), so the torch payload stays manageable.
# NOTE: not yet exercised by a frozen build — verify on the dev env via
# build_all_well.sh / build_executable.ps1 (prune unused torch backends if the
# bundle grows too large). cellpose/trackastra download model weights at
# runtime; those are not bundled.
_ml_binaries = []
for _pkg in ("torch", "torchvision", "cellpose", "btrack", "trackastra",
             "kornia", "lightning", "pytorch_lightning", "torchmetrics"):
    _ml_d, _ml_b, _ml_h = collect_all(_pkg)
    datas         += _ml_d
    _ml_binaries  += _ml_b
    hiddenimports += _ml_h

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
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data)   # noqa: F821


# ---------------------------------------------------------------------------
# Executable
# ---------------------------------------------------------------------------

exe = EXE(   # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AllWell",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX breaks macOS code-signing
    console=False,       # GUI app — no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,    # None = native; use "universal2" for M1+Intel fat binary
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(   # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AllWell",
)


# ---------------------------------------------------------------------------
# macOS .app bundle
# ---------------------------------------------------------------------------

app = BUNDLE(   # noqa: F821
    coll,
    name="AllWell.app",
    icon=None,           # replace with "all_well_icon.icns" if you have one
    bundle_identifier="com.allwell.app",
    version="6.1.9",
    info_plist={
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription":
            "AllWell needs access to open microscopy data files.",
        "CFBundleShortVersionString": "3.4.0",
        "CFBundleVersion": "3.4.0",
        "CFBundleName": "AllWell",
        "CFBundleDisplayName": "All Well",
        "CFBundleExecutable": "AllWell",
        # PySide6 / Qt6 requires macOS 10.15+.
        "LSMinimumSystemVersion": "10.15",
        "NSPrincipalClass": "NSApplication",
        "NSRequiresAquaSystemAppearance": False,  # support dark mode
    },
)
