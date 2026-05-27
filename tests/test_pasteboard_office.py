"""v5.2.30 pasteboard tests — Microsoft Office on macOS vector paste.

These pin the dual-declaration of PDF (modern UTI + legacy NSPasteboard
name) and the TIFF-then-PNG raster fallback. They use only the
``well_viewer.features.export.style`` helper, NOT matplotlib, so they
run in CI environments without matplotlib installed.
"""

from __future__ import annotations

import io
import sys

import pytest

# The export-style module imports PySide6 + matplotlib at module
# scope; skip the whole file when either isn't installed in the test
# env (some headless / minimal CI shapes).
pytest.importorskip("PySide6.QtWidgets")


def _build_fake_pasteboard():
    """A self-contained NSPasteboard / NSData double that records every
    type declared and every payload written."""
    declared_types: list[list[str]] = []
    set_calls: list[tuple[str, bytes]] = []

    class _FakeNSData:
        def __init__(self, data: bytes):
            self._data = data

        @classmethod
        def dataWithBytes_length_(cls, data, _length):
            return cls(bytes(data))

    class _FakePasteboard:
        def clearContents(self):
            pass

        def declareTypes_owner_(self, types, _owner):
            declared_types.append(list(types))

        def setData_forType_(self, ns_data, type_name):
            set_calls.append((type_name, ns_data._data))
            return True

    class _FakeNSPasteboard:
        @classmethod
        def generalPasteboard(cls):
            return _FakePasteboard()

    return _FakeNSPasteboard, _FakeNSData, declared_types, set_calls


def _patch_macos_clipboard(monkeypatch):
    """Pretend the test environment is macOS with pyobjc available,
    and inject the fake pasteboard. Returns the recording structures."""
    from well_viewer.features.export import style as export_style

    pb_cls, data_cls, declared, calls = _build_fake_pasteboard()
    monkeypatch.setattr(export_style.sys, "platform", "darwin")
    monkeypatch.setitem(
        sys.modules, "AppKit", type("appkit_stub", (), {"NSPasteboard": pb_cls}),
    )
    monkeypatch.setitem(
        sys.modules, "Foundation", type("fnd_stub", (), {"NSData": data_cls}),
    )
    return declared, calls


def test_macos_pasteboard_declares_pdf_under_both_legacy_and_uti(monkeypatch):
    """PowerPoint / Word / Excel on macOS don't read ``com.adobe.pdf``
    for vector paste — they key on the pre-UTI pasteboard name
    ``"Apple PDF pasteboard type"`` (lowercase 'p' in pasteboard).
    The native pasteboard writer must declare BOTH names with the
    legacy alias listed first so Office's vector-paste detector finds
    it before falling back to a raster type."""
    from well_viewer.features.export.style import _macos_pasteboard_write

    declared, calls = _patch_macos_clipboard(monkeypatch)
    ok = _macos_pasteboard_write(b"%PDF-1.4 fake", b"<svg/>", b"\x89PNG\r\n")
    assert ok is True
    types = declared[0]
    assert "Apple PDF pasteboard type" in types
    assert "com.adobe.pdf" in types
    assert types.index("Apple PDF pasteboard type") < types.index("com.adobe.pdf")
    # SVG still declared so Illustrator's vector-paste keeps working too.
    assert "public.svg-image" in types
    # PDF lands under BOTH names — Office grabs the first one it
    # recognises in declareTypes order.
    pdf_writes = [d for tn, d in calls
                  if tn in ("Apple PDF pasteboard type", "com.adobe.pdf")]
    assert len(pdf_writes) == 2
    assert all(d == b"%PDF-1.4 fake" for d in pdf_writes)


def test_macos_pasteboard_adds_tiff_when_pil_available(monkeypatch):
    """Office prefers TIFF over PNG for raster paste. When Pillow is
    importable the figure also lands on the pasteboard as TIFF; PNG
    stays as a fallback for apps that don't read TIFF."""
    from well_viewer.features.export.style import _macos_pasteboard_write
    PIL = pytest.importorskip("PIL")

    declared, _calls = _patch_macos_clipboard(monkeypatch)
    # Real PNG bytes so the TIFF conversion path can execute.
    buf = io.BytesIO()
    PIL.Image.new("RGB", (8, 8), (200, 100, 50)).save(buf, format="PNG")
    png = buf.getvalue()

    ok = _macos_pasteboard_write(b"%PDF-1.4 fake", b"<svg/>", png)
    assert ok is True
    types = declared[0]
    assert "public.tiff" in types
    assert "public.png" in types
    # TIFF before PNG so Office picks the better raster fallback.
    assert types.index("public.tiff") < types.index("public.png")


def test_macos_pasteboard_no_op_when_no_payload(monkeypatch):
    """Defensive: if every payload is empty, return False without
    touching the pasteboard at all."""
    from well_viewer.features.export.style import _macos_pasteboard_write
    _patch_macos_clipboard(monkeypatch)
    assert _macos_pasteboard_write(b"", b"", b"") is False
