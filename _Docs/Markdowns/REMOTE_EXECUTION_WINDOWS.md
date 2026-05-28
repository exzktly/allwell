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
   Pick any one of:
   - **Easiest:** download a recent `rsync.exe` + `msys-2.0.dll` from
     the MSYS2 package mirror
     (<https://repo.msys2.org/msys/x86_64/>) and copy both into
     `C:\Program Files\Git\usr\bin\` alongside `ssh.exe`.
   - **cwRsync** (paid + free editions): <https://itefix.net/cwrsync>.
     Install to the default location and AllWell finds it.
   - **MSYS2**: install MSYS2 (<https://www.msys2.org/>), open an
     MSYS2 shell, run `pacman -S rsync`. AllWell auto-detects
     `C:\msys64\usr\bin\rsync.exe`.

   AllWell probes all three locations plus `rsync` on PATH. If none
   match, the bootstrap aborts with an actionable error explaining
   the install options above — there's no silent
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
  via one of the three options under prerequisite #3 above (drop
  rsync.exe + msys-2.0.dll into Git's `usr\bin\`, install cwRsync,
  or install MSYS2). The bootstrap auto-detects rsync at any of
  those locations.
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
