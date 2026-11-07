"""Every recursive delete the app performs on a remote host, through one function.

A user's remote home folder was emptied by an ``rm -rf`` whose operand list reached the host cut after a ``"$HOME"``
(Git for Windows' ``ssh.exe`` caps a command at ~8,186 characters). Commands are packed now, so a cut one runs nothing
(``remote_transport.pack_for_login_shell``); this module is the second half: whatever builds a path, nothing outside the
directory a delete is meant for can be removed.

Two checks, because each sees something the other cannot:

* **Here, on the strings** (:func:`refusal`): every target must be strictly inside the declared *root* as written
  (``~/.allwell-staging/...``), with no ``.`` or ``..`` component, and its last component a plain name or a fixed
  pattern (letters, digits, ``._-*``).
* **On the host, on what the strings resolve to** (:data:`AW_RM`, the shell function ``aw_rm``): the root and each
  target's parent are resolved with ``pwd -P`` (so ``~``, symlinks and ``..`` are what the host says they are); a
  target must resolve strictly inside the resolved root, must not be ``.``/``..``, must be the user's own (or a
  symlink, which is removed as a link), and is removed with ``rm -rf --one-file-system``. A root that resolves to
  ``/`` or to ``$HOME`` is refused outright, so no caller can make the home folder a deletable parent.

``aw_rm`` is POSIX ``sh`` (dash runs it as well as bash), so node-side scripts -- the batch driver, the reservation
sweep -- embed :data:`AW_RM` and call ``aw_rm`` themselves. ``tests/test_every_recursive_delete_goes_through_the_layer.py``
fails on any recursive delete elsewhere that is not a named exemption with its reason.

Qt-free.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from services.remote_transport import _remote_quote

#: The exit code ``aw_rm`` returns, and :func:`remote_delete` reports, for a refused target.
REFUSED_EXIT = 3

#: ``aw_rm <root> <target>...``. Removes each target that resolves strictly inside the resolved root; refuses (exit 3)
#: anything else, and a root that is ``/`` or ``$HOME`` itself. A target that does not exist (or a glob that matched
#: nothing), or any target under a root that does not exist, is a no-op, as for ``rm -rf``. POSIX sh.
AW_RM = r'''aw_rm() {
  _aw_root=$(cd -- "$1" 2>/dev/null && pwd -P) || return 0     # no root: nothing inside it to remove, as rm -rf
  shift
  _aw_home=$(cd -- "$HOME" 2>/dev/null && pwd -P)
  if [ "$_aw_root" = / ] || [ "$_aw_root" = "$_aw_home" ]; then
    echo "aw_rm: refused: the root $_aw_root is / or the home folder" >&2; return 3
  fi
  if ! rm -rf --one-file-system -- "$_aw_root/.aw_rm_probe_absent" 2>/dev/null; then
    echo "aw_rm: refused: this rm cannot be kept on one filesystem" >&2; return 3
  fi
  _aw_rc=0
  for _aw_t in "$@"; do
    while [ "${_aw_t%/}" != "$_aw_t" ] && [ "$_aw_t" != / ]; do _aw_t=${_aw_t%/}; done
    [ -e "$_aw_t" ] || [ -L "$_aw_t" ] || continue
    _aw_b=${_aw_t##*/}
    case "$_aw_b" in ""|.|..) echo "aw_rm: refused: $_aw_t" >&2; _aw_rc=3; continue;; esac
    case "$_aw_t" in */*) _aw_p=${_aw_t%/*}; [ -n "$_aw_p" ] || _aw_p=/;; *) _aw_p=.;; esac
    _aw_pd=$(cd -- "$_aw_p" 2>/dev/null && pwd -P) || { echo "aw_rm: refused: $_aw_t" >&2; _aw_rc=3; continue; }
    case "$_aw_pd/$_aw_b" in
      "$_aw_root"/?*) ;;
      *) echo "aw_rm: refused: $_aw_t is not inside $_aw_root" >&2; _aw_rc=3; continue;;
    esac
    if [ ! -L "$_aw_t" ] && [ ! -O "$_aw_t" ]; then
      echo "aw_rm: refused: $_aw_t is not yours" >&2; _aw_rc=3; continue
    fi
    rm -rf --one-file-system -- "$_aw_pd/$_aw_b" || _aw_rc=1
  done
  return $_aw_rc
}
'''

#: A target's last component: a plain name, or a fixed pattern of one (``*_A01.csv``). Nothing a shell would read as
#: anything but a filename or a ``*``.
_NAME = re.compile(r"^[A-Za-z0-9._*+-]+$")


def _norm(path: str) -> str:
    path = re.sub(r"/+", "/", str(path).strip())
    return path.rstrip("/") if path not in ("/", "") else path


def refusal(root: str, target: str) -> str:
    """Why *target* must not be deleted under *root*, or ``""``: it must be strictly inside *root* as written, with no
    ``.``/``..`` component and a plain (or ``*``-pattern) last component. *root* itself must not be ``/``, ``~`` or
    empty -- the host refuses those too, but nothing should ever ask."""
    r, t = _norm(root), _norm(target)
    if r in ("", "/", "~", "$HOME"):
        return f"refused to delete under {root!r}: not a directory the app owns"
    parts = t.split("/")
    if not t.startswith(r + "/") or "." in parts or ".." in parts or not _NAME.match(parts[-1]):
        return f"refused to delete {target!r}: not strictly inside {root!r}"
    return ""


@dataclass(frozen=True)
class Refused:
    """A delete :func:`remote_delete` would not send. ``returncode`` and ``stderr`` read like a failed command's."""
    stderr: str
    returncode: int = REFUSED_EXIT
    stdout: str = ""


def command(root: str, targets) -> str:
    """The shell that deletes *targets* under *root* through ``aw_rm``: the function, then one call. Each target's
    directory part is quoted (``~/`` expanded by the host); its last component is left bare so a ``*`` pattern
    expands -- which :func:`refusal` has limited to a plain name. Raises ``ValueError`` on a target :func:`refusal`
    refuses."""
    words = []
    for target in targets:
        why = refusal(root, target)
        if why:
            raise ValueError(why)
        t = _norm(target)
        head, name = t.rsplit("/", 1)
        words.append(f"{_remote_quote(head)}/{name}")
    return AW_RM + f"aw_rm {_remote_quote(_norm(root))} {' '.join(words)}\n"


def remote_delete(cache, root: str, targets, *, timeout_s: float | None = None):
    """Delete *targets* (each strictly inside *root*) on *cache*'s host, through ``aw_rm``. Returns the command's
    result, or a :class:`Refused` when a target fails :func:`refusal` -- in which case nothing is sent."""
    targets = list(targets)
    if not targets:
        return subprocess.CompletedProcess([], 0, "", "")
    try:
        script = command(root, targets)
    except ValueError as exc:
        return Refused(str(exc))
    kwargs = {} if timeout_s is None else {"timeout_s": timeout_s}
    return cache._ssh_script(script, check=False, **kwargs)


def exec_sh(root: str) -> list[str]:
    """``find … -exec`` words that delete what ``find`` found through ``aw_rm`` under *root*:
    ``["-exec", "sh", "-c", '<AW_RM> aw_rm "$0" "$@"', <root>, "{}", "+"]``."""
    return ["-exec", "sh", "-c", AW_RM + 'aw_rm "$0" "$@"', _norm(root), "{}", "+"]


__all__ = ["AW_RM", "REFUSED_EXIT", "Refused", "command", "exec_sh", "refusal", "remote_delete"]
