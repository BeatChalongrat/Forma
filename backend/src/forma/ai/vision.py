"""Can this model read images? One small test picture settles it (ADR-049).

Provider catalogues do not say, and a gateway can serve anything, so FORMA asks the model what it sees. The
picture has a different colour on each side, chosen at random, so a model that cannot see it cannot pass by
guessing a usual answer.
"""

from __future__ import annotations

import re
import secrets
import struct
import time
import zlib
from typing import Any

from forma.ai.llm import ImagePart, LLMError, _parse_json

COLOURS: dict[str, tuple[int, int, int]] = {
    "red": (220, 38, 38),
    "green": (22, 163, 74),
    "blue": (37, 99, 235),
    "yellow": (250, 204, 21),
    "black": (0, 0, 0),
    "white": (255, 255, 255),
}
_SCHEMA = {
    "type": "object",
    "properties": {"left": {"type": "string"}, "right": {"type": "string"}},
    "required": ["left", "right"],
}
_NOT_SUPPORTED = re.compile(r"HTTP (400|404|415|422)\b")


def two_colour_png(left: str, right: str, width: int = 96, height: int = 48) -> bytes:
    row = b"\x00" + bytes(COLOURS[left]) * (width // 2) + bytes(COLOURS[right]) * (width - width // 2)

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data)
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


def probe(provider: Any, model: str, temperature: float = 0.0) -> dict[str, Any]:
    """``vision`` is True or False when the model answered or refused, None when it was unreachable."""
    left, right = secrets.SystemRandom().sample(sorted(COLOURS), 2)
    t0 = time.perf_counter()
    try:
        text, usage = provider.complete_json(
            system="You are a vision check. Answer with one colour word for each side of the image.",
            user=f'The image has two halves. Return {{"left": "", "right": ""}}, using: {sorted(COLOURS)}.',
            schema=_SCHEMA,
            model=model,
            temperature=temperature,
            images=[ImagePart("image/png", two_colour_png(left, right))],
        )
        data = _parse_json(text)
    except LLMError as error:
        rejected = bool(_NOT_SUPPORTED.search(str(error)))
        return {
            "vision": False if rejected else None,
            "model": model,
            "message": "the model does not accept images" if rejected else "the model could not be reached",
        }
    except Exception:  # any other failure says nothing about images
        return {"vision": None, "model": model, "message": "the model could not be reached"}
    seen = (str(data.get("left", "")).strip().lower(), str(data.get("right", "")).strip().lower())
    ok = seen == (left, right)
    return {
        "vision": ok,
        "model": model,
        "message": "reads images" if ok else "answered, but did not see the test image",
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": usage.cost_usd,
        },
    }
