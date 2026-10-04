// CPU-only DOM and control regressions. Browser layout and microphone are separate checks.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {formatTime, ageText, isFresh, capturePresentation, getControlState, translationMap, createApp} = require('../lecture-dashboard/app.js');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this._text = ''; this.value = '';
    this.hidden = false; this.disabled = false; this.dataset = {}; this.style = {}; this.attributes = {};
    this.handlers = {}; this.scrollTop = 0; this.scrollHeight = 300; this.clientHeight = 240;
    this.classes = new Set(); this.classList = {toggle: (name, value) => value ? this.classes.add(name) : this.classes.delete(name)};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw new Error('Untrusted content must not be rendered as HTML'); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  click() { return this.handlers.click?.(); }
  scrollIntoView() { this.scrolled = true; }
  focus() { this.focused = true; if (this.ownerDocument) this.ownerDocument.activeElement = this; }
}
function firstButton(node) {
  if (node.tagName === 'BUTTON') return node;
  for (const child of node.children) { const button = firstButton(child); if (button) return button; }
  return null;
}
function fixtureDOM() {
  const html = fs.readFileSync(path.join(__dirname, '../lecture-dashboard/index.html'), 'utf8');
  const nodes = new Map([...html.matchAll(/<(\w+)\b[^>]*\bid="([^"]+)"/g)].map(match => [match[2], new Element(match[1])]));
  nodes.get('language-select').value = 'auto'; nodes.get('provider-select').value = 'local';
  const doc = {nodes, activeElement: null, getElementById: id => { assert(nodes.has(id), `Unknown DOM element: ${id}`); return nodes.get(id); }, createElement: tag => { const node = new Element(tag); node.ownerDocument = doc; return node; }};
  for (const node of nodes.values()) node.ownerDocument = doc;
  return doc;
}
let clock = 1000;
function snapshot(overrides = {}) {
  return {schema_version: 1, updated_at: clock, session: null,
    capture: {state: 'idle', audio_seconds: 0}, asr: {state: 'idle', through_seconds: 0, queue_seconds: 0},
    analysis: {state: 'idle', through_seconds: 0, provider: 'local', result: null}, lines: [], ...overrides};
}
const response = payload => ({ok: true, status: 200, json: async () => payload});
const fixtures = [];
function appFixture(options = {}) {
  const dom = fixtureDOM(); const calls = []; let serverState = snapshot(); let rejectState = false; let postHandler = null;
  let devices = [{id: '7', name: 'USB audio'}, {id: '1', name: 'MacBook Proのマイク'}];
  let deviceFailure = null;
  const app = createApp(dom, async (url, init) => {
    calls.push({url, init});
    if (init.method === 'POST') return postHandler ? postHandler(url, init) : response({ok: true});
    if (url === '/api/devices') { if (deviceFailure) throw deviceFailure; return response({devices}); }
    if (rejectState) throw new Error('connection lost');
    return response(serverState);
  }, {autoStart: false, now: () => clock, ...options});
  const fixture = {app, dom, calls, $: id => dom.getElementById(id),
    state(value) {serverState = value;}, rejectState(value) {rejectState = value;}, post(handler) {postHandler = handler;}, devices(value) {devices = value;}, failDevices(error) {deviceFailure = error;}};
  fixtures.push(fixture); return fixture;
}

async function run() {
  assert.equal(formatTime(3661.9), '1:01:01'); assert.equal(formatTime(null), '—'); assert.equal(formatTime(-1), '0:00');
  assert.equal(ageText(999, 1000), '1秒前'); assert.equal(ageText(null, 1000), '時刻不明');
  assert.equal(isFresh(snapshot({updated_at: 950}), true, clock), false);
  assert.equal(capturePresentation(snapshot({capture: {state: 'recording', last_audio_at: 999}}), false, clock).label, '不明');
  assert.equal(capturePresentation(snapshot({capture: {state: 'recording', last_audio_at: 980}}), true, clock).label, '入力の更新待ち');
  assert.equal(getControlState(snapshot({asr: {state: 'running'}}), true, clock, true, null).startDisabled, true);
  assert.equal(getControlState(snapshot({processing_active: true, asr: {state: 'paused'}}), true, clock, true, null).startDisabled, true, 'Unconfirmed worker exit blocks new recordings even if recognition is paused');
  assert.equal(getControlState(snapshot({capture: {state: 'stalled'}}), true, clock, true, null).startDisabled, true);
  assert.equal(getControlState(snapshot({capture: {state: 'stalled'}}), true, clock, true, null).stopDisabled, false, 'Stalled audio is still stoppable');
  assert.equal(capturePresentation(snapshot({capture: {state: 'stalled'}}), true, clock).label, '入力途絶・要確認');
  assert.equal(translationMap(snapshot({lines: [{id: 'a', translation_ja: '保存済み'}], analysis: {result: {translations: [{source_id: 'a', text: '旧訳'}, {source_id: 'b', text: '新訳'}]}}})).get('a'), '保存済み');

  const first = appFixture(); await first.app.loadDevices(); await first.app.poll();
  assert.equal(first.$('device-select').value, '1', 'Prefer MacBook built-in input');
  first.$('device-select').value = '7'; await first.app.loadDevices();
  assert.equal(first.$('device-select').value, '7', 'Refreshing devices preserves a deliberate selection');
  assert.equal(first.$('start-button').disabled, false); assert.equal(first.$('stop-button').disabled, true);
  assert.equal(first.$('analysis-history').hidden, true, 'Legacy snapshots without history remain supported');
  assert.equal(first.$('translation-heading').textContent, '文脈で訳し直し', 'Idle does not imply that saved fragment translations exist');
  assert.equal(first.$('translation-caption').hidden, true);
  assert.equal(first.$('translation-caption').textContent, '');
  assert.match(first.$('translation-empty').textContent, /発言がまとまると/);
  const emptyReplay = appFixture(); emptyReplay.app.acceptState(snapshot({session: {id: 'demo-zero', source_kind: 'replay'}, capture: {state: 'completed', audio_seconds: 0}}));
  assert.equal(emptyReplay.$('translation-heading').textContent, '文脈で訳し直し', 'Replay at 0:00 has the same honest pending state');
  assert.equal(emptyReplay.$('translation-caption').hidden, true);
  assert.equal(emptyReplay.$('translation-blocks').children.length, 0);
  first.state(snapshot({session: {id: 's1', source_kind: 'microphone'}, capture: {state: 'recording', audio_seconds: 20, last_audio_at: 999, rms_dbfs: -22}, asr: {state: 'running', through_seconds: 10, queue_seconds: 10}}));
  await first.app.poll(); assert.equal(first.$('capture-state').textContent, '録音中');
  assert.equal(first.$('start-button').disabled, true); assert.equal(first.$('stop-button').disabled, false);
  first.rejectState(true); await first.app.poll();
  assert.equal(first.$('capture-state').textContent, '不明');
  assert.match(first.$('connection-warning').textContent, /停止を意味しません/);
  assert.equal(first.$('stop-button').disabled, false, 'Stop remains available when last known recording');
  assert.equal(first.$('input-meter-fill').style.width, '0%');

  const paused = appFixture(); await paused.app.loadDevices();
  paused.app.acceptState(snapshot({capture: {state: 'completed', audio_seconds: 80}, processing_active: true, message: '保存済みの結果を表示しています。現在の録音ではありません。',
    asr: {state: 'paused', through_seconds: 45, queue_seconds: 35, failed_chunks: [{index: 1, start_seconds: 15, end_seconds: 30}]}}));
  assert.equal(paused.$('asr-state').textContent, '保留');
  assert.equal(paused.$('start-button').disabled, true); assert.equal(paused.$('stop-button').disabled, true);
  assert.match(paused.$('setup-hint').textContent, /処理終了を確認/);
  assert.match(paused.$('session-message').textContent, /保存済み/);
  assert.match(paused.$('asr-failures-summary').textContent, /1件/);
  assert.equal(paused.$('asr-failures-list').children[0].textContent, '0:15–0:30');
  paused.app.acceptState(snapshot({capture: {state: 'stalled', audio_seconds: 80, last_audio_at: 950, rms_dbfs: -20}, processing_active: true,
    asr: {state: 'completed', through_seconds: 80, queue_seconds: 0, failed_chunks: [{index: 1, start_seconds: 15, end_seconds: 30}]}}));
  assert.equal(paused.$('capture-state').textContent, '入力途絶・要確認');
  assert.match(paused.$('capture-error').textContent, /停止を確認した状態ではありません/);
  assert.equal(paused.$('stop-button').disabled, false); assert.equal(paused.$('input-meter-fill').style.width, '0%');
  assert.equal(paused.$('asr-state').textContent, '一部未認識', 'Completed recognition cannot hide failed intervals');

  const disappeared = appFixture(); await disappeared.app.loadDevices();
  disappeared.$('device-select').value = '7'; disappeared.devices([{id: '1', name: 'MacBook Proのマイク'}]); await disappeared.app.loadDevices();
  assert.equal(disappeared.$('device-select').value, '', 'A missing selected device is never silently replaced');
  const native = appFixture(); native.devices([{id: 'coreaudio:40', name: 'Shokz'}, {id: 'coreaudio:91', name: 'MacBook Proのマイク'}]);
  await native.app.loadDevices(); await native.app.poll();
  assert.equal(native.$('device-select').value, 'coreaudio:91', 'Native CoreAudio IDs are kept intact while selecting by the MacBook name');
  native.post(() => { native.state(snapshot({capture: {state: 'recording', last_audio_at: 1000}})); return response({ok: true}); });
  await native.app.recordAction('start');
  assert.equal(JSON.parse(native.calls.find(call => call.url === '/api/start').init.body).device, 'coreaudio:91');

  const timedOutDevices = appFixture(); const timeoutError = new Error('signal is aborted without reason'); timeoutError.name = 'AbortError';
  timedOutDevices.failDevices(timeoutError); await timedOutDevices.app.loadDevices();
  assert.match(timedOutDevices.$('devices-error').textContent, /時間内に終わりませんでした/);
  assert.doesNotMatch(timedOutDevices.$('devices-error').textContent, /aborted/);

  const config = appFixture(); await config.app.loadDevices();
  config.$('device-select').value = '7'; config.$('language-select').value = 'ja'; config.$('provider-select').value = 'off'; config.$('model-input').value = 'saved-local';
  config.app.acceptState(snapshot({session: {id: 'active', source_kind: 'replay', language: 'en'}, capture: {state: 'recording', last_audio_at: clock}, asr: {state: 'running'}, analysis: {state: 'running', provider: 'local', model: 'qwen3:4b'}}));
  assert.equal(config.$('language-select').value, 'en'); assert.equal(config.$('provider-select').value, 'local');
  assert.equal(config.$('model-input').value, 'qwen3:4b'); assert.equal(config.$('device-select').value, '__replay__');
  assert.equal(config.$('language-select').disabled, true); assert.equal(config.$('local-quality-note').hidden, false);
  config.app.acceptState(snapshot({session: {id: 'active', source_kind: 'replay', language: 'en'}, capture: {state: 'completed'}, asr: {state: 'completed'}, analysis: {state: 'completed', provider: 'local', model: 'qwen3:4b'}}));
  assert.equal(config.$('language-select').value, 'ja'); assert.equal(config.$('provider-select').value, 'off');
  assert.equal(config.$('model-input').value, 'saved-local'); assert.equal(config.$('device-select').value, '7');
  assert.equal(config.$('local-quality-note').hidden, true, 'Provider-specific warning does not imply microphone or ASR failure');

  const replayDOM = fixtureDOM(); const replayCalls = [];
  const replayApp = createApp(replayDOM, async url => {replayCalls.push(url); return response(snapshot({session: {id: 'r1', source_kind: 'replay', language: 'en'}, capture: {state: 'recording', last_audio_at: clock}}));}, {now: () => clock, setTimer: () => 1, clearTimer: () => {}});
  await replayApp.poll(); await Promise.resolve();
  assert.deepEqual(replayCalls, ['/api/state'], 'A replay page does not enumerate microphone devices at startup');
  replayApp.dispose();

  const cloud = appFixture(); await cloud.app.loadDevices(); await cloud.app.poll();
  assert(!cloud.$('provider-select').children.some(option => option.value === 'openai'), 'Cloud provider is absent without a server capability');
  cloud.app.acceptState(snapshot({capabilities: {cloud_enabled: true, cloud_models: ['gpt-6-luna', 'gpt-6.1-sol']}, cloud_budget: {budget_usd: 1, spent_usd: .2, reserved_usd: .1, budget_date: '2030-06-04'}}));
  assert(cloud.$('provider-select').children.some(option => option.value === 'openai'));
  cloud.$('provider-select').value = 'openai'; cloud.$('provider-select').handlers.change();
  assert.equal(cloud.$('model-input').value, 'gpt-6-luna'); assert.match(cloud.$('cloud-notice').textContent, /認識した原文をOpenAIへ送信/);
  assert.match(cloud.$('cloud-notice').textContent, /残額 \$0\.8000 · 予約 \$0\.1000/, 'Backend spent already includes retained reservations');
  assert.equal(cloud.$('model-options').children.length, 2);
  cloud.$('model-input').value = 'gpt-6.1-sol'; cloud.$('provider-select').value = 'local'; cloud.$('provider-select').handlers.change();
  assert.equal(cloud.$('model-input').value, 'qwen3:4b');
  cloud.$('provider-select').value = 'openai'; cloud.$('provider-select').handlers.change();
  assert.equal(cloud.$('model-input').value, 'gpt-6.1-sol', 'Provider switching retains a deliberately chosen cloud model');
  cloud.post(() => response({ok: true}));
  await cloud.app.recordAction('start');
  const cloudPayload = JSON.parse(cloud.calls.find(call => call.url === '/api/start').init.body);
  assert.equal(cloudPayload.provider, 'openai'); assert.equal(cloudPayload.model, 'gpt-6.1-sol', 'Cloud settings reach only the local start endpoint');
  cloud.app.acceptState(snapshot());
  assert(!cloud.$('provider-select').children.some(option => option.value === 'openai'));
  assert.equal(cloud.$('provider-select').value, 'local'); assert.equal(cloud.$('model-input').value, 'qwen3:4b');

  const defaultCloudState = snapshot({capabilities: {cloud_enabled: true, cloud_models: ['gpt-6-luna', 'gpt-6.1-sol'], default_provider: 'openai', default_model: 'gpt-6-luna'}});
  const initialCloud = appFixture(); await initialCloud.app.loadDevices(); initialCloud.app.acceptState(defaultCloudState);
  assert.equal(initialCloud.$('provider-select').value, 'openai'); assert.equal(initialCloud.$('model-input').value, 'gpt-6-luna');
  initialCloud.$('provider-select').value = 'off'; initialCloud.$('provider-select').handlers.change();
  initialCloud.app.acceptState(structuredClone(defaultCloudState));
  assert.equal(initialCloud.$('provider-select').value, 'off', 'Polling never resets a deliberate provider choice to the server default');
  assert.match(initialCloud.$('processing-caption').textContent, /ローカル処理/);

  const solDefault = appFixture();
  solDefault.app.acceptState({...defaultCloudState, capabilities: {...defaultCloudState.capabilities, default_model: 'gpt-6.1-sol'}});
  solDefault.$('model-input').value = ''; solDefault.$('model-input').handlers.input();
  assert.equal(solDefault.$('model-input').placeholder, 'gpt-6.1-sol', 'An empty model field shows the actual server default');
  assert.equal(solDefault.$('model-setting-summary').textContent, 'モデル: Sol');

  const conference = snapshot({capabilities: {cloud_enabled: true, cloud_models: ['gpt-6-luna', 'gpt-6.1-sol'], default_provider: 'openai', default_model: 'gpt-6-luna', default_language: 'en',
    agenda: {date: '2030-06-04', timezone: 'Asia/Tokyo', items: [{time: '13:15', title: 'Synthetic lecture'}, {time: '14:55', title: '<img src=x onerror=alert(1)>'}]}},
    cloud_budget: {budget_usd: 10, spent_usd: 2, reserved_usd: .4, budget_date: '2030-06-04'},
    cloud_scope: {max_seconds: 21600, used_seconds: 3600, remaining_seconds: 18000, authorized_today: true},
    preflight: {state: 'ready', checked_at: clock, message: '録音の準備完了', checks: [{id: 'network', label: '接続', state: 'warning', message: 'オフライン。録音とASRは利用できます。'}]}});
  const storageMap = new Map(); const storage = {getItem: key => storageMap.get(key), setItem: (key, value) => storageMap.set(key, value)};
  const conferenceUI = appFixture({storage});
  conferenceUI.devices([{id: 'coreaudio:91', uid: 'builtin', name: 'MacBook Proのマイク'}, {id: 'coreaudio:92', uid: 'usb', name: 'USB'}]);
  conferenceUI.app.acceptState(conference); await conferenceUI.app.loadDevices();
  assert.equal(conferenceUI.$('language-select').value, 'en');
  assert.equal(conferenceUI.$('model-setting-summary').textContent, 'モデル: Luna');
  assert.equal(conferenceUI.$('start-button').disabled, false, 'An offline/cloud warning never blocks recording');
  assert.match(conferenceUI.$('preflight-summary').textContent, /録音可能/);
  assert.match(conferenceUI.$('cloud-notice').textContent, /残額 \$8\.0000 · 予約 \$0\.4000/);
  assert.match(conferenceUI.$('cloud-notice').textContent, /残り 5:00:00 \/ 共有上限 6:00:00/);
  assert.equal(conferenceUI.$('agenda-card').hidden, false);
  assert.equal(conferenceUI.$('agenda-card').tagName, 'DETAILS', 'Agenda starts collapsed without displacing concepts');
  conferenceUI.$('agenda-card').open = true;
  assert.equal(conferenceUI.$('agenda-list').children[1].children[1].textContent, '<img src=x onerror=alert(1)>', 'Agenda labels are plain text, never actions or model input');
  const agendaRow = conferenceUI.$('agenda-list').children[0];
  conferenceUI.app.acceptState(structuredClone(conference));
  assert.equal(conferenceUI.$('agenda-list').children[0], agendaRow, 'Unchanged agenda DOM stays stable');
  assert.equal(conferenceUI.$('agenda-card').open, true, 'Polling preserves the user-expanded agenda');
  conferenceUI.$('language-select').value = 'ja'; conferenceUI.$('language-select').handlers.change();
  conferenceUI.$('model-input').value = 'gpt-6.1-sol'; conferenceUI.$('model-input').handlers.input();
  conferenceUI.$('device-select').value = 'coreaudio:92'; conferenceUI.$('device-select').handlers.change();
  conferenceUI.app.acceptState(structuredClone(conference));
  assert.equal(conferenceUI.$('language-select').value, 'ja', 'Poll never resets chosen language');
  const reloaded = appFixture({storage}); reloaded.app.acceptState(structuredClone(conference));
  reloaded.devices([{id: 'coreaudio:31', uid: 'builtin', name: 'MacBook Proのマイク'}, {id: 'coreaudio:32', uid: 'usb', name: 'USB'}]);
  await reloaded.app.loadDevices();
  assert.equal(reloaded.$('language-select').value, 'ja'); assert.equal(reloaded.$('model-input').value, 'gpt-6.1-sol');
  assert.equal(reloaded.$('device-select').value, 'coreaudio:32', 'Restore stable device UID instead of an obsolete CoreAudio number');
  const localReload = appFixture({storage}); localReload.app.acceptState(snapshot({capabilities: {cloud_enabled: false, default_provider: 'local'}}));
  assert.equal(localReload.$('provider-select').value, 'local', 'Cloud preferences do not leak into the local launcher');
  const blockedStorage = appFixture({storage: {getItem() {throw new Error('blocked');}, setItem() {throw new Error('blocked');}}});
  blockedStorage.app.acceptState(structuredClone(conference));
  assert.equal(blockedStorage.$('language-select').value, 'en', 'Disabled browser storage still permits server defaults');
  blockedStorage.$('language-select').handlers.change();
  conferenceUI.app.acceptState({...conference, preflight: {...conference.preflight, state: 'checking'}});
  assert.equal(conferenceUI.$('start-button').disabled, true);
  conferenceUI.app.acceptState({...conference, preflight: {...conference.preflight, state: 'blocked'}});
  assert.equal(conferenceUI.$('start-button').disabled, true); assert.match(conferenceUI.$('preflight-summary').textContent, /問題/);
  conferenceUI.app.acceptState({...conference, session: {id: 'active', source_kind: 'microphone'}, capture: {state: 'recording', last_audio_at: clock}, preflight: {...conference.preflight, state: 'blocked'}});
  assert.equal(conferenceUI.$('stop-button').disabled, false, 'Preparation failures never disable stopping a recording');
  conferenceUI.state(conference); conferenceUI.rejectState(true); await conferenceUI.app.poll();
  assert.match(conferenceUI.$('preflight-summary').textContent, /不明/, 'A previous ready result does not override a lost server connection');
  initialCloud.$('provider-select').value = 'openai'; initialCloud.$('provider-select').handlers.change();
  assert.equal(initialCloud.$('processing-caption').textContent, '原音はこのMacに保存 · 文字のみOpenAIで解析', 'Idle cloud selection is reflected immediately in the footer');
  const touchedBeforeState = appFixture(); touchedBeforeState.$('provider-select').value = 'off'; touchedBeforeState.$('provider-select').handlers.change();
  touchedBeforeState.app.acceptState(defaultCloudState);
  assert.equal(touchedBeforeState.$('provider-select').value, 'off', 'Input before the first idle snapshot is also respected');
  const unavailableAPI = appFixture();
  unavailableAPI.app.acceptState(snapshot({session: {id: 'active-mic', source_kind: 'microphone'}, capture: {state: 'recording', last_audio_at: clock, audio_seconds: 30}, analysis: {state: 'failed', provider: 'openai', error: 'APIに接続できません'}}));
  assert.match(unavailableAPI.$('analysis-error').textContent, /録音・保存は継続しています/);
  unavailableAPI.app.acceptState(snapshot({session: {id: 'active-mic', source_kind: 'microphone'}, capture: {state: 'stalled', last_audio_at: clock - 30, audio_seconds: 30}, analysis: {state: 'failed', provider: 'openai', error: 'APIに接続できません'}}));
  assert.doesNotMatch(unavailableAPI.$('analysis-error').textContent, /継続しています/);
  assert.match(unavailableAPI.$('analysis-error').textContent, /現在の状態を確認/);
  assert.equal(unavailableAPI.$('processing-caption').textContent, '原音はこのMacに保存 · 文字のみOpenAIで解析', 'An active cloud session never claims local analysis');

  const progressMessage = appFixture();
  const microphoneState = snapshot({session: {id: 'mic-message', source_kind: 'microphone'}, message: 'マイクを起動しています。', capture: {state: 'starting', audio_seconds: 0}, analysis: {state: 'idle', provider: 'off'}});
  progressMessage.app.acceptState(microphoneState);
  assert.equal(progressMessage.$('session-message').textContent, 'マイクを起動しています。');
  progressMessage.app.acceptState({...microphoneState, capture: {state: 'recording', audio_seconds: 15, last_audio_at: clock}});
  assert.equal(progressMessage.$('session-message').textContent, '録音・保存が進んでいます。');
  progressMessage.app.acceptState({...microphoneState, capture: {state: 'stalled', audio_seconds: 15, last_audio_at: clock - 30}});
  assert.match(progressMessage.$('session-message').textContent, /音声入力が途絶えています/);
  progressMessage.app.acceptState({...microphoneState, capture: {state: 'failed', audio_seconds: 15, error: 'input failed'}});
  assert.match(progressMessage.$('session-message').textContent, /問題が起きました/);
  progressMessage.app.acceptState({...microphoneState, message: '保存済みの結果を表示しています。現在の録音ではありません。', capture: {state: 'failed', audio_seconds: 15}});
  assert.match(progressMessage.$('session-message').textContent, /保存済みの結果/, 'Saved-result notices retain their meaning instead of becoming live-status claims');

  const rich = appFixture(); const injected = '<img src=x onerror=alert(1)>';
  const richState = snapshot({session: {id: 's1', title: 'Fixture lecture', source_kind: 'replay'},
    capture: {state: 'recording', audio_seconds: 30, last_audio_at: 999},
    analysis: {state: 'running', provider: 'local', through_seconds: 12, result: {
      headline: {text: injected, source_ids: ['line-1']}, flow: [{text: '例から主張へ', source_ids: ['line-1']}],
      concepts: [{term: 'Concept', explanation: 'Explanation', basis: 'background', source_ids: ['line-1']}],
      translations: [{source_id: 'line-1', text: '和訳の例'}]}},
    lines: [{id: 'line-1', start_seconds: 3, end_seconds: 12, text: injected, language: 'en', uncertain: true}]});
  rich.app.acceptState(richState);
  assert.equal(rich.$('headline').textContent, injected); assert.equal(rich.$('headline').children.length, 0);
  assert.equal(rich.$('transcript').textContent, injected, 'Left original column contains raw text only');
  assert.equal(rich.$('translation-heading').textContent, '既存の断片訳');
  assert.match(rich.$('translation-blocks').textContent, /和訳の例/);
  assert.match(rich.$('translation-caption').textContent, /訳し直したものではありません/);
  assert.equal(rich.$('transcript').children[0].classes.has('uncertain-source'), true, 'Uncertain raw speech is styled gray, without repeated labels');
  assert.match(rich.$('transcript').children[0].title, /0:03–0:12 · line-1 · EN · 認識が不確か/);
  assert.equal(rich.$('transcript').children[0].dataset.sourceId, 'line-1');
  assert.match(rich.$('concepts-list').textContent, /背景補足/); assert.equal(rich.$('capture-state').textContent, '再生中');
  const sourceLine = rich.$('transcript').children[0]; firstButton(rich.$('headline-sources')).click();
  assert.equal(sourceLine.attributes['aria-current'], 'true');
  assert.equal(sourceLine.focused, true); assert.equal(rich.$('freeze-button').attributes['aria-pressed'], 'true');
  const newer = structuredClone(richState); newer.capture.audio_seconds = 40; newer.lines.push({id: 'line-2', start_seconds: 12, end_seconds: 21, text: 'new', language: 'en'}); newer.analysis.result.headline.text = '新論点';
  rich.app.acceptState(newer);
  assert.equal(rich.$('headline').textContent, injected, 'Reading freeze keeps text stable');
  assert.equal(rich.$('capture-time').textContent, '0:40', 'Reading freeze never freezes capture status');
  assert.match(rich.$('new-count').textContent, /1件/); assert.equal(rich.$('transcript').children.length, 1);
  assert.equal(sourceLine.classes.has('latest-source'), true, 'Frozen reading retains the latest marker for its displayed snapshot');
  rich.app.goLive(); assert.equal(rich.$('headline').textContent, '新論点'); assert.equal(rich.$('transcript').children.length, 2);
  assert.equal(sourceLine.classes.has('latest-source'), false, 'A new utterance clears the previous latest marker');
  assert.equal(rich.$('transcript').children[1].classes.has('latest-source'), true);
  assert.equal(sourceLine.attributes['aria-current'], 'false');
  rich.app.acceptState(richState);
  assert.equal(rich.$('transcript').children[0].classes.has('latest-source'), true, 'Rewind restores the latest utterance for that point');
  rich.app.acceptState(newer);
  assert.equal(rich.$('summary-heading').textContent, '直近の要点');
  assert.match(rich.$('coverage-summary').textContent, /未記録/);

  const blockedTranslation = appFixture();
  const withBlocks = structuredClone(richState);
  withBlocks.analysis.result.block_translations = [{text: '文脈を踏まえたまとまりの訳。' + injected, source_ids: ['line-1']}];
  blockedTranslation.app.acceptState(withBlocks);
  assert.equal(blockedTranslation.$('translation-heading').textContent, '文脈で訳し直し');
  assert.match(blockedTranslation.$('translation-range').textContent, /0:03–0:12/);
  assert.match(blockedTranslation.$('translation-blocks').textContent, /文脈を踏まえたまとまりの訳/);
  assert.doesNotMatch(blockedTranslation.$('translation-blocks').textContent, /和訳の例/, 'Block translations never mix with old fragment translations');
  assert.equal(blockedTranslation.$('translation-blocks').children[0].children[0].textContent, '文脈を踏まえたまとまりの訳。' + injected);
  firstButton(blockedTranslation.$('translation-blocks')).click();
  assert.equal(blockedTranslation.$('transcript').children[0].focused, true, 'Block evidence jumps to the preserved raw source');
  blockedTranslation.app.goLive();
  const emptyBlocks = structuredClone(withBlocks); emptyBlocks.analysis.result.block_translations = [];
  blockedTranslation.app.acceptState(emptyBlocks);
  assert.equal(blockedTranslation.$('translation-heading').textContent, '文脈で訳し直し');
  assert.equal(blockedTranslation.$('translation-blocks').children.length, 0, 'An explicitly empty new block field does not fall back to older fragment translations');
  assert.equal(blockedTranslation.$('translation-empty').hidden, false);

  const continuous = appFixture();
  const continuousState = structuredClone(newer);
  continuousState.analysis.result.block_translations = [{text: 'Old sampled translation', source_ids: ['line-1']}];
  continuousState.translation = {enabled: true, state: 'running', pending_lines: 2, excluded_uncertain_lines: 1, native_lines: 0,
    through_seconds: 12, generated_at: 960, error: null, blocks: [{id: 't1', text: '継続する文脈訳' + injected, source_ids: ['line-1'], start_seconds: 3, end_seconds: 12, generated_at: 959, published_at: 960}]};
  continuous.app.acceptState(continuousState);
  assert.equal(continuous.$('translation-heading').textContent, '文脈で訳し直し');
  assert.equal(continuous.$('translation-blocks').children[0].children[0].textContent, '継続する文脈訳' + injected, 'Continuous translations remain plain text');
  assert.doesNotMatch(continuous.$('translation-blocks').textContent, /Old sampled translation|和訳の例/, 'The independent queue takes precedence over sampled analysis translations');
  assert.match(continuous.$('translation-status').textContent, /翻訳中 · 未訳 2行 · 不確か除外 1行 · 日本語 0行/);
  assert.doesNotMatch(continuous.$('translation-caption').textContent, /断片訳|つなげて/);
  const continuousRow = continuous.$('translation-blocks').children[0];
  const continuousDisclosure = continuousRow.children[1].children[1]; continuousDisclosure.open = true;
  const continuousEvidence = firstButton(continuousRow); continuousEvidence.focus();
  continuous.$('translation-blocks').scrollHeight = 1000; continuous.$('translation-blocks').clientHeight = 240; continuous.$('translation-blocks').scrollTop = 100;
  const newRaw = structuredClone(continuousState); newRaw.lines.push({id: 'line-3', start_seconds: 21, end_seconds: 28, text: 'Third utterance', language: 'en'});
  newRaw.translation.pending_lines = 3;
  continuous.app.acceptState(newRaw);
  assert.equal(continuous.$('translation-blocks').children[0], continuousRow, 'Raw arrivals preserve translation DOM');
  assert.equal(continuousDisclosure.open, true); assert.equal(continuous.dom.activeElement, continuousEvidence);
  assert.equal(continuous.$('translation-blocks').scrollTop, 100);
  const translationAppend = structuredClone(newRaw);
  translationAppend.translation.blocks.push({id: 't2', text: '次の文脈訳', source_ids: ['line-2'], start_seconds: 12, end_seconds: 21, generated_at: 969, published_at: 970});
  translationAppend.translation.pending_lines = 1;
  continuous.app.acceptState(translationAppend);
  assert.equal(continuous.$('translation-blocks').children.length, 2, 'Translations append without requiring a new ASR or analysis snapshot');
  assert.equal(continuous.$('translation-blocks').children[0], continuousRow, 'Appending a translation keeps prior block elements');
  assert.equal(continuousDisclosure.open, true); assert.equal(continuous.dom.activeElement, continuousEvidence);
  assert.equal(continuous.$('translation-blocks').scrollTop, 100, 'Appending a translation does not move a reader above the bottom');
  continuous.$('translation-blocks').scrollTop = 740;
  const translationThird = structuredClone(translationAppend);
  translationThird.translation.blocks.push({id: 't3', text: '三つ目の文脈訳', source_ids: ['line-3'], start_seconds: 21, end_seconds: 28, generated_at: 979, published_at: 980});
  translationThird.translation.pending_lines = 0; translationThird.translation.state = 'completed';
  continuous.app.acceptState(translationThird);
  assert.equal(continuous.$('translation-blocks').children.length, 3, 'All completed blocks remain visible, rather than only the latest two');
  assert.equal(continuous.$('translation-blocks').scrollTop, 1000, 'A near-bottom reader follows newly translated blocks');
  firstButton(continuousRow).click();
  assert.equal(continuous.$('transcript').children[0].focused, true, 'Continuous translation evidence jumps to the original and freezes reading');
  const translationFourth = structuredClone(translationThird);
  translationFourth.translation.blocks.push({id: 't4', text: '固定中の新しい訳', source_ids: ['line-3'], start_seconds: 21, end_seconds: 28, generated_at: 989, published_at: 990});
  translationFourth.translation.pending_lines = 4;
  continuous.app.acceptState(translationFourth);
  assert.equal(continuous.$('translation-blocks').children.length, 3, 'Reading freeze also freezes the translation history');
  assert.match(continuous.$('translation-status').textContent, /現在の訳:.*未訳 4行/, 'Current processing counts remain explicitly current while reading is frozen');
  assert.match(continuous.$('new-count').textContent, /新しい訳/);
  continuous.app.goLive(); assert.equal(continuous.$('translation-blocks').children.length, 4);
  const historicalTranslations = structuredClone(translationFourth);
  historicalTranslations.translation.blocks.push({id: 'no-publication', text: '公開時刻不明の訳', source_ids: ['line-1'], generated_at: 940});
  historicalTranslations.analysis_history = [{through_seconds: 12, generated_at: 965, headline: {text: '早い分析', source_ids: ['line-1']}}];
  continuous.app.acceptState(historicalTranslations);
  continuous.$('history-list').children[0].children[0].click();
  assert.equal(continuous.$('translation-blocks').children.length, 1, 'History shows only translations published by that analysis timestamp');
  assert.match(continuous.$('translation-blocks').textContent, /継続する文脈訳/);
  assert.doesNotMatch(continuous.$('translation-blocks').textContent, /次の文脈訳|三つ目|公開時刻不明/);
  assert.match(continuous.$('translation-caption').textContent, /生成時刻までに公開/);
  const missingTime = structuredClone(historicalTranslations); delete missingTime.analysis_history[0].generated_at;
  continuous.app.acceptState(missingTime); continuous.$('history-list').children[0].children[0].click();
  assert.equal(continuous.$('translation-blocks').children.length, 0, 'A historical analysis with no timestamp never borrows current translations');
  assert.match(continuous.$('translation-caption').textContent, /生成時刻が未記録/);
  continuous.app.goLive();
  const disabledContinuous = structuredClone(continuousState); disabledContinuous.translation.enabled = false;
  continuous.app.acceptState(disabledContinuous);
  assert.match(continuous.$('translation-blocks').textContent, /Old sampled translation/, 'Disabled continuous translation keeps the existing analysis renderer');
  assert.equal(continuous.$('translation-status').hidden, true);

  const translationRetry = appFixture();
  const failedTranslation = structuredClone(continuousState); failedTranslation.translation.state = 'failed'; failedTranslation.translation.retry_required = true; failedTranslation.translation.error = 'API unavailable';
  translationRetry.state(failedTranslation); await translationRetry.app.poll();
  assert.equal(translationRetry.$('translation-retry-button').hidden, false);
  assert.match(translationRetry.$('translation-error').textContent, /API unavailable/);
  assert.match(translationRetry.$('translation-status').textContent, /失敗・要確認.*未訳 2行/);
  let resolveTranslationRetry; translationRetry.post(() => new Promise(resolve => {resolveTranslationRetry = resolve;}));
  const retryTranslationTask = translationRetry.app.retryTranslation();
  await translationRetry.app.retryTranslation();
  assert.equal(translationRetry.calls.filter(call => call.url === '/api/retry-translation').length, 1, 'A translation retry cannot be duplicated while its request is in flight');
  assert.equal(translationRetry.$('translation-retry-button').disabled, true);
  const resumedTranslation = structuredClone(failedTranslation); resumedTranslation.translation.state = 'running'; resumedTranslation.translation.retry_required = false; resumedTranslation.translation.error = null;
  translationRetry.state(resumedTranslation); resolveTranslationRetry(response({ok: true})); await retryTranslationTask;
  assert.equal(translationRetry.$('translation-retry-button').hidden, true);
  assert.equal(translationRetry.$('translation-error').hidden, true);
  const offlineTranslation = structuredClone(failedTranslation); offlineTranslation.translation.state = 'waiting'; offlineTranslation.translation.retry_required = false;
  translationRetry.state(offlineTranslation); await translationRetry.app.poll();
  assert.equal(translationRetry.$('translation-retry-button').hidden, true, 'An offline precheck does not offer duplicate manual retries');
  translationRetry.rejectState(true); await translationRetry.app.poll();
  assert.match(translationRetry.$('translation-status').textContent, /状態不明/);

  const scrolling = appFixture(); scrolling.app.acceptState(structuredClone(richState));
  const rawRow = scrolling.$('transcript').children[0];
  scrolling.$('transcript').scrollHeight = 1000; scrolling.$('transcript').clientHeight = 240; scrolling.$('transcript').scrollTop = 100;
  scrolling.app.acceptState(structuredClone(newer));
  assert.equal(scrolling.$('transcript').scrollTop, 100, 'Appending live speech does not pull a reader away from earlier text');
  assert.equal(scrolling.$('transcript').children[0], rawRow, 'Appending originals preserves existing source DOM nodes');
  const changedAnalysis = structuredClone(newer); changedAnalysis.analysis.result.headline.text = '分析だけ更新';
  scrolling.app.acceptState(changedAnalysis);
  assert.equal(scrolling.$('transcript').children[0], rawRow); assert.equal(scrolling.$('transcript').scrollTop, 100, 'Translation/analysis updates leave the original reading position intact');
  const flowRow = scrolling.$('flow-list').children[0];
  const flowDisclosure = flowRow.children[1].children[0];
  flowDisclosure.open = true;
  const evidenceButton = firstButton(flowDisclosure); evidenceButton.focus();
  scrolling.$('transcript').scrollTop = 740;
  const appended = structuredClone(changedAnalysis); appended.lines.push({id: 'line-3', text: 'latest raw speech', start_seconds: 21, end_seconds: 28, language: 'en'});
  scrolling.app.acceptState(appended);
  assert.equal(scrolling.$('flow-list').children[0], flowRow, 'Raw ASR arrivals do not rebuild unchanged interpretations');
  assert.equal(flowDisclosure.open, true, 'An opened source disclosure stays open as raw speech arrives');
  assert.equal(scrolling.dom.activeElement, evidenceButton, 'Raw ASR arrivals preserve focus within interpretation evidence');
  assert.equal(scrolling.$('transcript').scrollTop, scrolling.$('transcript').scrollHeight, 'Near-bottom readers follow new raw speech');
  assert.doesNotMatch(scrolling.$('transcript').textContent, /和訳未作成|認識が不確か|0:21|EN/);
  scrolling.$('setup-settings').open = true; scrolling.app.acceptState(structuredClone(appended));
  assert.equal(scrolling.$('setup-settings').open, true, 'Settings remain open when the user opened them');
  assert.equal(scrolling.dom.nodes.has('questions-list'), false, 'Question suggestions are removed from the interface');

  const timeline = appFixture();
  const historyState = structuredClone(newer);
  const early = {through_seconds: 12, generated_at: 960, headline: {text: injected, source_ids: ['line-1']}, summary: [{text: '当時の要点', source_ids: ['line-1']}], source_ranges: [{source_id: 'line-1', start_seconds: 3, end_seconds: 12}]};
  const later = {through_seconds: 21, generated_at: 990, headline: {text: '最新の整理', source_ids: ['line-2']}, source_ranges: [{source_id: 'line-1', start_seconds: 3, end_seconds: 12}, {source_id: 'line-2', start_seconds: 18, end_seconds: 21}]};
  historyState.analysis = {state: 'completed', provider: 'local', through_seconds: 21, generated_at: 990, result: later};
  historyState.lines[1].start_seconds = 18;
  historyState.analysis_history = [early, later];
  timeline.app.acceptState(historyState);
  assert.equal(timeline.$('analysis-history').hidden, false);
  assert.equal(timeline.$('history-list').children.length, 2);
  assert.match(timeline.$('history-list').children[0].textContent, /最新の整理/, 'History shows latest analysis first');
  assert.match(timeline.$('coverage-summary').textContent, /0:03〜0:21 · 2行（抜粋）/);
  assert.match(timeline.$('coverage-explanation').textContent, /全発言を参照した意味ではありません/);
  assert.equal(timeline.$('coverage-list').children.length, 2, 'Discontinuous source intervals remain individually inspectable');
  const originalEvidence = JSON.stringify(historyState.analysis_history[0]);
  const selectedHistoryButton = timeline.$('history-list').children[1].children[0];
  selectedHistoryButton.focus(); selectedHistoryButton.click();
  assert.equal(timeline.dom.activeElement, selectedHistoryButton, 'Selecting a historical analysis preserves keyboard focus on its button');
  assert.equal(timeline.$('headline').textContent, injected, 'History snapshot is plain text and uses the original interpretation');
  assert.equal(timeline.$('focus-heading').textContent, 'その時点の論点');
  assert.equal(timeline.$('summary-heading').textContent, '当時の要点');
  assert.equal(timeline.$('history-list').children[1].children[0].attributes['aria-pressed'], 'true');
  assert.equal(timeline.$('transcript').children.length, 2, 'Selecting history retains the current transcript for evidence jumps');
  assert.match(timeline.$('freeze-notice').textContent, /分析より後の発言も含まれます/);
  assert.equal(timeline.$('analysis-time').textContent, '0:21', 'Selecting history never rewinds processing progress');
  assert.equal(timeline.$('new-count').hidden, true, 'No false new-content badge immediately after choosing history');
  assert.match(timeline.$('headline-sources').children[0].textContent, /0:03/);
  assert.match(timeline.$('coverage-summary').textContent, /0:03〜0:12 · 1行（抜粋）/);
  firstButton(timeline.$('headline-sources')).click();
  assert.equal(timeline.$('transcript').children[0].focused, true, 'Historical evidence still jumps to its exact original line');
  const newest = structuredClone(historyState); newest.capture.audio_seconds = 55; newest.analysis.through_seconds = 45;
  newest.analysis.result = {through_seconds: 45, generated_at: 1000, headline: {text: 'さらに新しい整理', source_ids: ['line-2']}};
  newest.analysis_history.push(newest.analysis.result);
  selectedHistoryButton.focus();
  timeline.app.acceptState(newest);
  assert.equal(timeline.$('headline').textContent, injected); assert.equal(timeline.$('analysis-time').textContent, '0:45');
  assert.equal(timeline.$('history-list').children.length, 3, 'New history arrives while the selected interpretation remains fixed');
  assert.match(timeline.dom.activeElement.textContent, /0:12/, 'A new history snapshot preserves focus by the previous history key');
  assert.equal(JSON.stringify(historyState.analysis_history[0]), originalEvidence, 'History selection never changes stored evidence');
  timeline.app.goLive(); assert.equal(timeline.$('headline').textContent, 'さらに新しい整理');
  assert.equal(timeline.$('focus-heading').textContent, 'いまの論点'); assert.equal(timeline.$('latest-button').hidden, true);
  const manyHistory = structuredClone(historyState); manyHistory.analysis_history = Array.from({length: 65}, (_, index) => ({...early, through_seconds: index + 1, generated_at: 1000 + index}));
  timeline.app.acceptState(manyHistory); assert.equal(timeline.$('history-list').children.length, 60, 'History UI limits itself to the most recent 60 snapshots');

  const action = appFixture(); await action.app.loadDevices(); await action.app.poll();
  let release; action.post(() => new Promise(resolve => {release = resolve;}));
  const start = action.app.recordAction('start');
  await action.app.recordAction('start');
  assert.equal(action.calls.filter(call => call.url === '/api/start').length, 1, 'Repeated clicks cannot create duplicate starts');
  assert.equal(action.$('start-button').disabled, true);
  action.state(snapshot({session: {id: 's2'}, capture: {state: 'recording', last_audio_at: 1000, audio_seconds: 1}}));
  release(response({ok: true})); await start;
  assert.equal(action.$('stop-button').disabled, false);
  assert.deepEqual(JSON.parse(action.calls.find(call => call.url === '/api/start').init.body), {device: '1', language: 'auto', provider: 'local'});

  const lost = appFixture(); await lost.app.loadDevices(); await lost.app.poll();
  lost.post(() => {throw new Error('response lost');}); lost.rejectState(true);
  await lost.app.recordAction('start');
  assert.equal(lost.$('start-button').disabled, true, 'Unconfirmed POST must remain locked through disconnect');
  assert.match(lost.$('start-button').textContent, /確認中/);
  lost.rejectState(false); lost.state(snapshot({capture: {state: 'recording', audio_seconds: 2, last_audio_at: 1000}})); await lost.app.poll();
  assert.equal(lost.$('stop-button').disabled, false, 'Reconnect reconciles the actual recording state');
  assert.equal(lost.calls.filter(call => call.url === '/api/start').length, 1);

  const failure = appFixture(); await failure.app.loadDevices(); await failure.app.poll();
  failure.post(() => ({ok: false, status: 400, json: async () => ({error: 'マイクへのアクセスが拒否されました'})}));
  await failure.app.recordAction('start');
  assert.match(failure.$('action-message').textContent, /アクセスが拒否/);
  assert.equal(failure.$('start-button').disabled, false, 'Confirmed idle state permits a deliberate retry');
  clock = 1030; failure.app.renderStatus(); assert.equal(failure.$('start-button').disabled, true, 'A stale snapshot never permits a new start');
  assert.equal(failure.$('capture-state').textContent, '不明');
  for (const fixture of fixtures) fixture.app.dispose();
  console.log('Lecture UI checks passed (state freshness, reading freeze, source jumps, plain text, action reconciliation).');
}
run().catch(error => {for (const fixture of fixtures) fixture.app.dispose(); console.error(error); process.exitCode = 1;});
