import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
files=[
    "test_game_capture.py",
    "test_effect_screenshot.py",
    "test_windows_capture_integration.py",
    "test_native_effect_screenshot.py",
    "test_occlusion_cloaked.py",
    "test_app_overlay_dispatch.py",
    "test_overlay_lifecycle.py",
    "test_launcher.py",
    "test_launcher_gui_lifecycle.py",
    "test_settings_window.py",
    "test_shutdown.py",
    "test_profile_optimization.py",
    "test_detector.py",
    "test_state_machine.py",
    "test_stats_manager.py",
    "test_event_bus.py",
    "test_issue4_effect_replay.py",
    "test_resource_optimization.py",
    "test_diagnostic_report.py",
    "test_result_gate.py",
    "test_config.py",
    "test_history_store.py",
    "test_dashboard_runtime.py",
    "test_dashboard_server.py",
    "test_dashboard_history_view.py",
    "test_dashboard_static.py",
    "test_overlay_static.py",
    "test_server_static.py",
    "test_stable_distribution_static.py",
    "test_readme_bootstrap_hash.py",
    "test_startup_preflight.py",
    "../test_strict_clear_gate.py",
    "../test_game_overlay_static.py",
    "../test_game_overlay_lifecycle_static.py",
]
for name in files:
    print("="*70)
    print(name)
    # Every test receives an isolated Windows-style user-data root. This keeps
    # regression runs away from real stats/history on Windows and avoids a
    # home-directory fallback on macOS/Linux.
    with tempfile.TemporaryDirectory(prefix="ac6-tracker-test-") as local_app_data:
        environment = os.environ.copy()
        environment["LOCALAPPDATA"] = local_app_data
        r=subprocess.run(
            [sys.executable,str(HERE/name)], cwd=str(HERE.parent), env=environment
        )
        if r.returncode:
            raise SystemExit(r.returncode)
print("="*70)
print("ALL TESTS PASSED")
