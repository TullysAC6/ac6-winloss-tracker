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
assert 'publish_stats_active(s)' in s
print("runtime control + stats health restore: OK")

assert 'status, level = "", 0' in s
assert 'status, level = "通常", 0' not in s
assert '"next_target"' not in s
assert '"wins_to_next"' not in s
print("v16 status threshold display: OK")
