"""Additive user display preferences, kept out of the frozen config.json.

config.json is validated strictly by every build, and a build refuses to start
on a key it does not know.  A setting added to config.json therefore makes the
immediately previous build unstartable once the user saves it.  New additive
settings live here instead: a build older than this file never reads it, and a
build that does read it accepts a file one version newer (below), so a
one-version rollback needs no edit.

Rules (one-generation forward compatibility, still strict):

* The file is optional.  A missing file means every default, and nothing is
  written until a value differs from its default.
* ``preferences_version`` is required.  At or below this build's version every
  key must be known and correctly typed.
* A file written by the build exactly one version newer is accepted: its known
  keys are still type-checked, and its extra keys, which must be well-formed
  names with JSON scalar values, are preserved on save and never interpreted.
  This is what lets that newer build roll back to this one.
* Anything else is invalid: malformed JSON, a wrong type, an unknown key at
  this version, a file more than one version newer.  An invalid file is never
  used and never overwritten; callers fall back to defaults.

To add a setting: add it to DEFAULTS, bump PREFERENCES_VERSION, and never add it
to config.json.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

import config_utils

PREFERENCES_VERSION = 2
VERSION_KEY = "preferences_version"
DEFAULTS: dict[str, Any] = {
    # UI-1A (version 1): persistent Player streak-status wording (アツい … RUSH継続中).
    "player_streak_status_enabled": True,
    # UI-1B (version 2): 最高連勝 line of the OBS Broadcast Overlay.  Broadcast-only;
    # ON matches what every earlier build always showed.
    "broadcast_show_best_streak": True,
}
MAX_BYTES = 64 * 1024
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_lock = threading.Lock()
_cache: tuple[Any, dict[str, Any] | str] | None = None  # values, or an error message
_reported_error: str | None = None


def preferences_path() -> Path:
    """Beside config.json, so tests that redirect CONFIG_PATH redirect this too."""
    return config_utils.CONFIG_PATH.with_name("preferences.json")


def validate(raw: Any) -> dict[str, Any]:
    """Return the full effective mapping; raise ValueError when invalid."""
    if not isinstance(raw, dict):
        raise ValueError("preferences root must be a JSON object")
    version = raw.get(VERSION_KEY)
    if type(version) is not int or version < 1:
        raise ValueError(f"{VERSION_KEY}: must be a positive integer")
    if version > PREFERENCES_VERSION + 1:
        raise ValueError(f"{VERSION_KEY} {version} is more than one version newer than "
                         f"{PREFERENCES_VERSION}; rolling back that far is not supported")
    values = dict(DEFAULTS)
    for key, value in raw.items():
        if key == VERSION_KEY:
            continue
        if key in DEFAULTS:
            if type(value) is not type(DEFAULTS[key]):
                raise ValueError(f"{key}: must be {type(DEFAULTS[key]).__name__}")
            values[key] = value
        elif version <= PREFERENCES_VERSION:
            raise ValueError(f"unknown preference: {key!r}")
        elif not _NAME.fullmatch(key) or not _is_scalar(value):
            raise ValueError(f"malformed preference from a newer version: {key!r}")
    return values


def _is_scalar(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, (bool, int, str, type(None)))


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate preference: {key!r}")
        result[key] = value
    return result


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _read_raw(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(data) > MAX_BYTES:
        raise ValueError("preferences file is too large")
    try:
        raw = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"preferences file is not valid JSON: {type(error).__name__}: {error}") from error
    validate(raw)
    return raw


def load() -> dict[str, Any]:
    """Validated effective preferences; raises ValueError / OSError when invalid."""
    global _cache
    path = preferences_path()
    signature = _signature(path)
    with _lock:
        if _cache is not None and _cache[0] == (path, signature):
            if isinstance(_cache[1], str):  # the same invalid file: do not re-read it
                raise ValueError(_cache[1])
            return dict(_cache[1])
    try:
        raw = _read_raw(path)
        values = dict(DEFAULTS) if raw is None else validate(raw)
    except ValueError as error:
        with _lock:
            _cache = ((path, signature), str(error))
        raise
    with _lock:
        _cache = ((path, signature), values)
    return dict(values)


def effective(key: str, last: Any = None) -> Any:
    """For renderers: the valid value, else ``last`` (or the default); reports once."""
    global _reported_error
    try:
        value = load()[key]
        _reported_error = None
        return value
    except (OSError, ValueError) as error:
        message = f"{type(error).__name__}: {error}"
        if message != _reported_error:
            try:  # a log line must never stop the render loop
                print(f"[preferences] WARNING: {message!a}; keeping the previous display setting")
            except Exception:
                pass
            _reported_error = message
        return DEFAULTS[key] if last is None else last


def save(values: dict[str, Any]) -> bool:
    """Atomically merge known values; returns False when nothing had to change.

    Refuses to overwrite an invalid file.  Keys and the version marker written
    by a newer build are preserved so rolling forward again keeps them.
    """
    global _cache
    unknown = set(values) - set(DEFAULTS)
    if unknown:
        raise ValueError("unknown preference(s): " + ", ".join(sorted(unknown)))
    for key, value in values.items():
        if type(value) is not type(DEFAULTS[key]):
            raise ValueError(f"{key}: must be {type(DEFAULTS[key]).__name__}")
    path = preferences_path()
    original = path.read_bytes() if path.exists() else None
    raw = _read_raw(path) or {VERSION_KEY: PREFERENCES_VERSION}
    current = validate(raw)
    if all(current[key] == value for key, value in values.items()):
        return False
    raw = dict(raw)
    raw.update(values)
    raw[VERSION_KEY] = max(int(raw[VERSION_KEY]), PREFERENCES_VERSION)
    validate(raw)
    descriptor, name = tempfile.mkstemp(prefix=".preferences-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(raw, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        now = path.read_bytes() if path.exists() else None
        if now != original:
            raise OSError("保存中に設定が変更されました。設定を開き直してください。")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    with _lock:
        _cache = None
    return True
