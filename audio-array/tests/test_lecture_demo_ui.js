'use strict';
// Controller tests with deferred reads: no browser, listener, recording or API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {formatTime, nextPosition} = require('../lecture-demo/demo.js');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

class Element {
  constructor() { this.value = ''; this.disabled = false; this.children = []; this.handlers = {}; this.attributes = {}; this.dataset = {}; }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return this.text || ''; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  appendChild(node) { this.children.push(node); }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  click() { this.handlers.click?.(); }
}

async function run() {
  assert.equal(formatTime(322.85), '5:22');
  assert.equal(nextPosition(20, 1, 4, 30), 24);
  assert.equal(nextPosition(24, 4, 4, 30), 30);
  const nodes = new Map();
  const $ = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const doc = {getElementById: $, createElement: () => new Element(), querySelector: () => $('setup'), addEventListener() {}};
  let clock = 0, tick, renderer, passedTransport;
  const requests = [];
  const meta = {started_at: 1000, duration_seconds: 30, initial_seconds: 20, source_label: 'Saved lecture', model: 'sol',
    configuration: {analysis_interval: 90}, line_count: 2, analysis_count: 2, cache_hits: 1, recorded_api_usd: .02,
    timing_note: 'publication', landmarks: [{seconds: 0, label: 'start'}, {seconds: 20, label: 'rich'}, {seconds: 30, label: 'end'}]};
  function snapshot(at) { return {capture: {audio_seconds: at}, demo: {audio_input_ended: at === 30, published_lines: at >= 20 ? 2 : 0,
    published_analyses: at >= 20 ? 2 : 0, untranslated_lines: 0}, headline: at >= 20 ? 'future' : 'empty', at}; }
  const context = {document: doc, history: {replaceState() {}}, location: {pathname: '/'}, performance: {now: () => clock},
    setInterval(fn) {tick = fn;}, window: {LectureDemoRenderer: {createApp(_doc, transport, options) {
      passedTransport = transport;
      assert.equal(options.autoStart, false); assert.equal(options.storage, null);
      renderer = {frozen: false, acceptState(value) {this.state = value; if (!this.frozen) this.displayed = value;}, goLive() {this.frozen = false; this.displayed = this.state;}};
      return renderer;
    }}}, fetch: async (url, init) => {
      assert.equal(init.method, undefined, 'Only GET reads are sent');
      if (url === '/api/demo') return {ok: true, json: async () => meta};
      const at = Number(new URL(url, 'http://127.0.0.1').searchParams.get('at'));
      return new Promise(resolve => requests.push({at, resolve: () => resolve({ok: true, json: async () => snapshot(at)})}));
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../lecture-demo/demo.js'), 'utf8'), context);
  await flush(); assert.equal(requests[0].at, 20); requests.shift().resolve(); await flush();
  assert.equal(renderer.displayed.headline, 'future');
  assert.equal($('demo-play').textContent, '▶ 表示を再生', 'Initial rich state is paused');
  assert.equal($('setup').inert, true);
  await assert.rejects(() => passedTransport('/api/start', {method: 'POST'}), /操作要求を送信しません/);

  renderer.frozen = true;
  $('demo-slider').value = 0; $('demo-slider').handlers.input();
  requests.shift().resolve(); await flush();
  assert.equal(renderer.frozen, false, 'Seeking clears source/history reading freeze');
  assert.equal(renderer.displayed.headline, 'empty', 'No future card survives rewind');

  $('demo-slider').value = 25; $('demo-slider').handlers.input();
  $('demo-slider').value = 0; $('demo-slider').handlers.input();
  assert.equal(requests.length, 1, 'Only one fetch is in flight; the latest seek is queued');
  assert.match($('demo-position').textContent, /^0:00/, 'Applied time stays with the shown card while seeking');
  assert.match($('demo-loading').textContent, /移動中/);
  const old = requests.shift(); old.resolve(); await flush();
  assert.equal(renderer.displayed.at, 0, 'Obsolete in-flight response is discarded');
  assert.equal(requests.length, 1); requests.shift().resolve(); await flush();
  assert.equal(renderer.displayed.at, 0, 'An older response cannot overwrite a later rewind');

  $('demo-landmarks').children[1].click(); requests.shift().resolve(); await flush();
  $('demo-play').click(); clock = 1000; tick();
  assert.equal(requests[0].at, 24);
  clock = 3000; tick();
  assert.equal($('demo-play').textContent, '▶ 表示を再生');
  requests.shift().resolve(); await flush();
  clock = 3300; tick();
  assert.equal(requests[0].at, 30, 'Final frame is fetched even if previous request was in flight at playback end');
  requests.shift().resolve(); await flush(); assert.equal(renderer.displayed.at, 30);
  console.log('Lecture demo UI checks passed (paused start, read-only transport, rewind freeze, stale responses, final frame).');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
