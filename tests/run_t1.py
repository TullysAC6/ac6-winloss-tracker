"""Canonical T1 gate: fixture replay through the real recognition path.

    python tests/run_t1.py [--report-dir DIR] [--case-timeout S] [--suite-timeout S]

Exit status 0 means T1 PASS on the canonical corpus; anything else is a failure.
The stdout summary is followed by t1-report.json and t1-junit.xml in the report
directory (by default <TEMP>/ac6-t1-report).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from t1.runner import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
