"""The in-app help drawer renders the README, but a running app is already
installed — so ``_strip_install_help`` drops the install/build sections (and
their Contents-index entries) before rendering. This pins that contract."""
from __future__ import annotations

from allwell import _strip_install_help

_MD = """\
# All-Well

## Contents

1. [What you get](#what-you-get)
2. [Install](#install)
   - [macOS pre-built app](#macos-pre-built-app)
   - [Running from source](#running-from-source)
3. [Review mode](#review-mode)
4. [Building the macOS bundle](#building-the-macos-bundle)
5. [Troubleshooting](#troubleshooting)

## What you get
Overview.

## Install
### macOS pre-built app
Download it.
### Running from source
```
mamba create -n allwell ...
```

## Review mode
How to use the app.

## Building the macOS bundle
Build steps.

## Troubleshooting
Fixes.
"""


def test_strips_install_and_build_sections():
    out = _strip_install_help(_MD)
    # Install + Building sections and their bodies are gone.
    assert "## Install" not in out
    assert "### Running from source" not in out
    assert "mamba create" not in out
    assert "## Building the macOS bundle" not in out
    assert "Build steps." not in out
    # Usage + troubleshooting content is kept.
    assert "## Review mode" in out
    assert "How to use the app." in out
    assert "## Troubleshooting" in out


def test_prunes_their_contents_index_entries():
    out = _strip_install_help(_MD)
    toc = out.split("## What you get")[0]
    assert "install" not in toc            # #install, #macos-pre-built-app, …
    assert "macos-pre-built-app" not in toc
    assert "running-from-source" not in toc
    assert "building-the-macos-bundle" not in toc
    # Kept entries survive.
    assert "[What you get](#what-you-get)" in toc
    assert "[Review mode](#review-mode)" in toc
    assert "[Troubleshooting](#troubleshooting)" in toc
