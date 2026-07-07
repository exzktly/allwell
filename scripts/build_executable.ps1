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
#       pip install --no-build-isolation -r _Docs\requirements.txt
#
# Usage (from the repo root):
#   .\scripts\build_executable.ps1            # incremental build
#   .\scripts\build_executable.ps1 -Clean     # wipe build\ and dist\ first
#
# The default build is slim: StarDist + Cellpose inference only. Multi-frame
# tracking + its training stack is opt-in — install the extras and set the flag
# (the spec reads it via os.environ, same as the macOS/Linux build):
#   pip install --no-build-isolation -r _Docs\requirements-tracking.txt
#   $env:ALLWELL_WITH_TRACKING = '1'
#   .\scripts\build_executable.ps1 -Clean
#
# Output:
#   dist\AllWell\AllWell.exe    (the spec's BUNDLE step is a macOS-only
#                               no-op off macOS; no AllWell.app is produced).

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
    Write-Error "PyInstaller is not importable in the active Python env.`nRun: pip install --no-build-isolation -r _Docs\requirements.txt"
    exit 1
}

Write-Host "Running PyInstaller against $specFile ..."
if ($env:ALLWELL_WITH_TRACKING -and $env:ALLWELL_WITH_TRACKING -ne '0') {
    Write-Host '  (ALLWELL_WITH_TRACKING set - bundling tracking/training stack)'
}
$logLevel = if ($env:ALLWELL_LOG_LEVEL) { $env:ALLWELL_LOG_LEVEL } else { 'INFO' }
& $pythonCmd @pyArgs -m PyInstaller $specFile --noconfirm --log-level $logLevel
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller exited with code $LASTEXITCODE"
    exit $LASTEXITCODE
}

if (Test-Path 'dist\AllWell\AllWell.exe') {
    Write-Host 'Build complete: dist\AllWell\AllWell.exe'
} elseif (Test-Path 'dist\AllWell') {
    Write-Host 'Build complete: dist\AllWell\'
} else {
    Write-Error 'build finished but dist\AllWell\ was not produced.'
    exit 1
}
