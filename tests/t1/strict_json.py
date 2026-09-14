"""Strict JSON for T1 metadata.

Python's ``json`` module silently keeps the last of two duplicate keys and
accepts ``NaN`` and ``Infinity``. Either would let a fixture record say two
different things, or carry a number no assertion can compare, so both fail here.
"""
from __future__ import annotations

import json
import math

MAX_METADATA_BYTES = 256 * 1024


class MetadataError(ValueError):
    """A metadata file cannot be trusted; the T1 run must fail closed."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MetadataError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(name):
    raise MetadataError(f"non-finite JSON number is not allowed: {name}")


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise MetadataError(f"non-finite JSON number is not allowed: {text}")
    return value


def loads_strict(data: bytes, source: str = "metadata"):
    if not isinstance(data, (bytes, bytearray)):
        raise MetadataError(f"{source}: metadata must be read as bytes")
    if len(data) > MAX_METADATA_BYTES:
        raise MetadataError(f"{source}: metadata exceeds {MAX_METADATA_BYTES} bytes")
    if data.startswith(b"\xef\xbb\xbf"):
        raise MetadataError(f"{source}: a UTF-8 byte order mark is not allowed")
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError as error:
        raise MetadataError(f"{source}: metadata is not UTF-8: {error}") from None
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except MetadataError as error:
        raise MetadataError(f"{source}: {error}") from None
    except (json.JSONDecodeError, RecursionError) as error:
        raise MetadataError(f"{source}: malformed JSON: {error}") from None
