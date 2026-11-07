"""Validate an uploaded Office brand container without executing its content."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import PurePath
from typing import Any
from xml.etree import ElementTree

from forma.core.errors import ValidationFailed


def inspect_template(name: str, data: bytes, attachment_id: str) -> dict[str, Any]:
    fmt = PurePath(name).suffix.lower().lstrip(".")
    if fmt not in {"docx", "pptx"} or len(data) > 20_000_000:
        raise ValidationFailed("Use a DOCX or PPTX company template up to 20 MB")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 80_000_000 or len(archive.infolist()) > 2000:
                raise ValidationFailed("Company template is too large when unpacked")
            names = archive.namelist()
            if any("vbaproject" in n.lower() or "/embeddings/" in n.lower() for n in names):
                raise ValidationFailed("Remove macros and embedded documents from the template")
            for n in names:
                if n.endswith((".xml", ".rels")) and any(
                    marker in archive.read(n).upper() for marker in (b"<!DOCTYPE", b"<!ENTITY")
                ):
                    raise ValidationFailed("XML declarations are not supported in company templates")
                if n.endswith(".rels"):
                    tree = ElementTree.fromstring(archive.read(n))
                    if any(node.attrib.get("TargetMode") == "External" for node in tree):
                        raise ValidationFailed("Remove external links from the company template")
            required = "word/document.xml" if fmt == "docx" else "ppt/presentation.xml"
            if required not in names:
                raise ValidationFailed("Invalid Office template")
            if fmt == "pptx":
                tree = ElementTree.fromstring(archive.read(required))
                size = tree.find("{http://schemas.openxmlformats.org/presentationml/2006/main}sldSz")
                if size is None or abs(int(size.attrib["cx"]) / int(size.attrib["cy"]) - 16 / 9) > 0.02:
                    raise ValidationFailed("Use a widescreen 16:9 PowerPoint template")
    except (zipfile.BadZipFile, ValueError, KeyError, ElementTree.ParseError, ZeroDivisionError) as exc:
        raise ValidationFailed("Cannot read the company template") from exc
    return {
        "attachment_id": attachment_id,
        "name": name,
        "format": fmt,
        "sha256": hashlib.sha256(data).hexdigest(),
        "scope": "master/background artwork for PPTX; styles/header/footer for DOCX. "
        "Original body content is removed; review all rendered pages.",
    }
