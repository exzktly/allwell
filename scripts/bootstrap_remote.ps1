# bootstrap_remote.ps1 — one-time setup for an AllWell remote-execution host
# (native Windows PowerShell version).
#
# Thin wrapper around scripts\_bootstrap_lib.py — the real logic lives
# there so the Linux/macOS/Git Bash path (bootstrap_remote.sh) and this
# one share a single source of truth.
#
# Prereqs on the local Windows machine:
#   1. Python 3.10+ on PATH.
#   2. Git for Windows installed (provides ssh.exe and rsync.exe at
#      C:\Program Files\Git\usr\bin\). The framework picks these up
#      automatically — you do not need to add them to PATH.
#
# Usage:
#   .\scripts\bootstrap_remote.ps1 <ssh-alias> [-Force]

$ErrorActionPreference = 'Stop'

# Force UTF-8 on Python's stdout/stderr so the bootstrap's progress
# messages (which contain ``→``, ``…``, etc.) don't crash with
# ``UnicodeEncodeError: 'charmap' codec can't encode character`` on a
# console whose codepage is cp1252. ``_bootstrap_lib.py`` ALSO calls
# ``sys.stdout.reconfigure(encoding='utf-8')`` at startup, but the
# very earliest argparse/usage output can fire before that runs, and
# some older Python builds don't honour reconfigure for already-piped
# streams. Setting the env var here is the bulletproof fallback.
$env:PYTHONIOENCODING = 'utf-8'
# Match the PowerShell host's output encoding too, so any text the
# Python child prints is decoded by PowerShell as UTF-8 instead of
# the default OEM codepage (which would render ``→`` as ``ΓåÆ``).
$prevConsoleEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$libPath = Join-Path $here '_bootstrap_lib.py'

# Find a Python interpreter. Try the standard launcher first, fall back
# to plain 'python' on PATH. We don't try to be clever — if neither is
# available, the user sees a clear PowerShell error.
$pythonCmd = $null
foreach ($candidate in @('py', 'python', 'python3')) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $pythonCmd = $candidate
        break
    }
}
if (-not $pythonCmd) {
    Write-Error "No Python interpreter found on PATH. Install Python 3.10+ from python.org and retry."
    exit 1
}

# Translate PowerShell-style -Force into the Python module's --force
# without otherwise touching $args.
$pyArgs = @()
foreach ($a in $args) {
    if ($a -ieq '-Force') {
        $pyArgs += '--force'
    } else {
        $pyArgs += $a
    }
}

# 'py' takes a -3 selector; plain 'python'/'python3' doesn't.
try {
    if ($pythonCmd -eq 'py') {
        & $pythonCmd -3 $libPath @pyArgs
    } else {
        & $pythonCmd $libPath @pyArgs
    }
    $code = $LASTEXITCODE
} finally {
    # Restore the host's previous output encoding so we don't leave the
    # session in a half-modified state.
    [Console]::OutputEncoding = $prevConsoleEncoding
}
exit $code
