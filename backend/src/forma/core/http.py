"""HTTP attachment headers must remain ASCII even when a filename is Thai or Japanese."""

from urllib.parse import quote


def attachment_header(filename: str) -> str:
    return "attachment; filename*=UTF-8''" + quote(filename, safe="")
