"""T0 gate: unit, config, DB, schema, static and pure-logic tests.

    python tests/run_all_tests.py

Which tests are T0 is decided by tests/gate_registry.py, so no test runs in two
gates. T1 is tests/run_t1.py and T2 is tests/run_t2.py.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from gate_registry import T0, python_commands  # noqa: E402

ENTRY_TIMEOUT_SECONDS = 600

for label, command in python_commands(T0):
    print("=" * 70)
    print(label, flush=True)
    # Every test receives an isolated Windows-style user-data root. This keeps
    # regression runs away from real stats/history on Windows and avoids a
    # home-directory fallback on macOS/Linux.
    with tempfile.TemporaryDirectory(prefix="ac6-tracker-test-") as local_app_data:
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = local_app_data
        try:
            r = subprocess.run([sys.executable, *command], cwd=str(ROOT), env=environment,
                               timeout=ENTRY_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            print(f"T0: {label} exceeded {ENTRY_TIMEOUT_SECONDS}s")
            raise SystemExit(124)
        if r.returncode:
            raise SystemExit(r.returncode)
print("=" * 70)
print("T0: ALL TESTS PASSED")
