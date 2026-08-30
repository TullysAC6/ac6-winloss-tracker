from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "AC6WinLossTracker"
DISPLAY_NAME = "AC6 Win/Loss Tracker"
VERSION = "1.0.0"


def resource_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        root = Path(base) / APP_NAME
    else:
        root = Path.home() / ".ac6_winloss_tracker"
    root.mkdir(parents=True, exist_ok=True)
    return root


def diagnostics_dir() -> Path:
    path = data_dir() / "diagnostics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_path(name: str) -> Path:
    return resource_dir() / name
