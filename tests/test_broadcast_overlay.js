// UI-1B: the Broadcast Overlay's own script, executed against a small DOM double.
// Run by tests/test_broadcast_overlay.py (BroadcastDomTests).
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const html = fs.readFileSync(path.join(__dirname, '..', 'overlay.html'), 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert.equal(scripts.length, 1, 'overlay.html keeps exactly one inline script');
const script = scripts[0][1];

function element(name) {
  const children = new Map();
  let classes = new Set();
  return {
    name, style: {}, textContent: '',
    get className() { return [...classes].join(' '); },
    set className(value) { classes = new Set(String(value).split(' ').filter(Boolean)); },
    classList: {
      add(value) { classes.add(value); },
      remove(value) { classes.delete(value); },
      contains(value) { return classes.has(value); },
    },
    querySelector(selector) {
      if (!children.has(selector)) children.set(selector, element(selector));
      return children.get(selector);
    },
  };
}

async function overlay(config, start = 100000) {
  const elements = new Map();
  const listeners = {};
  const intervals = [];
  let current = config;
  let now = start;
  const context = {
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element(id));
        return elements.get(id);
      },
    },
    Date: {now: () => now},
    fetch: async () => ({ok: true, json: async () => current}),
    EventSource: class { addEventListener(name, callback) { listeners[name] = callback; } },
    setInterval(callback, ms) { intervals.push({callback, ms}); return intervals.length; },
    setTimeout() { return 1; },
    clearTimeout() {},
    console: {error() {}, log() {}},
  };
  await vm.runInNewContext(script, context);
  const panel = elements.get('stats-panel');
  return {
    panel,
    part: selector => panel.querySelector(selector),
    milestone: elements.get('milestone'),
    intervals,
    emit(name, data) { listeners[name]({data: JSON.stringify(data)}); },
    async poll(next) { current = next; await intervals[0].callback(); },
    time(value) { now = value; },
  };
}

const STATS = {wins: 12, losses: 7, win_rate: 63.2, streak: 3, best_streak: 6, status: 'アツい', status_level: 1};
const BASE = {stats_enabled: true, effect_enabled: true, overlay_stats_scope: 'session', config_health: {status: 'active'}};

function visibleBest(o) { return o.part('.sub').style.display !== 'none'; }

(async () => {
  // A server without the field (UI-1A and older): 最高連勝 stays shown.
  let o = await overlay(BASE);
  o.emit('stats', STATS);
  assert.equal(o.panel.style.display, 'block');
  assert.equal(o.part('.record').textContent, 'WIN 12　LOSE 7', 'existing copy');
  assert.equal(o.part('.rate').textContent, '　勝率 63.2%');
  assert.equal(o.part('.streak').textContent, '　連勝 3');
  assert.equal(o.part('.status').textContent, '　アツい');
  assert.equal(o.part('.sub').textContent, '最高連勝 6');
  assert.ok(visibleBest(o), 'missing field = shown');
  assert.equal(o.panel.className, 'hot-1');
  assert.equal(o.intervals.length, 1, 'still one /config poll');
  assert.equal(o.intervals[0].ms, 3000, 'at the existing 3 s cadence');

  // Explicit ON, then OFF and back ON through the ordinary poll, without a new
  // stats event and without a reload.
  o = await overlay({...BASE, broadcast_show_best_streak: true});
  o.emit('stats', STATS);
  assert.ok(visibleBest(o));
  assert.equal(o.part('.sub').textContent, '最高連勝 6');
  await o.poll({...BASE, broadcast_show_best_streak: false});
  assert.equal(o.part('.sub').style.display, 'none', 'OFF hides the line');
  assert.equal(o.part('.sub').textContent, '', 'and leaves no stale text');
  assert.equal(o.part('.record').textContent, 'WIN 12　LOSE 7', 'nothing else changes');
  assert.equal(o.part('.status').textContent, '　アツい');
  await o.poll({...BASE, broadcast_show_best_streak: true});
  assert.ok(visibleBest(o), 'ON restores it');
  assert.equal(o.part('.sub').textContent, '最高連勝 6');

  // OFF from the first render; later stats keep it hidden.
  o = await overlay({...BASE, broadcast_show_best_streak: false});
  o.emit('stats', STATS);
  o.emit('stats', {...STATS, wins: 13, streak: 4, best_streak: 6});
  assert.equal(o.part('.sub').style.display, 'none');
  assert.equal(o.part('.record').textContent, 'WIN 13　LOSE 7');

  // Only an explicit false hides it; anything else fails safe to shown.
  for (const value of ['false', 0, null, undefined, 'no']) {
    o = await overlay({...BASE, broadcast_show_best_streak: value});
    o.emit('stats', STATS);
    assert.ok(visibleBest(o), `value ${String(value)} keeps 最高連勝 shown`);
  }

  // Lifetime scope: totals and BEST from lifetime, streak and status from the session.
  o = await overlay({...BASE, overlay_stats_scope: 'lifetime', broadcast_show_best_streak: true});
  o.emit('stats', STATS);
  o.emit('lifetime', {wins: 190, losses: 83, win_rate: 69.6, best_streak: 8});
  assert.equal(o.part('.record').textContent, 'WIN 190　LOSE 83');
  assert.equal(o.part('.rate').textContent, '　勝率 69.6%');
  assert.equal(o.part('.streak').textContent, '　連勝 3');
  assert.equal(o.part('.sub').textContent, '累計　最高連勝 8');
  await o.poll({...BASE, overlay_stats_scope: 'lifetime', broadcast_show_best_streak: false});
  assert.equal(o.part('.sub').style.display, 'none');
  await o.poll({...BASE, overlay_stats_scope: 'session', broadcast_show_best_streak: true});
  assert.equal(o.part('.record').textContent, 'WIN 12　LOSE 7');
  assert.equal(o.part('.sub').textContent, '最高連勝 6');

  // The Player streak-status preference is not the Broadcast's: it never
  // hides BEST or the status here.
  for (const player of [true, false]) {
    for (const best of [true, false]) {
      o = await overlay({...BASE, player_streak_status_enabled: player, broadcast_show_best_streak: best});
      o.emit('stats', STATS);
      assert.equal(o.part('.status').textContent, '　アツい', 'status follows the stats only');
      assert.equal(visibleBest(o), best, 'BEST follows the Broadcast preference only');
    }
  }

  // Status levels still map to the same classes, and a level-0 streak shows no status.
  o = await overlay(BASE);
  for (const [streak, status, level] of [[2, '', 0], [3, 'アツい', 1], [5, '激アツ', 2], [10, '超激アツ', 3],
                                          [15, '覚醒ゾーン', 4], [20, 'RUSH継続中', 5]]) {
    o.emit('stats', {...STATS, streak, status, status_level: level});
    assert.equal(o.panel.className, level ? `hot-${level}` : '');
    assert.equal(o.part('.status').textContent, level ? `　${status}` : '');
  }

  // Stats disabled hides the whole panel, whatever the BEST preference.
  o = await overlay({...BASE, stats_enabled: false, broadcast_show_best_streak: true});
  o.emit('stats', STATS);
  assert.equal(o.panel.style.display, 'none');

  // Milestones: the same map (including 25) behind the same freshness gate.
  const expected = {5: '5連勝　激アツ!!', 10: '10連勝　超激アツ!!', 15: '15連勝　覚醒ゾーン突入',
    20: '20連勝　RUSH突入!!', 25: '25連勝　RUSH継続!!', 30: '30連勝!!', 35: '35連勝!!', 40: '40連勝!!',
    45: '45連勝!!', 50: '50連勝　LEGEND'};
  for (const best of [true, false]) {
    o = await overlay({...BASE, broadcast_show_best_streak: best}, 100000);
    let now = 100000;
    for (const [n, text] of Object.entries(expected)) {
      now += 10;
      o.time(now);
      o.emit('effect', {effect: 'milestone', effect_id: `e${n}`, milestone: Number(n), created_at_ms: now});
      assert.equal(o.milestone.querySelector('.inner').textContent, text);
      assert.ok(o.milestone.classList.contains(`effect-${n}`));
    }
    o.time(now + 3000);
    o.emit('effect', {effect: 'milestone', effect_id: 'late', milestone: 5, created_at_ms: now});
    assert.ok(!o.milestone.classList.contains('effect-5'), 'an expired effect is still refused');
  }

  console.log('UI-1B Broadcast Overlay executable behaviour: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
