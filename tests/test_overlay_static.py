from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=(ROOT/'overlay.html').read_text(encoding='utf-8')
assert 'WIN ${s.wins}' in s and 'LOSE ${s.losses}' in s
assert '勝率 ${Number(s.win_rate).toFixed(1)}%' in s
assert '連勝 ${s.streak}' in s
assert '最高連勝 ${s.best_streak}' in s
assert '激アツ' in s and '超激アツ' in s and '覚醒ゾーン' in s
assert 'EventSource("/events")' in s
assert 'stats_health' in s and 'config_health' in s and 'detector' in s
print('stats-only overlay regression checks: OK')
