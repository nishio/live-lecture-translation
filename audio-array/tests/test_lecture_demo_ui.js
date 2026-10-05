'use strict';
// Controller tests use synthetic audio and deferred reads, with no real playback or API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {formatTime, nextPosition, audioPosition} = require('../lecture-demo/demo.js');
const {createApp} = require('../lecture-dashboard/app.js');
const {Element, fixtureDOM} = require('./lecture_ui_test_dom');
const flush = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };

class FakeAudio extends Element {
  constructor() { super(); this.currentTime = 0; this.paused = true; this.playCalls = []; this.muted = false; }
  play() {
    return new Promise((resolve, reject) => this.playCalls.push({resolve: () => {this.paused = false; resolve();}, reject}));
  }
  pause() { this.paused = true; }
  ended() { this.paused = true; this.handlers.ended(); }
}
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'lecture-dashboard/index.html'), 'utf8') + fs.readFileSync(path.join(root, 'lecture-demo/toolbar.html'), 'utf8');
function fixture(overrides = {}, {realRenderer = false} = {}) {
  const doc = fixtureDOM(html), nodes = doc.nodes;
  const audio = new FakeAudio(); nodes.set('demo-audio', audio); const setup = new Element();
  const $ = id => {assert(nodes.has(id), `Missing real DOM element: ${id}`); return nodes.get(id);};
  doc.querySelector = selector => {assert.equal(selector, '.setup'); return setup;};
  let clock = 0, tick, renderer, passedTransport;
  const requests = [];
  const meta = {started_at: 1000, duration_seconds: 35, audio_seconds: 30, initial_seconds: 0, audio_start_seconds: 0, audio_url: null, ...overrides};
  function snapshot(at) {
    const schedule = (interval, clock = 'wall') => ({state: 'waiting', reason: clock === 'audio' ? 'recording' : 'interval',
      remaining_seconds: interval - at % interval, interval_seconds: interval, wait_seconds: interval, clock});
    return {schema_version: 1, updated_at: meta.started_at + at, session: {id: 'synthetic-replay', started_at: meta.started_at, source_kind: 'replay'},
      capture: {state: 'completed', audio_seconds: Math.min(at, 30)}, demo: {cursor_seconds: at}, headline: at >= 20 ? 'published' : 'empty', at,
      asr: {state: 'waiting', through_seconds: 0, queue_seconds: 0, schedule: schedule(15, 'audio')},
      translation: {enabled: true, state: 'waiting', blocks: [], schedule: schedule(60)},
      analysis: {state: 'waiting', through_seconds: 0, provider: 'openai', result: null, schedule: schedule(120)},
      lines: at >= 2 ? [{id: 'synthetic-line', start_seconds: 0, end_seconds: at, text: `Synthetic speech ${Math.floor(at)}`, language: 'en'}] : []};
  }
  const context = {document: doc, history: {replaceState() {}}, location: {pathname: '/'}, performance: {now: () => clock},
    setInterval(fn) {tick = fn;}, window: {LectureDemoRenderer: {createApp(_doc, transport, options) {
      passedTransport = transport;
      assert.equal(options.autoStart, false); assert.equal(options.storage, null);
      if (realRenderer) {renderer = createApp(_doc, transport, options); return renderer;}
      renderer = {frozen: false, accepted: [], acceptState(value) {this.accepted.push(value.at); this.state = value; if (!this.frozen) this.displayed = value;}, goLive() {this.frozen = false; this.displayed = this.state;}};
      return renderer;
    }}}, fetch: async (url, init) => {
      assert.equal(init.method, undefined, 'The playback controller only reads');
      if (url === '/api/demo') return {ok: true, json: async () => meta};
      const at = Number(new URL(url, 'http://127.0.0.1').searchParams.get('at'));
      return new Promise(resolve => requests.push({at,
        resolve: () => resolve({ok: true, json: async () => snapshot(at)}),
        fail: () => resolve({ok: false, json: async () => ({error: 'synthetic read failure'})})}));
    }};
  vm.runInNewContext(fs.readFileSync(path.join(root, 'lecture-demo/demo.js'), 'utf8'), context);
  return {$, audio, setup, requests, meta, get renderer() {return renderer;},
    get transport() {return passedTransport;}, tick(ms) {clock = ms; tick();}, setClock(ms) {clock = ms;},
    async ready() {await flush(); assert.equal(requests[0].at, meta.initial_seconds || 0); requests.shift().resolve(); await flush();},
    async respond() {assert(requests.length); const value = requests[0].at; requests.shift().resolve(); await flush(); return value;},
    seek(value) {$('demo-slider').value = value; return $('demo-slider').handlers.input();}};
}

async function run() {
  assert.equal(formatTime(322.85), '5:22');
  assert.equal(nextPosition(20, 1, 1, 30), 21); assert.equal(nextPosition(24, 40, 1, 30), 30);
  assert.equal(audioPosition(12, 2, 35), 14); assert.equal(audioPosition(40, 2, 35), 35);
  assert.doesNotMatch(fs.readFileSync(path.join(root, 'lecture-demo/toolbar.html'), 'utf8'), /demo-cost|demo-speed|demo-progress|demo-landmarks|費用|音声再生なし/);
  assert.match(html, /id="demo-slider"[^>]*step="any"/, 'The native range must not round its maximum below the final publication');
  const fractionalEnd = fixture({duration_seconds: 349.60897612571716}); await fractionalEnd.ready();
  fractionalEnd.seek(String(fractionalEnd.meta.duration_seconds));
  assert.equal(fractionalEnd.requests[0].at, fractionalEnd.meta.duration_seconds, 'Seeking to the range maximum preserves the exact fractional endpoint');
  await fractionalEnd.respond();
  assert.equal(fractionalEnd.renderer.displayed.at, fractionalEnd.meta.duration_seconds, 'The final frame is reachable without starting playback');

  const silent = fixture(); await silent.ready();
  assert.equal(silent.renderer.displayed.headline, 'empty');
  assert.equal(silent.$('demo-play').textContent, '▶ 再生', 'Playback always starts paused at the beginning');
  assert.equal(silent.setup.inert, true);
  await assert.rejects(() => silent.transport('/api/start', {method: 'POST'}), /処理要求を送信しません/);
  assert.equal(silent.audio.playCalls.length, 0, 'Opening the page never plays audio');
  await silent.$('demo-play').click(); silent.tick(1000);
  assert.equal(silent.requests[0].at, 1, 'No-audio playback advances at exactly 1x');
  silent.tick(2000); silent.tick(3000);
  assert.equal(silent.requests.length, 1, 'Slow reads are coalesced instead of accumulating requests');
  await silent.respond(); assert.equal(silent.renderer.displayed.at, 1, 'A slow response still publishes a frame');
  silent.tick(3300); assert.equal(silent.requests[0].at, 3.3); await silent.respond();
  await silent.$('demo-play').click(); silent.tick(8000); assert.equal(Number(silent.$('demo-slider').value), 3.3, 'Pause holds the playback clock');

  silent.renderer.frozen = true;
  silent.seek(25); silent.seek(0);
  assert.equal(silent.requests.length, 1);
  await silent.respond(); assert.equal(silent.renderer.displayed.at, 3.3, 'An obsolete seek cannot publish a future card');
  assert.equal(silent.requests.length, 1); await silent.respond();
  assert.equal(silent.renderer.frozen, false); assert.equal(silent.renderer.displayed.headline, 'empty');
  silent.seek(34); await silent.respond(); await silent.$('demo-play').click();
  silent.tick(8500); assert.equal(silent.requests[0].at, 34.5);
  silent.tick(10000); assert.equal(silent.$('demo-play').attributes['aria-pressed'], 'false');
  await silent.respond(); silent.tick(10300); assert.equal(silent.requests[0].at, 35); await silent.respond();
  assert.equal(silent.renderer.displayed.at, 35, 'The final publication is fetched after an earlier request completes');
  silent.$('demo-restart').click(); await silent.respond();
  assert.equal(silent.renderer.displayed.headline, 'empty'); assert.equal(silent.$('demo-play').attributes['aria-pressed'], 'false');

  const sound = fixture({audio_url: '/audio.wav'}); await sound.ready();
  assert.equal(sound.audio.src, '/audio.wav'); assert.equal(sound.audio.playbackRate, 1);
  const pendingPlay = sound.$('demo-play').click();
  assert.equal(sound.$('demo-play').attributes['aria-pressed'], 'false', 'A pending play promise is not reported as playing');
  sound.tick(1000); assert.equal(Number(sound.$('demo-slider').value), 0);
  sound.audio.playCalls.shift().resolve(); await pendingPlay;
  sound.audio.currentTime = 4; sound.tick(1200);
  assert.equal(sound.requests[0].at, 4, 'Audio time, not elapsed wall time, controls the display'); await sound.respond();
  sound.tick(2200); assert.equal(Number(sound.$('demo-slider').value), 4, 'A stalled audio clock cannot advance the transcript');
  await sound.$('demo-play').click(); assert.equal(sound.audio.paused, true);
  sound.seek(20); await sound.respond(); assert.equal(sound.audio.currentTime, 20, 'Paused seeking moves audio and text together');
  sound.renderer.frozen = true; sound.$('demo-restart').click(); await sound.respond();
  assert.equal(sound.audio.currentTime, 0); assert.equal(sound.renderer.frozen, false); assert.equal(sound.renderer.displayed.headline, 'empty');

  sound.seek(29); await sound.respond();
  const resumed = sound.$('demo-play').click(); sound.audio.playCalls.shift().resolve(); await resumed;
  sound.audio.currentTime = 30; sound.setClock(3000); sound.audio.ended(); await sound.respond();
  assert.equal(sound.$('demo-play').attributes['aria-pressed'], 'true', 'Audio end keeps the final processing timeline running');
  sound.tick(4500); assert.equal(sound.requests[0].at, 31.5); await sound.respond();
  await sound.$('demo-play').click(); sound.tick(9000);
  assert.equal(Number(sound.$('demo-slider').value), 31.5, 'The silent tail can be paused');
  const oldPlayCount = sound.audio.playCalls.length;
  await sound.$('demo-play').click(); assert.equal(sound.audio.playCalls.length, oldPlayCount, 'Resuming after audio ends does not replay it');
  sound.tick(12500); assert.equal(sound.requests[0].at, 35); await sound.respond();
  assert.equal(sound.$('demo-play').attributes['aria-pressed'], 'false');

  const denied = fixture({audio_url: '/audio.wav'}); await denied.ready();
  const denial = denied.$('demo-play').click(); denied.audio.playCalls.shift().reject(new Error('blocked')); await denial;
  assert.equal(denied.$('demo-play').attributes['aria-pressed'], 'false'); assert.match(denied.$('demo-error').textContent, /再試行/);
  assert.equal(denied.audio.paused, true);
  const cancellation = denied.$('demo-play').click();
  denied.seek(10); await denied.respond(); denied.audio.playCalls.shift().resolve(); await cancellation;
  assert.equal(denied.audio.paused, true, 'An old play promise cannot restart audio after a seek');
  assert.equal(denied.$('demo-play').attributes['aria-pressed'], 'false');
  const retry = denied.$('demo-play').click(); denied.audio.playCalls.shift().resolve(); await retry;
  denied.audio.currentTime = 12; denied.tick(1000); denied.requests.shift().fail(); await flush();
  assert.equal(denied.audio.paused, true); assert.equal(denied.$('demo-play').attributes['aria-pressed'], 'false');
  assert.match(denied.$('demo-error').textContent, /表示を取得できません/);

  const offset = fixture({audio_url: '/audio.wav', audio_start_seconds: 2, duration_seconds: 37}); await offset.ready();
  const priming = offset.$('demo-play').click(); assert.equal(offset.audio.muted, true);
  offset.audio.playCalls.shift().resolve(); await priming; assert.equal(offset.audio.paused, true); assert.equal(offset.audio.muted, false);
  offset.tick(1000); assert.equal(offset.requests[0].at, 1); await offset.respond();
  offset.tick(2100); assert.equal(Number(offset.$('demo-slider').value), 2); assert.equal(offset.audio.playCalls.length, 1);
  await offset.respond(); offset.audio.playCalls.shift().resolve(); await flush();
  offset.audio.currentTime = 3; offset.tick(2500); assert.equal(offset.requests[0].at, 5); await offset.respond();
  offset.seek(1); await offset.respond(); assert.equal(offset.audio.currentTime, 0, 'Seeking into the startup gap rewinds audio');

  // Use the production renderer here: a controller-only stub cannot catch stopped circles.
  const circles = fixture({audio_url: '/audio.wav'}, {realRenderer: true}); await circles.ready();
  const kinds = ['asr', 'translation', 'analysis'];
  const degrees = () => kinds.map(kind => Number(circles.$(`${kind}-schedule-ring`).style.background.match(/ ([\d.]+)deg,/)[1]));
  assert.deepEqual(degrees(), [360, 360, 360]);
  const circlePlayback = circles.$('demo-play').click(); circles.audio.playCalls.shift().resolve(); await circlePlayback;
  circles.audio.currentTime = 5; circles.tick(5000); await circles.respond();
  assert.deepEqual(degrees(), [240, 330, 345], 'Audio advancement reaches all three rendered countdown circles');
  assert.equal(circles.$('asr-schedule-compact').textContent, '10秒');
  assert.match(circles.$('transcript').textContent, /Synthetic speech 5/);
  circles.renderer.freeze();
  circles.audio.currentTime = 6; circles.tick(6000); await circles.respond();
  assert.deepEqual(degrees(), [216, 324, 342], 'Holding reading content does not hold the playback timers');
  assert.match(circles.$('transcript').textContent, /Synthetic speech 5/);
  await circles.$('demo-play').click(); circles.tick(26000);
  assert.deepEqual(degrees(), [216, 324, 342], 'Paused playback does not spend countdown time');
  assert.equal(circles.requests.length, 0);
  circles.seek(10); await circles.respond();
  assert.equal(degrees()[0], 120, 'A paused seek sets the circle to the selected audio position');
  assert.match(circles.$('transcript').textContent, /Synthetic speech 10/, 'Seeking releases a held reading view');
  circles.$('demo-restart').click(); await circles.respond();
  assert.deepEqual(degrees(), [360, 360, 360], 'Restart restores all countdowns to their beginning');
  circles.renderer.dispose();
  console.log('Lecture demo UI checks passed (1x playback, audio synchronization, rendered countdowns, paused seek, slow reads, stale responses, final publications).');
}
run().catch(error => {console.error(error); process.exitCode = 1;});
