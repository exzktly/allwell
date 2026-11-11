#!/bin/bash
# Install the system libraries Qt needs (libEGL and friends) on a CI runner or a
# cloud session, without depending on Ubuntu's own servers being up.
#
# PySide6 needs libEGL.so.1 even under the offscreen QPA, and the GitHub runner
# image does not ship it, so every pytest job installs it with apt before a line
# of Python runs. The runner's apt sources point at azure.archive.ubuntu.com,
# archive.ubuntu.com and security.ubuntu.com only. On 2026-11-11 all three were
# down (DNS failures on the runner, HTTP 503 from elsewhere) for well over half
# an hour, and every pytest job on every open PR failed in this step: about five
# minutes of timeouts, then exit 100 fetching libegl1 and libegl-mesa0.
#
# So the packages are fetched through a mirror list of this script's own: three
# independent full Ubuntu mirrors, and not Ubuntu's own archive. apt's mirror
# method does not try the list in order: it spreads requests across every entry
# of equal priority, and retries a failed file on another. With
# archive.ubuntu.com listed "last" during the outage, apt still sent it a share
# of the requests, each of which waited out its timeout and retries before
# moving on; the install step took eight minutes on the runner and left the
# fast lane too little of its fifteen-minute cap. A dead mirror in the list
# costs time on every run, so Ubuntu's archive is left out, and the timeout is
# kept short so a mirror that goes down later costs seconds, not minutes.
# The mirrors are untrusted transport
# only: apt still verifies every index against Ubuntu's archive signing key
# (/usr/share/keyrings/ubuntu-archive-keyring.gpg), exactly as it does for the
# runner's own sources, so a mirror can make the install fail but cannot change
# what is installed.
#
# The runner's own source list is not used at all (Dir::Etc::SourceList and
# SourceParts are pointed at this script's files). That also keeps the
# third-party repositories the image ships out of the update, one of which
# (a stale google-chrome index) has failed this step on its own before.
#
# Usage: scripts/install_qt_system_libs.sh [extra-package ...]
#   Extra packages (the slow lane's fonts) are installed in the same apt call,
#   from main or universe; with any given, the libEGL presence check is skipped.
#   Does nothing when libEGL.so.1 is already installed, unless
#   ALLWELL_FORCE_QT_LIBS_INSTALL is set (which is how it is tested on a machine
#   that already has the libraries).
#   ALLWELL_APT_MIRRORS overrides the mirror list (whitespace-separated URLs).
set -euo pipefail

PACKAGES=(libegl1 libgl1 libxkbcommon0 libdbus-1-3 "$@")

if [ "$#" -eq 0 ] && [ -z "${ALLWELL_FORCE_QT_LIBS_INSTALL:-}" ] && ldconfig -p 2>/dev/null | grep -q "libEGL.so.1"; then
  echo "Qt system libraries already present"
  exit 0
fi

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

# shellcheck disable=SC1091
. /etc/os-release
CODENAME="${VERSION_CODENAME:?/etc/os-release names no VERSION_CODENAME}"
KEYRING=/usr/share/keyrings/ubuntu-archive-keyring.gpg
[ -r "$KEYRING" ] || { echo "::error::$KEYRING is missing; cannot verify an Ubuntu mirror" >&2; exit 1; }

MIRRORS="${ALLWELL_APT_MIRRORS:-https://mirrors.edge.kernel.org/ubuntu https://mirrors.mit.edu/ubuntu https://ftp.halifax.rwth-aachen.de/ubuntu}"

WORK="$(mktemp -d)"
chmod 755 "$WORK"   # apt downloads as the unprivileged _apt user, which must reach the lists dir
# apt runs under sudo on a CI runner, so the lists it writes here are root's:
# the cleanup needs the same sudo, and a cleanup that cannot finish must never
# turn a successful install into a failed step (exit status is kept as it was).
trap 'status=$?; $SUDO rm -rf "$WORK" || echo "::warning::could not remove $WORK" >&2; exit $status' EXIT
mkdir -p "$WORK/lists/partial" "$WORK/parts"
tr ' ' '\n' <<<"$MIRRORS" | sed '/^$/d' > "$WORK/mirrors.txt"
# Every pocket the packages can come from: the release, its updates and its
# security fixes. The Qt libraries are all in main. universe is read only when
# extra packages are asked for (the slow lane's fonts live there), because its
# index is about 19 MB a pocket against main's 2 MB and nothing else needs it.
COMPONENTS="main"
[ "$#" -gt 0 ] && COMPONENTS="main universe"
: > "$WORK/sources.list"
for suite in "$CODENAME" "$CODENAME-updates" "$CODENAME-security"; do
  echo "deb [signed-by=$KEYRING] mirror+file:$WORK/mirrors.txt $suite $COMPONENTS" >> "$WORK/sources.list"
done

APT_OPTS=(
  -o "Dir::Etc::SourceList=$WORK/sources.list"
  -o "Dir::Etc::SourceParts=$WORK/parts"
  -o "Dir::State::Lists=$WORK/lists"
  -o "Acquire::Retries=2"
  -o "Acquire::http::Timeout=10"
  -o "Acquire::https::Timeout=10"
)

echo "Fetching ${PACKAGES[*]} for $CODENAME via: $(tr '\n' ' ' < "$WORK/mirrors.txt")"
$SUDO apt-get "${APT_OPTS[@]}" update
DEBIAN_FRONTEND=noninteractive $SUDO apt-get "${APT_OPTS[@]}" install -y "${PACKAGES[@]}"
ldconfig -p | grep -q "libEGL.so.1" || { echo "::error::libEGL.so.1 is still missing after the install" >&2; exit 1; }
echo "Qt system libraries installed"
