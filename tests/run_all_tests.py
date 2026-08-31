import subprocess
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
files=[
    "test_app_overlay_dispatch.py",
    "test_overlay_lifecycle.py",
    "test_launcher.py",
    "test_shutdown.py",
    "test_profile_optimization.py",
    "test_detector.py",
    "test_state_machine.py",
    "test_stats_manager.py",
    "test_event_bus.py",
    "test_result_gate.py",
    "test_config.py",
    "test_overlay_static.py",
    "test_server_static.py",
    "test_msix_packaging_static.py",
]
for name in files:
    print("="*70)
    print(name)
    r=subprocess.run([sys.executable,str(HERE/name)],cwd=str(HERE.parent))
    if r.returncode:
        raise SystemExit(r.returncode)
print("="*70)
print("ALL TESTS PASSED")
