# build_executable.ps1 — PyInstaller wrapper for AllWell on native Windows.
#
# Parallel to scripts\build_executable.sh (the Linux / macOS / Git Bash
# entry point). Same spec file, same flags; uses `python -m PyInstaller`
# directly so we don't depend on the `pyinstaller.exe` shim being on PATH
# from a fresh Miniforge Prompt.
#
# Prereqs:
#   - The `allwell` conda env is activated (mamba activate allwell).
#   - Dependencies installed:
#       pip install --no-build-isolation -r requirements.txt
#
# Usage (from the repo root):
#   .\scripts\build_executable.ps1            # incremental build
#   .\scripts\build_executable.ps1 -Clean     # wipe build\ and dist\ first
#
# The build is slim: StarDist + Cellpose inference only. There is no
# tracking/training extra to opt into — the manifest this block used to name
# (`requirements-tracking.txt`) has never existed in this repository, and
# `all_well.spec` reads only ALLWELL_PROFILE and ALLWELL_THEME from the
# environment, never ALLWELL_WITH_TRACKING. The flag and the line it printed
# are gone rather than repointed: a build that announces "bundling
# tracking/training stack" and bundles nothing is the one thing this codebase
# treats as worse than failing outright.
#
# The other two build-time switches the spec reads from the environment work
# the same way here as on macOS/Linux:
#   $env:ALLWELL_PROFILE = 'thin'    # feature subset, from profiles\thin.toml
#   $env:ALLWELL_THEME   = 'grey'    # baked soft default theme (runtime-overridable)
#
# Output:
#   dist\AllWell-<version>[-<profile>]\AllWell.exe
# The bundle folder carries the app version from version.py, plus the profile
# name for a feature subset. The spec's BUNDLE step is a macOS-only no-op off
# macOS, so no AllWell.app is produced.

param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Split-Path -Parent $scriptDir
Set-Location $repoRoot

$specFile = Join-Path '_Docs\_Installation' 'all_well.spec'
if (-not (Test-Path $specFile)) {
    Write-Error "spec file not found at $specFile"
    exit 1
}

if ($Clean) {
    Write-Host 'Cleaning build\ and dist\ ...'
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue 'build', 'dist'
}

# Resolve a Python interpreter — prefer the active conda env's `python`,
# fall back to the py launcher. Then verify PyInstaller is importable;
# `python -m PyInstaller` always works from the activated env, whereas
# the `pyinstaller.exe` shim isn't always on PATH after a fresh
# Miniforge Prompt.
$pythonCmd = $null
foreach ($candidate in @('python', 'py', 'python3')) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $pythonCmd = $candidate
        break
    }
}
if (-not $pythonCmd) {
    Write-Error "No Python interpreter found on PATH. Activate the allwell env (mamba activate allwell) and retry."
    exit 1
}

$pyArgs = @()
if ($pythonCmd -eq 'py') { $pyArgs += '-3' }

& $pythonCmd @pyArgs -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller is not importable in the active Python env.`nRun: pip install --no-build-isolation -r requirements.txt"
    exit 1
}

Write-Host "Running PyInstaller against $specFile ..."
if ($env:ALLWELL_PROFILE -and $env:ALLWELL_PROFILE -ne 'full') {
    Write-Host "  (ALLWELL_PROFILE=$env:ALLWELL_PROFILE - building a feature subset)"
}
if ($env:ALLWELL_THEME) {
    Write-Host "  (ALLWELL_THEME=$env:ALLWELL_THEME - baking as the default theme; runtime-overridable)"
}
$logLevel = if ($env:ALLWELL_LOG_LEVEL) { $env:ALLWELL_LOG_LEVEL } else { 'INFO' }
& $pythonCmd @pyArgs -m PyInstaller $specFile --noconfirm --log-level $logLevel
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller exited with code $LASTEXITCODE"
    exit $LASTEXITCODE
}

# The bundle folder is named AllWell-<version>[-<profile>], so ask the same
# helper the spec used (scripts\profile_build.py::bundle_name) what to look for
# rather than hardcoding a path that goes stale on the next version bump. This
# also names the right folder on an incremental build, where dist\ still holds
# the bundles of earlier versions.
$bundleName = & $pythonCmd @pyArgs -c "import os, sys; sys.path[:0] = ['.', 'scripts']; from profile_build import bundle_name; from version import __version__; print(bundle_name(__version__, os.environ.get('ALLWELL_PROFILE', '')))"
if ($LASTEXITCODE -ne 0 -or -not $bundleName) {
    Write-Error "PyInstaller succeeded but the expected bundle name could not be resolved from version.py / scripts\profile_build.py. Check dist\ by hand."
    exit 1
}
$bundleDir = Join-Path 'dist' $bundleName.Trim()
$exePath   = Join-Path $bundleDir 'AllWell.exe'

if (Test-Path $exePath) {
    Write-Host "Build complete: $exePath"
} elseif (Test-Path $bundleDir) {
    Write-Host "Build complete: $bundleDir"
} else {
    Write-Error "build finished but $bundleDir was not produced."
    exit 1
}
