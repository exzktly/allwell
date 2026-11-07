"""How much of a long command line reaches a remote host, through the ssh this app uses -- sent the way the app sent
control commands before they were packed: as one argument after ``ssh <alias>``.

Read-only on the host: every probe is ``printf`` piped to ``wc`` / ``tail``; nothing is created or removed.

    python scripts/ssh_command_line_probe.py <host name from Manage Remotes>

Why it exists: a user's remote home folder was emptied after "could not clear the handshake files … rm: cannot remove
'<home>'". That clear was one ``rm -rf`` naming every batch's flag file, ~10 000-12 000 characters for a 32-batch run,
each operand ``"$HOME"/.allwell-staging/runs/<id>/<flag>``. A command cut right after one operand's ``"$HOME"`` is
``rm -rf … "$HOME"``. This probe shows whether, and where, this machine's ssh cuts or re-parses a long command.

Two probes:

1. **Length** -- ``printf %s AAAA… | wc -c`` for lengths from 2 000 to 33 000: the count the host saw, against the
   count sent. A shortfall is where the command line was cut.
2. **Shape** -- the old command's shape with ``printf '%s\\n'`` in place of ``rm -rf``: 170 operands written as the app
   wrote them. Prints how many arrived and the last one. A last operand that is the bare home folder is the deletion.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.remote_config import load_remotes, resolve_ssh_binary  # noqa: E402
from services.remote_transport import _remote_join  # noqa: E402

LENGTHS = (2000, 4000, 6000, 7000, 7500, 8000, 8100, 8150, 8190, 8200, 8300, 9000, 12000, 16000, 24000, 33000)


def _send(host, command: str) -> subprocess.CompletedProcess:
    argv = host.ssh_argv(allocate_tty=False) + [command]      # unpacked, as control commands were sent
    return subprocess.run(argv, capture_output=True, text=True, timeout=60)


def length_probe(host) -> None:
    print("Length probe: characters sent in the payload -> characters the host received")
    for n in LENGTHS:
        command = f"printf %s {'A' * n} | wc -c"
        try:
            out = _send(host, command)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"  {n:>6}: could not send ({exc})")
            continue
        got = (out.stdout or "").strip()
        verdict = "whole" if got == str(n) else "CUT OR CHANGED"
        shown = got if len(got) <= 12 else f"{got[:8]}... ({len(got):,} chars of output: the | wc -c never arrived)"
        print(f"  {n:>6} (command {len(command):,} chars): host saw {shown or '-'}  {verdict}  "
              f"exit {out.returncode}{'  ' + out.stderr.strip()[-160:] if out.stderr.strip() else ''}")


def shape_probe(host, operands: int = 170) -> None:
    run_dir = f"{host.remote_staging_dir}/runs/probe0123456789ab"
    names = [f"{run_dir}/.aw_batch_{i}_{k}" for i in range(1, operands // 5 + 1)
             for k in ("ready", "done", "reanchor", "skip", "staged")]
    command = _remote_join(["printf", "%s\\n", *names]) + " | awk 'END { print NR; print $0 }'"
    out = _send(host, command)
    lines = (out.stdout or "").strip().splitlines()
    print(f"Shape probe: {len(names)} operands, command {len(command):,} chars")
    print(f"  host received {lines[0] if lines else '-'} operand(s); the last: {lines[1] if len(lines) > 1 else '-'}")
    print(f"  exit {out.returncode}{'  ' + out.stderr.strip()[-200:] if out.stderr.strip() else ''}")
    if len(lines) > 1 and not lines[1].endswith("_staged"):
        print("  -> the command was CUT: the last operand is not the last one sent.")


def main(argv: list[str]) -> int:
    hosts = load_remotes()
    if len(argv) != 2 or argv[1] not in hosts:
        print(f"usage: python {Path(argv[0]).name} <host>   (hosts: {', '.join(hosts) or 'none configured'})")
        return 2
    host = hosts[argv[1]]
    print(f"ssh: {resolve_ssh_binary()}   host: {host.name} ({host.ssh_alias})   python: {sys.version.split()[0]} "
          f"on {sys.platform}")
    length_probe(host)
    shape_probe(host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
