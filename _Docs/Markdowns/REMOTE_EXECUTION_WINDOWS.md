# Remote execution from a Windows local machine

v5.0.1 adds first-class support for driving an AllWell remote-execution
host from a Windows PC. The remote machine is still expected to be
Linux (or macOS). What changed is the local side: ssh, rsync, paths,
and the bootstrap script all work natively on Windows now.

## One-time prerequisites

1. **Python 3.10 or newer.** Install from <https://www.python.org/downloads/>
   and make sure "Add Python to PATH" is ticked. Verify in PowerShell:
   ```powershell
   python --version
   ```
2. **Git for Windows.** Install from <https://git-scm.com/download/win>.
   The default options are fine — accept everything. This gives you
   `ssh.exe` at `C:\Program Files\Git\usr\bin\`. AllWell detects it
   automatically; you do not need to add it to PATH.
3. **rsync for Windows.** Git for Windows does NOT bundle `rsync.exe`
   by default. AllWell needs it for every file sync to the remote.
   The recommended path is **MSYS2** — it gives you rsync AND a
   matching ssh.exe (the two must come from the same MSYS runtime
   or rsync exits 12 with a `dup() in/out/err failed` error mid-sync).

   ### Recommended: MSYS2 via `pacman`

   1. Install MSYS2 from <https://www.msys2.org/> (use the default
      install location `C:\msys64\`).
   2. Open the **MSYS2 MSYS** shell (start menu).
   3. Install rsync + ssh into the same `usr\bin\`:
      ```bash
      pacman -Sy rsync openssh
      ```
      `openssh` matters: AllWell pairs ssh.exe with rsync.exe from
      the same directory to avoid cross-runtime fork failures.
   4. Add `C:\msys64\usr\bin\` to your **user** PATH so AllWell's
      `shutil.which` discovery finds them (MSYS2 doesn't add itself
      to PATH by default). In an admin-free PowerShell:
      ```powershell
      [Environment]::SetEnvironmentVariable(
          "Path",
          [Environment]::GetEnvironmentVariable("Path", "User") + ";C:\msys64\usr\bin",
          "User"
      )
      ```
      Open a **new** PowerShell / Miniforge Prompt afterwards — existing
      sessions inherit the old PATH and won't see rsync until restarted.

   ### Troubleshooting: pacman fails on TLS / certificate verification

   On a Windows machine with a broken or corporate-MITM'd root-CA
   store, `pacman -Sy` may fail with an OpenSSL / certificate error
   while trying to refresh the package database. Bypass both checks
   by editing `/etc/pacman.conf` inside the MSYS2 shell and adding
   these lines under `[options]`:

   ```ini
   SigLevel = Never
   XferCommand = /usr/bin/curl -k -L -C - -f -o %o %u
   ```

   - **`XferCommand = curl -k`** replaces pacman's built-in libcurl
     downloader with the external curl binary, passing `-k` to skip
     TLS verification entirely.
   - **`SigLevel = Never`** disables GPG signature checks on the
     downloaded packages (otherwise pacman fetches the package fine
     but refuses to install because the keyring can't be refreshed,
     which also needs network access).

   After editing, save and re-run `pacman -Sy rsync openssh`. This
   bypass is a workaround, not a permanent recommendation — once
   your CA store is fixed, remove both lines from `pacman.conf`
   and let pacman go back to verifying signatures.

   ### Alternative: manual MSYS2 package extraction

   If pacman is blocked entirely (no network, no shell), download
   the packages by hand from the MSYS2 mirror
   (<https://repo.msys2.org/msys/x86_64/>) in a browser and extract
   each `.pkg.tar.zst` with 7-Zip (two passes: `.zst` → `.tar` →
   folder). Drop every DLL from each `usr/bin/` into Git for
   Windows' `usr\bin\` alongside `ssh.exe`. You'll need:

   | Package | Provides |
   |---|---|
   | `rsync-*` | `rsync.exe` |
   | `msys2-runtime-*` | `msys-2.0.dll` (only if Git for Windows' is too old) |
   | `libxxhash-*` | `msys-xxhash-0.dll` |
   | `libzstd-*` | `msys-zstd-1.dll` |
   | `liblz4-*` | `msys-lz4-1.dll` |
   | `libopenssl-*` | `msys-crypto-*.dll`, `msys-ssl-*.dll` |
   | `libiconv-*` | `msys-iconv-2.dll` (Git already ships this — usually fine) |

   To find the next missing DLL after extracting, `cd` into the
   target folder in cmd and run `rsync.exe --version` — Windows
   pops up a dialog naming the first missing dependency, then repeat
   until rsync prints its version. **Use ONLY when MSYS2 pacman
   cannot be made to work** — the package list above is rsync 3.4.x
   today and the deps drift over time. Pacman keeps it in sync
   automatically; manual extraction is point-in-time.

   ### Defunct: cwRsync

   The free cwRsync builds at `https://itefix.net/cwrsync` have
   been discontinued. AllWell still probes the standard cwRsync
   install paths for backwards compatibility with existing setups,
   but new installs should use MSYS2.

   ---

   AllWell auto-detects rsync at any of the install paths above
   (MSYS2's `C:\msys64\usr\bin\`, Git's `usr\bin\`, cwRsync, Cygwin,
   anywhere on PATH). If none match, the bootstrap aborts with an
   actionable error pointing back here — there's no silent
   `[WinError 2] The system cannot find the file specified`.
4. **AllWell's Python dependencies** (only if you're running the GUI or
   driving the runner directly from your Windows machine — not needed
   if you only use `scripts\bootstrap_remote.ps1`).

   For the GUI install, follow the full
   [Windows (from source)](README_WellViewer.md#windows-from-source)
   section of the main README — it uses Miniforge + mamba so the
   `setuptools<70` / `pkg_resources` and MSVC-runtime constraints
   are handled correctly.

   If you only need a minimal install for `scripts\allwell_remote.py`
   (no GUI, no PySide6), a system-Python pip install is enough:
   ```powershell
   python -m pip install --no-build-isolation -r _Docs\requirements-remote.txt
   ```
   Use Python 3.10 or 3.11 — TensorFlow 2.13.0 has no Windows wheel
   for 3.12+.
5. **An SSH key configured for the remote.** Standard Windows SSH config
   lives at `%USERPROFILE%\.ssh\config`. Add the alias you want to use:
   ```
   Host bigbox
       HostName bigbox.example.edu
       User zsinger
       IdentityFile ~/.ssh/id_ed25519
   ```
   Test it before continuing:
   ```powershell
   ssh bigbox echo ok
   ```

## Bootstrap a host

**Recommended (v5.1.0+, GUI):** Launch AllWell, open the **Analyze**
tab, click **Manage…** next to the "Run on:" dropdown. Click **Add…**,
fill in the SSH alias, save. Then select the new entry in the list and
click **Bootstrap…** — a sub-dialog streams the bootstrap output live
so you can watch the conda/pip install progress through the
10–30 minute first-run pip install.

**CLI alternative:** From PowerShell, in the AllWell repo root:

```powershell
.\scripts\bootstrap_remote.ps1 bigbox
```

This runs `scripts\_bootstrap_lib.py` (the same module the bash wrapper
calls on Linux/macOS and the same module the GUI Bootstrap… button
invokes) and produces an identical result: miniforge + conda env
installed under `$HOME` on the remote, four required files synced,
bandwidth measured, `remotes.toml` written.

Re-running is a fast no-op. Pass `-Force` to wipe the remote miniforge
+ env and start clean.

## Where the config lives on Windows

On Windows the framework prefers `%APPDATA%\allwell\remotes.toml`
(typically `C:\Users\<you>\AppData\Roaming\allwell\remotes.toml`) for
new installs. If you already have `~/.config/allwell/remotes.toml`
(from running the bash wrapper under Git Bash, say) it keeps working —
the loader checks both.

You can override with the `ALLWELL_REMOTES` environment variable for
ad-hoc testing.

## Running the pipeline

From PowerShell:

```powershell
python scripts\allwell_remote.py --remote bigbox `
    --input_dir C:\Users\me\plate `
    --output_dir C:\Users\me\out
```

Windows paths like `C:\Users\me\plate` are translated to the form
rsync expects (`/c/Users/me/plate`) automatically — you pass the path
the Windows way.

Or use the GUI: open AllWell, go to **Analyze**, pick `bigbox` from the
**Run on:** dropdown, hit **Run Pipeline**.

## Troubleshooting

- **`ssh: command not found`** — the framework didn't find Git for
  Windows. Reinstall it; the default install path is
  `C:\Program Files\Git\`.
- **`rsync.exe is not installed on this Windows machine ...`** or
  **`[WinError 2] The system cannot find the file specified`** during
  bootstrap sync — Git for Windows does NOT ship rsync. Install it
  via MSYS2 + `pacman -Sy rsync openssh`, then add `C:\msys64\usr\bin\`
  to PATH (full instructions under prerequisite #3 above, including
  the pacman SSL-bypass workaround if your machine's root-CA store
  is broken).
- **`rsync: connection unexpectedly closed (0 bytes received so far)`
  / `code 12` / `dup() in/out/err failed`** — rsync.exe and ssh.exe
  are loading DIFFERENT MSYS / Cygwin runtimes. Cygwin's
  fork-emulation duplicates the parent's handle table into the child,
  then fails when the child loads an incompatible `msys-2.0.dll`.
  The framework picks an ssh.exe that's co-located with rsync.exe to
  avoid this, but only if you installed both — make sure you ran
  `pacman -Sy rsync openssh` (not just `rsync`) under MSYS2, so both
  binaries live in `C:\msys64\usr\bin\` together.
- **`UNC paths aren't supported by rsync on Windows`** — your
  `--input_dir` points at a UNC share like `\\fileserver\plates`.
  Map the share to a drive letter first (`net use Z: \\fileserver\plates`)
  and pass `Z:\plates`.
- **Slow bandwidth probe** — the 50 MB upload includes SSH handshake +
  cipher negotiation, which is per-connection overhead. On Windows
  the framework runs without ControlMaster (Cygwin's Unix-socket
  emulation is unreliable on Windows), so every ssh call pays a
  fresh handshake — a few hundred ms each. Total bootstrap overhead
  is still bounded by the conda/pip installs, not by this.
- **WSL2 alternative** — if you have Ubuntu installed under WSL2, you
  can run the bash wrapper from inside WSL2 instead. From AllWell's
  point of view that's just a Linux client.
