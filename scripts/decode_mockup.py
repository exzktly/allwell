#!/usr/bin/env python3
"""Decode the self-unpacking v2 mockup bundle into readable HTML.

The source file ``design/All-Well Redesign v2 _standalone_.html`` is a
~200-line HTML shell + three ``<script type="__bundler/*">`` data blocks:

* ``__bundler/manifest`` — JSON map ``{uuid: {mime, compressed, data}}`` where
  ``data`` is base64-encoded raw bytes, optionally gzip-compressed beforehand.
* ``__bundler/ext_resources`` — unused (``[]`` in the v2 bundle).
* ``__bundler/template`` — JSON-encoded HTML string with the asset UUIDs left
  in as placeholders; the loader substitutes blob URLs at render time.

The browser loader (lines 57-192 of the shell) atob → gzip-decompress → Blob
URL → ``template.split(uuid).join(blobUrl)`` → ``DOMParser.parseFromString``.
This script reproduces that pipeline in Python and writes the result to
``design/mockup-decoded.html``, with the UUID placeholders rewritten to
``./mockup-assets/<uuid>.<ext>`` so the decoded file renders standalone with
its fonts + lucide when opened from disk. Re-run any time the source changes.

Usage::

    python scripts/decode_mockup.py
    # or, with explicit paths:
    python scripts/decode_mockup.py --source design/in.html --out design/out.html
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import mimetypes
import re
import sys
from pathlib import Path

_BLOCK_RE = r'<script type="__bundler/{tag}">\s*(.*?)\s*</script>'

# MIME → preferred file extension for the per-asset write-out. mimetypes
# guesses .ksh for text/javascript on some platforms, so pin the common ones.
_MIME_EXT = {
    "text/javascript": ".js",
    "application/javascript": ".js",
    "font/woff2": ".woff2",
    "font/woff": ".woff",
    "image/svg+xml": ".svg",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "text/css": ".css",
}


def _read_block(src: str, tag: str) -> str:
    m = re.search(_BLOCK_RE.format(tag=re.escape(tag)), src, re.DOTALL)
    if m is None:
        raise SystemExit(f"bundle has no <script type='__bundler/{tag}'> block")
    return m.group(1)


def _ext_for(mime: str) -> str:
    if mime in _MIME_EXT:
        return _MIME_EXT[mime]
    guess = mimetypes.guess_extension(mime)
    return guess or ".bin"


def decode_bundle(source: Path, out_html: Path) -> dict:
    """Decode *source* and write the resulting HTML + assets next to *out_html*.

    Returns a summary dict with sizes and per-asset stats so the CLI can print
    a one-liner.
    """
    src = source.read_text(encoding="utf-8")

    manifest = json.loads(_read_block(src, "manifest"))
    template = json.loads(_read_block(src, "template"))  # JSON-encoded HTML

    assets_dir = out_html.parent / f"{out_html.stem}-assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    asset_stats = []
    for uuid, entry in manifest.items():
        raw = base64.b64decode(entry["data"])
        if entry.get("compressed"):
            raw = gzip.decompress(raw)
        ext = _ext_for(entry["mime"])
        asset_path = assets_dir / f"{uuid}{ext}"
        asset_path.write_bytes(raw)
        # Relative URL the decoded HTML will reference.
        rel = f"./{assets_dir.name}/{asset_path.name}"
        template = template.replace(uuid, rel)
        asset_stats.append({
            "uuid": uuid,
            "mime": entry["mime"],
            "compressed": bool(entry.get("compressed")),
            "b64": len(entry["data"]),
            "decoded": len(raw),
            "path": str(asset_path.relative_to(out_html.parent)),
        })

    # Drop SRI / crossorigin for the same reason the loader does — local font
    # files from a relative path would fail SRI checks against the original
    # CDN-pinned hashes.
    template = re.sub(r'\s+integrity="[^"]*"', "", template, flags=re.I)
    template = re.sub(r'\s+crossorigin="[^"]*"', "", template, flags=re.I)

    # Prepend a banner so anyone opening the decoded file knows it's generated.
    banner = (
        "<!--\n"
        "  AUTO-GENERATED from design/All-Well Redesign v2 _standalone_.html\n"
        "  by scripts/decode_mockup.py — do not edit by hand. Re-run the\n"
        "  decoder if the source bundle changes:\n"
        "      python scripts/decode_mockup.py\n"
        "-->\n"
    )
    if template.lstrip().lower().startswith("<!doctype"):
        # Keep DOCTYPE first so the browser doesn't fall into quirks mode.
        idx = template.lower().index("<!doctype")
        end = template.index(">", idx) + 1
        template = template[:end] + "\n" + banner + template[end:]
    else:
        template = banner + template

    out_html.write_text(template, encoding="utf-8")

    return {
        "source": str(source),
        "out": str(out_html),
        "assets_dir": str(assets_dir),
        "source_bytes": len(src),
        "decoded_bytes": len(template),
        "assets": asset_stats,
    }


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    default_src = repo_root / "design" / "All-Well Redesign v2 _standalone_.html"
    default_out = repo_root / "design" / "mockup-decoded.html"

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=Path, default=default_src,
                    help=f"path to encoded bundle (default: {default_src.relative_to(repo_root)})")
    ap.add_argument("--out", type=Path, default=default_out,
                    help=f"path to write decoded HTML (default: {default_out.relative_to(repo_root)})")
    ap.add_argument("--quiet", action="store_true", help="suppress per-asset summary")
    args = ap.parse_args(argv)

    if not args.source.is_file():
        print(f"error: {args.source} not found", file=sys.stderr)
        return 2

    summary = decode_bundle(args.source, args.out)

    if args.quiet:
        return 0

    print(f"decoded:  {summary['source']}  ({summary['source_bytes']:,} bytes)")
    print(f"  → html: {summary['out']}  ({summary['decoded_bytes']:,} bytes)")
    print(f"  → assets: {summary['assets_dir']}  ({len(summary['assets'])} files)")
    mime_counts: dict[str, int] = {}
    for a in summary["assets"]:
        mime_counts[a["mime"]] = mime_counts.get(a["mime"], 0) + 1
    for mime, n in sorted(mime_counts.items(), key=lambda kv: -kv[1]):
        print(f"     {mime:<25s} × {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
