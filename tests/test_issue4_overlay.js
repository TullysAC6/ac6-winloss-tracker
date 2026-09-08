const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const script = fs.readFileSync(path.join(__dirname, '..', 'overlay.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];

async function browser(start) {
  let now = start;
  const played = [], listeners = {};
  function element(id) {
    let classes = new Set();
    return {style: {}, textContent: '', id,
      get className() {return [...classes].join(' ');},
      set className(value) {classes = new Set(value.split(' ').filter(Boolean));},
      classList: {add(value) {classes.add(value); if (id === 'milestone' && value === 'show') played.push([...classes].find(x => x.startsWith('effect-')));}, remove(value) {classes.delete(value);}},
      querySelector() {return element('inner');}};
  }
  const elements = new Map();
  const context = {document: {getElementById(id) {if (!elements.has(id)) elements.set(id, element(id)); return elements.get(id);}},
    Date: {now: () => now}, fetch: async () => ({ok: true, json: async () => ({stats_enabled: true})}),
    EventSource: class {addEventListener(name, callback) {listeners[name] = callback;}},
    setInterval() {}, setTimeout() {return 1;}, clearTimeout() {}, console};
  await vm.runInNewContext(script, context);
  return {played, emit(n, created, id = `effect-${n}`) {listeners.effect({data: JSON.stringify({effect: 'milestone', effect_id: id, milestone: n, created_at_ms: created})});}, time(value) {now = value;}};
}

(async () => {
  const b = await browser(99000);
  b.time(100000);
  b.emit(5, 100000); b.emit(5, 100000); // live delivery plus reconnect snapshot
  assert.deepEqual(b.played, ['effect-5'], 'duplicate replay restarted banner');
  b.time(100100); b.emit(10, 100100); b.emit(5, 100000);
  assert.deepEqual(b.played, ['effect-5', 'effect-10']);
  b.time(110000); b.emit(15, 100100);
  assert.equal(b.played.length, 2, 'expired effect played');
  const restarted = await browser(110000);
  restarted.emit(10, 109500);
  assert.equal(restarted.played.length, 0, 'pre-restart effect resurrected');
  const reconnect = await browser(99000);
  reconnect.time(102900); reconnect.emit(5, 100000);
  assert.deepEqual(reconnect.played, ['effect-5']);
  console.log('Issue #4 overlay.html executable handler regression: OK');
})().catch(error => {console.error(error); process.exitCode = 1;});
