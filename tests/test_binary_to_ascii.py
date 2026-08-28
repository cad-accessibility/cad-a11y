"""Unit tests for binary → ASCII STL conversion in src.converter.binary_to_ascii."""

from __future__ import annotations

import io
import struct
from types import SimpleNamespace

from src.converter.binary_to_ascii import execute

# Same minimal ASCII STL used by upload/session tests.
_MINIMAL_ASCII_STL = (
    b"solid test\n"
    b"  facet normal 0 0 1\n"
    b"    outer loop\n"
    b"      vertex 0 0 0\n"
    b"      vertex 1 0 0\n"
    b"      vertex 0 1 0\n"
    b"    endloop\n"
    b"  endfacet\n"
    b"endsolid test\n"
)


def make_minimal_binary_stl() -> bytes:
    """Build a valid one-triangle binary STL (80-byte header + count + 50-byte facet)."""
    header = b"\0" * 80
    count = struct.pack("<I", 1)
    triangle = struct.pack(
        "<12fH",
        0.0, 0.0, 1.0,  # normal
        0.0, 0.0, 0.0,  # v1
        1.0, 0.0, 0.0,  # v2
        0.0, 1.0, 0.0,  # v3
        0,              # attribute byte count
    )
    return header + count + triangle


def _as_upload(raw: bytes, filename: str = "part.stl") -> SimpleNamespace:
    """Minimal stand-in for Flask FileStorage: execute() only needs .stream."""
    return SimpleNamespace(stream=io.BytesIO(raw), filename=filename)


def test_binary_stl_converts_to_ascii_text():
    upload = _as_upload(make_minimal_binary_stl(), filename="bin.stl")
    result = execute(upload)

    assert isinstance(result, str), f"expected ASCII text str, got {type(result)}"
    lowered = result.lower()
    assert lowered.lstrip().startswith("solid"), "ASCII STL must start with 'solid'"
    assert "facet" in lowered
    assert "vertex" in lowered
    assert "endsolid" in lowered
    print("OK: binary STL converted to ASCII text")


def test_ascii_stl_is_returned_unchanged():
    upload = _as_upload(_MINIMAL_ASCII_STL, filename="ascii.stl")
    result = execute(upload)

    assert result is upload, "already-ASCII STL should return the same upload object"
    print("OK: ASCII STL left unchanged (no conversion)")
