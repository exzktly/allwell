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
   `ssh.exe` and `rsync.exe` at
   `C:\Program Files\Git\usr\bin\`. AllWell detects them automatically;
   you do not need to add them to PATH.
3. **AllWell's Python dependencies** (only if you're running the GUI or
   driving the runner directly from your Windows machine — not needed
   if you only use `scripts\bootstrap_remote.ps1`):
   ```powershell
   python -m pip install -r _Docs\requirements.txt
   ```
4. **An SSH key configured for the remote.** Standard Windows SSH config
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

- **`ssh: command not found` or `rsync: command not found`** — the
  framework didn't find Git for Windows. Reinstall it; the default
  install path is `C:\Program Files\Git\`.
- **`UNC paths aren't supported by rsync on Windows`** — your
  `--input_dir` points at a UNC share like `\\fileserver\plates`.
  Map the share to a drive letter first (`net use Z: \\fileserver\plates`)
  and pass `Z:\plates`.
- **Slow bandwidth probe** — the 50 MB upload includes SSH handshake +
  cipher negotiation, which is per-connection overhead. Subsequent
  runs use ControlMaster for the same TCP connection and are much
  faster.
- **WSL2 alternative** — if you have Ubuntu installed under WSL2, you
  can run the bash wrapper from inside WSL2 instead. From AllWell's
  point of view that's just a Linux client.
