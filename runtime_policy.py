from __future__ import annotations

import json
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path


POLICY_PATH = Path(__file__).resolve().with_name("runtime-policy.json")


def _load_policy(path: Path = POLICY_PATH) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if type(raw.get("policy_version")) is not int or raw["policy_version"] < 1:
        raise ValueError("runtime policy version is invalid")
    for role in ("preferred", "fallback"):
        entry = raw.get(role)
        if not isinstance(entry, dict):
            raise ValueError(f"runtime policy {role} entry is missing")
        for field in ("major", "minor", "minimum_patch"):
            if type(entry.get(field)) is not int or entry[field] < 0:
                raise ValueError(f"runtime policy {role}.{field} is invalid")
    if raw.get("allow_prerelease") is not False:
        raise ValueError("Stable runtime policy must reject pre-release builds")
    if raw.get("allow_free_threaded") is not False:
        raise ValueError("Stable runtime policy must reject free-threaded builds")
    return raw


POLICY = _load_policy()
POLICY_VERSION = POLICY["policy_version"]


@dataclass(frozen=True)
class RuntimeStatus:
    supported: bool
    role: str
    version: str
    releaselevel: str
    free_threaded: bool | None
    reason: str
    policy_version: int = POLICY_VERSION


class UnsupportedRuntimeError(RuntimeError):
    def __init__(self, status: RuntimeStatus):
        self.status = status
        super().__init__(unsupported_runtime_message(status))


def _normalize_gil_disabled(value) -> bool | None:
    if value is None:
        # Builds predating the free-threaded option do not define this key and
        # are standard-GIL builds. Supported 3.13/3.14 builds normally expose 0.
        return False
    if value in (0, False, "0", "false", "False", ""):
        return False
    if value in (1, True, "1", "true", "True"):
        return True
    return None


def evaluate_runtime(
    version: tuple[int, int, int],
    *,
    releaselevel: str = "final",
    gil_disabled=0,
) -> RuntimeStatus:
    major, minor, patch = (int(part) for part in version)
    version_text = f"{major}.{minor}.{patch}"
    free_threaded = _normalize_gil_disabled(gil_disabled)
    if releaselevel != "final":
        return RuntimeStatus(False, "unsupported", version_text, releaselevel, free_threaded, "pre-release")
    if free_threaded is None:
        return RuntimeStatus(False, "unsupported", version_text, releaselevel, None, "GIL build status unknown")
    if free_threaded:
        return RuntimeStatus(False, "unsupported", version_text, releaselevel, True, "free-threaded build")
    for role in ("preferred", "fallback"):
        entry = POLICY[role]
        if (major, minor) == (entry["major"], entry["minor"]):
            if patch >= entry["minimum_patch"]:
                return RuntimeStatus(True, role, version_text, releaselevel, False, "supported")
            return RuntimeStatus(False, "unsupported", version_text, releaselevel, False, "below minimum patch")
    return RuntimeStatus(False, "unsupported", version_text, releaselevel, False, "unapproved Python series")


def current_runtime_status() -> RuntimeStatus:
    info = sys.version_info
    return evaluate_runtime(
        (info.major, info.minor, info.micro),
        releaselevel=info.releaselevel,
        gil_disabled=sysconfig.get_config_var("Py_GIL_DISABLED"),
    )


def supported_runtime_lines() -> tuple[str, str]:
    preferred = POLICY["preferred"]
    fallback = POLICY["fallback"]
    return (
        f"Python {preferred['major']}.{preferred['minor']}.{preferred['minimum_patch']}+ "
        f"({preferred['major']}.{preferred['minor']} series)",
        f"Python {fallback['major']}.{fallback['minor']}.{fallback['minimum_patch']}+ "
        f"({fallback['major']}.{fallback['minor']} series)",
    )


def unsupported_runtime_message(status: RuntimeStatus | None = None) -> str:
    status = current_runtime_status() if status is None else status
    preferred, fallback = supported_runtime_lines()
    return (
        "[ENV-PYTHON-UNSUPPORTED] このPythonはStable v1.0.0の検証済みRuntimeではありません。\n"
        f"現在: Python {status.version} ({status.reason})\n"
        f"対応: {preferred}\n"
        f"代替: {fallback}\n"
        "Stable installerをもう一度実行してください。"
    )


def require_supported_runtime() -> RuntimeStatus:
    status = current_runtime_status()
    if not status.supported:
        raise UnsupportedRuntimeError(status)
    return status
