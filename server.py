import json
import os
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


from app_paths import data_dir, resource_dir, resource_path
from config_utils import DEFAULT_CONFIG, CONFIG_PATH, get_config_health, load_config
from diagnostics import RECORDER
from event_bus import EventBus
from result_detector import ResultDetector
from result_gate import ResultGate
from stats_manager import StatsCorruptError, StatsManager


ROOT = resource_dir()
DATA_ROOT = data_dir()
OVERLAY = resource_path("overlay.html")
RUNTIME_PATH = DATA_ROOT / ".runtime.json"

stop_event = threading.Event()
stats = StatsManager(DATA_ROOT)
result_lock = threading.RLock()
result_gate = ResultGate()

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


def create_control_token():
    return secrets.token_urlsafe(32)


CONTROL_TOKEN = create_control_token()


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
    with result_lock:
        c = load_config()
        cooldown = 5.0

        # Auto and manual paths share one gate. Rejected duplicates do NOT
        # mutate detector state or extend the cooldown.
        if not result_gate.try_accept(cooldown, now=now):
            print(f"[result] duplicate/conflict ignored: {result} from {source}")
            return False

        # Only keep the cooldown reservation when the stats mutation actually
        # succeeds. If disk I/O or stats validation fails, release the gate so
        # the same visible result can be retried after the detector re-arms.
        try:
            before = stats.snapshot()
            s = stats.add(result, source)
        except Exception:
            result_gate.clear_for_manual_correction()
            raise

        milestone = None
        if result == "win":
            for n in (5, 10, 15, 20):
                if before["streak"] < n <= s["streak"]:
                    milestone = n
                    break

        with detector_lock:
            current = detector
        if current:
            current.external_mutation()

        publish_stats_active(s)

        if milestone:
            publish("effect", {
                "system": True,
                "kind": "effect",
                "effect": "milestone",
                "milestone": milestone,
                "created_at_ms": int(time.time() * 1000),
            }, remember=False)

        RECORDER.record("result_accepted", result=result, source=source, wins=s["wins"], losses=s["losses"], streak=s["streak"])
        print(
            f"[result] {result.upper()} ({source}) | "
            f"WIN {s['wins']} LOSE {s['losses']} | 連勝 {s['streak']}"
        )
        return True


def undo_result():
    with result_lock:
        s, removed = stats.undo()
        result_gate.clear_for_manual_correction()
        with detector_lock:
            current = detector
        if current:
            current.after_undo()
        publish_stats_active(s)
        return s, removed


def reset_stats():
    with result_lock:
        s = stats.reset()
        result_gate.lock_now()
        with detector_lock:
            current = detector
        if current:
            current.external_mutation()
        publish_stats_active(s)
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
                        record = client.queue.get(timeout=1.0)
                    except queue.Empty:
                        continue
                    if client.overflow.is_set():
                        break
                    self.wfile.write(encode_sse(record))
                    self.wfile.flush()

            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                if client is not None:
                    event_bus.unregister(client)
            return

        self.json_response({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if self.headers.get("X-Control-Token", "") != CONTROL_TOKEN:
            self.json_response({"error": "unauthorized"}, 403)
            return

        try:
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


def main():
    ensure_first_run_config()
    try:
        c = load_config(use_last_good=False)
    except Exception as e:
        print(f"[startup] config error: {e}")
        input("Enterキーで終了...")
        return

    # First run is self-initializing; no setup script is required.
    if not stats.path.exists():
        stats.reset()
        RECORDER.record("first_run_stats_initialized")

    # Stats and detector failures are isolated from the local HTTP server.
    try:
        stats.snapshot()
    except StatsCorruptError as e:
        print(f"[stats] WARNING: {e}")

    threading.Thread(target=detector_supervisor, daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", c["port"]), Handler)
    write_runtime_file(server.server_address[1])

    print("=" * 68)
    print(" AC6 Win/Loss Tracker")
    print(f" OBS URL : http://127.0.0.1:{c['port']}/")
    print(" AC6 Auto Win/Loss + Win Rate + Streak UI")
    print("=" * 68)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.server_close()
        remove_runtime_file()


if __name__ == "__main__":
    main()
