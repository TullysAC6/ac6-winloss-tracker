import json
import importlib
import importlib.util
import os
import queue
import secrets
import socket
import sys
import threading
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


from app_paths import data_dir, resource_dir, resource_path
from config_utils import (
    DEFAULT_CONFIG, CONFIG_PATH, get_config_health, load_config, validate_config,
)
from diagnostics import RECORDER
from event_bus import EventBus
from history_store import HistoryStore, read_history_schema_version
from result_detector import ResultDetector
from result_gate import ResultGate
from stats_manager import StatsCorruptError, StatsManager


ROOT = resource_dir()
DATA_ROOT = data_dir()
OVERLAY = resource_path("overlay.html")
RUNTIME_PATH = DATA_ROOT / ".runtime.json"
OVERLAY_RUNTIME_PATH = DATA_ROOT / ".overlay-runtime.json"
DASHBOARD_RUNTIME_PATH = DATA_ROOT / ".dashboard-runtime.json"
OVERLAY_HEARTBEAT_MAX_AGE = 5.0
SSE_KEEPALIVE_SECONDS = 2.0
SSE_KEEPALIVE = b": keepalive\n\n"

stop_event = threading.Event()
stats = StatsManager(DATA_ROOT)
result_lock = threading.RLock()
result_gate = ResultGate()
history = None
history_lock = threading.RLock()
history_health = {"status": "starting", "error": None}
history_event_ids = []
dashboard_cache_lock = threading.Lock()
dashboard_cache = None

detector = None
detector_lock = threading.Lock()
detector_fallback_lock = threading.Lock()
detector_fallback = {
    "system": True,
    "kind": "detector",
    "status": "starting",
    "error": None,
    "last_capture": None,
    "last_result": None,
}



event_bus = EventBus(history_size=300)


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Suppress only expected short-lived localhost client disconnect noise."""

    # Windows SO_REUSEADDR can permit two listeners on one address. Require
    # exclusive ownership there so the HTTP bind remains the single-instance
    # boundary before stats/history mutation.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
            )
        super().server_bind()

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def create_control_token():
    return secrets.token_urlsafe(32)


_startup_token = os.environ.pop("AC6_STARTUP_TOKEN", "")
CONTROL_TOKEN = (_startup_token if len(_startup_token) == 43
                 and all(c.isascii() and (c.isalnum() or c in "-_") for c in _startup_token)
                 else create_control_token())
del _startup_token


class StartupEnvironmentError(RuntimeError):
    def __init__(self, code, description, action):
        self.code = str(code)
        self.description = str(description)
        self.action = str(action)
        super().__init__(f"[{self.code}] {self.description} {self.action}")


def write_runtime_file(port):
    payload = {
        "port": int(port),
        "token": CONTROL_TOKEN,
        "pid": os.getpid(),
        "started_at": time.time(),
    }
    tmp = RUNTIME_PATH.with_name(".runtime.json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, RUNTIME_PATH)


def remove_runtime_file():
    try:
        RUNTIME_PATH.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"[runtime] WARNING: could not remove .runtime.json: {e}")


def process_is_alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong(0)
            if not ctypes.windll.kernel32.GetExitCodeProcess(
                handle, ctypes.byref(exit_code)
            ):
                return False
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def lifecycle_health(now=None):
    now = time.time() if now is None else float(now)
    detector_health = detector_snapshot()
    detector_status = str(detector_health.get("status", "error"))
    detector_ok = detector_status != "error"
    overlay = {"ok": False, "state": "missing"}
    try:
        raw = json.loads(OVERLAY_RUNTIME_PATH.read_text(encoding="utf-8"))
        overlay_pid = int(raw["pid"])
        server_pid = int(raw["server_pid"])
        heartbeat_age = max(0.0, now - float(raw["heartbeat_at"]))
        state = str(raw.get("state", ""))
        overlay_ok = (
            state == "ready"
            and server_pid == os.getpid()
            and heartbeat_age <= OVERLAY_HEARTBEAT_MAX_AGE
            and process_is_alive(overlay_pid)
        )
        overlay = {
            "ok": overlay_ok, "pid": overlay_pid, "state": state,
            "heartbeat_age": round(heartbeat_age, 3), "server_pid": server_pid,
            "panel_hwnd": int(raw.get("panel_hwnd", 0)),
            "text_hwnd": int(raw.get("text_hwnd", 0)),
            "target_process": str(raw.get("target_process", "")),
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    dashboard = {"open": False}
    try:
        raw = json.loads(DASHBOARD_RUNTIME_PATH.read_text(encoding="utf-8"))
        dashboard_pid = int(raw["pid"])
        dashboard_server_pid = int(raw["server_pid"])
        heartbeat_age = max(0.0, now - float(raw["heartbeat_at"]))
        dashboard = {
            "open": (
                dashboard_server_pid == os.getpid()
                and process_is_alive(dashboard_pid)
                and heartbeat_age <= 5.0
            ),
            "pid": dashboard_pid,
            "server_pid": dashboard_server_pid,
            "heartbeat_age": round(heartbeat_age, 3),
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    ok = bool(detector_ok and overlay["ok"])
    return {
        "ok": ok,
        "server": {"ok": True, "pid": os.getpid()},
        "http": {"ok": True},
        "detector": {"ok": detector_ok, **detector_health},
        "overlay": overlay,
        "dashboard": dashboard,
    }


def invalidate_dashboard_summary():
    global dashboard_cache
    with dashboard_cache_lock:
        dashboard_cache = None


def set_history_health(status, error=None):
    changed = False
    with history_lock:
        next_health = {
            "status": str(status),
            "error": None if error is None else str(error),
        }
        changed = next_health != history_health
        history_health.update(
            status=next_health["status"], error=next_health["error"]
        )
    if changed:
        invalidate_dashboard_summary()


def history_failure(operation, error):
    message = f"{type(error).__name__}: {error}"
    set_history_health("degraded", message)
    RECORDER.record("history_error", operation=operation, error=message)
    print(f"[history] WARNING: {operation} failed: {message}")


def _dashboard_summary_uncached():
    current = status_payload(stats.snapshot())
    with history_lock:
        store = history
        health = dict(history_health)
    empty_lifetime = {
        "wins": 0, "losses": 0, "draws": 0, "matches": 0,
        "win_rate": 0.0, "best_streak": 0,
    }
    session_meta = None
    lifetime = empty_lifetime
    recent = []
    if store is not None:
        try:
            session_meta = store.session_metadata()
            lifetime = store.lifetime_summary()
            recent = store.recent_matches(10)
            set_history_health("active")
            with history_lock:
                health = dict(history_health)
        except Exception as e:
            history_failure("dashboard_summary", e)
            with history_lock:
                health = dict(history_health)
    summary = {
        "system": True,
        "session": {
            "id": session_meta["id"] if session_meta else None,
            "started_at": session_meta["started_at"] if session_meta else None,
            "wins": current["wins"], "losses": current["losses"],
            "win_rate": current["win_rate"], "streak": current["streak"],
            "best_streak": current["best_streak"],
        },
        "lifetime": lifetime,
        "recent_matches": recent,
        "history_health": health,
    }
    return summary


def dashboard_summary():
    global dashboard_cache
    with dashboard_cache_lock:
        if dashboard_cache is not None:
            return deepcopy(dashboard_cache)
    # All authoritative stats/history mutations also hold result_lock. This
    # prevents a stale snapshot from being cached after a concurrent result.
    with result_lock:
        with dashboard_cache_lock:
            if dashboard_cache is not None:
                return deepcopy(dashboard_cache)
        summary = _dashboard_summary_uncached()
        with dashboard_cache_lock:
            dashboard_cache = deepcopy(summary)
        return summary


def status_payload(s, milestone=None):
    wins = s["wins"]
    losses = s["losses"]
    total = wins + losses
    rate = round(wins / total * 100.0, 1) if total else 0.0
    streak = s["streak"]

    if streak >= 20:
        status, level = "RUSH継続中", 5
    elif streak >= 15:
        status, level = "覚醒ゾーン", 4
    elif streak >= 10:
        status, level = "超激アツ", 3
    elif streak >= 5:
        status, level = "激アツ", 2
    elif streak >= 3:
        status, level = "アツい", 1
    else:
        status, level = "", 0

    return {
        "system": True,
        "kind": "stats",
        "wins": wins,
        "losses": losses,
        "total": total,
        "win_rate": rate,
        "streak": streak,
        "best_streak": s["best_streak"],
        "status": status,
        "status_level": level,
        "milestone": milestone,
    }


def publish(event_type, payload, remember=True):
    return event_bus.publish(event_type, payload, remember=remember)


def detector_snapshot():
    with detector_lock:
        current = detector
    if current is not None:
        return current.health.snapshot()
    with detector_fallback_lock:
        return dict(detector_fallback)


def set_detector_fallback(**kwargs):
    with detector_fallback_lock:
        detector_fallback.update(kwargs)
        snap = dict(detector_fallback)
    publish("detector", snap, remember=False)




def safe_snapshot_bundle():
    snapshots = []
    try:
        snapshots.append(("stats", status_payload(stats.snapshot())))
        snapshots.append(("stats_health", {
            "system": True,
            "kind": "stats_health",
            "status": "active",
            "error": None,
        }))
    except StatsCorruptError as e:
        snapshots.append(("stats_health", {
            "system": True,
            "kind": "stats_health",
            "status": "error",
            "error": str(e),
        }))

    snapshots.append(("config_health", {"system": True, "kind": "config_health", **get_config_health()}))
    snapshots.append(("detector", detector_snapshot()))
    return snapshots


def encode_sse(record):
    return (
        f"id: {record['id']}\n"
        f"event: {record['event']}\n"
        f"data: {record['data']}\n\n"
    ).encode("utf-8")


def encode_snapshot(event_type, payload):
    # Snapshot intentionally has no id. It cannot move Last-Event-ID ahead of
    # remembered queued events.
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    ).encode("utf-8")



def publish_stats_active(s):
    publish("stats", status_payload(s))
    publish("stats_health", {
        "system": True,
        "kind": "stats_health",
        "status": "active",
        "error": None,
    }, remember=False)


def record_result(result, source):
    now = time.monotonic()
    result_detected_at = time.time()
    with result_lock:
        c = load_config()
        cooldown = 5.0

        # Auto and manual paths share one gate. Rejected duplicates do NOT
        # mutate detector state or extend the cooldown.
        if not result_gate.try_accept(cooldown, now=now):
            print(f"[result] duplicate/conflict ignored: {result} from {source}")
            return False

        # Allocate the context-row identity before authoritative writes, but do
        # not let optional context creation participate in those writes. The
        # stable logical match relation remains the result event_id.
        context_id = secrets.token_urlsafe(18)

        # Only keep the cooldown reservation when the stats mutation actually
        # succeeds. If disk I/O or stats validation fails, release the gate so
        # the same visible result can be retried after the detector re-arms.
        try:
            before = stats.snapshot()
            s = stats.add(result, source)
            invalidate_dashboard_summary()
        except Exception:
            result_gate.clear_for_manual_correction()
            raise

        event_id = secrets.token_urlsafe(18)
        milestone = None
        if result == "win":
            for n in range(5, 51, 5):
                if before["streak"] < n <= s["streak"]:
                    milestone = n
                    break

        with detector_lock:
            current = detector
        if current:
            current.external_mutation()

        publish_stats_active(s)

        with history_lock:
            store = history
        stored_event_id = None
        if store is not None:
            try:
                if store.record_result(event_id, result, source, s):
                    stored_event_id = event_id
                set_history_health("active")
            except Exception as e:
                # Current stats and detector flow remain authoritative even if
                # the optional lifetime store is temporarily unavailable.
                history_failure("record_result", e)
        if store is not None and stored_event_id is not None:
            try:
                store.create_match_context(
                    context_id,
                    stored_event_id,
                    result_detected_at=result_detected_at,
                )
            except Exception as e:
                # Phase 0 context is enrichment. The accepted stats/history row
                # above remains authoritative even if this separate write fails.
                RECORDER.record(
                    "match_context_error",
                    context_id=context_id,
                    error=f"{type(e).__name__}: {e}",
                )
        history_event_ids.append(stored_event_id)

        if milestone:
            publish("effect", {
                "system": True,
                "kind": "effect",
                "effect": "milestone",
                "effect_id": event_id,
                "milestone": milestone,
                "streak": s["streak"],
                "tier": min(10, milestone // 5),
                "created_at_ms": int(time.time() * 1000),
            }, remember=False)

        RECORDER.flush_frame_context("result_accepted")
        RECORDER.record("result_accepted", context_id=context_id, result=result, source=source, wins=s["wins"], losses=s["losses"], streak=s["streak"])
        print(
            f"[result] {result.upper()} ({source}) | "
            f"WIN {s['wins']} LOSE {s['losses']} | 連勝 {s['streak']}"
        )
        return True


def undo_result():
    with result_lock:
        s, removed = stats.undo()
        invalidate_dashboard_summary()
        result_gate.clear_for_manual_correction()
        with detector_lock:
            current = detector
        if current:
            current.after_undo()
        publish_stats_active(s)
        stored_event_id = history_event_ids.pop() if history_event_ids else None
        with history_lock:
            store = history
        if store is not None and stored_event_id is not None:
            try:
                store.undo_event(stored_event_id)
                set_history_health("active")
            except Exception as e:
                history_failure("undo", e)
        return s, removed


def reset_stats():
    with result_lock:
        s = stats.reset()
        invalidate_dashboard_summary()
        result_gate.lock_now()
        with detector_lock:
            current = detector
        if current:
            current.external_mutation()
        publish_stats_active(s)
        with history_lock:
            store = history
        if store is not None:
            try:
                store.reset_session()
                set_history_health("active")
            except Exception as e:
                history_failure("reset_session", e)
        history_event_ids.clear()
        return s



def detector_supervisor():
    global detector
    while not stop_event.is_set():
        try:
            c = load_config()
            if not c["result_detector_enabled"]:
                set_detector_fallback(status="disabled", error=None)
                stop_event.wait(1.0)
                continue

            try:
                instance = ResultDetector(
                    ROOT,
                    load_config,
                    record_result,
                    publish,
                    stop_event,
                    diagnostic_recorder=RECORDER,
                )
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                print(f"[result] detector construction failed: {msg}")
                set_detector_fallback(status="error", error=msg)
                stop_event.wait(10.0)
                continue

            with detector_lock:
                detector = instance
            instance.run()
            with detector_lock:
                detector = None

            if not stop_event.is_set():
                set_detector_fallback(
                    status="error",
                    error="detector stopped unexpectedly; retrying",
                )
                stop_event.wait(5.0)

        except Exception as e:
            set_detector_fallback(
                status="error",
                error=f"supervisor: {type(e).__name__}: {e}",
            )
            stop_event.wait(5.0)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def json_response(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            body = OVERLAY.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/config":
            try:
                c = load_config()
                self.json_response({
                    "stats_enabled": c["stats_enabled"],
                    "config_health": get_config_health(),
                })
            except Exception as e:
                self.json_response(
                    {"error": "invalid config", "detail": str(e)}, 503
                )
            return

        if path == "/stats":
            try:
                self.json_response(status_payload(stats.snapshot()))
            except StatsCorruptError as e:
                self.json_response({"error": str(e)}, 503)
            return

        if path == "/health":
            health = lifecycle_health()
            self.json_response(health, 200 if health["ok"] else 503)
            return

        if path == "/api/dashboard/summary":
            try:
                self.json_response(dashboard_summary())
            except Exception as e:
                history_failure("dashboard_summary", e)
                self.json_response({"error": "history unavailable"}, 503)
            return

        if path == "/events":
            client = None
            try:
                last_id = self.headers.get("Last-Event-ID", "")
                client, replay, snapshots = event_bus.register_with_snapshots(
                    last_id,
                    safe_snapshot_bundle,
                )

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                for record in replay:
                    self.wfile.write(encode_sse(record))
                for event_type, payload in snapshots:
                    self.wfile.write(encode_snapshot(event_type, payload))
                self.wfile.flush()

                while not stop_event.is_set():
                    if client.overflow.is_set():
                        # Force reconnect so remembered events replay from the
                        # browser's last successfully delivered Event-ID.
                        break
                    try:
                        record = client.queue.get(timeout=SSE_KEEPALIVE_SECONDS)
                    except queue.Empty:
                        self.wfile.write(SSE_KEEPALIVE)
                        self.wfile.flush()
                        continue
                    if client.overflow.is_set():
                        break
                    self.wfile.write(encode_sse(record))
                    self.wfile.flush()

            except (
                BrokenPipeError, ConnectionResetError,
                ConnectionAbortedError, OSError,
            ):
                pass
            finally:
                if client is not None:
                    event_bus.unregister(client)
            return

        self.json_response({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        supplied_token = self.headers.get("X-Control-Token", "")
        if not secrets.compare_digest(str(supplied_token), CONTROL_TOKEN):
            self.json_response({"error": "unauthorized"}, 403)
            return

        try:
            if path == "/api/system/identity":
                self.json_response({"pid": os.getpid()})
                return
            if path == "/api/system/shutdown":
                self.json_response({"ok": True, "status": "shutting_down"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path == "/api/stats/undo":
                s, removed = undo_result()
                self.json_response({
                    "ok": True,
                    "removed": removed,
                    "stats": status_payload(s),
                })
                return
            if path == "/api/stats/reset":
                s = reset_stats()
                self.json_response({"ok": True, "stats": status_payload(s)})
                return
            self.json_response({"error": "not found"}, 404)
        except StatsCorruptError as e:
            publish("stats_health", {
                "system": True,
                "kind": "stats_health",
                "status": "error",
                "error": str(e),
            }, remember=False)
            self.json_response({"error": str(e)}, 503)
        except Exception as e:
            self.json_response(
                {"error": f"{type(e).__name__}: {e}"}, 500
            )



def ensure_first_run_config():
    if CONFIG_PATH.exists():
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CONFIG_PATH)
    RECORDER.record("first_run_initialized")


def inspect_startup_config():
    """Read and validate config without migrating or creating it."""
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
            return validate_config(json.load(config_file))
    except Exception as error:
        raise StartupEnvironmentError(
            "ENV-CONFIG-INVALID",
            "設定ファイルを安全に読み込めません。",
            "config.jsonを確認するか、診断ZIPを作成して報告してください。",
        ) from error


def inspect_startup_environment():
    """Perform non-mutating runtime/dependency checks before ownership."""
    if sys.version_info < (3, 10):
        raise StartupEnvironmentError(
            "ENV-PYTHON-VERSION",
            "Python 3.10以上が必要です。",
            "公式Pythonを更新してからinstall.ps1を再実行してください。",
        )
    for module_name in ("mss", "ttkbootstrap", "tkinter"):
        if importlib.util.find_spec(module_name) is None:
            raise StartupEnvironmentError(
                "ENV-DEPENDENCY-MISSING",
                f"必要なPythonモジュール {module_name} がありません。",
                "install.ps1を再実行して依存関係を修復してください。",
            )
        try:
            importlib.import_module(module_name)
        except Exception as error:
            raise StartupEnvironmentError(
                "ENV-DEPENDENCY-IMPORT",
                f"Pythonモジュール {module_name} を読み込めません。",
                "install.ps1を再実行し、改善しない場合は診断ZIPを添えて報告してください。",
            ) from error


def preflight_history_schema():
    """Reject newer history formats before any authoritative mutation."""
    try:
        version = read_history_schema_version(DATA_ROOT)
    except Exception as error:
        raise StartupEnvironmentError(
            "ENV-HISTORY-UNREADABLE",
            "戦績履歴を読み取り専用で確認できません。",
            "history.dbを変更せず、診断ZIPを作成して報告してください。",
        ) from error
    if version > HistoryStore.SCHEMA_VERSION:
        raise StartupEnvironmentError(
            "ENV-HISTORY-FUTURE-SCHEMA",
            f"このアプリより新しい履歴形式（schema {version}）です。",
            "新しいバージョンのAC6 Win/Loss Trackerを使用してください。",
        )
    return version


def validate_owned_filesystem():
    """Verify the owned data directory before resetting session state."""
    probe = DATA_ROOT / ".startup-write-test.tmp"
    try:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        with probe.open("xb") as output:
            output.write(b"ok")
            output.flush()
            os.fsync(output.fileno())
        probe.unlink()
    except Exception as error:
        try:
            probe.unlink()
        except OSError:
            pass
        raise StartupEnvironmentError(
            "ENV-WRITE-PERMISSION",
            "ユーザーデータ保存先へ安全に書き込めません。",
            "フォルダーの権限と空き容量を確認してください。",
        ) from error


def main(on_ready=None):
    global history
    c = inspect_startup_config()
    inspect_startup_environment()

    try:
        server = QuietThreadingHTTPServer(("127.0.0.1", c["port"]), Handler)
    except OSError as error:
        raise StartupEnvironmentError(
            "ENV-PORT-IN-USE",
            f"ローカルポート {c['port']} を使用できません。",
            "すでにTrackerが起動していないか確認してください。",
        ) from error

    # Binding the configured port is the ownership boundary: never reset
    # another running instance's stats before this succeeds.
    try:
        preflight_history_schema()
        validate_owned_filesystem()
        ensure_first_run_config()
        c = load_config(use_last_good=False)
        stats.reset()
        stats.snapshot()
    except Exception as e:
        print(f"[startup] owned initialization failed: {type(e).__name__}: {e}")
        server.server_close()
        raise

    print("[lifecycle] session stats reset after server bind")
    history_event_ids.clear()
    try:
        store = HistoryStore(DATA_ROOT)
        store.start_session()
        invalidate_dashboard_summary()
        with history_lock:
            history = store
        set_history_health("active")
        print(f"[history] session {store.current_session_id} started")
    except Exception as e:
        history_failure("startup", e)

    try:
        threading.Thread(target=detector_supervisor, daemon=True).start()
        write_runtime_file(server.server_address[1])
        if on_ready is not None:
            on_ready()

        print("=" * 68)
        print(" AC6 Win/Loss Tracker")
        print(f" OBS URL : http://127.0.0.1:{c['port']}/")
        print(" AC6 Auto Win/Loss + Win Rate + Streak UI")
        print("=" * 68)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        with history_lock:
            store = history
        if store is not None:
            try:
                store.close_session("shutdown")
            except Exception as e:
                history_failure("shutdown", e)
        server.server_close()
        remove_runtime_file()


if __name__ == "__main__":
    main()
