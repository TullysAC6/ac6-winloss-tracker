"""T2 gate: isolated E2E, lifecycle and process tests owned by tests/gate_registry.py.

    python tests/run_t2.py [--ci]

``--ci`` runs the entries the Windows CI matrix runs; without it the manual T2
entries run too. tests/test_source_install_flow.ps1 is a T2 entry run by its own
CI step. Every test file runs in its own process with an owned LOCALAPPDATA and
a real-time bound.
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from gate_registry import T2, python_commands  # noqa: E402

ENTRY_TIMEOUT_SECONDS = 900


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ci", action="store_true", help="only the entries the CI matrix runs")
    arguments = parser.parse_args(argv)
    for label, command in python_commands(T2, ci_only=arguments.ci):
        print("=" * 70)
        print(label, flush=True)
        with tempfile.TemporaryDirectory(prefix="ac6-tracker-test-") as local_app_data:
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = local_app_data
            try:
                result = subprocess.run([sys.executable, *command], cwd=str(ROOT), env=environment,
                                        timeout=ENTRY_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                print(f"T2: {label} exceeded {ENTRY_TIMEOUT_SECONDS}s")
                return 124
            if result.returncode:
                return result.returncode
    print("=" * 70)
    print("T2: ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
