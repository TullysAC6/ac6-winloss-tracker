"""Fixture-root confinement for metadata paths.

A metadata path is data, so it is checked as if hostile: it must name a regular
file under the fixture root through plain directories only. Symlinks and
Windows reparse points (junctions included) are refused component by component,
and the resolved path is compared with the resolved root as a final check.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

MAX_RELATIVE_PATH = 240
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_DRIVE = re.compile(r"^[A-Za-z]:")
_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"] + [f"COM{i}" for i in range(1, 10)] + [f"LPT{i}" for i in range(1, 10)])
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class FixturePathError(ValueError):
    """A metadata path does not safely name a fixture file."""


def validate_relative_path(relative):
    if not isinstance(relative, str) or not relative:
        raise FixturePathError("fixture path must be a non-empty string")
    if len(relative) > MAX_RELATIVE_PATH:
        raise FixturePathError("fixture path is too long")
    if "\x00" in relative:
        raise FixturePathError("fixture path contains NUL")
    if "\\" in relative:
        raise FixturePathError(f"fixture path must use forward slashes: {relative!r}")
    if relative.startswith("/"):
        raise FixturePathError(f"absolute or UNC fixture path: {relative!r}")
    if _DRIVE.match(relative):
        raise FixturePathError(f"drive-qualified fixture path: {relative!r}")
    parts = relative.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise FixturePathError(f"fixture path has an empty, '.' or '..' component: {relative!r}")
        if not _COMPONENT.fullmatch(part) or part.endswith("."):
            raise FixturePathError(f"fixture path component is not allowed: {part!r}")
        if part.split(".", 1)[0].upper() in _DEVICE_NAMES:
            raise FixturePathError(f"fixture path names a Windows device: {part!r}")
    return parts


def _is_link(path):
    info = os.lstat(path)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def resolve_fixture_file(root, relative):
    parts = validate_relative_path(relative)
    root = Path(root)
    if _is_link(root):
        raise FixturePathError("the fixture root itself must not be a link")
    root_real = os.path.realpath(root)
    current = root
    for part in parts:
        current = current / part
        try:
            if _is_link(current):
                raise FixturePathError(f"fixture path crosses a symlink or reparse point: {relative!r}")
        except FileNotFoundError:
            raise FixturePathError(f"fixture file does not exist: {relative!r}") from None
    real = os.path.realpath(current)
    try:
        inside = os.path.commonpath([root_real, real]) == root_real
    except ValueError:
        inside = False
    if not inside:
        raise FixturePathError(f"fixture path escapes the fixture root: {relative!r}")
    if not stat.S_ISREG(os.stat(real).st_mode):
        raise FixturePathError(f"fixture path is not a regular file: {relative!r}")
    return Path(real)


def is_within(root, candidate):
    """True when ``candidate`` resolves inside ``root`` (both resolved)."""
    root_real = os.path.realpath(root)
    real = os.path.realpath(candidate)
    try:
        return os.path.commonpath([root_real, real]) == root_real
    except ValueError:
        return False
