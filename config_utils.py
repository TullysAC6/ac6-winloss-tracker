import json
import os
import re
import threading
import time
from pathlib import Path

from app_paths import data_dir

ROOT = data_dir()
CONFIG_PATH = ROOT / "config.json"

CONFIG_VERSION = 17

_lock = threading.Lock()
_last_good = None
_health = {"status": "starting", "error": None, "last_error_at": None}
_last_error_signature = None

DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "port": 8765,
    "stats_enabled": True,
    "result_detector_enabled": True,
}


def _bounded(name, value, lo, hi, cast):
    if type(value) is bool:
        raise ValueError(f"{name}: boolean is not a valid numeric value")
    try:
        value = cast(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name}: invalid value") from e
    if not lo <= value <= hi:
        raise ValueError(f"{name}: must be {lo}..{hi}")
    return value


def _migrate_known_config(raw):
    if not isinstance(raw, dict):
        raise ValueError("config root must be a JSON object")
    version = raw.get("config_version")
    if version == CONFIG_VERSION:
        return dict(raw), None
    if version is None:
        return dict(DEFAULT_CONFIG), None
    if type(version) is not int:
        raise ValueError("config_version: must be an integer")
    if version not in (12, 13, 14, 15, 16):
        raise ValueError(f"config_version: unsupported version {version}")

    migrated = {}
    for key in DEFAULT_CONFIG:
        if key != "config_version" and key in raw:
            migrated[key] = raw[key]
    migrated["config_version"] = CONFIG_VERSION
    return migrated, version


def _write_migrated_config(raw, from_version):
    backup = CONFIG_PATH.with_name(f"config.json.v{from_version}.bak")
    tmp = CONFIG_PATH.with_name("config.json.tmp")
    if not backup.exists() and CONFIG_PATH.exists():
        backup.write_bytes(CONFIG_PATH.read_bytes())
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONFIG_PATH)


def validate_config(raw):
    raw, _ = _migrate_known_config(raw)
    unknown = set(raw) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError("unknown config key(s): " + ", ".join(sorted(unknown)))
    c = dict(DEFAULT_CONFIG)
    c.update(raw)
    if type(c["config_version"]) is not int or c["config_version"] != CONFIG_VERSION:
        raise ValueError(f"config_version: expected {CONFIG_VERSION}, got {c['config_version']}")
    c["port"] = _bounded("port", c["port"], 1024, 65535, int)
    for k in ("stats_enabled", "result_detector_enabled"):
        if type(c[k]) is not bool:
            raise ValueError(f"{k}: must be true or false")
    return c


def _set_health(status, error=None):
    global _last_error_signature
    now = time.time()
    signature = (status, error)
    with _lock:
        _health["status"] = status
        _health["error"] = error
        _health["last_error_at"] = now if error else None
    if error and signature != _last_error_signature:
        print(f"[config] WARNING: {error}; using last-good config if available")
    _last_error_signature = signature if error else None


def get_config_health():
    with _lock:
        return dict(_health)


def load_config(use_last_good=True):
    global _last_good
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        migrated_raw, migrated_from = _migrate_known_config(raw)
        c = validate_config(migrated_raw)
        if migrated_from is not None:
            _write_migrated_config(migrated_raw, migrated_from)
            print(f"[config] migrated config version {migrated_from} -> {CONFIG_VERSION}")
    except (OSError, json.JSONDecodeError, ValueError) as e:
        _set_health("degraded", str(e))
        if use_last_good:
            with _lock:
                if _last_good is not None:
                    return dict(_last_good)
        raise
    with _lock:
        _last_good = dict(c)
    _set_health("active", None)
    return c
