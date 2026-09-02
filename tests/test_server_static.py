from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=(ROOT/'server.py').read_text(encoding='utf-8')

assert 'client = None' in s
assert 'if client is not None:' in s and 'event_bus.unregister(client)' in s
assert '}, remember=False)' in s[s.index('if milestone:'):s.index('print(',s.index('if milestone:'))]
assert 'config_health' in s
print('server SSE/effect/config-health static checks: OK')

assert '"アツい"' in s
assert '"前兆中"' not in s

assert 'RUNTIME_PATH = DATA_ROOT / ".runtime.json"' in s
assert 'write_runtime_file(server.server_address[1])' in s
assert 'if path == "/api/system/shutdown":' in s
assert 'threading.Thread(target=self.server.shutdown, daemon=True).start()' in s
assert 'secrets.compare_digest(str(supplied_token), CONTROL_TOKEN)' in s
assert 'if path == "/health":' in s
assert 'def lifecycle_health(' in s
assert 'OVERLAY_HEARTBEAT_MAX_AGE = 5.0' in s
assert 'SSE_KEEPALIVE_SECONDS = 2.0' in s
assert 'SSE_KEEPALIVE = b": keepalive\\n\\n"' in s
assert 'publish_stats_active(s)' in s
print("runtime control + stats health restore: OK")

assert 'for n in range(5, 51, 5):' in s
assert 'event_id = secrets.token_urlsafe(18)' in s
assert '"effect_id": event_id' in s
assert '"tier": min(10, milestone // 5)' in s
environment_pos = s.index('inspect_startup_environment()', s.index('def main('))
bind_pos = s.index('server = QuietThreadingHTTPServer', environment_pos)
preflight_pos = s.index('preflight_history_schema()', bind_pos)
filesystem_pos = s.index('validate_owned_filesystem()', preflight_pos)
reset_pos = s.index('stats.reset()', filesystem_pos)
detector_pos = s.index('threading.Thread(target=detector_supervisor', reset_pos)
history_pos = s.index('store = HistoryStore(DATA_ROOT)', reset_pos)
session_pos = s.index('store.start_session()', history_pos)
runtime_pos = s.index('write_runtime_file(server.server_address[1])', detector_pos)
ready_pos = s.index('on_ready()', runtime_pos)
assert environment_pos < bind_pos < preflight_pos < filesystem_pos < reset_pos
assert reset_pos < history_pos < session_pos < detector_pos < runtime_pos < ready_pos
print("session reset ordering and 5..50 milestone source: OK")

assert 'store.record_result(event_id, result, source, s)' in s
assert s.index('s = stats.add(result, source)') < s.index('store.record_result(event_id, result, source, s)')
assert 'store.create_match_context(' in s
assert s.index('store.record_result(event_id, result, source, s)') < s.index('store.create_match_context(')
assert '"match_context_error"' in s
assert 'if path == "/api/dashboard/summary":' in s
assert 'store.reset_session()' in s
print("history accepted-result flow / dashboard API / session reset: OK")

assert "class QuietThreadingHTTPServer(ThreadingHTTPServer):" in s
assert "ConnectionAbortedError" in s
assert "super().handle_error(request, client_address)" in s
print("benign localhost disconnect suppression: OK")

assert 'status, level = "", 0' in s
assert 'status, level = "通常", 0' not in s
assert '"next_target"' not in s
assert '"wins_to_next"' not in s
print("v16 status threshold display: OK")
