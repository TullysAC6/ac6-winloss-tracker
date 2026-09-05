import importlib.machinery
import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path


root = Path(sys.argv[1]).resolve()
result_path = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(root))
loader = importlib.machinery.SourceFileLoader("venv_product_launcher", str(root / "launcher.pyw"))
spec = importlib.util.spec_from_loader(loader.name, loader)
launcher = importlib.util.module_from_spec(spec)
loader.exec_module(launcher)

result = {
    "launcher_pid": os.getpid(),
    "launcher_executable": sys.executable,
    "launcher_prefix": sys.prefix,
    "launcher_base_prefix": sys.base_prefix,
}
try:
    launch_id = launcher.new_launch_id()
    process = launcher.start_application(
        root / "app.py", root, launcher.STARTUP_LOG, launch_id
    )
    result.update({"launch_id": launch_id, "popen_pid": process.pid})
    if not launcher.wait_for_application(
        process, launcher.RUNTIME_PATH, timeout=20, expected_launch_id=launch_id
    ):
        raise RuntimeError("actual app runtime did not become ready")
    runtime = launcher.read_runtime(launcher.RUNTIME_PATH)
    if runtime is None:
        raise RuntimeError("runtime metadata disappeared after readiness")
    result["runtime"] = runtime
except Exception:
    result["error"] = traceback.format_exc()
finally:
    result_path.write_text(json.dumps(result), encoding="utf-8")
