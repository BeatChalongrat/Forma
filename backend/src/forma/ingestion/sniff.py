"""Content sniffing for uploads (ADR-013): the file *bytes* decide, never the extension alone."""

from __future__ import annotations

import io
import zipfile

from forma.core.errors import UnsupportedFile

_ZIP = b"PK\x03\x04"
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "utf-16", "cp874", "tis-620")


def sniff_kind(data: bytes, name: str) -> str:
    """Return ``excel`` or ``csv`` when the content matches the claimed extension; raise otherwise."""
    from pathlib import Path

    from forma.ingestion.readers import CSV_EXT, EXCEL_EXT

    ext = Path(name).suffix.lower()
    head = data[:8]
    if ext in EXCEL_EXT:
        if head.startswith(_ZIP):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    names = set(z.namelist())
            except zipfile.BadZipFile as e:
                raise UnsupportedFile(f"{name}: not a valid Excel workbook", details={"file": name}) from e
            if "[Content_Types].xml" not in names or not any(n.startswith("xl/") for n in names):
                raise UnsupportedFile(
                    f"{name}: zip container is not an Excel workbook", details={"file": name}
                )
            return "excel"
        if head.startswith(_OLE):
            return "excel"
        raise UnsupportedFile(
            f"{name}: content is not an Excel workbook (extension says {ext})", details={"file": name}
        )
    if ext in CSV_EXT:
        sample = data[:65536]
        if b"\x00" in sample and not sample.startswith((b"\xff\xfe", b"\xfe\xff")):
            raise UnsupportedFile(f"{name}: binary content in a text file", details={"file": name})
        if sample.startswith(_ZIP) or sample.startswith(_OLE):
            raise UnsupportedFile(f"{name}: this is a binary workbook, not a CSV", details={"file": name})
        for enc in _TEXT_ENCODINGS:
            try:
                sample.decode(enc)
                return "csv"
            except UnicodeDecodeError:
                continue
        raise UnsupportedFile(f"{name}: text is not decodable as CSV", details={"file": name})
    raise UnsupportedFile(f"Unsupported file type {ext!r}", details={"file": name})
