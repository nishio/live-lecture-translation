// CPU-only DOM and control regressions. Browser layout and microphone are separate checks.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {formatTime, ageText, isFresh, capturePresentation, getControlState, translationMap, schedulePresentation, createApp} = require('../lecture-dashboard/app.js');

const {fixtureDOM} = require('./lecture_ui_test_dom');

function firstButton(node) {
  if (node.tagName === 'BUTTON') return node;
  for (const child of node.children) { const button = firstButton(child); if (button) return button; }
  return null;
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
  const dom = fixtureDOM(); const calls = []; let serverState = snapshot(); let rejectState = false; let postHandler = null; let historyHandler = null;
  let devices = [{id: '7', name: 'USB audio'}, {id: '1', name: 'MacBook Proのマイク'}];
  let deviceFailure = null;
  const app = createApp(dom, async (url, init) => {
    calls.push({url, init});
    if (init.method === 'POST') return postHandler ? postHandler(url, init) : response({ok: true});
    if (url.startsWith('/api/analysis-history?') && historyHandler) return historyHandler(url);
    if (url === '/api/devices') { if (deviceFailure) throw deviceFailure; return response({devices}); }
    if (rejectState) throw new Error('connection lost');
    return response(serverState);
  }, {autoStart: false, now: () => clock, ...options});
  const fixture = {app, dom, calls, $: id => dom.getElementById(id),
    state(value) {serverState = value;}, history(handler) {historyHandler = handler;}, rejectState(value) {rejectState = value;}, post(handler) {postHandler = handler;}, devices(value) {devices = value;}, failDevices(error) {deviceFailure = error;}};
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
  assert.equal(getControlState(snapshot({session: {id: 'draining'}, capture: {state: 'completed'}, processing_active: true}), true, clock, true, null).stopDisabled, false, 'Pending processing remains stoppable after capture ends');
  assert.equal(getControlState(snapshot({processing_stop_requested: true, processing_stop_status: 'stopping'}), true, clock, true, null).startDisabled, true, 'Unconfirmed stop blocks a new session even after worker flags clear');
  assert.equal(capturePresentation(snapshot({capture: {state: 'stalled'}}), true, clock).label, '入力途絶・要確認');
  assert.equal(translationMap(snapshot({lines: [{id: 'a', translation_ja: '保存済み'}], analysis: {result: {translations: [{source_id: 'a', text: '旧訳'}, {source_id: 'b', text: '新訳'}]}}})).get('a'), '保存済み');

  const splitSettings = new Map([['lecture-idle:v1:local:default', '{"version":1,"language":"ja"}']]);
  const splitStorage = {getItem: key => splitSettings.get(key), setItem: (key, value) => splitSettings.set(key, value)};
  const split = appFixture({storage: splitStorage});
  const divider = split.$('source-splitter');
  const sourceColumn = split.$('source-column');
  function dividerEvent(type, overrides = {}) {
    const event = {button: 0, isPrimary: true, pointerId: 7, clientY: 300, defaultPrevented: false,
      preventDefault() {this.defaultPrevented = true;}, ...overrides};
    divider.handlers[type](event); return event;
  }
  assert.equal(sourceColumn.style['--source-share'], '50%');
  assert.equal(divider.attributes['aria-valuemin'], '25'); assert.equal(divider.attributes['aria-valuemax'], '75');
  assert.equal(divider.attributes['aria-valuenow'], '50'); assert.equal(divider.attributes['aria-valuetext'], '原文 50%、日本語訳 50%');
  assert.equal(splitSettings.size, 1, 'Opening the page never rewrites recording preferences or creates layout settings');
  for (const invalid of [{button: 2}, {isPrimary: false}, {clientY: NaN}]) {
    assert.equal(dividerEvent('pointerdown', invalid).defaultPrevented, false);
    assert.equal(divider.hasPointerCapture(7), false, 'Only a usable primary pointer can resize');
  }
  for (const height of [0, -1, 14, NaN, Infinity]) {
    sourceColumn.rect = {top: 100, height};
    dividerEvent('pointerdown'); assert.equal(divider.hasPointerCapture(7), false, 'Collapsed and invalid layouts cannot begin a drag');
  }
  sourceColumn.rect = {top: 100, height: 400};
  assert.equal(dividerEvent('pointerdown', {clientY: 304}).defaultPrevented, true);
  assert.equal(divider.hasPointerCapture(7), true); assert.equal(divider.dataset.dragging, 'true');
  assert.equal(split.dom.activeElement, divider); assert.equal(sourceColumn.style['--source-share'], '50%', 'Grabbing the handle edge never jumps its center');
  dividerEvent('pointermove', {pointerId: 8, clientY: 900});
  dividerEvent('pointerup', {pointerId: 8});
  assert.equal(sourceColumn.style['--source-share'], '50%'); assert.equal(divider.hasPointerCapture(7), true, 'A second pointer cannot move or end the active drag');
  dividerEvent('pointermove', {clientY: 384});
  assert.equal(sourceColumn.style['--source-share'], '70%', 'Dragging preserves the point where the handle was grabbed');
  assert.equal(divider.attributes['aria-valuetext'], '原文 70%、日本語訳 30%');
  assert.equal(splitSettings.has('lecture-layout:v1'), false, 'Pointer movement does not synchronously write storage on every frame');
  dividerEvent('pointermove', {clientY: 1000}); assert.equal(sourceColumn.style['--source-share'], '75%');
  dividerEvent('pointermove', {clientY: -1000}); assert.equal(sourceColumn.style['--source-share'], '25%');
  dividerEvent('pointerup', {clientY: 384});
  assert.equal(sourceColumn.style['--source-share'], '70%');
  assert.equal(divider.hasPointerCapture(7), false); assert.equal(divider.dataset.dragging, 'false');
  assert.deepEqual(JSON.parse(splitSettings.get('lecture-layout:v1')), {version: 1, sourceShare: 70});
  assert.equal(splitSettings.get('lecture-idle:v1:local:default'), '{"version":1,"language":"ja"}');
  assert.equal(appFixture({storage: splitStorage}).$('source-column').style['--source-share'], '70%', 'A new page restores the chosen split');
  assert.equal(dividerEvent('keydown', {key: 'ArrowUp'}).defaultPrevented, true); assert.equal(sourceColumn.style['--source-share'], '68%');
  dividerEvent('keydown', {key: 'ArrowDown'}); assert.equal(sourceColumn.style['--source-share'], '70%');
  dividerEvent('keydown', {key: 'Home'}); dividerEvent('keydown', {key: 'ArrowUp'}); assert.equal(sourceColumn.style['--source-share'], '25%');
  dividerEvent('keydown', {key: 'End'}); dividerEvent('keydown', {key: 'ArrowDown'}); assert.equal(sourceColumn.style['--source-share'], '75%');
  assert.equal(dividerEvent('keydown', {key: 'Tab'}).defaultPrevented, false);
  assert.equal(dividerEvent('keydown', {key: 'ArrowUp', metaKey: true}).defaultPrevented, false);
  assert.equal(dividerEvent('dblclick').defaultPrevented, true); assert.equal(sourceColumn.style['--source-share'], '50%');
  dividerEvent('pointerdown'); dividerEvent('pointermove', {clientY: 340}); dividerEvent('pointercancel');
  assert.equal(sourceColumn.style['--source-share'], '60%'); assert.equal(divider.hasPointerCapture(7), false);
  assert.equal(divider.dataset.dragging, 'false'); assert.equal(JSON.parse(splitSettings.get('lecture-layout:v1')).sourceShare, 60);
  dividerEvent('pointermove', {clientY: 390}); assert.equal(sourceColumn.style['--source-share'], '60%', 'A cancelled pointer cannot continue resizing');
  dividerEvent('pointerdown', {clientY: 340}); dividerEvent('pointermove', {clientY: 360});
  divider.releasePointerCapture(7); dividerEvent('lostpointercapture');
  assert.equal(sourceColumn.style['--source-share'], '65%'); assert.equal(divider.dataset.dragging, 'false');
  split.app.acceptState(snapshot({session: {id: 'split-layout', source_kind: 'replay'}})); split.app.freeze(); split.app.goLive();
  split.app.acceptState(snapshot({session: {id: 'next-layout', source_kind: 'replay'}}));
  assert.equal(sourceColumn.style['--source-share'], '65%', 'State updates, reading navigation, and a new session preserve the layout choice');
  for (const stored of ['broken JSON', 'null', '{"version":1,"sourceShare":"70"}', '{"version":2,"sourceShare":70}']) {
    const invalid = appFixture({storage: {getItem: () => stored}});
    assert.equal(invalid.$('source-column').style['--source-share'], '50%', 'Invalid stored settings cannot produce an unusable split');
  }
  assert.equal(appFixture({storage: {getItem: () => '{"version":1,"sourceShare":100}'}}).$('source-column').style['--source-share'], '75%');
  const unsavedSplit = appFixture({storage: {getItem() {throw new Error('private mode');}, setItem() {throw new Error('storage full');}}});
  unsavedSplit.$('source-splitter').handlers.keydown({key: 'ArrowUp', preventDefault() {}});
  assert.equal(unsavedSplit.$('source-column').style['--source-share'], '48%', 'Unavailable storage never disables in-memory resizing');
  dividerEvent('pointerdown', {clientY: 360}); split.app.dispose();
  assert.equal(divider.hasPointerCapture(7), false); assert.equal(divider.dataset.dragging, 'false', 'Disposal releases an active drag');

  const first = appFixture(); await first.app.loadDevices(); await first.app.poll();
  assert.equal(first.$('device-select').value, '1', 'Prefer MacBook built-in input');
  first.$('device-select').value = '7'; await first.app.loadDevices();
  assert.equal(first.$('device-select').value, '7', 'Refreshing devices preserves a deliberate selection');
  assert.equal(first.$('start-button').disabled, false); assert.equal(first.$('stop-button').disabled, true);
  assert.equal(first.$('translation-heading').textContent, '文脈付きの日本語訳', 'Idle does not imply that saved fragment translations exist');
  assert.match(first.$('translation-empty').textContent, /発言がまとまると/);
  const emptyReplay = appFixture(); emptyReplay.app.acceptState(snapshot({session: {id: 'demo-zero', source_kind: 'replay'}, capture: {state: 'completed', audio_seconds: 0}}));
  assert.equal(emptyReplay.$('translation-heading').textContent, '文脈付きの日本語訳', 'Replay at 0:00 has the same honest pending state');
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
  const touchedBeforeState = appFixture(); touchedBeforeState.$('provider-select').value = 'off'; touchedBeforeState.$('provider-select').handlers.change();
  touchedBeforeState.app.acceptState(defaultCloudState);
  assert.equal(touchedBeforeState.$('provider-select').value, 'off', 'Input before the first idle snapshot is also respected');
  const unavailableAPI = appFixture();
  unavailableAPI.app.acceptState(snapshot({session: {id: 'active-mic', source_kind: 'microphone'}, capture: {state: 'recording', last_audio_at: clock, audio_seconds: 30}, analysis: {state: 'failed', provider: 'openai', error: 'APIに接続できません'}}));
  assert.match(unavailableAPI.$('analysis-error').textContent, /録音・保存は継続しています/);
  unavailableAPI.app.acceptState(snapshot({session: {id: 'active-mic', source_kind: 'microphone'}, capture: {state: 'stalled', last_audio_at: clock - 30, audio_seconds: 30}, analysis: {state: 'failed', provider: 'openai', error: 'APIに接続できません'}}));
  assert.doesNotMatch(unavailableAPI.$('analysis-error').textContent, /継続しています/);
  assert.match(unavailableAPI.$('analysis-error').textContent, /現在の状態を確認/);

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
  assert.equal(rich.$('translation-blocks').textContent, '和訳の例', 'Legacy fragment translations show only their prose');
  assert.equal(firstButton(rich.$('translation-blocks')), null, 'Translation prose has no source-reference controls');
  assert.equal(rich.$('transcript').children[0].classes.has('uncertain-source'), true, 'Uncertain raw speech is styled gray, without repeated labels');
  assert.match(rich.$('transcript').children[0].title, /0:03–0:12 · line-1 · EN · 認識が不確か/);
  assert.equal(rich.$('transcript').children[0].dataset.sourceId, 'line-1');
  assert.equal(rich.$('concepts-list').textContent, 'ConceptExplanation'); assert.equal(rich.$('capture-state').textContent, '再生中');
  const sourceLine = rich.$('transcript').children[0];
  rich.$('freeze-button').click();
  assert.equal(rich.$('freeze-button').attributes['aria-pressed'], 'true');
  const newer = structuredClone(richState); newer.capture.audio_seconds = 40; newer.lines.push({id: 'line-2', start_seconds: 12, end_seconds: 21, text: 'new', language: 'en'}); newer.analysis.result.headline.text = '新論点';
  rich.app.acceptState(newer);
  assert.equal(rich.$('headline').textContent, injected, 'Reading freeze keeps text stable');
  assert.equal(rich.$('capture-time').textContent, '0:40', 'Reading freeze never freezes capture status');
  assert.equal(sourceLine.classes.has('latest-source'), true, 'Frozen reading retains the latest marker for its displayed snapshot');
  rich.app.goLive(); assert.equal(rich.$('headline').textContent, '新論点'); assert.equal(rich.$('transcript').children.length, 2);
  assert.equal(sourceLine.classes.has('latest-source'), false, 'A new utterance clears the previous latest marker');
  assert.equal(rich.$('transcript').children[1].classes.has('latest-source'), true);
  assert.equal(sourceLine.attributes['aria-current'], 'false');
  rich.app.acceptState(richState);
  assert.equal(rich.$('transcript').children[0].classes.has('latest-source'), true, 'Rewind restores the latest utterance for that point');
  rich.app.acceptState(newer);
  assert.equal(rich.$('summary-heading').textContent, '直近の要点');

  const blockedTranslation = appFixture();
  const withBlocks = structuredClone(richState);
  withBlocks.analysis.result.block_translations = [{text: '文脈を踏まえたまとまりの訳。' + injected, source_ids: ['line-1']}];
  blockedTranslation.app.acceptState(withBlocks);
  assert.equal(blockedTranslation.$('translation-heading').textContent, '文脈付きの日本語訳');
  assert.match(blockedTranslation.$('translation-blocks').textContent, /文脈を踏まえたまとまりの訳/);
  assert.doesNotMatch(blockedTranslation.$('translation-blocks').textContent, /和訳の例/, 'Block translations never mix with old fragment translations');
  assert.equal(blockedTranslation.$('translation-blocks').children[0].children[0].textContent, '文脈を踏まえたまとまりの訳。' + injected);
  assert.equal(blockedTranslation.$('translation-blocks').textContent, '文脈を踏まえたまとまりの訳。' + injected, 'Legacy blocks contain no added time ranges or source labels');
  assert.equal(firstButton(blockedTranslation.$('translation-blocks')), null);
  assert.deepEqual(withBlocks.analysis.result.block_translations[0].source_ids, ['line-1'], 'Hiding references preserves the saved source IDs');
  const emptyBlocks = structuredClone(withBlocks); emptyBlocks.analysis.result.block_translations = [];
  blockedTranslation.app.acceptState(emptyBlocks);
  assert.equal(blockedTranslation.$('translation-heading').textContent, '文脈付きの日本語訳');
  assert.equal(blockedTranslation.$('translation-blocks').children.length, 0, 'An explicitly empty new block field does not fall back to older fragment translations');
  assert.equal(blockedTranslation.$('translation-empty').hidden, false);

  const continuous = appFixture();
  const continuousState = structuredClone(newer);
  continuousState.analysis.result.block_translations = [{text: 'Old sampled translation', source_ids: ['line-1']}];
  continuousState.translation = {enabled: true, state: 'running', pending_lines: 2, excluded_uncertain_lines: 1, native_lines: 0,
    through_seconds: 12, generated_at: 960, error: null, blocks: [{id: 't1', text: '継続する文脈訳' + injected, source_ids: ['line-1'], start_seconds: 3, end_seconds: 12, generated_at: 959, published_at: 960}]};
  continuous.app.acceptState(continuousState);
  assert.equal(continuous.$('translation-heading').textContent, '文脈付きの日本語訳');
  assert.equal(continuous.$('translation-blocks').children[0].children[0].textContent, '継続する文脈訳' + injected, 'Continuous translations remain plain text');
  assert.equal(continuous.$('translation-blocks').textContent, '継続する文脈訳' + injected, 'Continuous blocks contain only prose, without time ranges or source labels');
  assert.equal(firstButton(continuous.$('translation-blocks')), null);
  assert.deepEqual(continuousState.translation.blocks[0], {id: 't1', text: '継続する文脈訳' + injected, source_ids: ['line-1'], start_seconds: 3, end_seconds: 12, generated_at: 959, published_at: 960}, 'Presentation never strips provenance or publication timing from the received data');
  assert.doesNotMatch(continuous.$('translation-blocks').textContent, /Old sampled translation|和訳の例/, 'The independent queue takes precedence over sampled analysis translations');
  const continuousRow = continuous.$('translation-blocks').children[0];
  const continuousText = continuousRow.children[0];
  continuous.$('translation-blocks').focus();
  continuous.$('translation-blocks').scrollHeight = 1000; continuous.$('translation-blocks').clientHeight = 240; continuous.$('translation-blocks').scrollTop = 100;
  const newRaw = structuredClone(continuousState); newRaw.lines.push({id: 'line-3', start_seconds: 21, end_seconds: 28, text: 'Third utterance', language: 'en'});
  newRaw.translation.pending_lines = 3;
  continuous.app.acceptState(newRaw);
  assert.equal(continuous.$('translation-blocks').children[0], continuousRow, 'Raw arrivals preserve translation DOM');
  assert.equal(continuousRow.children[0], continuousText); assert.equal(continuous.dom.activeElement, continuous.$('translation-blocks'));
  assert.equal(continuous.$('translation-blocks').scrollTop, 100);
  const translationAppend = structuredClone(newRaw);
  translationAppend.translation.blocks.push({id: 't2', text: '次の文脈訳', source_ids: ['line-2'], start_seconds: 12, end_seconds: 21, generated_at: 969, published_at: 970});
  translationAppend.translation.pending_lines = 1;
  continuous.app.acceptState(translationAppend);
  assert.equal(continuous.$('translation-blocks').children.length, 2, 'Translations append without requiring a new ASR or analysis snapshot');
  assert.equal(continuous.$('translation-blocks').children[0], continuousRow, 'Appending a translation keeps prior block elements');
  assert.equal(continuousRow.children[0], continuousText); assert.equal(continuous.dom.activeElement, continuous.$('translation-blocks'));
  assert.equal(continuous.$('translation-blocks').textContent, '継続する文脈訳' + injected + '次の文脈訳', 'Appended blocks remain prose-only');
  assert.equal(continuous.$('translation-blocks').scrollTop, 100, 'Appending a translation does not move a reader above the bottom');
  continuous.$('translation-blocks').scrollTop = 740;
  const translationThird = structuredClone(translationAppend);
  translationThird.translation.blocks.push({id: 't3', text: '三つ目の文脈訳', source_ids: ['line-3'], start_seconds: 21, end_seconds: 28, generated_at: 979, published_at: 980});
  translationThird.translation.pending_lines = 0; translationThird.translation.state = 'completed';
  continuous.app.acceptState(translationThird);
  assert.equal(continuous.$('translation-blocks').children.length, 3, 'All completed blocks remain visible, rather than only the latest two');
  assert.equal(continuous.$('translation-blocks').scrollTop, 1000, 'A near-bottom reader follows newly translated blocks');
  continuous.$('freeze-button').click();
  assert.equal(continuous.$('freeze-button').attributes['aria-pressed'], 'true', 'The reading toolbar still freezes prose-only translation history');
  const translationFourth = structuredClone(translationThird);
  translationFourth.translation.blocks.push({id: 't4', text: '固定中の新しい訳', source_ids: ['line-3'], start_seconds: 21, end_seconds: 28, generated_at: 989, published_at: 990});
  translationFourth.translation.pending_lines = 4;
  continuous.app.acceptState(translationFourth);
  assert.equal(continuous.$('translation-blocks').children.length, 3, 'Reading freeze also freezes the translation history');
  continuous.app.goLive(); assert.equal(continuous.$('translation-blocks').children.length, 4);
  const historicalTranslations = structuredClone(translationFourth);
  historicalTranslations.translation.blocks.push({id: 'no-publication', text: '公開時刻不明の訳', source_ids: ['line-1'], generated_at: 940});
  historicalTranslations.analysis_history = [{through_seconds: 12, generated_at: 965, headline: {text: '早い分析', source_ids: ['line-1']}}];
  continuous.app.acceptState(historicalTranslations);
  await continuous.$('previous-analysis-button').click();
  assert.equal(continuous.$('translation-blocks').children.length, 1, 'History shows only translations published by that analysis timestamp');
  assert.equal(continuous.$('translation-blocks').textContent, '継続する文脈訳' + injected, 'Historical translations also show only prose');
  assert.doesNotMatch(continuous.$('translation-blocks').textContent, /次の文脈訳|三つ目|公開時刻不明/);
  const missingTime = structuredClone(historicalTranslations); delete missingTime.analysis_history[0].generated_at;
  continuous.app.acceptState(missingTime);
  await continuous.$('previous-analysis-button').click();
  assert.equal(continuous.$('translation-blocks').children.length, 0, 'A historical analysis with no timestamp never borrows current translations');
  continuous.app.goLive();
  const disabledContinuous = structuredClone(continuousState); disabledContinuous.translation.enabled = false;
  continuous.app.acceptState(disabledContinuous);
  assert.match(continuous.$('translation-blocks').textContent, /Old sampled translation/, 'Disabled continuous translation keeps the existing analysis renderer');

  const translationRetry = appFixture();
  const failedTranslation = structuredClone(continuousState); failedTranslation.translation.state = 'failed'; failedTranslation.translation.retry_required = true; failedTranslation.translation.error = 'API unavailable';
  translationRetry.state(failedTranslation); await translationRetry.app.poll();
  assert.equal(translationRetry.$('translation-retry-button').hidden, false);
  assert.match(translationRetry.$('translation-error').textContent, /API unavailable/);
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

  const scrolling = appFixture(); scrolling.app.acceptState(structuredClone(richState));
  const rawRow = scrolling.$('transcript').children[0];
  scrolling.$('transcript').scrollHeight = 1000; scrolling.$('transcript').clientHeight = 240; scrolling.$('transcript').scrollTop = 100;
  scrolling.app.acceptState(structuredClone(newer));
  assert.equal(scrolling.$('transcript').scrollTop, 100, 'Appending live speech does not pull a reader away from earlier text');
  assert.equal(scrolling.$('transcript').children[0], rawRow, 'Appending originals preserves existing source DOM nodes');
  const changedAnalysis = structuredClone(newer); changedAnalysis.analysis.result.headline.text = '分析だけ更新';
  scrolling.app.acceptState(changedAnalysis);
  assert.equal(scrolling.$('transcript').children[0], rawRow); assert.equal(scrolling.$('transcript').scrollTop, 100, 'Translation/analysis updates leave the original reading position intact');
  const interpretation = scrolling.$('headline');
  scrolling.$('concepts-list').focus();
  scrolling.$('transcript').scrollTop = 740;
  const appended = structuredClone(changedAnalysis); appended.lines.push({id: 'line-3', text: 'latest raw speech', start_seconds: 21, end_seconds: 28, language: 'en'});
  scrolling.app.acceptState(appended);
  assert.equal(scrolling.$('headline'), interpretation);
  assert.equal(scrolling.dom.activeElement, scrolling.$('concepts-list'), 'Raw ASR arrivals preserve the reader focus');
  assert.equal(scrolling.$('transcript').scrollTop, scrolling.$('transcript').scrollHeight, 'Near-bottom readers follow new raw speech');
  assert.doesNotMatch(scrolling.$('transcript').textContent, /和訳未作成|認識が不確か|0:21|EN/);
  scrolling.$('setup-settings').open = true; scrolling.app.acceptState(structuredClone(appended));
  assert.equal(scrolling.$('setup-settings').open, true, 'Settings remain open when the user opened them');
  assert.equal(scrolling.dom.nodes.has('questions-list'), false, 'Question suggestions are removed from the interface');

  const uncertain = appFixture();
  const uncertainLine = (id, value, overrides = {}) => ({id, text: value, start_seconds: 0, end_seconds: 5, language: 'en', uncertain: true, ...overrides});
  const firstUncertain = uncertainLine('u1', '  A   synthetic statement.  ');
  const repeatedUncertain = uncertainLine('u2', 'a synthetic\nstatement.', {start_seconds: 5, end_seconds: 10});
  const thirdUncertain = uncertainLine('u3', 'A synthetic statement.', {start_seconds: 10, end_seconds: 15});
  const uncertainState = snapshot({session: {id: 'uncertain-repeats', source_kind: 'replay'}, demo: {cursor_seconds: 5},
    capture: {state: 'recording', audio_seconds: 5}, lines: [firstUncertain]});
  uncertain.app.acceptState(uncertainState);
  const uncertainRow = uncertain.$('transcript').children[0];
  uncertainRow.focus();
  uncertain.$('transcript').scrollHeight = 1000;
  uncertain.$('transcript').scrollTop = 100;
  const duplicateState = {...uncertainState, demo: {cursor_seconds: 10}, capture: {...uncertainState.capture, audio_seconds: 10},
    lines: [firstUncertain, repeatedUncertain]};
  const duplicateEvidence = JSON.stringify(duplicateState);
  uncertain.app.acceptState(duplicateState);
  assert.equal(uncertain.$('transcript').children.length, 1, 'Consecutive uncertain repetitions differing only in whitespace or case appear once');
  assert.equal(uncertain.$('transcript').children[0], uncertainRow, 'A repeated recognition extends the existing row instead of replacing it');
  assert.equal(uncertainRow.textContent, firstUncertain.text, 'Grouping preserves the first original text without normalizing its display');
  assert.equal(uncertainRow.dataset.sourceId, 'u1', 'The first source ID remains the representative');
  assert.deepEqual(JSON.parse(uncertainRow.dataset.sourceIds), ['u1', 'u2'], 'Every repeated source ID remains attached to the displayed row');
  assert.equal(uncertainRow.classes.has('uncertain-source'), true);
  assert.equal(uncertainRow.classes.has('latest-source'), true, 'The group stays latest when the last incoming line is a duplicate');
  assert.equal(uncertainRow.attributes['aria-current'], 'true');
  assert.equal(uncertain.dom.activeElement, uncertainRow, 'Grouping does not move focus');
  assert.equal(uncertain.$('transcript').scrollTop, 100, 'A duplicate arrival does not pull a reader away from earlier text');
  assert.equal(JSON.stringify(duplicateState), duplicateEvidence, 'Display grouping never modifies source evidence or its IDs');
  assert.equal(duplicateState.lines[0], firstUncertain); assert.equal(duplicateState.lines[1], repeatedUncertain);

  uncertain.app.freeze();
  const thirdState = {...duplicateState, demo: {cursor_seconds: 15}, capture: {...duplicateState.capture, audio_seconds: 15},
    lines: [firstUncertain, repeatedUncertain, thirdUncertain]};
  uncertain.app.acceptState(thirdState);
  assert.deepEqual(JSON.parse(uncertainRow.dataset.sourceIds), ['u1', 'u2'], 'Reading freeze also freezes group membership');
  uncertain.app.goLive();
  assert.equal(uncertain.$('transcript').children[0], uncertainRow);
  assert.deepEqual(JSON.parse(uncertainRow.dataset.sourceIds), ['u1', 'u2', 'u3'], 'Returning to live reveals the later duplicate without a second row');
  const differentState = {...thirdState, demo: {cursor_seconds: 20}, capture: {...thirdState.capture, audio_seconds: 20},
    lines: [...thirdState.lines, uncertainLine('u4', 'A different statement.', {start_seconds: 15, end_seconds: 20})]};
  uncertain.app.acceptState(differentState);
  assert.equal(uncertain.$('transcript').children.length, 2);
  assert.equal(uncertainRow.classes.has('latest-source'), false);
  assert.equal(uncertainRow.attributes['aria-current'], 'false');
  assert.equal(uncertain.$('transcript').children[1].classes.has('latest-source'), true);
  uncertain.app.acceptState(duplicateState);
  assert.equal(uncertain.$('transcript').children.length, 1, 'Rewinding removes later nonduplicate rows');
  assert.deepEqual(JSON.parse(uncertain.$('transcript').children[0].dataset.sourceIds), ['u1', 'u2'], 'Rewinding removes future member IDs from a group');
  assert.equal(uncertain.$('transcript').children[0].classes.has('latest-source'), true);
  uncertain.app.acceptState(uncertainState);
  assert.deepEqual(JSON.parse(uncertain.$('transcript').children[0].dataset.sourceIds), ['u1'], 'Rewinding before the repetition restores a single-source row');
  assert.equal(JSON.stringify(duplicateState), duplicateEvidence, 'Freeze and rewind leave the received evidence unchanged');

  const repetitionBoundaries = appFixture();
  for (const [label, lines] of [
    ['reliable repetition', [uncertainLine('a', 'Same text.', {uncertain: false}), uncertainLine('b', 'Same text.', {uncertain: false})]],
    ['a reliable boundary', [uncertainLine('a', 'Same text.'), uncertainLine('b', 'Same text.', {uncertain: false}), uncertainLine('c', 'Same text.')]],
    ['unmarked recognition', [uncertainLine('a', 'Same text.'), uncertainLine('b', 'Same text.', {uncertain: undefined})]],
    ['different intervening content', [uncertainLine('a', 'Same text.'), uncertainLine('b', 'Other text.'), uncertainLine('c', 'Same text.')]],
    ['different language', [uncertainLine('a', 'Same text.'), uncertainLine('b', 'Same text.', {language: 'ja'})]],
    ['different punctuation', [uncertainLine('a', 'Same text.'), uncertainLine('b', 'Same text!')]],
    ['empty content', [uncertainLine('a', ''), uncertainLine('b', ' \t\n ')]],
    ['an empty boundary', [uncertainLine('a', 'Same text.'), uncertainLine('b', ''), uncertainLine('c', 'Same text.')]],
  ]) {
    const boundaryState = snapshot({session: {id: label, source_kind: 'replay'}, lines});
    const savedEvidence = JSON.stringify(boundaryState);
    repetitionBoundaries.app.acceptState(boundaryState);
    assert.equal(repetitionBoundaries.$('transcript').children.length, lines.length, `${label} must not be collapsed`);
    assert.deepEqual(repetitionBoundaries.$('transcript').children.map(row => row.dataset.sourceId), lines.map(line => line.id));
    assert.equal(JSON.stringify(boundaryState), savedEvidence, `${label} retains its original evidence`);
  }

  const timeline = appFixture();
  const historyState = structuredClone(newer);
  const early = {through_seconds: 12, generated_at: 960, headline: {text: injected, source_ids: ['line-1']}, summary: [{text: '当時の要点', source_ids: ['line-1']}], source_ranges: [{source_id: 'line-1', start_seconds: 3, end_seconds: 12}]};
  const later = {through_seconds: 21, generated_at: 990, headline: {text: '最新の整理', source_ids: ['line-2']}, source_ranges: [{source_id: 'line-1', start_seconds: 3, end_seconds: 12}, {source_id: 'line-2', start_seconds: 18, end_seconds: 21}]};
  historyState.analysis = {state: 'completed', provider: 'local', through_seconds: 21, generated_at: 990, result: later};
  historyState.lines[1].start_seconds = 18;
  historyState.analysis_history = [early, later];
  timeline.app.acceptState(historyState);
  assert.equal(timeline.$('headline').textContent, '最新の整理');
  const originalEvidence = JSON.stringify(historyState.analysis_history[0]);
  const previousButton = timeline.$('previous-analysis-button');
  previousButton.focus(); await previousButton.click();
  assert.equal(timeline.dom.activeElement, previousButton, 'History arrows retain keyboard focus');
  assert.equal(timeline.$('headline').textContent, injected, 'The previous arrow uses the preserved original interpretation');
  assert.equal(timeline.$('summary-list').textContent, '当時の要点');
  assert.equal(firstButton(timeline.$('summary-list')), null);
  assert.equal(timeline.$('focus-heading').textContent, 'その時点で伝えていたこと');
  assert.equal(timeline.$('transcript').children.length, 2, 'History selection retains the source pane');
  assert.equal(timeline.$('analysis-time').textContent, '0:21', 'Reading history never rewinds processing status');
  assert.equal(previousButton.disabled, true);
  assert.equal(timeline.$('next-analysis-button').disabled, false);
  const newest = structuredClone(historyState); newest.capture.audio_seconds = 55; newest.analysis.through_seconds = 45;
  newest.analysis.result = {through_seconds: 45, generated_at: 1000, headline: {text: 'さらに新しい整理', source_ids: ['line-2']}};
  newest.analysis_history.push(newest.analysis.result);
  timeline.app.acceptState(newest);
  assert.equal(timeline.$('headline').textContent, injected); assert.equal(timeline.$('analysis-time').textContent, '0:45');
  assert.equal(timeline.dom.activeElement, previousButton, 'New analyses do not replace the navigation control being used');
  await timeline.$('next-analysis-button').click(); assert.equal(timeline.$('headline').textContent, '最新の整理');
  await timeline.$('next-analysis-button').click(); assert.equal(timeline.$('headline').textContent, 'さらに新しい整理');
  assert.equal(JSON.stringify(historyState.analysis_history[0]), originalEvidence, 'Reading navigation preserves original provenance');
  timeline.app.goLive(); assert.equal(timeline.$('headline').textContent, 'さらに新しい整理');
  assert.equal(timeline.$('focus-heading').textContent, 'いま伝えていること'); assert.equal(timeline.$('latest-button').hidden, true);
  const manyHistory = structuredClone(historyState);
  manyHistory.analysis_history = Array.from({length: 65}, (_, index) => ({...early, through_seconds: index + 1, generated_at: 1000 + index}));
  manyHistory.analysis.result = manyHistory.analysis_history.at(-1);
  timeline.app.acceptState(manyHistory);
  let steps = 0;
  while (!previousButton.disabled && steps < 100) { await previousButton.click(); steps++; }
  assert(steps >= 65, 'Arrow navigation retains previously received history beyond the server window');

  const paging = appFixture();
  const pagedState = {...structuredClone(historyState), capabilities: {analysis_history_paging: true}};
  const oldest = {through_seconds: 1, generated_at: 900, headline: {text: 'Persisted earlier idea', source_ids: []}};
  paging.app.acceptState(pagedState);
  await paging.$('previous-analysis-button').click();
  paging.history(async () => {throw new Error('synthetic history failure');});
  await paging.$('previous-analysis-button').click();
  assert.equal(paging.$('headline').textContent, injected, 'A paging failure retains the interpretation being read');
  assert.equal(paging.$('action-message').hidden, false);
  assert.match(paging.$('action-message').textContent, /履歴を取得できません/);
  assert.equal(paging.$('previous-analysis-button').disabled, false, 'The same arrow permits a deliberate retry');
  paging.history(async () => response({session_id: 's1', items: [oldest], has_more: false, next_cursor: null}));
  await paging.$('previous-analysis-button').click();
  assert.equal(paging.$('headline').textContent, 'Persisted earlier idea', 'The previous arrow loads and selects older persisted history');
  assert.equal(paging.$('previous-analysis-button').disabled, true);
  assert.equal(paging.$('action-message').hidden, true, 'Successful retry clears the previous history error');

  const pendingPage = appFixture(); pendingPage.app.acceptState(pagedState);
  await pendingPage.$('previous-analysis-button').click();
  let releaseHistory;
  pendingPage.history(() => new Promise(resolve => {releaseHistory = resolve;}));
  const loadingHistory = pendingPage.$('previous-analysis-button').click();
  assert.equal(pendingPage.$('previous-analysis-button').attributes['aria-busy'], 'true');
  await pendingPage.$('next-analysis-button').click();
  releaseHistory(response({session_id: 's1', items: [oldest], has_more: false, next_cursor: null}));
  await loadingHistory;
  assert.equal(pendingPage.$('headline').textContent, '最新の整理', 'A delayed page never undoes navigation made while it was loading');

  const schedules = appFixture();
  const scheduled = structuredClone(continuousState);
  scheduled.translation.schedule = {state: 'waiting', reason: 'interval', remaining_seconds: 30, interval_seconds: 60, wait_seconds: 60};
  scheduled.analysis.schedule = {state: 'busy', reason: 'shared_slot'};
  schedules.app.acceptState(scheduled);
  assert.match(schedules.$('translation-schedule-text').textContent, /30秒/);
  assert.equal(schedules.$('translation-schedule-compact').textContent, '30秒', 'A normal timer keeps only remaining seconds beside its heading');
  assert.match(schedules.$('translation-schedule-summary').title, /翻訳: 次の開始判定まで 30秒/);
  assert.match(schedules.$('translation-schedule-summary').attributes['aria-label'], /生成完了までの時間ではありません/);
  assert.match(schedules.$('translation-schedule-text').textContent, /生成完了までの時間ではありません/);
  assert.equal(schedules.$('translation-schedule-summary').attributes['aria-live'], 'off', 'The per-second countdown never becomes a screen-reader announcement');
  assert.match(schedules.$('translation-schedule-ring').style.background, /180deg/);
  assert.match(schedules.$('analysis-schedule-text').textContent, /ほかの処理/);
  assert.equal(schedules.$('analysis-schedule-compact').textContent, '順番待ち');
  schedules.$('translation-schedule').open = true;
  clock += 5; schedules.app.renderStatus();
  assert.match(schedules.$('translation-schedule-text').textContent, /25秒/);
  assert.equal(schedules.$('translation-schedule-compact').textContent, '25秒');
  assert.equal(schedules.$('translation-schedule').open, true, 'Timer ticks keep its explanation open while being read');
  schedules.app.freeze(); schedules.app.renderStatus();
  assert.match(schedules.$('translation-schedule-text').textContent, /現在の処理/);
  clock += 20; schedules.app.renderStatus();
  assert.match(schedules.$('translation-schedule-text').textContent, /状態不明/, 'Stale snapshots cannot promise an update');
  assert.equal(schedules.$('translation-schedule-compact').textContent, '状態不明');
  assert.match(schedules.$('translation-schedule-ring').style.background, /0deg/);
  clock = 1000;
  scheduled.translation.schedule = {state: 'waiting', reason: 'retry', remaining_seconds: 2, wait_seconds: 4,
    retry: {attempts: 1, max_attempts: 3}};
  schedules.app.acceptState(scheduled);
  assert.equal(schedules.$('translation-schedule-compact').textContent, '再試行 2秒');
  assert.match(schedules.$('translation-schedule-summary').attributes['aria-label'], /自動再試行まで 2秒（1\/3）/);
  assert.match(schedules.$('translation-schedule-ring').style.background, /180deg/, 'Retry uses its own wait duration');
  assert.equal(schedules.$('translation-pause-retries').hidden, false);
  clock = 1003; schedules.app.renderStatus();
  assert.match(schedules.$('translation-schedule-text').textContent, /開始待ち/, 'Zero never pretends generation is completed');
  assert.equal(schedules.$('translation-schedule-compact').textContent, '開始待ち');
  clock = 1000;
  scheduled.translation.schedule = {state: 'busy', reason: 'request'};
  schedules.app.acceptState(scheduled);
  assert.match(schedules.$('translation-schedule-text').textContent, /生成中/);
  assert.equal(schedules.$('translation-schedule-compact').textContent, '生成中');
  assert.equal(schedules.$('translation-pause-retries').hidden, true);
  for (const remaining of [NaN, Infinity, null]) assert.doesNotMatch(schedulePresentation({state: 'waiting', remaining_seconds: remaining}, true).label, /NaN|Infinity/);
  assert.equal(schedulePresentation({state: 'complete', reason: 'no_pending'}, true).label, '処理済み');
  assert.equal(schedulePresentation({state: 'blocked', reason: 'manual_retry'}, true).compact, '要確認');
  assert.equal(schedulePresentation({state: 'complete', reason: 'no_pending'}, true).compact, '処理済み');
  assert.equal(schedulePresentation({state: 'waiting', reason: 'offline', remaining_seconds: 20}, true).compact, '接続待ち 20秒');
  const continuationSchedule = {state: 'waiting', reason: 'continuation', remaining_seconds: null, due_at: null, interval_seconds: 60};
  assert.deepEqual(schedulePresentation(continuationSchedule, true, 120),
    {state: 'waiting', label: '文の続き待ち', compact: '続き待ち', fraction: 0});
  const continuation = appFixture();
  const continuationState = {...structuredClone(continuousState), updated_at: clock};
  continuationState.translation = {...continuationState.translation, state: 'waiting', schedule: continuationSchedule};
  const continuationEvidence = JSON.stringify(continuationState.translation.schedule);
  continuation.app.acceptState(continuationState);
  assert.equal(continuation.$('translation-schedule').dataset.state, 'waiting');
  assert.equal(continuation.$('translation-schedule-compact').textContent, '続き待ち');
  assert.match(continuation.$('translation-schedule-summary').attributes['aria-label'], /翻訳: 文の続き待ち/);
  assert.match(continuation.$('translation-schedule-text').textContent, /開始時刻は未定/);
  assert.match(continuation.$('translation-schedule-ring').style.background, /0deg/);
  assert.equal(continuation.$('translation-pause-retries').hidden, true);
  clock += 5; continuation.app.renderStatus();
  assert.equal(continuation.$('translation-schedule-compact').textContent, '続き待ち', 'Wall time cannot complete an unfinished sentence');
  assert.match(continuation.$('translation-schedule-ring').style.background, /0deg/);
  clock += 20; continuation.app.renderStatus();
  assert.equal(continuation.$('translation-schedule-compact').textContent, '状態不明', 'A stale continuation wait is no longer confirmed');
  clock = 1000; continuation.app.acceptState(continuationState);
  continuation.rejectState(true); await continuation.app.poll();
  assert.equal(continuation.$('translation-schedule-compact').textContent, '状態不明', 'A disconnected continuation wait remains unknown');
  assert.equal(JSON.stringify(continuationState.translation.schedule), continuationEvidence, 'Rendering preserves the missing deadline');
  let finishPause;
  schedules.post(() => new Promise(resolve => {finishPause = resolve;})); schedules.state(scheduled);
  const pausing = schedules.app.pauseRetries('translation'); await schedules.app.pauseRetries('translation');
  assert.equal(schedules.calls.filter(call => call.url === '/api/pause-retries').length, 1);
  assert.equal(schedules.calls.find(call => call.url === '/api/pause-retries').init.headers['Content-Type'], 'application/json');
  finishPause(response({ok: true})); await pausing;

  const recognitionSchedule = appFixture();
  recognitionSchedule.app.acceptState(snapshot());
  assert.equal(recognitionSchedule.$('asr-schedule').hidden, true, 'No session means no invented recognition countdown');
  const recognitionState = snapshot({session: {id: 'asr-schedule', source_kind: 'microphone'},
    capture: {state: 'recording', audio_seconds: 5, last_audio_at: clock},
    asr: {state: 'waiting', schedule: {state: 'waiting', reason: 'recording', clock: 'audio', remaining_seconds: 10, interval_seconds: 15}}});
  recognitionSchedule.app.acceptState(recognitionState);
  assert.equal(recognitionSchedule.$('asr-schedule').hidden, false);
  assert.equal(recognitionSchedule.$('asr-schedule-compact').textContent, '10秒');
  assert.match(recognitionSchedule.$('asr-schedule-ring').style.background, /240deg/);
  assert.match(recognitionSchedule.$('asr-schedule-summary').attributes['aria-label'], /原文: 次の音声区間の受付まで 10秒/);
  assert.match(recognitionSchedule.$('asr-schedule-text').textContent, /15秒ごと/);
  assert.match(recognitionSchedule.$('asr-schedule-text').textContent, /受信済みの音声時間/);
  assert.match(recognitionSchedule.$('asr-schedule-text').textContent, /文字起こしの完了時刻を予測するものではありません/);
  assert.equal(recognitionSchedule.$('asr-schedule-summary').attributes['aria-live'], 'off');
  recognitionSchedule.$('asr-schedule').open = true;
  clock += 5; recognitionSchedule.app.renderStatus();
  assert.equal(recognitionSchedule.$('asr-schedule-compact').textContent, '10秒', 'Wall time cannot invent audio that has not been observed');
  assert.equal(recognitionSchedule.$('asr-schedule').open, true);
  const moreAudio = {...recognitionState, updated_at: clock, capture: {...recognitionState.capture, audio_seconds: 7, last_audio_at: clock},
    asr: {...recognitionState.asr, schedule: {...recognitionState.asr.schedule, remaining_seconds: 8}}};
  recognitionSchedule.app.acceptState(moreAudio);
  assert.equal(recognitionSchedule.$('asr-schedule-compact').textContent, '8秒', 'An observed audio update advances the recognition circle');
  recognitionSchedule.app.freeze(); recognitionSchedule.app.renderStatus();
  assert.match(recognitionSchedule.$('asr-schedule-text').textContent, /現在の処理/);
  clock += 13; recognitionSchedule.app.renderStatus();
  assert.equal(recognitionSchedule.$('asr-schedule-compact').textContent, '状態不明');
  assert.match(recognitionSchedule.$('asr-schedule-ring').style.background, /0deg/, 'A stale source clock cannot imply capture progress');
  clock = 1000;
  for (const [state, reason, label] of [
    ['busy', 'request', '認識中'], ['busy', 'queued', '認識待ち'], ['blocked', 'stalled', '入力待ち'],
    ['blocked', 'failed', '要確認'], ['blocked', 'saved_view', '保存結果'], ['blocked', 'closed', '処理停止'],
    ['blocked', 'unknown', '状態不明'], ['blocked', 'stopping', '保存待ち'], ['idle', 'no_source', '音声待ち'], ['idle', 'finalizing', '完了確認中'],
  ]) {
    recognitionSchedule.app.acceptState({...recognitionState, asr: {state: 'waiting', schedule: {state, reason, clock: 'audio'}}});
    assert.equal(recognitionSchedule.$('asr-schedule-compact').textContent, label);
    assert.match(recognitionSchedule.$('asr-schedule-ring').style.background, /0deg/, `${state}/${reason} never claims a completion ETA`);
  }
  recognitionSchedule.app.acceptState({...recognitionState, asr: {state: 'completed', schedule: {state: 'complete', reason: 'no_pending', clock: 'audio'}}});
  assert.equal(recognitionSchedule.$('asr-schedule-compact').textContent, '処理済み');
  for (const incomplete of [
    {failed_chunks: [{index: 0, start_seconds: 0, end_seconds: 15}]}, {error: 'Unconfirmed synthetic recognition'},
    {state: 'failed'}, {state: 'paused'}, {completion_confirmed: false},
  ]) {
    const unresolved = {...recognitionState, asr: {state: 'completed', schedule: {state: 'complete', reason: 'no_pending'}, ...incomplete}};
    recognitionSchedule.app.acceptState(unresolved);
    assert.equal(recognitionSchedule.$('asr-schedule-compact').textContent, '要確認', 'Unresolved recognition never becomes a completed circle');
    assert.equal(unresolved.asr.schedule.state, 'complete', 'Display safeguards never rewrite received evidence');
  }
  recognitionSchedule.app.acceptState({...recognitionState, asr: {state: 'waiting'}});
  assert.equal(recognitionSchedule.$('asr-schedule-compact').textContent, '未取得', 'Older snapshots without an ASR schedule remain honest');
  assert.equal(schedulePresentation({...recognitionState.asr.schedule, remaining_seconds: 0}, true, 5, 'asr').compact, '受付待ち');
  assert.equal(schedulePresentation(recognitionState.asr.schedule, true, 5).compact, '10秒', 'The explicit audio clock applies to the shared schedule renderer');

  const processing = appFixture();
  processing.app.renderStatus();
  assert.equal(processing.$('processing-overview').textContent, '準備中');
  assert(!processing.$('processing-details').open, 'The initial lack of a snapshot is not an incident');
  const healthyProcessing = snapshot({session: {id: 'processing', source_kind: 'microphone'},
    capture: {state: 'recording', audio_seconds: 30, last_audio_at: clock}, asr: {state: 'running'},
    translation: {enabled: true, state: 'running', completion_confirmed: false}, analysis: {state: 'waiting', provider: 'openai'}});
  processing.app.acceptState(healthyProcessing);
  assert.equal(processing.$('processing-overview').textContent, '録音中 · 文字起こし中 · 翻訳中 · 整理待ち');
  assert(!processing.$('processing-details').open, 'Ordinary in-flight work never opens processing details');
  processing.$('processing-details').open = true;
  processing.app.acceptState(healthyProcessing);
  assert.equal(processing.$('processing-details').open, true, 'Healthy polls leave user-opened processing details open');
  processing.$('processing-details').open = false;
  processing.app.acceptState(healthyProcessing);
  assert.equal(processing.$('processing-details').open, false, 'Healthy polls leave a collapsed panel collapsed');
  const failedProcessing = {...healthyProcessing, translation: {enabled: true, state: 'failed', error: 'Temporary network failure'}};
  processing.app.acceptState(failedProcessing);
  assert.equal(processing.$('processing-details').open, true, 'A newly failed hidden stage is immediately discoverable');
  assert.match(processing.$('processing-overview').textContent, /翻訳失敗/);
  assert.equal(processing.$('processing-overview').dataset.tone, 'error');
  processing.$('processing-details').open = false;
  processing.app.acceptState(failedProcessing); processing.app.renderStatus();
  assert.equal(processing.$('processing-details').open, false, 'Repeated polls and clock ticks respect dismissing the same error');
  processing.app.acceptState({...failedProcessing, translation: {...failedProcessing.translation, error: 'Different failure'}});
  assert.equal(processing.$('processing-details').open, true, 'A new failure remains discoverable after closing an earlier one');
  processing.$('processing-details').open = false;
  clock += 13; processing.app.renderStatus();
  assert.equal(processing.$('processing-details').open, true, 'Losing current state is visible even if the previous error was dismissed');
  assert.equal(processing.$('processing-overview').textContent, '状態不明');
  processing.$('processing-details').open = false; processing.app.renderStatus();
  assert.equal(processing.$('processing-details').open, false, 'An unchanged stale snapshot does not reopen details every second');
  clock = 1000;
  processing.app.acceptState({...failedProcessing, translation: {...failedProcessing.translation, error: 'Different failure'}});
  assert.equal(processing.$('processing-details').open, false, 'Reconnection to the same failure does not undo its dismissal');
  processing.app.acceptState(healthyProcessing);
  processing.app.acceptState(failedProcessing);
  assert.equal(processing.$('processing-details').open, true, 'A failure that clears and returns opens details again');
  for (const changed of [
    {capture: {state: 'stalled'}}, {asr: {state: 'paused'}}, {analysis: {state: 'unknown'}},
    {asr: {state: 'completed', failed_chunks: [{index: 1, start_seconds: 0, end_seconds: 15}]}},
    {analysis: {state: 'completed', completion_confirmed: false}},
    {analysis: {state: 'waiting', schedule: {state: 'blocked', reason: 'manual_retry', retry: {paused: true}}}},
  ]) {
    processing.app.acceptState(healthyProcessing); processing.$('processing-details').open = false;
    processing.app.acceptState({...healthyProcessing, ...changed});
    assert.equal(processing.$('processing-details').open, true, `A new unresolved condition opens details: ${JSON.stringify(changed)}`);
    if (changed.analysis?.completion_confirmed === false) {
      assert.match(processing.$('processing-overview').textContent, /整理未確認/);
      assert.doesNotMatch(processing.$('processing-overview').textContent, /整理済み/, 'An unconfirmed worker exit never becomes a completed summary');
    }
  }
  processing.app.acceptState(healthyProcessing); processing.$('processing-details').open = false;
  const reservedProcessing = {...healthyProcessing, cloud_budget: {reserved_usd: 0.01}};
  processing.app.acceptState(reservedProcessing);
  assert.equal(processing.$('processing-details').open, false, 'The normal budget reservation for a running request is not an incident');
  processing.app.acceptState({...reservedProcessing, translation: {enabled: true, state: 'completed'}});
  assert.equal(processing.$('processing-details').open, true, 'An unresolved reservation after generation ends is discoverable');
  assert.match(processing.$('processing-overview').textContent, /費用未確定/);

  const concepts = appFixture();
  const conceptState = structuredClone(historyState);
  conceptState.capabilities = {analysis_history_paging: true};
  conceptState.analysis_history[0].concepts = [{term: 'A', explanation: 'Earlier definition', basis: 'background', source_ids: ['line-1']}];
  conceptState.analysis_history[1].concepts = [{term: 'B', explanation: 'Current definition', basis: 'lecture', source_ids: ['line-2']}];
  conceptState.analysis.result = conceptState.analysis_history[1];
  concepts.app.acceptState(conceptState);
  const conceptFirst = concepts.$('concepts-list').children[0];
  assert.equal(conceptFirst.textContent, 'AEarlier definition', 'Concept cards show the term and explanation without badges, time, or references');
  assert.equal(firstButton(conceptFirst), null);
  concepts.$('concepts-list').focus();
  concepts.$('concepts-list').scrollTop = 90;
  const revisedConcept = structuredClone(conceptState);
  const revision = {...revisedConcept.analysis.result, generated_at: 1000, through_seconds: 30,
    headline: {text: 'Revised idea', source_ids: ['line-2']},
    concepts: [{term: 'B', explanation: 'Corrected definition', basis: 'background', source_ids: ['line-2']}]};
  revisedConcept.analysis = {...revisedConcept.analysis, generated_at: 1000, through_seconds: 30, result: revision};
  revisedConcept.analysis_history.push(revision);
  concepts.app.acceptState(revisedConcept);
  assert.equal(concepts.$('concepts-list').children.length, 3, 'Changed meaning and basis retain an earlier version');
  assert.equal(concepts.$('concepts-list').children[0], conceptFirst);
  assert.equal(concepts.dom.activeElement, concepts.$('concepts-list'));
  assert.equal(concepts.$('concepts-list').scrollTop, 90, 'New analysis never pulls the reader away from a concept');
  assert.equal(concepts.$('concepts-latest-button').hidden, false);
  concepts.$('concepts-latest-button').click();
  assert.equal(concepts.$('concepts-list').scrollTop, concepts.$('concepts-list').scrollHeight);
  const emptyConcept = structuredClone(revisedConcept);
  emptyConcept.analysis.result = {...revision, concepts: [], generated_at: 1001, through_seconds: 40};
  emptyConcept.analysis.generated_at = 1001; emptyConcept.analysis.through_seconds = 40;
  emptyConcept.analysis_history.push(emptyConcept.analysis.result);
  concepts.app.acceptState(emptyConcept);
  assert.equal(concepts.$('concepts-list').children.length, 3, 'An empty new analysis does not erase prior explanations');
  concepts.$('previous-analysis-button').click();
  assert.equal(concepts.$('headline').textContent, 'Revised idea');
  concepts.$('previous-analysis-button').click();
  assert.equal(concepts.$('concepts-list').children.length, 2, 'Earlier views never show a later correction');
  concepts.app.goLive();
  concepts.history(async () => response({session_id: 's1', items: [{through_seconds: 1, generated_at: 900,
    headline: {text: 'Before the in-memory window', source_ids: []},
    concepts: [{term: 'Old', explanation: 'Persisted definition', basis: 'lecture', source_ids: []}]}], has_more: false, next_cursor: null, skipped: {malformed: 1}}));
  await concepts.app.loadOlderHistory();
  assert.equal(concepts.$('concepts-list').children[0].children[0].children[0].textContent, 'Old');
  assert.match(concepts.$('action-message').textContent, /1件/);
  const replacement = structuredClone(conceptState); replacement.session.id = 'replacement'; replacement.analysis_history = [];
  replacement.analysis.result = null;
  concepts.app.acceptState(replacement);
  assert.equal(concepts.$('concepts-list').children.length, 0, 'Explanations never cross session boundaries');
  assert.equal(concepts.$('latest-button').hidden, true);

  const provenance = structuredClone(historyState);
  const originalData = JSON.stringify(provenance);
  const reading = appFixture(); reading.app.acceptState(provenance);
  assert.equal(JSON.stringify(provenance), originalData, 'Removing provenance widgets does not strip stored source IDs or timing');
  for (const id of ['headline-sources', 'analysis-coverage', 'coverage-list', 'analysis-history', 'history-list', 'history-more-button',
    'translation-caption', 'translation-range', 'translation-status', 'flow-list', 'line-count', 'new-count', 'freeze-notice',
    'processing-caption', 'model-caption', 'connection-text', 'observed-at', 'session-title']) {
    assert.equal(reading.dom.nodes.has(id), false, `Removed metadata has no dormant DOM widget: ${id}`);
  }
  const readingHTML = fs.readFileSync(path.join(__dirname, '../lecture-dashboard/index.html'), 'utf8');
  assert.doesNotMatch(readingHTML, /翻訳の詳細|整理の詳細|補足について|これまでの整理|AIの解釈|自動認識 · グレー/);
  assert(readingHTML.indexOf('id="action-message"') > readingHTML.indexOf('</section>'), 'Action errors are outside setup, so saved views do not hide them');

  const rewind = appFixture();
  rewind.app.acceptState({...conceptState, demo: {}, capture: {audio_seconds: 30}});
  rewind.app.acceptState({...snapshot(), session: conceptState.session, demo: {}, capture: {audio_seconds: 0}});
  assert.equal(rewind.$('concepts-list').children.length, 0);
  assert.equal(rewind.$('previous-analysis-button').disabled, true, 'Demo rewind clears future navigation history');
  rewind.app.acceptState({...conceptState, demo: {cursor_seconds: 100}, capture: {audio_seconds: 30}});
  rewind.app.acceptState({...snapshot(), session: conceptState.session, demo: {cursor_seconds: 90}, capture: {audio_seconds: 30}});
  assert.equal(rewind.$('previous-analysis-button').disabled, true, 'Rewinding generation after audio ended clears future results');

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

  const stopping = appFixture(); await stopping.app.loadDevices();
  const beforeStop = snapshot({session: {id: 'stop-session', source_kind: 'microphone'},
    processing_active: true, processing_stop_requested: false, processing_stop_status: null,
    capture: {state: 'recording', last_audio_at: clock, audio_seconds: 80},
    asr: {state: 'completed', through_seconds: 75, queue_seconds: 5, schedule: {state: 'waiting', reason: 'interval', remaining_seconds: 10, wait_seconds: 15}},
    translation: {enabled: true, state: 'failed', retry_required: true, error: 'Previous translation failure',
      schedule: {state: 'waiting', reason: 'retry', remaining_seconds: 4, wait_seconds: 10}},
    analysis: {state: 'failed', provider: 'openai', error: 'Previous analysis failure', schedule: {state: 'blocked', reason: 'manual_retry'}},
    lines: [{id: 'kept', start_seconds: 0, end_seconds: 15, text: 'Saved before stop.', translation_ja: '停止前に保存した訳。', language: 'en'}]});
  stopping.app.acceptState(beforeStop);
  assert.equal(stopping.$('stop-button').textContent, '録音・処理を停止');
  assert.equal(stopping.$('retry-button').hidden, false);
  assert.equal(stopping.$('translation-pause-retries').hidden, false);
  const savedReading = stopping.$('transcript').textContent;
  let releaseStop;
  stopping.post(() => new Promise(resolve => {releaseStop = resolve;}));
  const stopTask = stopping.app.recordAction('stop');
  await stopping.app.recordAction('stop');
  assert.equal(stopping.calls.filter(call => call.url === '/api/stop').length, 1, 'A stop POST cannot be duplicated before its response');
  assert.equal(stopping.$('stop-button').disabled, true);
  assert.equal(stopping.$('start-button').disabled, true);
  assert.match(stopping.$('stop-button').textContent, /要求中/);
  assert.equal(stopping.$('retry-button').hidden, true);
  assert.equal(stopping.$('translation-retry-button').hidden, true);
  assert.equal(stopping.$('translation-pause-retries').hidden, true, 'A pending stop hides automatic retry controls immediately');
  assert.equal(stopping.$('translation-schedule-compact').textContent, '停止確認中');
  assert.match(stopping.$('asr-schedule-ring').style.background, / 0deg/);
  await stopping.app.retryAnalysis(); await stopping.app.retryTranslation(); await stopping.app.pauseRetries('translation');
  assert.equal(stopping.calls.filter(call => call.init.method === 'POST').length, 1, 'Retry actions cannot race a pending stop');
  const awaitingStop = structuredClone(beforeStop);
  awaitingStop.processing_stop_requested = true; awaitingStop.processing_stop_status = 'stopping';
  awaitingStop.capture.state = 'stopping'; awaitingStop.asr.state = 'paused';
  awaitingStop.analysis = {state: 'running', provider: 'openai', worker_alive: true, schedule: {state: 'blocked', reason: 'stopped'}};
  stopping.state(awaitingStop); releaseStop(response({ok: true})); await stopTask;
  assert.match(stopping.$('session-message').textContent, /音声の保存を確認/);
  assert.equal(stopping.$('capture-state').textContent, '保存完了を待機');
  assert.equal(stopping.$('analysis-schedule-compact').textContent, '終了待ち', 'Already-started work is not presented as cancelled');
  assert.match(stopping.$('setup-hint').textContent, /送信済み.*取り消せず.*料金/);
  assert.doesNotMatch(stopping.$('action-message').textContent, /残りの認識・分析は続きます/);
  assert.equal(stopping.$('start-button').disabled, true);
  assert.equal(stopping.$('stop-button').disabled, true);
  assert.equal(stopping.$('transcript').textContent, savedReading, 'Stopping preserves the text already being read');
  const savedAudioWaiting = structuredClone(awaitingStop); savedAudioWaiting.capture.state = 'completed';
  stopping.app.acceptState(savedAudioWaiting);
  assert.equal(stopping.$('capture-state').textContent, '完了');
  assert.match(stopping.$('session-message').textContent, /開始した処理の終了/);
  const fullyStopped = structuredClone(savedAudioWaiting);
  fullyStopped.processing_active = false; fullyStopped.processing_stop_status = 'stopped';
  fullyStopped.asr.completion_confirmed = false;
  fullyStopped.analysis = {state: 'paused', provider: 'openai', completion_confirmed: false, schedule: {state: 'blocked', reason: 'stopped'}};
  stopping.app.acceptState(fullyStopped);
  assert.equal(stopping.$('start-button').disabled, false, 'A confirmed stop permits a new session despite retained unfinished work');
  assert.equal(stopping.$('stop-button').textContent, '停止済み');
  assert.match(stopping.$('session-message').textContent, /未処理分は残しています/);
  assert.equal(stopping.$('asr-state').textContent, '停止・未処理あり');
  assert.equal(stopping.$('analysis-schedule-compact').textContent, '停止');
  assert.match(stopping.$('processing-overview').textContent, /文字起こし未処理/);
  assert.doesNotMatch(stopping.$('processing-overview').textContent, /文字起こし未確認/);
  assert.match(stopping.$('translation-error').textContent, /Previous translation failure/, 'Stopping does not erase a prior failure');
  await stopping.app.retryAnalysis(); await stopping.app.retryTranslation(); await stopping.app.pauseRetries('analysis');
  assert.equal(stopping.calls.filter(call => call.init.method === 'POST').length, 1, 'Stopped work cannot be restarted by hidden retry handlers');
  const stoppedAfterCaptureFailure = structuredClone(fullyStopped);
  stoppedAfterCaptureFailure.capture = {...fullyStopped.capture, state: 'failed', error: 'PCM input stopped'};
  stopping.app.acceptState(stoppedAfterCaptureFailure);
  assert.equal(stopping.$('capture-state').textContent, '失敗・要確認');
  assert.equal(stopping.$('capture-error').textContent, 'PCM input stopped');
  assert.match(stopping.$('session-message').textContent, /録音・保存は失敗/);
  assert.match(stopping.$('processing-overview').textContent, /録音失敗/, 'A confirmed processing stop never relabels capture failure as successful saving');
  stopping.app.acceptState({...beforeStop, session: {...beforeStop.session, id: 'next-session'}});
  assert.equal(stopping.$('retry-button').hidden, false, 'A new session has its own retry controls');
  assert.equal(stopping.$('stop-button').disabled, false);

  const lostStop = appFixture(); await lostStop.app.loadDevices(); lostStop.app.acceptState(beforeStop);
  lostStop.post(() => {throw new Error('stop response lost');}); lostStop.rejectState(true);
  await lostStop.app.recordAction('stop');
  assert.equal(lostStop.$('stop-button').disabled, true, 'An unconfirmed stop remains locked through a disconnect');
  assert.equal(lostStop.$('translation-retry-button').hidden, true);
  assert.match(lostStop.$('connection-warning').textContent, /停止を意味しません/);
  lostStop.rejectState(false); lostStop.state(fullyStopped); await lostStop.app.poll();
  assert.equal(lostStop.$('stop-button').textContent, '停止済み');
  assert.equal(lostStop.calls.filter(call => call.url === '/api/stop').length, 1);

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
  console.log('Lecture UI checks passed (state freshness, reading freeze, reading navigation, plain text, action reconciliation).');
}
run().catch(error => {for (const fixture of fixtures) fixture.app.dispose(); console.error(error); process.exitCode = 1;});
