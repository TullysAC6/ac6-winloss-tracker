"""Unique T0 / T1 / T2 ownership for every test entry point (issue #14).

T0  Unit, config, DB, schema, static and pure logic. A T0 test may run a
    read-only tool (git, node, mklink) to inspect content.
T1  Stored pixels replayed through the real recognition, state-machine and gate
    path. Only ``tests/run_t1.py``.
T2  Anything that starts the Tracker server, binds a socket, spawns an app or
    worker process, opens a GUI window or exercises native capture, plus the T2
    harness self-checks.
T3  Real AC6, performed by the user. Never automated.

A file whose tests belong to two gates is split with unittest selectors
(``Class`` or ``Class.method``) that are passed to the file's own
``unittest.main()``. ``tests/test_gate_registry.py`` proves every test is owned
by exactly one gate and that no test file is left unregistered.
"""
from __future__ import annotations

from dataclasses import dataclass

T0, T1, T2 = "T0", "T1", "T2"
GATES = (T0, T1, T2)


@dataclass(frozen=True)
class Entry:
    path: str
    gate: str
    reason: str
    selectors: tuple = ()
    runner: str = "python"   # python | powershell-ci | t1
    ci: bool = True


CAPTURE_SPAWN = ("CaptureTests.test_repeated_owned_children_do_not_accumulate",
                 "CaptureTests.test_abnormal_parent_exit_reaps_child",
                 "CaptureTests.test_actual_spawn_shutdown_is_bounded")
CAPTURE_PURE = ("CaptureTests.test_native_callback_roi_slot_consumed_once_and_closed",
                "CaptureTests.test_native_time_does_not_refresh_delayed_frames",
                "CaptureTests.test_job_failure_never_releases_child",
                "CaptureTests.test_unreaped_worker_prevents_replacement",
                "CaptureTests.test_responsive_worker_without_frames_is_reaped",
                "CaptureTests.test_desktop_resize_breaks_consecutive_evidence_during_retry",
                "CaptureTests.test_background_new_frames_only",
                "CaptureTests.test_foreground_fallback_rechecks_identity_and_occlusion",
                "CaptureTests.test_minimized_and_changed_target",
                "CaptureTests.test_hung_worker_closed_before_retry",
                "CaptureTests.test_geometry_borderless_decorated_resize",
                "CaptureGapTests")
SCREENSHOT_PROCESS = ("ScreenshotTests.test_real_pythonw_worker_has_durable_skip_reason",)
SCREENSHOT_PURE = ("ScreenshotTests.test_diagnostics_without_stdout_and_io_failure_isolation",
                   "ScreenshotTests.test_filename_and_dedup_and_write_failure",
                   "ScreenshotTests.test_default_off_and_strict_boolean",
                   "ScreenshotTests.test_job_failure_and_worker_timeout",
                   "ScreenshotTests.test_cleanup_failure_never_spawns_another_child",
                   "ScreenshotTests.test_once_after_visible_paint_and_fifty_flash",
                   "ScreenshotTests.test_disabled_hidden_expired_spawn_failure",
                   "ScreenshotTests.test_exact_desktop_pixels_and_alt_tab_rejection")
WRITABLE_HISTORY_SERVER = ("SuccessfulPurgeLeavesAWritableSession", "UnrecoverableSessionIsNeverSuccess",
                           "FailedPurgeKeepsRecordingInHistory", "SessionResetCannotProduceStatsOnlyResults",
                           "NormalPathIsUnchanged")
PLAYER_OVERLAY_PURE = ("PlayerMetricsTests", "PlayerRenderTests", "LayoutAndSafeZoneTests",
                       "ExplicitOffsetCompatibilityTests", "ResultAcknowledgementTests",
                       "MilestoneCharacterizationTests", "StructuralBudgetTests")

# Order within a gate is execution order. T0 keeps the relative order the
# pre-#14 tests/run_all_tests.py used; T2 runs the launcher first, as CI did.
ENTRIES = (
    Entry("tests/test_python_spawn.py", T0, "pure executable/environment and launch identity contracts"),
    # ------------------------------------------------------------------ T0
    Entry("tests/test_game_capture.py", T0, "capture validation with mocked workers and the gap loop",
          selectors=CAPTURE_PURE),
    Entry("tests/test_effect_screenshot.py", T0, "screenshot logic with a mocked worker context",
          selectors=SCREENSHOT_PURE),
    Entry("tests/test_app_overlay_dispatch.py", T0, "overlay dispatch against a fake process object"),
    Entry("tests/test_overlay_lifecycle.py", T0, "overlay lifecycle logic without a process"),
    Entry("tests/test_settings_window.py", T0, "update-check parsing with urlopen mocked", selectors=("ReleaseTests",)),
    Entry("tests/test_profile_optimization.py", T0, "classifier profile numerics"),
    Entry("tests/test_detector.py", T0, "classifier regressions on inputs constructed in code; template validation"),
    Entry("tests/test_state_machine.py", T0, "state machine on labels"),
    Entry("tests/test_stats_manager.py", T0, "stats persistence and in-process concurrency"),
    Entry("tests/test_event_bus.py", T0, "event bus logic"),
    Entry("tests/test_issue4_effect_replay.py", T0, "effect replay logic; runs overlay JS through node",
          selectors=("ReplayTests",)),
    Entry("tests/test_diagnostic_report.py", T0, "diagnostic report content in an owned root"),
    Entry("tests/test_result_gate.py", T0, "result gate on explicit times"),
    Entry("tests/test_config.py", T0, "config validation and migration"),
    Entry("tests/test_settings_actions.py", T0, "settings, purge store and diagnostics logic without a server",
          selectors=("SettingsFileTests", "PurgeRequestValidationTests", "OverlayScopeTests", "PurgeStoreTests",
                     "StoppedTrackerTests", "DiagnosticReportTests", "CaptureGapDiagnosticsTests")),
    Entry("tests/test_purge_atomicity.py", T0, "purge coordination with in-process fault injection"),
    Entry("tests/test_purge_history_writability.py", T0, "history store session transactions",
          selectors=("HistoryStoreSessionTests",)),
    Entry("tests/test_test_isolation.py", T0, "test isolation guards"),
    Entry("tests/test_history_store.py", T0, "history DB schema and store"),
    Entry("tests/test_history_analytics.py", T0, "analytics, CSV export and integrity checks"),
    Entry("tests/test_dashboard_static.py", T0, "dashboard static checks"),
    Entry("tests/test_overlay_static.py", T0, "overlay static checks"),
    Entry("tests/test_server_static.py", T0, "server static checks"),
    Entry("tests/test_stable_distribution_static.py", T0, "distribution static checks"),
    Entry("tests/test_readme_bootstrap_hash.py", T0, "README bootstrap hash from the committed blob"),
    Entry("test_strict_clear_gate.py", T0, "strict CLEAR gate on labels"),
    Entry("test_game_overlay_static.py", T0, "game overlay static checks"),
    Entry("test_game_overlay_lifecycle_static.py", T0, "game overlay lifecycle static checks"),
    Entry("tests/test_player_overlay_layout.py", T0, "UI-1A Player layout, safe zone and acknowledgement logic",
          selectors=PLAYER_OVERLAY_PURE),
    Entry("tests/test_t1_contract.py", T0, "T1 harness contract: schema, loader, integrity, security, reports"),
    Entry("tests/test_t1_legacy_coverage.py", T0, "every pre-#14 pixel assertion is still enforced"),
    Entry("tests/test_gate_registry.py", T0, "this registry: unique ownership and CI order"),
    Entry("tests/test_runtime_policy_installer.ps1", T0, "installer Python selection in isolation",
          runner="powershell-ci"),
    Entry("tests/test_bootstrap.ps1", T0, "verified bootstrap against fixtures", runner="powershell-ci"),
    Entry("tests/test_readme_commands.ps1", T0, "README commands against fixtures", runner="powershell-ci"),
    Entry("tests/test_uninstaller.ps1", T0, "uninstaller in an isolated root", runner="powershell-ci"),
    # ------------------------------------------------------------------ T1
    Entry("tests/run_t1.py", T1, "canonical fixture replay", runner="t1"),
    # ------------------------------------------------------------------ T2
    Entry("tests/test_python_spawn_integration.py", T2, "real venv python/pythonw PID identity and job-contained workers"),
    Entry("tests/test_launcher.py", T2, "launcher processes and a local HTTP server"),
    Entry("tests/test_game_capture.py", T2, "real spawned capture workers and job objects", selectors=CAPTURE_SPAWN),
    Entry("tests/test_effect_screenshot.py", T2, "a real pythonw screenshot worker", selectors=SCREENSHOT_PROCESS),
    Entry("tests/test_windows_capture_integration.py", T2, "native WGC; opt-in through AC6_RUN_WGC_INTEGRATION"),
    Entry("tests/test_native_effect_screenshot.py", T2, "native screenshot; opt-in through AC6_RUN_SCREENSHOT_INTEGRATION"),
    Entry("tests/test_occlusion_cloaked.py", T2, "real windows and DWM queries"),
    Entry("tests/test_player_overlay_layout.py", T2, "real Tk fonts and the three real overlay HWNDs",
          selectors=("RealTkCanvasTests",)),
    Entry("tests/test_launcher_gui_lifecycle.py", T2, "launcher GUI and processes"),
    Entry("tests/test_settings_window.py", T2, "settings GUI and launcher processes", selectors=("SettingsTests",)),
    Entry("tests/test_shutdown.py", T2, "shutdown over a local HTTP server"),
    Entry("tests/test_issue4_effect_replay.py", T2, "a live server and socket reconnects",
          selectors=("LiveServerTests",)),
    Entry("tests/test_resource_optimization.py", T2, "resource behaviour over a local HTTP server"),
    Entry("tests/test_settings_actions.py", T2, "a live server and the settings GUI",
          selectors=("LiveServerPurgeTests", "SettingsWindowTests")),
    Entry("tests/test_purge_history_writability.py", T2, "the real server.main over HTTP",
          selectors=WRITABLE_HISTORY_SERVER),
    Entry("tests/test_result_persistence_invariant.py", T2, "the real server.main over HTTP"),
    Entry("tests/test_pending_history_recovery.py", T2, "fresh server processes and live reconnects"),
    Entry("tests/test_t2_harness_selfcheck.py", T2, "T2 harness teardown, orphan and job ownership"),
    Entry("tests/test_dashboard_runtime.py", T2, "dashboard over a local HTTP server"),
    Entry("tests/test_dashboard_server.py", T2, "dashboard server over HTTP"),
    Entry("tests/test_dashboard_history_view.py", T2, "dashboard history GUI"),
    Entry("tests/test_startup_preflight.py", T2, "startup preflight with server.main and ports"),
    Entry("tests/test_t1_runner_lifecycle.py", T2, "T1 worker timeout, leak, exit and cleanup ownership"),
    Entry("tests/t2_settings_analytics_e2e.py", T2, "settings/analytics isolated E2E; run manually, as before #14",
          ci=False),
    Entry("tests/test_source_install_flow.ps1", T2, "isolated install, update, rollback and uninstall",
          runner="powershell-ci"),
)


def entries(gate, ci_only=False):
    return [entry for entry in ENTRIES if entry.gate == gate and (entry.ci or not ci_only)]


def python_commands(gate, ci_only=False):
    """(label, argv after the interpreter) for the Python entries of a gate."""
    commands = []
    for entry in entries(gate, ci_only):
        if entry.runner != "python":
            continue
        label = entry.path if not entry.selectors else f"{entry.path} [{len(entry.selectors)} selector(s)]"
        commands.append((label, [entry.path, *entry.selectors]))
    return commands
