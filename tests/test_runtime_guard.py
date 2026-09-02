import importlib
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import runtime_policy


with tempfile.TemporaryDirectory() as temporary:
    os.environ["LOCALAPPDATA"] = temporary
    app = importlib.import_module("app")
    rejected = runtime_policy.evaluate_runtime((3, 12, 10))
    original_app_guard = app.require_supported_runtime
    original_show_error = app._show_error
    shown = []
    app._show_error = shown.append
    if not runtime_policy.current_runtime_status().supported:
        assert app.main() == 2
        assert shown and "ENV-PYTHON-UNSUPPORTED" in shown.pop()
        assert not (Path(temporary) / "AC6WinLossTracker").exists()
    app.require_supported_runtime = lambda: (_ for _ in ()).throw(
        runtime_policy.UnsupportedRuntimeError(rejected)
    )
    try:
        assert app.main() == 2
        assert shown and "ENV-PYTHON-UNSUPPORTED" in shown[0]
        assert not (Path(temporary) / "AC6WinLossTracker").exists()
    finally:
        app.require_supported_runtime = original_app_guard
        app._show_error = original_show_error

    original_module_guard = runtime_policy.require_supported_runtime
    runtime_policy.require_supported_runtime = lambda: runtime_policy.evaluate_runtime((3, 14, 7))
    try:
        server = importlib.import_module("server")
    finally:
        runtime_policy.require_supported_runtime = original_module_guard
    server.require_supported_runtime = original_module_guard
    data = Path(temporary) / "AC6WinLossTracker"
    if data.exists():
        import shutil
        shutil.rmtree(data)
    server.DATA_ROOT = data
    server.CONFIG_PATH = data / "config.json"

    original_guard = server.require_supported_runtime
    server.require_supported_runtime = lambda: (_ for _ in ()).throw(
        runtime_policy.UnsupportedRuntimeError(rejected)
    )
    try:
        try:
            server.inspect_startup_environment()
            raise AssertionError("unsupported runtime must be rejected")
        except server.StartupEnvironmentError as error:
            assert error.code == "ENV-PYTHON-UNSUPPORTED"
        assert not data.exists()
    finally:
        server.require_supported_runtime = original_guard

server_source = (ROOT / "server.py").read_text(encoding="utf-8")
assert server_source.index("require_supported_runtime()") < server_source.index("from diagnostics import RECORDER")

for entrypoint in ("launcher.pyw", "dashboard.py", "game_overlay.py"):
    source = (ROOT / entrypoint).read_text(encoding="utf-8")
    main = source.index("def main(")
    assert source.index("require_supported_runtime()", main) > main

print("Unsupported runtime is rejected before persistent state mutation: OK")
