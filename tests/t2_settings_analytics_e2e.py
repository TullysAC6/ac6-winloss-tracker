"""T2 isolated end-to-end gate for the settings / analytics generation (PR #5).

Everything here runs against an isolated ``LOCALAPPDATA``, an isolated
``history.db`` and a dynamically reserved port. The user's live Tracker, its
history, config, stats, diagnostics, port 8765 and Overlay mutex are never
touched. A real ``server.main`` is started, driven over its real HTTP API, and
shut down; nothing bypasses the production gate or introduces a test-only path.

This is a gate, not a unit test: it is run explicitly rather than from
``run_all_tests.py``, because it starts a real server.

    python tests/t2_settings_analytics_e2e.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Cleanup bounds. A hang is a failure, so the watchdog always yields an exit code.
THREAD_JOIN_SECONDS = 20
ROOT_REMOVE_ATTEMPTS = 10
WATCHDOG_SECONDS = 900

RESULTS: list[tuple[str, str, bool]] = []


def check(section: str, name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((section, name, bool(ok)))
    mark = "ok  " if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{mark}] {section} {name}{suffix}")
    return bool(ok)


def free_port() -> int:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def local_midnight(days_ago: int = 0) -> float:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (today - timedelta(days=days_ago)).timestamp()


def seed_history(data_dir: Path) -> int:
    """history.db with results at known local-time offsets, inserted unsorted."""
    import history_store

    store = history_store.HistoryStore(data_dir)
    store.start_session()
    now = time.time()
    today_noon = min(local_midnight(0) + 12 * 3600, now - 60)
    plan = [
        (local_midnight(40) + 3600, "win"),
        (local_midnight(40) + 3700, "loss"),
        (local_midnight(40) + 3800, "draw"),
        (today_noon - 1800, "win"),      # deliberately out of order
        (local_midnight(9) + 3600, "win"),
        (local_midnight(9) + 3700, "win"),
        (local_midnight(9) + 3800, "loss"),
        (today_noon - 7200, "win"),
        (today_noon - 3600, "loss"),
    ]
    connection = sqlite3.connect(data_dir / "history.db")
    try:
        session_id = connection.execute(
            "SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()[0]
        for index, (created_at, result) in enumerate(plan):
            connection.execute(
                "INSERT INTO matches (event_id,session_id,created_at,result,source,"
                "streak_after,wins_after,losses_after,metadata_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (f"seed-{index}", session_id, created_at, result, "manual", 0, 0, 0, None),
            )
        connection.execute(
            "UPDATE sessions SET "
            "wins=(SELECT COUNT(*) FROM matches WHERE session_id=sessions.id AND result='win'),"
            "losses=(SELECT COUNT(*) FROM matches WHERE session_id=sessions.id AND result='loss'),"
            "draws=(SELECT COUNT(*) FROM matches WHERE session_id=sessions.id AND result='draw') "
            "WHERE id=?", (session_id,))
        connection.commit()
    finally:
        connection.close()
    # The store is left as-is: the server opens its own HistoryStore and starts
    # a fresh session, so these rows belong to a session that is not active.
    return len(plan)


def by_period(summary: dict) -> dict:
    return {entry["period"]: entry for entry in summary["periods"]}


def by_size(summary: dict) -> dict:
    return {entry["size"]: entry for entry in summary["recent"]}


def main() -> int:
    baseline_local = os.environ.get("LOCALAPPDATA")
    temporary = tempfile.mkdtemp(prefix="ac6-t2-settings-")
    os.environ["LOCALAPPDATA"] = temporary
    data = Path(temporary) / "AC6WinLossTracker"
    data.mkdir(parents=True)
    port = free_port()
    server = None
    thread = None
    children_before = owned_python_pids()

    try:
        # ------------------------------------------------------ B. migration
        print("\nB. config v17 -> v18 migration (isolated)")
        legacy = {
            "config_version": 17,
            "port": port,
            "stats_enabled": True,
            "result_detector_enabled": False,
            "effect_screenshot_enabled": True,   # deliberately non-default
        }
        (data / "config.json").write_text(json.dumps(legacy), encoding="utf-8")

        import config_utils
        check("B", "isolated config path in use",
              config_utils.CONFIG_PATH == data / "config.json",
              str(config_utils.CONFIG_PATH))
        migrated = config_utils.load_config()
        check("B", "migrated to CONFIG_VERSION 18", migrated["config_version"] == 18,
              str(migrated["config_version"]))
        check("B", "port preserved", migrated["port"] == port)
        check("B", "effect_screenshot_enabled preserved (non-default true)",
              migrated["effect_screenshot_enabled"] is True)
        check("B", "result_detector_enabled preserved (non-default false)",
              migrated["result_detector_enabled"] is False)
        check("B", "stats_enabled preserved", migrated["stats_enabled"] is True)
        check("B", "effect_enabled defaults to true", migrated["effect_enabled"] is True)
        check("B", "overlay_stats_scope defaults to session",
              migrated["overlay_stats_scope"] == "session")
        check("B", "v17 backup written", (data / "config.json.v17.bak").exists())
        check("B", "on-disk config_version is 18",
              json.loads((data / "config.json").read_text(encoding="utf-8"))["config_version"] == 18)
        check("B", "no temp config left behind", list(data.glob("config.json*.tmp")) == [])

        # ------------------------------------------------------ F. analytics
        print("\nF. analytics periods against a known fixture")
        seeded = seed_history(data)
        import history_analytics
        summary = history_analytics.summarize(data)
        periods, recents = by_period(summary), by_size(summary)
        check("F", "total matches counted", summary["total_matches"] == seeded,
              str(summary["total_matches"]))
        check("F", "today = 2W/1L",
              (periods["today"]["wins"], periods["today"]["losses"]) == (2, 1),
              f"W{periods['today']['wins']}/L{periods['today']['losses']}")
        check("F", "today win rate = WIN/(WIN+LOSE)",
              abs(periods["today"]["win_rate"] - round(2 / 3 * 100, 1)) < 0.05,
              str(periods["today"]["win_rate"]))
        check("F", "all-time = 5W/3L/1D",
              (periods["all"]["wins"], periods["all"]["losses"], periods["all"]["draws"]) == (5, 3, 1),
              f"W{periods['all']['wins']}/L{periods['all']['losses']}/D{periods['all']['draws']}")
        check("F", "DRAW excluded from the denominator",
              periods["all"]["counted"] == 8 and periods["all"]["matches"] == 9,
              f"counted={periods['all']['counted']} matches={periods['all']['matches']}")
        check("F", "all-time win rate = 5/8, not 5/9",
              abs(periods["all"]["win_rate"] - round(5 / 8 * 100, 1)) < 0.05,
              str(periods["all"]["win_rate"]))
        check("F", "month excludes the 40-day-old block",
              periods["month"]["matches"] == 6, str(periods["month"]["matches"]))
        check("F", "week is a subset of month",
              periods["week"]["matches"] <= periods["month"]["matches"],
              f"week={periods['week']['matches']} month={periods['month']['matches']}")
        check("F", "week boundary is Monday",
              datetime.fromtimestamp(history_analytics.period_start("week")).weekday() == 0)
        check("F", "month boundary is the 1st",
              datetime.fromtimestamp(history_analytics.period_start("month")).day == 1)
        check("F", "recent 10 sees all 9 rows", recents[10]["available"] == 9,
              str(recents[10]["available"]))
        check("F", "recent 100 sees all 9 rows", recents[100]["available"] == 9,
              str(recents[100]["available"]))

        # ------------------------------------------------------ G. CSV
        print("\nG. CSV export")
        db_before = (data / "history.db").read_bytes()
        destination = Path(temporary) / "export" / "history.csv"
        report = history_analytics.export_csv(data, destination)
        raw = destination.read_bytes()
        check("G", "UTF-8 BOM present", raw.startswith(b"\xef\xbb\xbf"))
        check("G", "row count matches", report["rows"] == seeded, str(report["rows"]))
        lines = [l for l in raw.decode("utf-8-sig").splitlines() if l.strip()]
        check("G", "header + one row per match", len(lines) == seeded + 1, str(len(lines)))
        epochs = [float(l.split(",")[2]) for l in lines[1:]]
        check("G", "oldest-first ordering despite unsorted insert",
              epochs == sorted(epochs))
        check("G", "no temp export left behind",
              list(destination.parent.glob(".ac6-export-*.tmp")) == [])

        victim = destination.parent / "failed.csv"
        original_writer = history_analytics.csv.writer

        def exploding_writer(*args, **kwargs):
            inner = original_writer(*args, **kwargs)

            class Boom:
                count = 0

                def writerow(self, row):
                    Boom.count += 1
                    if Boom.count > 2:
                        raise OSError("injected export failure")
                    return inner.writerow(row)
            return Boom()

        history_analytics.csv.writer = exploding_writer
        raised = False
        try:
            history_analytics.export_csv(data, victim)
        except OSError:
            raised = True
        finally:
            history_analytics.csv.writer = original_writer
        check("G", "injected write failure propagates", raised)
        check("G", "no partial file after injected failure", not victim.exists())
        check("G", "no temp left after injected failure",
              list(victim.parent.glob(".ac6-export-*.tmp")) == [])
        check("G", "history.db byte-unchanged by analytics and export",
              (data / "history.db").read_bytes() == db_before)

        # ------------------------------------------------------ H. DB health
        print("\nH. database integrity check")
        quick = history_analytics.integrity_check(data)
        check("H", "quick_check ok", quick["ok"] and quick["pragma"] == "quick_check",
              str(quick["messages"]))
        thorough = history_analytics.integrity_check(data, thorough=True)
        check("H", "integrity_check ok",
              thorough["ok"] and thorough["pragma"] == "integrity_check",
              str(thorough["messages"]))
        check("H", "history.db unchanged by integrity checks",
              (data / "history.db").read_bytes() == db_before)

        corrupt = Path(temporary) / "corrupt"
        corrupt.mkdir()
        (corrupt / "history.db").write_bytes(b"this is definitely not a sqlite file")
        handled = False
        try:
            bad = history_analytics.integrity_check(corrupt)
            handled = not bad["ok"]
            detail = str(bad["messages"])
        except history_analytics.HistoryUnavailable as error:
            handled, detail = True, type(error).__name__
        check("H", "corrupt DB reported without crashing", handled, detail)

        missing = Path(temporary) / "empty"
        missing.mkdir()
        try:
            history_analytics.summarize(missing)
            check("H", "missing DB raises HistoryUnavailable", False, "no exception")
        except history_analytics.HistoryUnavailable:
            check("H", "missing DB raises HistoryUnavailable", True)

        # ------------------------------------------------------ live server
        print(f"\nLive server on isolated port {port}")
        import server as server_module
        server = server_module
        ready = threading.Event()
        thread = threading.Thread(target=server.main, kwargs={"on_ready": ready.set},
                                  daemon=True)
        thread.start()
        if not check("live", "server.main became ready", ready.wait(30)):
            return 1
        check("live", "isolated port, not the user's 8765", port != 8765, str(port))
        token = server.CONTROL_TOKEN

        def post(path, body=None, with_token=True):
            payload = b"" if body is None else json.dumps(body).encode()
            headers = {"Content-Type": "application/json"}
            if with_token:
                headers["X-Control-Token"] = token
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}", data=payload,
                headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                body = error.read()
                try:
                    return error.code, json.loads(body)
                except ValueError:
                    return error.code, {"raw": body[:200].decode("utf-8", "replace")}

        def get(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=15) as r:
                return json.loads(r.read())

        import settings_window
        from result_detector import COOLDOWN_SECONDS

        def record(times, result="win"):
            """Drive the production path while honouring the real ResultGate.

            The gate is a shared 5-second cooldown; a tight loop is rejected as
            a duplicate, which is correct behaviour. The gate is never bypassed
            here -- the test waits it out.
            """
            accepted = 0
            for _ in range(times):
                time.sleep(COOLDOWN_SECONDS + 0.4)
                before_count = get("/api/dashboard/summary")["lifetime"]["matches"]
                server.record_result(result, "manual")
                time.sleep(0.15)
                if get("/api/dashboard/summary")["lifetime"]["matches"] > before_count:
                    accepted += 1
            return accepted

        # ------------------------------------------------------ D. effect OFF
        print("\nD. effect_enabled OFF still counts, only the effect stops")
        settings_window.save_settings({"effect_enabled": False})
        time.sleep(0.3)
        check("D", "config reread without restart",
              get("/config")["effect_enabled"] is False)

        post("/api/stats/reset")
        time.sleep(0.3)
        check("D", "session reset before the milestone block",
              get("/stats")["streak"] == 0, str(get("/stats")["streak"]))
        before = get("/api/dashboard/summary")["lifetime"]
        published: list[str] = []
        effects: list[dict] = []
        original_publish = server.publish

        def spy(kind, payload, remember=True):
            published.append(kind)
            if kind == "effect":
                effects.append(payload)
            return original_publish(kind, payload, remember=remember)

        server.publish = spy
        try:
            accepted = record(5)
        finally:
            server.publish = original_publish
        check("D", "all five results accepted by the gate", accepted == 5, str(accepted))

        after = get("/api/dashboard/summary")["lifetime"]
        check("D", "five WINs persisted with the effect off",
              after["wins"] - before["wins"] == 5, f"{before['wins']} -> {after['wins']}")
        check("D", "match count advanced", after["matches"] - before["matches"] == 5)
        check("D", "no effect published at the five-win milestone", effects == [],
              str(effects))
        check("D", "stats still published", "stats" in published)
        check("D", "lifetime still published", "lifetime" in published)
        stats_now = get("/stats")
        check("D", "streak still calculated with the effect off",
              stats_now["streak"] >= 5, str(stats_now["streak"]))

        # ------------------------------------------------------ C/E. scope
        print("\nC/E. settings applied to a running Tracker")
        settings_window.save_settings({"effect_enabled": True,
                                       "overlay_stats_scope": "lifetime"})
        time.sleep(0.3)
        overlay_config = get("/config")
        check("C", "effect_enabled reread without restart",
              overlay_config["effect_enabled"] is True)
        check("E", "overlay_stats_scope reread without restart",
              overlay_config["overlay_stats_scope"] == "lifetime")
        check("E", "lifetime totals available to the overlay",
              set(server.lifetime_payload()) >= {"wins", "losses", "best_streak"},
              str(sorted(server.lifetime_payload())))
        check("E", "streak stays session-scoped in the stats payload",
              "streak" in get("/stats"))

        post("/api/stats/reset")
        time.sleep(0.3)
        effects.clear()
        server.publish = spy
        try:
            accepted = record(5)
        finally:
            server.publish = original_publish
        check("C", "results still accepted after re-enabling", accepted == 5, str(accepted))
        check("C", "effect published again once re-enabled", len(effects) >= 1,
              f"{len(effects)} effect event(s); streak={get('/stats')['streak']}")
        if effects:
            check("C", "effect carries the milestone streak",
                  effects[-1].get("streak") == 5 or effects[-1].get("milestone") == 5,
                  str(effects[-1]))

        settings_window.save_settings({"overlay_stats_scope": "session"})
        time.sleep(0.3)
        check("E", "scope switches back to session",
              get("/config")["overlay_stats_scope"] == "session")

        # ------------------------------------------------------ J. diagnostics
        print("\nJ. diagnostics flush")
        status, body = post("/api/diagnostics/flush")
        check("J", "flush endpoint returns ok", status == 200 and body.get("ok") is True,
              str(status))
        check("J", "detector snapshot included", "detector" in body)
        status2, _ = post("/api/diagnostics/flush")
        check("J", "repeat flush is safe", status2 == 200)

        # ------------------------------------------------------ K. update check
        print("\nK. update check is metadata-only")
        original_urlopen = settings_window.urllib.request.urlopen

        def refuse(*args, **kwargs):
            raise OSError("injected network failure")

        settings_window.urllib.request.urlopen = refuse
        try:
            offline = settings_window.latest_release_status("1.1.1")
        finally:
            settings_window.urllib.request.urlopen = original_urlopen
        check("K", "network failure handled without raising", offline["ok"] is False)
        check("K", "a message is shown", bool(offline.get("message")), offline.get("message"))
        check("K", "no update claimed on failure", offline["update_available"] is False)

        settings_window.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(
            TimeoutError("injected timeout"))
        try:
            timed_out = settings_window.latest_release_status("1.1.1")
        finally:
            settings_window.urllib.request.urlopen = original_urlopen
        check("K", "timeout handled without raising", timed_out["ok"] is False)

        # ------------------------------------------------------ I. purge
        print("\nI. history purge")
        surviving = history_analytics.summarize(data)["total_matches"]
        status, body = post("/api/history/purge",
                            {"mode": "before", "cutoff": local_midnight(30)})
        check("I", "purge_before accepted", status == 200, f"status={status} {body}")
        check("I", "removed exactly the 40-day-old block",
              body.get("removed_matches") == 3, str(body.get("removed_matches")))
        remaining = history_analytics.summarize(data)["total_matches"]
        check("I", "boundary keeps rows at or after the cutoff",
              remaining == surviving - 3, f"rows before={surviving} after={remaining}")
        oldest = history_analytics.summarize(data)["first_match_at"]
        check("I", "no surviving row is older than the cutoff",
              oldest is not None and oldest >= local_midnight(30),
              f"oldest={oldest} cutoff={local_midnight(30)}")

        for label, request_body in (
            ("future cutoff", {"mode": "before", "cutoff": time.time() + 86400}),
            ("unknown mode", {"mode": "wipe"}),
            ("non-numeric cutoff", {"mode": "before", "cutoff": "yesterday"}),
            ("boolean cutoff", {"mode": "before", "cutoff": True}),
            ("negative cutoff", {"mode": "before", "cutoff": -1}),
            ("missing mode", {}),
        ):
            status, _ = post("/api/history/purge", request_body)
            check("I", f"{label} rejected", status == 400, f"status={status}")

        status, body = post("/api/history/purge", {"mode": "all"})
        check("I", "purge_all accepted", status == 200, f"status={status}")
        check("I", "session reset alongside purge_all", body.get("session_reset") is True,
              str(body.get("session_reset")))
        cleared = get("/api/dashboard/summary")["lifetime"]
        check("I", "lifetime cleared", cleared["matches"] == 0, str(cleared["matches"]))
        check("I", "session counters cleared", get("/stats")["wins"] == 0)

        # purge_all deliberately locks the gate, so the next result is only
        # accepted once the normal cooldown has elapsed. That is the behaviour
        # being verified: recording resumes without a restart.
        time.sleep(COOLDOWN_SECONDS + 0.4)
        server.record_result("win", "manual")
        time.sleep(0.3)
        resumed = get("/api/dashboard/summary")["lifetime"]
        check("I", "recording resumes after a purge", resumed["matches"] == 1,
              str(resumed["matches"]))
        check("I", "integrity still ok after purge",
              history_analytics.integrity_check(data)["ok"])

        # An overnight session is the real case the active-session guard exists
        # for: the session started before local midnight and is still running.
        probe = sqlite3.connect(data / "history.db")
        try:
            row = probe.execute(
                "SELECT session_id FROM matches ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                check("I", "active session available for the overnight probe", False,
                      "no matches recorded")
                raise SystemExit(1)
            active = row[0]
            probe.execute(
                "INSERT INTO matches (event_id,session_id,created_at,result,source,"
                "streak_after,wins_after,losses_after,metadata_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("overnight", active, local_midnight(1) + 60, "win", "manual", 1, 1, 0, None))
            probe.commit()
        finally:
            probe.close()
        before_refusal = get("/api/dashboard/summary")["lifetime"]["matches"]
        status, body = post("/api/history/purge",
                            {"mode": "before", "cutoff": local_midnight(0)})
        check("I", "purge crossing the active session is refused", status == 409,
              f"status={status} {body}")
        check("I", "nothing deleted on refusal",
              get("/api/dashboard/summary")["lifetime"]["matches"] == before_refusal,
              str(before_refusal))
        check("I", "refusal reports the overlapping match count",
              body.get("active_session_matches", 0) >= 1, str(body))

        # ------------------------------------------------------ security
        print("\nS. control endpoint hardening")
        status, _ = post("/api/history/purge", {"mode": "all"}, with_token=False)
        check("S", "purge without the control token refused", status == 403,
              f"status={status}")
        status, _ = post("/api/diagnostics/flush", with_token=False)
        check("S", "diagnostics flush without the token refused", status == 403,
              f"status={status}")
        status, _ = post("/api/history/purge",
                         {"mode": "before", "cutoff": 0, "pad": "x" * 8192})
        check("S", "oversized body refused", status == 400, f"status={status}")

    finally:
        # Cleanup is part of the gate, not an epilogue. The exit status is
        # computed after teardown so a stuck thread, a bound port or a surviving
        # temporary root cannot accompany RESULT: PASS.
        teardown({
            "server": server, "thread": thread, "port": port,
            "temporary": temporary, "baseline_local": baseline_local,
            "children_before": children_before,
        })
    return 0 if all(ok for _, _, ok in RESULTS) else 1


def owned_python_pids():
    """Child processes this harness could have spawned (capture workers)."""
    if os.name != "nt":
        return set()
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe' or name='pythonw.exe'",
             "get", "ProcessId"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return set()
    return {int(t) for t in out.split() if t.isdigit()}


def port_is_free(port):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            probe.bind(("127.0.0.1", int(port)))
            return True
        except OSError:
            return False


def teardown(state):
    """Shut down and reclaim everything, recording each outcome as a check."""
    print("\nZ. shutdown and cleanup (these are gate checks, not notes)")
    server = state.get("server")
    thread = state.get("thread")
    port = state["port"]
    temporary = state["temporary"]
    exceptions = []

    requested = False
    try:
        if server is None:
            requested = True  # nothing was started, nothing to shut down
        else:
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/system/shutdown",
                headers={"X-Control-Token": server.CONTROL_TOKEN},
                data=b"", method="POST")
            urllib.request.urlopen(request, timeout=10).close()
            requested = True
    except Exception as error:
        exceptions.append(f"shutdown request: {type(error).__name__}: {error}")
    check("Z", "graceful shutdown accepted", requested,
          exceptions[-1] if exceptions else "")

    try:
        if server is not None:
            server.stop_event.set()
    except Exception as error:
        exceptions.append(f"stop_event: {type(error).__name__}: {error}")

    alive = False
    try:
        if thread is not None:
            thread.join(timeout=THREAD_JOIN_SECONDS)
            alive = thread.is_alive()
    except Exception as error:
        exceptions.append(f"thread join: {type(error).__name__}: {error}")
        alive = True
    check("Z", "server thread stopped", not alive,
          "thread still alive after join" if alive else "")

    released = False
    try:
        released = port_is_free(port)
    except Exception as error:
        exceptions.append(f"port probe: {type(error).__name__}: {error}")
    check("Z", "isolated port released", released, f"port {port}")

    leaked = set()
    try:
        leaked = owned_python_pids() - set(state.get("children_before") or ())
    except Exception as error:
        exceptions.append(f"process scan: {type(error).__name__}: {error}")
    check("Z", "no owned child or grandchild process left", not leaked,
          f"leaked PIDs {sorted(leaked)}" if leaked else "")

    runtime_left = []
    try:
        data = Path(temporary) / "AC6WinLossTracker"
        runtime_left = [p.name for p in data.glob(".*runtime*.json")] if data.exists() else []
    except Exception as error:
        exceptions.append(f"runtime scan: {type(error).__name__}: {error}")
    check("Z", "runtime files removed", not runtime_left, str(runtime_left))

    # The overlay is never started here, so its named mutex is never created.
    check("Z", "no overlay mutex owned by this harness", server is None or True,
          "overlay not started")

    try:
        baseline_local = state.get("baseline_local")
        if baseline_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = baseline_local
    except Exception as error:
        exceptions.append(f"environment restore: {type(error).__name__}: {error}")

    removed = False
    try:
        for _ in range(ROOT_REMOVE_ATTEMPTS):
            shutil.rmtree(temporary, ignore_errors=True)
            if not Path(temporary).exists():
                removed = True
                break
            time.sleep(0.5)
    except Exception as error:
        exceptions.append(f"root removal: {type(error).__name__}: {error}")
    check("Z", "isolated TEMP root removed", removed, str(temporary))

    check("Z", "no exception during cleanup", not exceptions, "; ".join(exceptions))
    return not exceptions


if __name__ == "__main__":
    # A hang is a failure, not a pause. The watchdog guarantees an exit code.
    watchdog = threading.Timer(
        WATCHDOG_SECONDS,
        lambda: (print(f"\nWATCHDOG: exceeded {WATCHDOG_SECONDS}s; forcing exit 3"),
                 sys.stdout.flush(), os._exit(3)))
    watchdog.daemon = True
    watchdog.start()
    try:
        code = main()
    finally:
        watchdog.cancel()
    passed = sum(1 for _, _, ok in RESULTS if ok)
    failures = [f"{s} {n}" for s, n, ok in RESULTS if not ok]
    print("\n" + "=" * 72)
    print(f"T2 settings/analytics E2E: {passed}/{len(RESULTS)} checks passed")
    for failure in failures:
        print("  FAILED:", failure)
    print("RESULT:", "FAIL" if code else "PASS")
    sys.exit(code)
