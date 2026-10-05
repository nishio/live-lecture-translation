/* Local-only lecture companion. All model and transcript text stays plain text. */
(() => {
  'use strict';

  const ACTIVE_CAPTURE = new Set(['starting', 'recording', 'stalled', 'stopping']);
  const BUSY_WORK = new Set(['starting', 'waiting', 'running', 'stopping']);
  const number = value => typeof value === 'number' && Number.isFinite(value);
  const text = value => typeof value === 'string' ? value : (number(value) ? String(value) : '');
  const array = value => Array.isArray(value) ? value : [];

  function formatTime(value) {
    if (!number(value)) return '—';
    const seconds = Math.max(0, Math.floor(value));
    const hours = Math.floor(seconds / 3600);
    return (hours ? `${hours}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}` : String(Math.floor(seconds / 60))) + `:${String(seconds % 60).padStart(2, '0')}`;
  }

  function ageText(at, now) {
    if (!number(at) || at <= 0 || at > now + 10) return '時刻不明';
    const seconds = Math.max(0, Math.floor(now - at));
    if (seconds < 60) return `${seconds}秒前`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}分前`;
    return `${Math.floor(seconds / 3600)}時間前`;
  }

  function isFresh(state, connected, now) {
    return !!(connected && state && number(state.updated_at) && state.updated_at <= now + 10 && now - state.updated_at <= 12);
  }

  function capturePresentation(state, connected, now) {
    if (!isFresh(state, connected, now)) return {label: '不明', tone: 'warning'};
    const capture = state.capture || {};
    if (capture.state === 'recording') {
      if (!number(capture.last_audio_at) || now - capture.last_audio_at > 12) return {label: '入力の更新待ち', tone: 'warning'};
      return {label: state.session?.source_kind === 'replay' ? '再生中' : '録音中', tone: 'good'};
    }
    return ({
      idle: {label: '待機', tone: 'muted'},
      starting: {label: '開始処理中', tone: 'warning'},
      stalled: {label: '入力途絶・要確認', tone: 'warning'},
      stopping: {label: '保存完了を待機', tone: 'warning'},
      completed: {label: '完了', tone: 'muted'},
      failed: {label: '失敗・要確認', tone: 'error'},
    })[capture.state] || {label: '不明', tone: 'warning'};
  }

  function getControlState(state, connected, now, hasDevice, pending) {
    const active = ACTIVE_CAPTURE.has(state?.capture?.state);
    const preparing = state?.asr_preparation?.state === 'preparing';
    const preparationNotReady = !!state?.asr_preparation && state.asr_preparation.state !== 'ready';
    const processing = preparing || state?.processing_active === true || state?.processing_stop_status === 'stopping'
      || BUSY_WORK.has(state?.asr?.state) || BUSY_WORK.has(state?.analysis?.state) || BUSY_WORK.has(state?.translation?.state);
    return {
      startDisabled: !isFresh(state, connected, now) || !hasDevice || active || processing || preparationNotReady || !!pending || ['checking', 'blocked'].includes(state?.preflight?.state),
      stopDisabled: !(active || preparing || (state?.session && !state?.demo && processing))
        || (preparing ? state.asr_preparation.stop_requested === true : state?.processing_stop_requested === true) || !!pending,
      settingsDisabled: active || processing || !!pending,
    };
  }

  function translationMap(state) {
    const result = new Map();
    for (const item of array(state?.analysis?.result?.translations)) {
      if (text(item?.source_id) && text(item?.text)) result.set(text(item.source_id), text(item.text));
    }
    for (const line of array(state?.lines)) {
      if (text(line?.translation_ja)) result.set(text(line.id), text(line.translation_ja));
    }
    return result;
  }

  function transcriptDisplayGroups(lines) {
    const groups = [];
    for (const line of lines) {
      const normalized = text(line.text).replace(/\s+/g, ' ').trim().toLowerCase();
      const key = line.uncertain && normalized ? JSON.stringify([text(line.language), normalized]) : null;
      const previous = groups[groups.length - 1];
      // This only groups visible rows. Keep each source line and its uncertainty
      // intact in the received state for translation, history and saved evidence.
      if (key !== null && previous?.key === key) previous.lines.push(line);
      else groups.push({key, lines: [line]});
    }
    return groups;
  }

  function provisionalSignature(preview) {
    return [preview?.enabled, preview?.state, preview?.error, preview?.revision, preview?.window_start_seconds,
      preview?.through_seconds, preview?.published_at, preview?.lines];
  }

  function contentSignature(state) {
    return JSON.stringify([state?.session?.id, state?.analysis?.generated_at, state?.analysis?.through_seconds, state?.analysis?.result, state?.lines,
      state?.translation?.enabled, state?.translation?.blocks, state?.analysis_history, provisionalSignature(state?.provisional_asr), state?.asr?.through_seconds]);
  }

  function schedulePresentation(schedule, fresh, elapsed = 0, kind = 'analysis') {
    if (!fresh) return {state: 'unknown', label: '状態不明', compact: '状態不明', fraction: 0};
    if (!schedule) return {state: 'unknown', label: '更新予定は未取得', compact: '未取得', fraction: 0};
    if (schedule.reason === 'stopped') return {state: 'blocked', label: '新しい処理は停止', compact: '停止', fraction: 0};
    if (schedule.reason === 'stop_requested') return {state: 'blocked', label: '停止の反映を確認中', compact: '停止確認中', fraction: 0};
    if (schedule.reason === 'finishing') return {state: 'busy', label: '開始済みの処理の終了待ち', compact: '終了待ち', fraction: 0};
    if (schedule.state === 'complete') return {state: 'complete', label: '処理済み', compact: '処理済み', fraction: 0};
    if (schedule.state === 'waiting' && schedule.reason === 'continuation') {
      return {state: 'waiting', label: '文の続き待ち', compact: '続き待ち', fraction: 0};
    }
    if (schedule.reason === 'initial_translation') {
      return {state: 'waiting', label: '最初の日本語訳を優先しています', compact: '最初の訳待ち', fraction: 0};
    }
    const audioClock = schedule.clock === 'audio' || kind === 'asr';
    const remaining = number(schedule.remaining_seconds) ? Math.max(0, schedule.remaining_seconds - (audioClock ? 0 : Math.max(0, elapsed))) : null;
    const total = number(schedule.wait_seconds) ? schedule.wait_seconds : schedule.interval_seconds;
    if (schedule.state === 'waiting' && remaining !== null) {
      const action = kind === 'asr' ? '次の音声区間の受付' : (({retry: '自動再試行', offline: '接続の再確認'})[schedule.reason] || '次の開始判定');
      const attempt = schedule.reason === 'retry' && number(schedule.retry?.attempts) && number(schedule.retry?.max_attempts)
        ? `（${schedule.retry.attempts}/${schedule.retry.max_attempts}）` : '';
      const prefix = ({retry: '再試行 ', offline: '接続待ち '})[schedule.reason] || '';
      return {state: 'waiting', label: remaining > 0 ? `${action}まで ${Math.ceil(remaining)}秒${attempt}` : (kind === 'asr' ? '次の音声区間の受付待ち' : `${action}の開始待ち${attempt}`),
        compact: remaining > 0 ? `${prefix}${Math.ceil(remaining)}秒` : (kind === 'asr' ? '受付待ち' : '開始待ち'),
        fraction: number(total) && total > 0 ? Math.min(1, remaining / total) : 0};
    }
    if (kind === 'asr') {
      const label = ({request: '認識中', queued: '認識待ち', stalled: '入力待ち', stopping: '音声の保存待ち', finalizing: '認識の完了確認中', no_source: '音声待ち',
        saved_view: '保存結果の閲覧中', closed: '処理停止', unknown: '状態不明', failed: '文字起こしの確認が必要', error: '文字起こしの確認が必要'})[schedule.reason]
        || ({busy: '認識中', due: '認識待ち', blocked: '確認が必要', idle: '待機'})[schedule.state] || '状態不明';
      const compact = ({stopping: '保存待ち', finalizing: '完了確認中', saved_view: '保存結果', failed: '要確認', error: '要確認'})[schedule.reason]
        || (schedule.state === 'blocked' && label === '確認が必要' ? '要確認' : label);
      return {state: schedule.state, label, compact, fraction: 0};
    }
    const label = ({request: '生成中', shared_slot: 'ほかの処理の完了待ち', asr: '文字起こし待ち',
      manual_retry: '再試行の操作待ち', disabled: 'オフ', no_source: '原文待ち', no_pending: '新しい対象待ち',
      closed: '処理停止', saved_view: '保存結果の閲覧中'})[schedule.reason]
      || ({busy: '処理中', due: '開始待ち', blocked: '確認が必要', idle: '待機', complete: '処理済み'})[schedule.state] || '状態不明';
    const compact = ({shared_slot: '順番待ち', manual_retry: '要確認', saved_view: '保存結果', no_pending: '新着待ち'})[schedule.reason] || label;
    return {state: schedule.state, label, compact, fraction: 0};
  }

  function createApp(doc, transport, options = {}) {
    const now = options.now || (() => Date.now() / 1000);
    const setTimer = options.setTimer || setTimeout;
    const clearTimer = options.clearTimer || clearTimeout;
    const $ = id => doc.getElementById(id);
    let state = null;
    let connected = false;
    let connectionError = '';
    let lastReceivedAt = null;
    let devices = [];
    let deviceSelectionInitialized = false;
    let devicesRequested = false;
    let devicesLoading = false;
    let idlePreferences = null;
    let lastChosenProvider = 'local';
    const providerModels = {local: 'qwen3:4b', openai: 'gpt-6-luna'};
    let modelOptionsSignature = '';
    let initialDefaultsApplied = false;
    let providerSettingsTouched = false;
    let languageSettingsTouched = false;
    let deviceSettingsTouched = false;
    let storedDevicePreference = null;
    let preferenceKey = null;
    let agendaSignature = '';
    let preflightSignature = '';
    let storage = options.storage;
    if (storage === undefined) { try { storage = doc.defaultView?.localStorage; } catch (_) { storage = null; } }
    const layoutPreferenceKey = 'lecture-layout:v1';
    let sourceShare = 50;
    let sourceDrag = null;
    let failuresSignature = '';
    let processingAlerts = new Set();
    let processingSession = null;
    let pending = null;
    let retryBusy = false;
    let translationRetryBusy = false;
    let frozen = false;
    let historySelection = null;
    let historyNotice = '';
    let historyItems = new Map();
    let historySession = null;
    let historyCursor = null;
    let historyHasMore = true;
    let historyLoading = false;
    let historyGeneration = 0;
    let conceptElements = new Map();
    let conceptSeenCount = 0;
    let conceptSession = null;
    const pauseRetryBusy = new Set();
    const stopRequested = () => state?.processing_stop_requested === true || pending?.kind === 'stop';
    let displayedState = null;
    let renderedSignature = '';
    let lineElements = new Map();
    let renderedTranscriptSignature = '';
    let transcriptEmptyElement = null;
    let renderedProvisionalSignature = '';
    // These nodes move into the one transcript scroller when needed. Keep
    // references while detached; they are never a second reading pane.
    const previewNodes = new Map(['provisional-region', 'provisional-heading', 'provisional-help', 'provisional-error',
      'provisional-lines'].map(id => [id, $(id)]));
    const previewNode = id => previewNodes.get(id);
    previewNode('provisional-region').replaceChildren(...['provisional-heading', 'provisional-help', 'provisional-error', 'provisional-lines'].map(previewNode));
    let renderedTranslationSignature = '';
    let translationElements = new Map();
    let translationSession = null;
    let translationMode = '';
    let renderedInterpretationSignature = '';
    let pollTask = null;
    let pollSequence = 0;
    let pollTimer = null;
    let tickTimer = null;
    let disposed = false;

    const put = (id, value) => { $(id).textContent = value; };
    const showText = (id, value) => { put(id, value); $(id).hidden = !value; };
    const element = (tag, className, value) => {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (value !== undefined) node.textContent = value;
      return node;
    };
    const pill = (id, label, tone) => { put(id, label); $(id).dataset.tone = tone; };
    const message = (value, tone = 'good') => { showText('action-message', value); $('action-message').dataset.tone = tone; };

    function saveSourceShare() {
      try { storage?.setItem(layoutPreferenceKey, JSON.stringify({version: 1, sourceShare})); } catch (_) { /* Resizing still works without browser storage. */ }
    }

    function setSourceShare(value, save = false) {
      if (!number(value)) return;
      const next = Math.round(Math.max(25, Math.min(75, value)) * 10) / 10;
      const changed = next !== sourceShare;
      sourceShare = next;
      $('source-column').style.setProperty('--source-share', `${sourceShare}%`);
      const splitter = $('source-splitter');
      splitter.setAttribute('aria-valuemin', '25');
      splitter.setAttribute('aria-valuemax', '75');
      splitter.setAttribute('aria-valuenow', String(sourceShare));
      splitter.setAttribute('aria-valuetext', `原文 ${sourceShare}%、日本語訳 ${Math.round((100 - sourceShare) * 10) / 10}%`);
      if (save && changed) saveSourceShare();
    }

    function sourceColumnRect() {
      const rect = $('source-column').getBoundingClientRect();
      return number(rect.top) && number(rect.height) && rect.height > 14 ? rect : null;
    }

    function endSourceDrag(event, save = true) {
      if (!sourceDrag || (event && event.pointerId !== sourceDrag.pointerId)) return;
      const {pointerId, initialShare} = sourceDrag;
      sourceDrag = null;
      const splitter = $('source-splitter');
      splitter.dataset.dragging = 'false';
      try { if (splitter.hasPointerCapture(pointerId)) splitter.releasePointerCapture(pointerId); } catch (_) { /* Capture can already be lost. */ }
      if (save && sourceShare !== initialShare) saveSourceShare();
    }

    function moveSourceDivider(event) {
      if (!sourceDrag || event.pointerId !== sourceDrag.pointerId || !number(event.clientY)) return;
      const rect = sourceColumnRect();
      if (!rect) return;
      event.preventDefault();
      setSourceShare((event.clientY - sourceDrag.grabOffset - rect.top) / rect.height * 100);
    }

    function initializeSourceSplitter() {
      let saved;
      try { saved = JSON.parse(storage?.getItem(layoutPreferenceKey) || 'null'); } catch (_) { /* Invalid or unavailable settings use an even split. */ }
      setSourceShare(saved?.version === 1 && number(saved.sourceShare) ? saved.sourceShare : 50);
      const splitter = $('source-splitter');
      splitter.addEventListener('pointerdown', event => {
        if (disposed || sourceDrag || event.button !== 0 || event.isPrimary === false || !number(event.pointerId) || !number(event.clientY)) return;
        const rect = sourceColumnRect();
        if (!rect) return;
        try { splitter.setPointerCapture(event.pointerId); } catch (_) { return; }
        sourceDrag = {pointerId: event.pointerId, initialShare: sourceShare,
          grabOffset: event.clientY - rect.top - rect.height * sourceShare / 100};
        splitter.dataset.dragging = 'true';
        splitter.focus({preventScroll: true});
        event.preventDefault();
      });
      splitter.addEventListener('pointermove', moveSourceDivider);
      splitter.addEventListener('pointerup', event => { moveSourceDivider(event); endSourceDrag(event); });
      splitter.addEventListener('pointercancel', event => endSourceDrag(event));
      splitter.addEventListener('lostpointercapture', event => endSourceDrag(event));
      splitter.addEventListener('keydown', event => {
        if (disposed || event.altKey || event.ctrlKey || event.metaKey) return;
        const next = {ArrowUp: sourceShare - 2, ArrowDown: sourceShare + 2, Home: 25, End: 75}[event.key];
        if (!number(next)) return;
        event.preventDefault();
        endSourceDrag(null);
        setSourceShare(next, true);
      });
      splitter.addEventListener('dblclick', event => {
        if (disposed || event.button !== 0) return;
        event.preventDefault();
        endSourceDrag(null);
        setSourceShare(50, true);
      });
    }

    function saveIdlePreferences() {
      if (!preferenceKey || getControlState(state, connected, now(), true, pending).settingsDisabled) return;
      const device = devices.find(item => text(item.id) === $('device-select').value);
      const saved = {version: 1, language: $('language-select').value, provider: $('provider-select').value,
        model: $('model-input').value.trim().slice(0, 100), deviceUid: text(device?.uid), deviceName: text(device?.name)};
      try { storage?.setItem(preferenceKey, JSON.stringify(saved)); } catch (_) { /* Private browsing may disable storage. */ }
    }

    function applyStoredDevice() {
      if (!storedDevicePreference || deviceSettingsTouched || !devices.length) return;
      const preferred = storedDevicePreference.deviceUid ? devices.find(device => text(device.uid) === storedDevicePreference.deviceUid)
        : devices.find(device => text(device.name) === storedDevicePreference.deviceName);
      if (preferred) $('device-select').value = text(preferred.id);
      else {
        const placeholder = element('option', '', '前回の入力がありません。選び直してください');
        placeholder.value = ''; $('device-select').appendChild(placeholder); $('device-select').value = '';
      }
      deviceSelectionInitialized = true;
      if (idlePreferences) { idlePreferences.device = $('device-select').value; idlePreferences.deviceInitialized = true; }
      storedDevicePreference = null;
    }

    function applyInitialPreferences() {
      if (!state || initialDefaultsApplied) return;
      initialDefaultsApplied = true;
      const capability = state.capabilities || {};
      const date = /^\d{4}-\d{2}-\d{2}$/.test(text(capability.agenda?.date)) ? capability.agenda.date : 'default';
      preferenceKey = `lecture-idle:v1:${capability.cloud_enabled === true ? 'cloud' : 'local'}:${date}`;
      let saved = null;
      try { saved = JSON.parse(storage?.getItem(preferenceKey) || 'null'); } catch (_) { /* Use server defaults if invalid. */ }
      if (!saved || saved.version !== 1) saved = null;
      if ((state.capture?.state !== 'idle' || state.session) && !saved) return;
      if (!languageSettingsTouched) {
        const language = ['auto', 'en', 'ja'].includes(saved?.language) ? saved.language : capability.default_language;
        if (['auto', 'en', 'ja'].includes(language)) $('language-select').value = language;
      }
      if (!providerSettingsTouched) {
        const allowed = provider => ['local', 'off'].includes(provider) || (provider === 'openai' && capability.cloud_enabled === true);
        const preferred = allowed(saved?.provider) ? saved.provider : capability.default_provider;
        if (allowed(preferred)) {
          $('provider-select').value = preferred;
          lastChosenProvider = preferred;
          const savedModel = saved?.provider === preferred && text(saved?.model).length <= 100 ? text(saved.model) : '';
          const permittedModel = preferred !== 'openai' || array(capability.cloud_models).includes(savedModel);
          const model = (permittedModel && savedModel) || (preferred === capability.default_provider ? text(capability.default_model) : providerModels[preferred]);
          if (preferred !== 'off' && model) { $('model-input').value = model; providerModels[preferred] = model; }
        }
      }
      if (!deviceSettingsTouched && (text(saved?.deviceUid) || text(saved?.deviceName))) {
        storedDevicePreference = {deviceUid: text(saved.deviceUid), deviceName: text(saved.deviceName)};
        applyStoredDevice();
      }
    }

    async function fetchJSON(url, init = {}) {
      const controller = new AbortController();
      const timeout = setTimer(() => controller.abort(), 12000);
      try {
        const response = await transport(url, {cache: 'no-store', ...init, signal: controller.signal});
        let payload;
        try { payload = await response.json(); } catch (_) { throw new Error('サーバーの応答を読み取れませんでした'); }
        if (!response.ok) {
          const error = new Error(text(payload?.error) || text(payload?.message) || `HTTP ${response.status}`);
          error.confirmed = true;
          throw error;
        }
        return payload;
      } finally { clearTimer(timeout); }
    }

    function syncProviderOptions() {
      const enabled = state?.capabilities?.cloud_enabled === true;
      const select = $('provider-select');
      const existing = Array.from(select.children).find(option => option.value === 'openai');
      if (enabled && !existing) { const option = element('option', '', 'OpenAI（文字のみ送信）'); option.value = 'openai'; select.appendChild(option); }
      else if (!enabled && existing) {
        const wasCloud = select.value === 'openai';
        select.replaceChildren(...Array.from(select.children).filter(option => option.value !== 'openai'));
        if (wasCloud) { select.value = 'local'; $('model-input').value = providerModels.local; lastChosenProvider = 'local'; }
      }
    }

    function renderProviderDetails() {
      const chosen = $('provider-select').value;
      const candidates = chosen === 'openai' ? array(state?.capabilities?.cloud_models).map(text).filter(Boolean) : ['qwen3:4b'];
      const signature = JSON.stringify([chosen, candidates]);
      if (signature !== modelOptionsSignature) {
        modelOptionsSignature = signature;
        $('model-options').replaceChildren();
        for (const model of candidates) { const option = element('option'); option.value = model; $('model-options').appendChild(option); }
      }
      $('model-input').placeholder = chosen === 'openai' ? (text(state?.capabilities?.default_model) || 'サーバーの既定モデル') : 'qwen3:4b';
      $('local-quality-note').hidden = !(chosen === 'local' || (state?.analysis?.provider === 'local' && state.analysis.result));
      const budget = state?.cloud_budget || {};
      const known = number(budget.budget_usd) && number(budget.spent_usd) && number(budget.reserved_usd);
      // The backend's spent_usd already includes reservations retained on failures.
      const remaining = known ? Math.max(0, budget.budget_usd - budget.spent_usd) : null;
      const limit = number(budget.budget_usd) ? `$${budget.budget_usd.toFixed(2)}` : '未取得';
      const available = number(remaining) ? `$${remaining.toFixed(4)}` : '未取得';
      const reserved = number(budget.reserved_usd) ? `$${budget.reserved_usd.toFixed(4)}` : '未取得';
      const scope = state?.cloud_scope || {};
      const scopeLimit = number(scope.max_seconds) ? scope.max_seconds : scope.microphone_max_seconds;
      const scopeText = number(scopeLimit) && number(scope.remaining_seconds)
        ? `文字送信の音声範囲: 残り ${formatTime(scope.remaining_seconds)} / 共有上限 ${formatTime(scopeLimit)}` : '文字送信の音声範囲: 未取得';
      const scopeError = scope.authorized_today === false || text(scope.error) ? ` · 範囲確認: ${text(scope.error) || '本日は送信できません'}` : '';
      showText('cloud-notice', chosen === 'openai' ? `原音はMacに保存し、認識した原文をOpenAIへ送信します。共有の日額上限 ${limit} · 残額 ${available} · 予約 ${reserved}${text(budget.budget_date) ? `（${text(budget.budget_date)} JST）` : ''}\n${scopeText}${scopeError}` : '');
      const chosenModel = $('model-input').value.trim() || text(state?.capabilities?.default_model);
      const shortModel = ({'gpt-6-luna': 'Luna', 'gpt-6.1-sol': 'Sol'})[chosenModel] || chosenModel;
      put('model-setting-summary', chosen === 'off' ? 'モデル設定（オフ）' : `モデル: ${shortModel || '既定'}`);
    }

    function providerChanged() {
      providerSettingsTouched = true;
      if (lastChosenProvider !== 'off') providerModels[lastChosenProvider] = $('model-input').value.trim() || providerModels[lastChosenProvider];
      lastChosenProvider = $('provider-select').value;
      if (lastChosenProvider !== 'off') $('model-input').value = providerModels[lastChosenProvider] || '';
      renderControls();
      saveIdlePreferences();
    }

    function renderControls() {
      syncProviderOptions();
      applyInitialPreferences();
      const sessionIsBusy = state?.session && (ACTIVE_CAPTURE.has(state.capture?.state) || state.processing_active === true || BUSY_WORK.has(state.asr?.state) || BUSY_WORK.has(state.analysis?.state));
      if (sessionIsBusy) {
        if (!idlePreferences) idlePreferences = {device: $('device-select').value, deviceInitialized: deviceSelectionInitialized,
          language: $('language-select').value, provider: $('provider-select').value, model: $('model-input').value};
        if (['auto', 'en', 'ja'].includes(state.session.language)) $('language-select').value = state.session.language;
        if (['local', 'off'].includes(state.analysis?.provider) || (state.analysis?.provider === 'openai' && state.capabilities?.cloud_enabled === true)) $('provider-select').value = state.analysis.provider;
        $('model-input').value = text(state.analysis?.model);
        const selected = state.session.source_kind === 'replay' ? '__replay__' : text(state.capture?.device);
        if (selected) {
          const select = $('device-select');
          if (!Array.from(select.children).some(option => option.value === selected)) {
            const option = element('option', '', selected === '__replay__' ? '保存音声（マイク不使用）' : `使用中の入力: ${selected}`);
            option.value = selected; select.appendChild(option);
          }
          select.value = selected;
        }
      } else if (idlePreferences) {
        $('language-select').value = idlePreferences.language;
        const cloudUnavailable = idlePreferences.provider === 'openai' && state?.capabilities?.cloud_enabled !== true;
        $('provider-select').value = cloudUnavailable ? 'local' : idlePreferences.provider;
        $('model-input').value = cloudUnavailable ? providerModels.local : idlePreferences.model;
        const preferred = devices.find(device => text(device.id) === idlePreferences.device) || (!idlePreferences.deviceInitialized ? (devices.find(device => /macbook/i.test(device.name)) || devices[0]) : null);
        if (preferred) $('device-select').value = text(preferred.id);
        else {
          const placeholder = element('option', '', devices.length ? '音声入力を選択してください' : '音声入力を再取得してください');
          placeholder.value = ''; $('device-select').appendChild(placeholder); $('device-select').value = '';
        }
        idlePreferences = null;
      }
      const currentControl = getControlState(state, connected, now(), !!$('device-select').value, pending);
      $('start-button').disabled = currentControl.startDisabled;
      $('stop-button').disabled = currentControl.stopDisabled;
      for (const id of ['device-select', 'language-select', 'provider-select']) $(id).disabled = currentControl.settingsDisabled;
      $('model-input').disabled = currentControl.settingsDisabled || $('provider-select').value === 'off';
      $('devices-button').disabled = currentControl.settingsDisabled || devicesLoading;
      renderProviderDetails();
      const selectedDevice = devices.find(device => text(device.id) === $('device-select').value);
      const selectedModel = $('model-input').value.trim() || text(state?.capabilities?.default_model);
      const modelLabel = ({'gpt-6-luna': 'Luna', 'gpt-6.1-sol': 'Sol'})[selectedModel] || selectedModel;
      const inputLabel = $('device-select').value === '__replay__' ? '保存音声' : (text(selectedDevice?.name) || '入力を確認');
      const languageLabel = ({en: '英語', ja: '日本語', auto: '自動'})[$('language-select').value] || '';
      const providerLabel = $('provider-select').value === 'off' ? '原文のみ' : modelLabel;
      const preparing = ['checking', 'blocked'].includes(state?.preflight?.state) ? ' · 準備を確認' : '';
      put('settings-summary', [inputLabel, languageLabel, providerLabel].filter(Boolean).join(' · ') + preparing);
      const preparation = state?.asr_preparation;
      put('start-button', pending?.kind === 'start' ? (pending.phase === 'sending' ? '開始を要求中…' : '開始結果を確認中…')
        : preparation?.state === 'preparing' ? '音声認識を準備中…' : '● 録音を開始');
      $('preparation-retry-button').hidden = !['failed', 'paused'].includes(preparation?.state);
      $('preparation-retry-button').disabled = !!pending || !isFresh(state, connected, now());
      put('preparation-retry-button', pending?.kind === 'prepare' ? '準備の再試行を確認中…' : '準備を再試行');
      put('stop-button', pending?.kind === 'stop' ? (pending.phase === 'sending' ? '停止を要求中…' : '停止を確認中…')
        : (preparation?.state === 'preparing' ? (preparation.stop_requested ? '準備の停止を確認中…' : '準備を停止')
          : (state?.processing_stop_requested === true ? (state.processing_stop_status === 'stopped' ? '停止済み' : '停止を確認中…') : '録音・処理を停止')));
      $('retry-button').hidden = stopRequested() || state?.analysis?.state !== 'failed' || state?.analysis?.provider === 'off'
        || state?.analysis?.schedule?.reason === 'retry';
      $('retry-button').disabled = stopRequested() || retryBusy || !isFresh(state, connected, now());
      put('retry-button', retryBusy ? '分析の再試行を要求中…' : '分析を再試行');
      $('freeze-button').disabled = !displayedState;
      $('freeze-button').setAttribute('aria-pressed', String(frozen));
      put('freeze-button', frozen ? '表示を固定中' : '閲覧を固定');
      $('latest-button').hidden = !frozen;
      renderHistoryNavigation();
    }

    function renderStatus() {
      const current = now();
      const fresh = isFresh(state, connected, current);
      renderEmptyTranscriptStatus(fresh);
      showText('connection-warning', !fresh && state ? `現在の録音状態は不明です。通信の途絶は、録音の停止を意味しません。${connectionError ? `\n${connectionError}` : '\n最新の状態を取得しています。'}` : (!connected && connectionError ? `サーバーに接続できません。${connectionError}` : ''));
      const capture = state?.capture || {};
      const asr = state?.asr || {};
      const analysis = state?.analysis || {};
      const captureView = capturePresentation(state, connected, current);
      let sessionMessage = text(state?.message);
      const isProgressMessage = ['マイクを起動しています。', '録音を終了し、保存済みの音声を処理しています。'].includes(sessionMessage);
      if (state?.session?.source_kind === 'microphone' && isProgressMessage) {
        if (!fresh) sessionMessage = '現在の録音状態を確認できません。録音・保存欄の最新状態を確認してください。';
        else if (capture.state === 'recording') sessionMessage = captureView.tone === 'good' ? '録音・保存が進んでいます。' : '音声入力の更新を待っています。録音・保存欄の最終受信を確認してください。';
        else if (capture.state === 'stalled') sessionMessage = '音声入力が途絶えています。録音の停止を確認した状態ではありません。';
        else if (capture.state === 'stopping') sessionMessage = '録音の停止と保存完了を確認しています。';
        else if (capture.state === 'completed') sessionMessage = '録音・保存は完了しました。原文と解析の処理状態を確認してください。';
        else if (capture.state === 'failed') sessionMessage = '録音・保存で問題が起きました。録音・保存欄のエラーを確認してください。';
      }
      if (state?.processing_stop_requested === true) {
        if (!fresh) sessionMessage = '停止を要求済みですが、現在の録音・処理状態は確認できません。';
        else if (state.processing_stop_status === 'stopped') sessionMessage = capture.state === 'failed'
          ? '処理は停止しましたが、録音・保存は失敗した状態です。保存済みの内容と未処理分は残しています。'
          : '録音と新しい処理の停止を確認しました。未処理分は残しています。';
        else if (ACTIVE_CAPTURE.has(capture.state)) sessionMessage = '新しい認識・翻訳・分析を停止しました。録音の停止と音声の保存を確認しています。';
        else sessionMessage = '新しい処理を停止しました。すでに開始した処理の終了を確認しています。';
      }
      showText('session-message', sessionMessage);
      pill('capture-state', captureView.label, captureView.tone);
      put('capture-label', state?.session?.source_kind === 'replay' ? '保存音声の再生' : '録音・保存');
      put('capture-time', formatTime(capture.audio_seconds));
      const level = number(capture.rms_dbfs) ? Math.max(-60, Math.min(0, capture.rms_dbfs)) : -60;
      const inputIsLive = fresh && capture.state === 'recording' && number(capture.last_audio_at) && current - capture.last_audio_at <= 12;
      $('input-meter-fill').style.width = `${inputIsLive ? (level + 60) / 60 * 100 : 0}%`;
      $('input-meter').setAttribute('aria-valuenow', String(level));
      $('input-meter').setAttribute('aria-valuetext', inputIsLive && number(capture.rms_dbfs) ? `${capture.rms_dbfs.toFixed(1)} dBFS` : '入力レベル不明');
      const inputDetails = [];
      if (number(capture.rms_dbfs)) inputDetails.push(`${inputIsLive ? '入力' : '前回入力'} ${capture.rms_dbfs.toFixed(1)} dBFS`);
      if (capture.last_audio_at) inputDetails.push(`音声の受信 ${ageText(capture.last_audio_at, current)}`);
      put('capture-detail', inputDetails.join(' · ') || (state?.session ? '保存した音声の長さ' : '音声入力を待っています'));
      showText('capture-error', [capture.state === 'stalled' ? '音声入力が途絶えています。録音の停止を確認した状態ではありません。' : '', text(capture.error)].filter(Boolean).join('\n'));

      const stageLabels = {idle: '待機', waiting: '待機中', running: '処理中', completed: '完了', failed: '失敗・要確認', paused: '保留', off: 'オフ', disabled: 'オフ'};
      for (const [name, stage] of [['asr', asr], ['analysis', analysis]]) {
        const partial = name === 'asr' && stage.state === 'completed' && array(stage.failed_chunks).length > 0;
        const label = name === 'analysis' && stage.provider === 'off' ? 'オフ' : (partial ? '一部未認識'
          : (state?.processing_stop_requested === true && stage.state === 'paused' ? '停止・未処理あり' : (stageLabels[stage.state] || '不明')));
        pill(`${name}-state`, fresh ? label : '不明', !fresh || partial || stage.state === 'paused' ? 'warning' : (stage.state === 'failed' ? 'error' : (stage.state === 'running' ? 'good' : 'muted')));
        put(`${name}-time`, formatTime(stage.through_seconds));
        showText(`${name}-error`, text(stage.error));
      }
      if (analysis.provider === 'openai' && analysis.state === 'failed') {
        const continues = captureView.tone === 'good' && capture.state === 'recording' && state?.session?.source_kind !== 'replay';
        const note = continues ? 'API解析が利用できなくても、録音・保存は継続しています。' : 'API解析と録音・保存は独立しています。録音・保存欄で現在の状態を確認してください。';
        showText('analysis-error', [text(analysis.error), note].filter(Boolean).join('\n'));
      }
      put('asr-detail', (asr.state === 'paused' ? (state?.processing_stop_requested === true ? '未認識分を残して停止 · ' : '認識を保留 · ') : '') + (number(asr.queue_seconds) ? `${state?.processing_stop_requested === true ? '未認識' : '認識待ち'} ${formatTime(asr.queue_seconds)}${!fresh ? '（前回観測）' : ''}` : '認識した音声の位置'));
      const failures = array(asr.failed_chunks);
      $('asr-failures').hidden = !failures.length;
      put('asr-failures-summary', `未認識の区間 ${failures.length}件`);
      const signature = JSON.stringify(failures);
      if (signature !== failuresSignature) {
        failuresSignature = signature;
        $('asr-failures-list').replaceChildren();
        for (const failure of failures) $('asr-failures-list').appendChild(element('li', '', `${formatTime(failure.start_seconds)}–${formatTime(failure.end_seconds)}`));
      }
      const analysisDetails = [];
      if (number(capture.audio_seconds) && number(analysis.through_seconds)) analysisDetails.push(`音声との差 ${formatTime(Math.max(0, capture.audio_seconds - analysis.through_seconds))}`);
      if (analysis.generated_at) analysisDetails.push(`生成 ${ageText(analysis.generated_at, current)}`);
      put('analysis-detail', analysis.provider === 'off' ? '原文のみ。理解支援はオフです。' : (analysisDetails.join(' · ') || '分析した音声の位置'));
      const session = state?.session;
      const waitingForWorker = (state?.processing_active === true || state?.processing_stop_status === 'stopping') && !ACTIVE_CAPTURE.has(capture.state);
      const preflightWait = ['checking', 'blocked'].includes(state?.preflight?.state) && !ACTIVE_CAPTURE.has(capture.state);
      const cloudInFlight = analysis.provider === 'openai' && ['analysis', 'translation'].some(kind => state[kind]?.state === 'running' || state[kind]?.worker_alive === true);
      const preparation = state?.asr_preparation;
      const preparationOnly = !!preparation && !session;
      const preparationMessage = preparationOnly ? ({
        idle: '音声認識の準備を待っています。録音はまだ始まっていません。',
        preparing: preparation.stop_requested ? '準備の停止を確認しています。録音はまだ始まっていません。' : '音声認識を準備しています。録音はまだ始まっていません。',
        ready: '音声認識の準備ができました。録音は開始ボタンを押してから始まります。',
        failed: '音声認識を準備できませんでした。準備を再試行してください。録音はまだ始まっていません。',
        paused: '音声認識の準備を停止しました。再試行すると録音の準備をやり直します。',
      })[preparation.state] : '';
      showText('preparation-error', preparationOnly ? text(preparation.error) : '');
      put('setup-hint', preparationMessage ? (fresh ? preparationMessage : '音声認識の準備状態を確認できません。最新の状態を待っています。') : stopRequested() && cloudInFlight
        ? '開始済みのAPI処理の終了を待っています。送信済みの要求は取り消せず、料金が発生することがあります。'
        : (waitingForWorker ? '前のセッションの処理終了を確認するまで、新しい録音は開始できません。録音・認識・分析の状態を確認してください。' : (preflightWait ? '下の準備確認が終わると録音を開始できます。問題がある項目を確認してください。' : (session?.source_kind === 'replay' ? '保存済み音声の逐次再生です。Macのマイクは使用していません。' : 'Macのマイクから音声を保存します。録音はボタンを押してから始まります。'))));
      renderPreflight(fresh, current);
      renderAgenda();
      renderControls();
      renderTranslationStatus(fresh);
      renderEmptyTranslationStatus(fresh);
      renderSchedules(fresh);
      renderProcessingOverview(fresh, captureView);
    }

    function renderProcessingOverview(fresh, captureView) {
      const sessionId = state?.session?.id || null;
      if (processingSession !== sessionId) { processingAlerts = new Set(); processingSession = sessionId; }
      const alerts = new Set();
      const labels = [];
      let tone = 'muted';
      if (!fresh) {
        labels.push(state || connectionError ? '状態不明' : '準備中');
        // A disconnect does not clear an already observed failure. Reconnecting
        // to that same failure must respect the reader closing this disclosure.
        for (const key of processingAlerts) alerts.add(key);
        if (state || connectionError) alerts.add('connection:unknown');
      } else {
        const capture = state.capture || {};
        const stageLabels = {
          capture: {idle: '録音待ち', starting: '録音準備中', recording: captureView.label, stalled: '入力途絶', stopping: '保存待ち', completed: '保存済み', failed: '録音失敗'},
          asr: {idle: '原文待ち', waiting: '文字起こし待ち', running: '文字起こし中', completed: '文字起こし済み', failed: '文字起こし失敗', paused: '文字起こし保留'},
          translation: {idle: '翻訳待ち', waiting: '翻訳待ち', running: '翻訳中', completed: '翻訳済み', failed: '翻訳失敗', paused: '翻訳保留'},
          analysis: {idle: '整理待ち', waiting: '整理待ち', running: '整理中', completed: '整理済み', failed: '整理失敗', paused: '整理保留', off: '整理オフ', disabled: '整理オフ'},
        };
        for (const kind of ['capture', 'asr', 'translation', 'analysis']) {
          const stage = state[kind] || {};
          if (kind === 'translation' && stage.enabled !== true) continue;
          const off = kind === 'analysis' && stage.provider === 'off';
          const known = stageLabels[kind][stage.state];
          const intentionallyPaused = state.processing_stop_requested === true && stage.state === 'paused';
          const unresolved = stage.completion_confirmed === false && !intentionallyPaused && !BUSY_WORK.has(stage.state) && !ACTIVE_CAPTURE.has(stage.state);
          const name = {capture: '保存', asr: '文字起こし', translation: '翻訳', analysis: '整理'}[kind];
          const label = off ? '整理オフ' : (intentionallyPaused ? `${name}未処理` : (unresolved ? `${name}未確認`
            : (stage.schedule?.retry?.paused && stage.state !== 'failed' ? `${name}保留` : known)));
          labels.push(kind === 'asr' && array(stage.failed_chunks).length ? '未認識あり' : (label || '状態不明'));
          if (off) continue;
          if (!known || (!intentionallyPaused && ['failed', 'stalled', 'paused', 'unknown'].includes(stage.state))) {
            alerts.add(`${kind}:${stage.state || 'unknown'}:${text(stage.error)}`);
            if (stage.state === 'failed') tone = 'error';
          }
          if (unresolved) alerts.add(`${kind}:unresolved`);
          if (!stopRequested() && stage.schedule?.retry?.paused) alerts.add(`${kind}:retry-paused`);
          if (!stopRequested() && (stage.schedule?.retry?.exhausted || stage.schedule?.reason === 'manual_retry')) alerts.add(`${kind}:retry-required`);
        }
        if (capture.state === 'recording' && captureView.tone === 'warning') alerts.add('capture:input-stalled');
        for (const failure of array(state.asr?.failed_chunks)) alerts.add(`asr:chunk:${failure.index}:${failure.start_seconds}:${failure.end_seconds}`);
        const cloudBusy = ['analysis', 'translation'].some(kind => state[kind]?.state === 'running' || state[kind]?.worker_alive === true);
        if (number(state.cloud_budget?.reserved_usd) && state.cloud_budget.reserved_usd > 0 && !cloudBusy) {
          alerts.add('cloud:unresolved-reservation'); labels.push('費用未確定');
        }
      }
      if (pending?.phase === 'reconcile') { alerts.add(`action:${pending.kind}:unresolved`); labels.push('操作確認中'); }
      if ([...alerts].some(key => !processingAlerts.has(key))) $('processing-details').open = true;
      processingAlerts = alerts;
      const overview = $('processing-overview');
      const label = labels.join(' · ');
      if (overview.textContent !== label) overview.textContent = label;
      overview.dataset.tone = tone === 'error' ? 'error' : (alerts.size ? 'warning' : tone);
      overview.title = alerts.size ? '確認が必要な処理があります。展開して各処理の状態を確認できます。' : '展開すると録音・文字起こし・生成の状態を確認できます。';
    }

    function renderSchedules(fresh) {
      for (const kind of ['asr', 'translation', 'analysis']) {
        const stage = kind === 'asr' && state?.provisional_asr?.enabled === true ? state.provisional_asr : state?.[kind];
        let schedule = stage?.schedule;
        if (kind === 'asr' && schedule?.state === 'complete' && (array(stage.failed_chunks).length || stage.error || schedule.error
          || ['failed', 'paused'].includes(stage.state) || stage.completion_confirmed === false)) {
          schedule = {...schedule, state: 'blocked', reason: 'error'};
        }
        if (stopRequested()) schedule = {state: 'blocked', reason: state?.processing_stop_requested !== true ? 'stop_requested'
          : (stage?.state === 'running' || stage?.worker_alive === true ? 'finishing' : 'stopped')};
        const box = $(`${kind}-schedule`);
        box.hidden = !state?.session || (kind === 'translation' && state?.translation?.enabled !== true);
        const view = schedulePresentation(schedule, fresh, lastReceivedAt === null ? 0 : now() - lastReceivedAt, kind);
        box.dataset.state = view.state;
        const label = `${frozen ? '現在の処理: ' : ''}${view.label}`;
        const explanation = stopRequested()
          ? (state?.processing_stop_requested !== true ? '停止要求の結果を確認しています。'
            : (view.state === 'busy' ? '新しい処理は始めません。すでに開始した処理の終了時刻は未定です。' : '未処理分を残したまま、新しい処理と再試行を止めています。'))
          : kind === 'asr'
          ? `円は原文を更新する次の音声区間${number(schedule?.interval_seconds) && schedule.interval_seconds > 0 ? `（${schedule.interval_seconds}秒ごと）` : ''}を受け付けるまでの目安です。受信済みの音声時間をもとに更新し、文字起こしの完了時刻を予測するものではありません。`
          : schedule?.reason === 'initial_translation'
            ? '最初の訳を作成してから、要点の整理を始めます。'
          : (view.state === 'waiting' && schedule?.reason === 'continuation'
            ? '文の区切りを待っています。開始時刻は未定です。'
            : '円は次の処理を開始できるまでの目安です。生成完了までの時間ではありません。');
        put(`${kind}-schedule-text`, `${label}。${explanation}`);
        put(`${kind}-schedule-compact`, view.compact);
        const summary = $(`${kind}-schedule-summary`);
        summary.title = `${({asr: '原文', translation: '翻訳', analysis: '整理'})[kind]}: ${label}。${explanation}`;
        summary.setAttribute('aria-label', summary.title);
        summary.setAttribute('aria-live', 'off');
        $(`${kind}-schedule-ring`).style.background = `conic-gradient(#39765b ${view.fraction * 360}deg, #e1e8e2 0deg)`;
        box.title = explanation;
        if (kind !== 'asr') {
          const button = $(`${kind}-pause-retries`);
          button.hidden = stopRequested() || !(schedule?.reason === 'retry' && schedule?.state === 'waiting');
          button.disabled = stopRequested() || !fresh || pauseRetryBusy.has(kind);
        }
      }
    }

    async function pauseRetries(kind) {
      if (stopRequested() || pauseRetryBusy.has(kind) || !isFresh(state, connected, now())) return;
      pauseRetryBusy.add(kind); renderStatus();
      try {
        await fetchJSON('/api/pause-retries', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({stage: kind})});
        message('自動再試行の保留を要求しました。処理状態を確認します。');
      } catch (error) { message(error.message, 'warning'); }
      finally { pauseRetryBusy.delete(kind); await poll(); renderStatus(); }
    }

    function renderTranslationStatus(fresh) {
      const translation = state?.translation;
      const enabled = translation?.enabled === true;
      $('translation-retry-button').hidden = stopRequested() || !enabled || translation.state !== 'failed' || translation.retry_required !== true
        || translation.schedule?.reason === 'retry';
      $('translation-retry-button').disabled = stopRequested() || translationRetryBusy || !fresh;
      put('translation-retry-button', translationRetryBusy ? '翻訳の再試行を要求中…' : '翻訳を再試行');
      showText('translation-error', enabled ? text(translation.error) : '');
    }

    function renderEmptyTranslationStatus(fresh) {
      const next = displayedState;
      if (next?.translation?.enabled !== true || $('translation-empty').hidden) return;
      let hint = '最初の日本語訳の開始を待っています。';
      if (historySelection || frozen) hint = 'この時点に公開済みの訳はありません。';
      else if (!fresh) hint = '翻訳の状態を確認できません。接続の回復を待っています。';
      else if (stopRequested()) hint = '翻訳の停止を要求しました。公開済みの訳はありません。';
      else if (state.translation.state === 'failed') hint = '翻訳に失敗しました。処理の状態を確認してください。';
      else if (state.translation.state === 'running') hint = '最初の日本語訳を作成しています。';
      else if (state.translation.state === 'completed' && state.translation.pending_lines === 0) hint = 'この時点に翻訳対象の原文はありません。';
      else if (state.translation.schedule?.reason === 'continuation') hint = '翻訳する文の区切りを待っています。';
      else if (!array(next.lines).length) hint = '翻訳する原文を待っています。';
      put('translation-empty', hint);
    }

    function renderPreflight(fresh, current) {
      const preflight = state?.preflight;
      $('preflight-details').hidden = !preflight;
      if (!preflight) return;
      const warnings = array(preflight.checks).filter(check => check.state === 'warning').length;
      const preparation = state?.asr_preparation;
      const preparationLabel = ({idle: '音声認識の準備待ち', preparing: '音声認識を準備中', failed: '音声認識の準備に問題があります', paused: '音声認識の準備を停止'})[preparation?.state];
      const label = !fresh ? '現在の準備状態は不明' : preparationLabel || ({ready: warnings ? '録音可能・解析の注意あり' : '録音の準備ができています', checking: '録音の準備を確認中', blocked: '録音の準備に問題があります'})[preflight.state] || '準備状態は不明';
      put('preflight-summary', `準備確認: ${label}`);
      $('preflight-summary').dataset.tone = !fresh || preparationLabel || preflight.state !== 'ready' || warnings ? 'warning' : 'good';
      put('preflight-message', [text(preflight.message), preflight.checked_at ? `確認 ${ageText(preflight.checked_at, current)}` : ''].filter(Boolean).join(' · '));
      const signature = JSON.stringify(preflight.checks);
      if (signature !== preflightSignature) {
        preflightSignature = signature;
        $('preflight-checks').replaceChildren();
        for (const check of array(preflight.checks)) {
          const row = element('li');
          const status = ({ready: '確認済み', warning: '注意', blocked: '要確認', checking: '確認中'})[check.state] || '不明';
          row.appendChild(element('strong', '', `${text(check.label)} · ${status}`));
          row.appendChild(element('span', '', text(check.message)));
          row.dataset.tone = check.state === 'ready' ? 'good' : 'warning';
          $('preflight-checks').appendChild(row);
        }
      }
    }

    function renderAgenda() {
      const agenda = state?.capabilities?.agenda;
      const items = array(agenda?.items).filter(item => /^\d{2}:\d{2}$/.test(text(item?.time)) && text(item?.title));
      $('agenda-card').hidden = !items.length;
      const signature = JSON.stringify([agenda?.date, agenda?.timezone, items]);
      if (signature === agendaSignature) return;
      agendaSignature = signature;
      put('agenda-heading', `${text(agenda?.date) || '当日'}の予定${agenda?.timezone === 'Asia/Tokyo' ? ' · JST' : ''}`);
      $('agenda-list').replaceChildren();
      for (const item of items) {
        const row = element('li');
        row.appendChild(element('time', '', item.time));
        row.appendChild(element('span', '', item.title));
        $('agenda-list').appendChild(row);
      }
    }

    function renderTranslations(next, result, lines) {
      if (next.translation?.enabled === true) {
        renderContinuousTranslations(next);
        return;
      }
      if (translationMode !== 'legacy') { renderedTranslationSignature = ''; translationElements = new Map(); }
      translationMode = 'legacy';
      const hasBlocks = Object.prototype.hasOwnProperty.call(result, 'block_translations');
      let blocks = [];
      if (hasBlocks) blocks = array(result.block_translations).filter(item => text(item?.text)).map(item => ({text: text(item.text), source_ids: array(item.source_ids).map(text)}));
      else {
        // A selected legacy history has no historical translation snapshot. Do
        // not borrow current/future line translations and label them as that time.
        const translated = historySelection ? new Map(array(result.translations).map(item => [text(item.source_id), text(item.text)])) : translationMap(next);
        let group = {text: '', source_ids: []};
        const flush = () => { if (group.text) blocks.push(group); group = {text: '', source_ids: []}; };
        for (const line of lines) {
          const value = translated.get(text(line.id));
          if (!value) { flush(); continue; }
          group.text += (group.text ? ' ' : '') + value;
          group.source_ids.push(text(line.id));
          if (group.source_ids.length >= 4 || group.text.length >= 300) flush();
        }
        flush();
      }
      const hasLegacyTranslations = !hasBlocks && blocks.length > 0;
      put('translation-heading', hasLegacyTranslations ? '既存の断片訳' : '文脈付きの日本語訳');
      put('translation-empty', hasBlocks ? 'この時点には、まとまりの訳がありません。' : (historySelection ? 'この履歴には当時の断片訳が保存されていません。' : (next.analysis?.provider === 'off' ? '理解支援はオフです。' : '発言がまとまると、ここに訳を表示します。')));
      $('translation-empty').hidden = !!blocks.length;
      const signature = JSON.stringify([next.session?.id, hasBlocks, blocks]);
      if (signature === renderedTranslationSignature) return;
      const panel = $('translation-blocks');
      const scrollTop = panel.scrollTop;
      const nearBottom = panel.scrollHeight - panel.clientHeight - scrollTop < 60;
      const firstRender = !renderedTranslationSignature;
      renderedTranslationSignature = signature;
      panel.replaceChildren();
      for (const block of blocks) {
        const article = element('article', 'translation-block');
        article.appendChild(element('p', 'block-translation-text', block.text));
        panel.appendChild(article);
      }
      panel.scrollTop = firstRender || nearBottom ? panel.scrollHeight : scrollTop;
    }

    function renderContinuousTranslations(next) {
      const cutoff = historySelection?.generated_at;
      const hasCutoff = number(cutoff) && cutoff > 0;
      const blocks = array(next.translation.blocks).filter(block => text(block?.id) && text(block?.text)
        && (!historySelection || (hasCutoff && number(block.published_at) && block.published_at <= cutoff)));
      put('translation-heading', '文脈付きの日本語訳');
      put('translation-empty', historySelection ? 'この時点に対応する公開済みの訳はありません。' : '原文のまとまりから順に訳を追加します。');
      $('translation-empty').hidden = blocks.length > 0;
      renderEmptyTranslationStatus(isFresh(state, connected, now()));
      const signature = JSON.stringify([next.session?.id, blocks]);
      if (translationMode === 'continuous' && signature === renderedTranslationSignature) return;
      const panel = $('translation-blocks');
      const scrollTop = panel.scrollTop;
      const nearBottom = panel.scrollHeight - panel.clientHeight - scrollTop < 60;
      const newSession = translationMode !== 'continuous' || translationSession !== next.session?.id;
      if (newSession) translationElements = new Map();
      const oldIds = [...translationElements.keys()];
      const focused = doc.activeElement;
      const nextElements = new Map();
      const rows = [];
      for (const block of blocks) {
        const id = text(block.id);
        if (nextElements.has(id)) continue;
        const article = translationElements.get(id) || element('article', 'translation-block');
        const rowSignature = JSON.stringify(block);
        article.dataset.translationId = id;
        if (article.dataset.translationSignature !== rowSignature) {
          article.dataset.translationSignature = rowSignature;
          article.replaceChildren(element('p', 'block-translation-text', text(block.text)));
        }
        nextElements.set(id, article); rows.push(article);
      }
      const newIds = [...nextElements.keys()];
      const appendOnly = !newSession && oldIds.length <= rows.length && oldIds.every((id, index) => id === newIds[index]);
      if (appendOnly) for (const row of rows.slice(oldIds.length)) panel.appendChild(row);
      else panel.replaceChildren(...rows);
      translationElements = nextElements;
      translationMode = 'continuous';
      translationSession = next.session?.id;
      renderedTranslationSignature = signature;
      panel.scrollTop = newSession || nearBottom ? panel.scrollHeight : scrollTop;
      if (focused && rows.some(row => row.contains(focused)) && doc.activeElement !== focused) focused.focus({preventScroll: true});
    }

    function provisionalView(next) {
      const preview = next.provisional_asr;
      // A selected analysis does not carry the temporary recognition view that
      // existed then. Never borrow a later revision for that historical view.
      const available = preview?.enabled === true && !historySelection;
      const caughtUp = number(next.asr?.through_seconds) && number(preview?.through_seconds)
        && next.asr.through_seconds >= preview.through_seconds;
      const lines = available && !caughtUp ? array(preview.lines) : [];
      const error = available ? text(preview.error) || (preview.state === 'failed' ? '速報の更新に失敗しました。' : '') : '';
      return {preview, lines, error};
    }

    function renderEmptyTranscriptStatus(fresh) {
      if (frozen || !transcriptEmptyElement || transcriptEmptyElement.parentNode !== $('transcript')) return;
      let label = 'まだ原文はありません';
      const preparation = state?.asr_preparation;
      const stage = state?.provisional_asr?.enabled ? state.provisional_asr : state?.asr;
      if (state && !fresh) label = '原文の状態を確認しています。';
      else if (!state?.session && preparation) {
        label = ({preparing: preparation.stop_requested ? '音声認識の準備を停止しています。' : '音声認識を準備しています。録音はまだ始まっていません。',
          ready: '録音を開始すると、原文をここに表示します。',
          failed: '音声認識を準備できませんでした。準備を再試行してください。',
          paused: '音声認識の準備を停止しました。', idle: '音声認識の準備を待っています。'})[preparation.state] || label;
      } else if (state?.session) {
        if (state.processing_stop_requested || stage?.state === 'paused') label = '原文はまだありません。処理は停止しています。';
        else if (stage?.schedule?.state === 'busy') label = stage.schedule.reason === 'request' ? '最初の原文を認識しています。' : '最初の原文の認識待ちです。';
        else if (stage?.state === 'failed' || stage?.error) label = '原文の認識に失敗しました。処理状態を確認してください。';
        else if (ACTIVE_CAPTURE.has(state.capture?.state)) label = state.capture.audio_seconds > 0 ? '音声を受信しています。原文が届くとここに表示します。' : '音声の到着を待っています。';
        else if (state.demo) label = 'この時点までに原文はありません。';
      }
      transcriptEmptyElement.textContent = label;
    }

    function renderProvisional(next, {preview, lines, error}) {
      previewNode('provisional-region').hidden = !lines.length && !error;
      const paused = preview?.state !== 'running' && (preview?.state === 'paused' || !!error);
      previewNode('provisional-heading').textContent = paused ? '更新停止' : '更新中';
      previewNode('provisional-heading').title = paused ? '原文の更新は停止しています。最後に届いた認識を残しています。' : '音声が増えると、この部分の認識が更新されます。';
      previewNode('provisional-help').textContent = previewNode('provisional-heading').title;
      previewNode('provisional-error').textContent = error ? `${preview?.state === 'running' ? '前の' : ''}原文の更新に失敗しました。` : '';
      previewNode('provisional-error').hidden = !error;
      previewNode('provisional-error').title = error;
      const signature = JSON.stringify([next.session?.id, preview?.revision, lines]);
      if (signature === renderedProvisionalSignature) return;
      renderedProvisionalSignature = signature;
      const panel = previewNode('provisional-lines');
      panel.replaceChildren(...lines.map(line => {
        const row = element('p', 'line-original', text(line.text));
        row.dataset.provisionalId = text(line.id);
        row.classList.toggle('uncertain-source', !!line.uncertain);
        row.title = `${formatTime(line.start_seconds)}–${formatTime(line.end_seconds)}${line.uncertain ? ' · 認識が不確か' : ''}`;
        return row;
      }));
    }

    function reconcileRows(parent, rows) {
      // Move existing rows without replacing the canonical row being read.
      for (let index = 0; index < rows.length; index++) {
        if (parent.children[index] !== rows[index]) parent.insertBefore(rows[index], parent.children[index] || null);
      }
      while (parent.children.length > rows.length) parent.removeChild(parent.children[parent.children.length - 1]);
    }

    function renderTranscript(next, lines, forceLatest, previousSession) {
      const provisional = provisionalView(next);
      const signature = JSON.stringify([next.session?.id, lines.map(line => [line.id, line.text, line.start_seconds, line.end_seconds, line.language, line.uncertain]),
        provisionalSignature(provisional.preview), provisional.lines, !!historySelection]);
      if (signature === renderedTranscriptSignature) {
        if (forceLatest) $('transcript').scrollTop = $('transcript').scrollHeight;
        return;
      }
      renderedTranscriptSignature = signature;
      const transcript = $('transcript');
      const scrollTop = transcript.scrollTop;
      const nearBottom = transcript.scrollHeight - transcript.clientHeight - scrollTop < 60;
      const oldIds = [...lineElements.keys()];
      const focusedId = oldIds.find(id => lineElements.get(id) === doc.activeElement);
      const nextElements = new Map();
      const rows = [];
      const hasTail = provisional.lines.length > 0;
      const latestId = text(lines[lines.length - 1]?.id);
      const groups = transcriptDisplayGroups(lines);
      for (const group of groups) {
        const line = group.lines[0];
        const id = text(line.id);
        const sourceIds = group.lines.map(source => text(source.id));
        const row = lineElements.get(id) || element('article', 'transcript-line');
        const rawSignature = JSON.stringify(group.lines.map(source => [source.id, source.text, source.start_seconds, source.end_seconds, source.language, source.uncertain]));
        row.tabIndex = -1; row.dataset.sourceId = id;
        row.dataset.sourceIds = JSON.stringify(sourceIds);
        row.classList.toggle('latest-source', !hasTail && sourceIds.includes(latestId));
        row.setAttribute('aria-current', !hasTail && sourceIds.includes(latestId) ? 'true' : 'false');
        if (row.dataset.rawSignature !== rawSignature) {
          row.dataset.rawSignature = rawSignature;
          row.title = `${formatTime(line.start_seconds)}–${formatTime(group.lines[group.lines.length - 1].end_seconds)} · ${id}${text(line.language) ? ` · ${text(line.language).toUpperCase()}` : ''}${line.uncertain ? ' · 認識が不確か' : ''}`;
          row.classList.toggle('uncertain-source', !!line.uncertain);
          row.replaceChildren(element('p', 'line-original', text(line.text)));
        }
        nextElements.set(id, row);
        // Keep every saved row in the same scroller, including speech that
        // overlaps the revisable tail. Never hide or trim text being read.
        rows.push(row);
      }
      renderProvisional(next, provisional);
      if (!previewNode('provisional-region').hidden) rows.push(previewNode('provisional-region'));
      transcriptEmptyElement = !rows.length ? element('p', 'transcript-empty', 'まだ原文はありません') : null;
      if (transcriptEmptyElement) rows.push(transcriptEmptyElement);
      reconcileRows(transcript, rows);
      lineElements = nextElements;
      const focusedGroup = focusedId && groups.find(group => group.lines.some(line => text(line.id) === focusedId));
      const focusedRow = focusedGroup && lineElements.get(text(focusedGroup.lines[0].id));
      transcript.scrollTop = forceLatest || previousSession !== next.session?.id || nearBottom ? transcript.scrollHeight : scrollTop;
      if (focusedRow && doc.activeElement !== focusedRow) focusedRow.focus({preventScroll: true});
      renderEmptyTranscriptStatus(isFresh(state, connected, now()));
    }

    function renderItems(id, emptyId, items) {
      const parent = $(id);
      parent.replaceChildren();
      let count = 0;
      for (const item of array(items)) {
        const value = text(item?.text);
        if (!value) continue;
        const li = element('li');
        li.appendChild(element('p', '', value));
        parent.appendChild(li);
        count++;
      }
      $(emptyId).hidden = count > 0;
    }

    function historyKey(item) {
      return JSON.stringify([state?.session?.id, item.through_seconds, item.generated_at, item.headline]);
    }

    function rememberHistory(next, previous) {
      const session = next.session?.id || null;
      const cursor = next.demo?.cursor_seconds ?? next.demo?.at ?? next.capture.audio_seconds;
      const previousCursor = previous?.demo?.cursor_seconds ?? previous?.demo?.at ?? previous?.capture?.audio_seconds;
      if (historySession !== session || (next.demo && cursor < previousCursor)) {
        historySession = session; historyItems = new Map(); historyCursor = null; historyHasMore = true;
        historyLoading = false; historyGeneration++;
        frozen = false; historySelection = null;
        showHistoryNotice('');
      }
      for (const item of [...array(next.analysis_history), next.analysis?.result]) {
        if (number(item?.through_seconds) && text(item?.headline?.text)) historyItems.set(historyKey(item), item);
      }
    }

    function orderedHistory() {
      return [...historyItems.values()].sort((a, b) => a.through_seconds - b.through_seconds
        || (a.generated_at || 0) - (b.generated_at || 0));
    }

    async function navigateHistory(direction) {
      let items = orderedHistory();
      const key = historySelection?.key || (displayedState?.analysis?.result && historyKey(displayedState.analysis.result));
      let index = items.findIndex(item => historyKey(item) === key);
      if (direction < 0 && index === 0 && canLoadOlderHistory()) {
        const generation = historyGeneration;
        await loadOlderHistory();
        const currentKey = historySelection?.key || (displayedState?.analysis?.result && historyKey(displayedState.analysis.result));
        if (generation !== historyGeneration || currentKey !== key) return;
        items = orderedHistory(); index = items.findIndex(item => historyKey(item) === key);
      }
      const target = index < 0 && direction < 0 ? items[items.length - 1] : items[index + direction];
      if (target) selectHistory(target);
    }

    function canLoadOlderHistory() {
      return !historyLoading && historyHasMore && !!state?.session && state?.capabilities?.analysis_history_paging === true && isFresh(state, connected, now());
    }

    function showHistoryNotice(value) {
      if (value) message(value, 'warning');
      else if (historyNotice && $('action-message').textContent === historyNotice) message('');
      historyNotice = value;
    }

    function renderHistoryNavigation() {
      const items = orderedHistory();
      const key = historySelection?.key || (displayedState?.analysis?.result && historyKey(displayedState.analysis.result));
      const index = items.findIndex(item => historyKey(item) === key);
      $('previous-analysis-button').disabled = !items.length || (index === 0 && !canLoadOlderHistory());
      $('next-analysis-button').disabled = index < 0 || index >= items.length - 1;
      $('previous-analysis-button').setAttribute('aria-busy', String(historyLoading));
      $('previous-analysis-button').title = historyLoading ? '以前の整理を読み込み中' : '前の整理';
    }

    async function loadOlderHistory() {
      if (!canLoadOlderHistory()) return;
      const session = state.session.id;
      const generation = historyGeneration;
      historyLoading = true; renderHistoryNavigation(); showHistoryNotice('');
      try {
        const query = new URLSearchParams({session_id: session, limit: '30'});
        if (historyCursor) query.set('cursor', historyCursor);
        else {
          const dates = [...historyItems.values()].map(item => item.generated_at).filter(value => number(value) && value > 0);
          if (dates.length) query.set('before', String(Math.min(...dates)));
        }
        const page = await fetchJSON(`/api/analysis-history?${query}`);
        if (generation !== historyGeneration || state?.session?.id !== session) return;
        if (page.session_id !== session || !Array.isArray(page.items)) throw new Error('履歴の形式を確認できません。');
        for (const item of page.items) if (number(item?.through_seconds) && text(item?.headline?.text)) historyItems.set(historyKey(item), item);
        historyCursor = text(page.next_cursor) || null;
        historyHasMore = page.has_more === true && !!historyCursor;
        const skipped = ['malformed', 'incomplete', 'missing_generated_at'].reduce((sum, key) =>
          sum + (number(page.skipped?.[key]) ? page.skipped[key] : 0), 0);
        if (skipped) showHistoryNotice(`未完了・形式不明の履歴 ${skipped}件は表示できません。`);
        renderHistoryNavigation();
        if (displayedState) renderConcepts(displayedState);
      } catch (error) {
        if (generation === historyGeneration) showHistoryNotice(`履歴を取得できません: ${error.message}`);
      } finally {
        if (generation === historyGeneration) { historyLoading = false; renderHistoryNavigation(); }
      }
    }

    function renderConcepts(next) {
      const panel = $('concepts-list');
      const newSession = conceptSession !== (next.session?.id || null);
      const previousTop = panel.scrollTop;
      const previousHeight = panel.scrollHeight;
      const oldKeys = newSession ? [] : [...conceptElements.keys()];
      const result = next.analysis?.result;
      const cutoff = next.analysis?.generated_at ?? result?.generated_at;
      const through = next.analysis?.through_seconds ?? result?.through_seconds;
      const snapshots = orderedHistory().filter(item => (item === result || historyKey(item) === (result && historyKey(result)))
        || (number(cutoff) && number(item.generated_at) && item.generated_at <= cutoff && item.through_seconds <= through));
      // Legacy snapshots may omit through_seconds on the result object.
      if (result && !snapshots.includes(result)) snapshots.push(result);
      const entries = new Map();
      for (const item of snapshots) for (const concept of array(item.concepts)) {
        if (!text(concept?.term) || !text(concept?.explanation)) continue;
        const key = JSON.stringify([concept.term, concept.explanation, concept.basis, array(concept.source_ids)]);
        if (!entries.has(key)) entries.set(key, {concept, through: item.through_seconds ?? through});
      }
      const rows = [];
      const nextElements = new Map();
      for (const [key, entry] of entries) {
        const {concept} = entry;
        const card = (!newSession && conceptElements.get(key)) || element('article', 'concept');
        const signature = JSON.stringify(key);
        if (card.dataset.signature !== signature) {
          card.dataset.signature = signature;
          const header = element('div', 'concept-header');
          header.appendChild(element('h3', '', text(concept.term)));
          card.replaceChildren(header, element('p', '', text(concept.explanation)));
        }
        nextElements.set(key, card); rows.push(card);
      }
      const keys = [...nextElements.keys()];
      const appendOnly = !newSession && oldKeys.length <= keys.length && oldKeys.every((key, index) => key === keys[index]);
      if (appendOnly) for (const row of rows.slice(oldKeys.length)) panel.appendChild(row);
      else {
        // Insert older cards without detaching the card being read or its selection.
        for (let index = 0; index < rows.length; index++) if (panel.children[index] !== rows[index]) panel.insertBefore(rows[index], panel.children[index] || null);
        while (panel.children.length > rows.length) panel.removeChild(panel.children[panel.children.length - 1]);
      }
      const prepended = oldKeys.length > 0 && keys.indexOf(oldKeys[0]) > 0;
      panel.scrollTop = newSession ? panel.scrollHeight : previousTop + (prepended ? panel.scrollHeight - previousHeight : 0);
      if (newSession || !oldKeys.length) conceptSeenCount = keys.length;
      else if (prepended) conceptSeenCount += keys.indexOf(oldKeys[0]);
      conceptSeenCount = Math.min(conceptSeenCount, keys.length);
      conceptElements = nextElements; conceptSession = next.session?.id || null;
      $('concepts-empty').hidden = rows.length > 0;
      const added = Math.max(0, keys.length - conceptSeenCount);
      $('concepts-latest-button').hidden = added === 0;
      put('concepts-latest-button', '新しい説明へ');
    }

    function selectHistory(item) {
      if (!state || !number(item?.through_seconds) || !text(item?.headline?.text)) return;
      historySelection = {key: historyKey(item), through_seconds: item.through_seconds, generated_at: item.generated_at};
      frozen = true;
      // Preserve the original interpretation and its source IDs at this time.
      const selected = {...state, analysis: {...state.analysis, through_seconds: item.through_seconds,
        generated_at: item.generated_at, result: item}};
      renderContent(selected, true);
      renderHistoryNavigation();
      renderControls();
    }

    function renderContent(next, forceLatest = false) {
      const signature = contentSignature(next);
      if (!forceLatest && signature === renderedSignature) return;
      const previousSession = displayedState?.session?.id;
      displayedState = next;
      renderedSignature = signature;
      const lines = array(next.lines);
      const result = next.analysis?.result || {};
      const interpretationSignature = JSON.stringify([next.session?.id, historySelection?.key, next.analysis?.provider,
        next.analysis?.through_seconds, result.headline, result.summary]);
      // New source speech does not rebuild the interpretation being read.
      if (interpretationSignature !== renderedInterpretationSignature) {
        renderedInterpretationSignature = interpretationSignature;
        const headline = text(result.headline?.text);
        put('focus-heading', historySelection ? 'その時点で伝えていたこと' : 'いま伝えていること');
        put('summary-heading', historySelection ? '当時の要点' : '直近の要点');
        put('headline', headline || (next.analysis?.provider === 'off' ? '理解支援はオフです。原文を表示しています。' : '話のまとまりが届くと、いま伝えていることを表示します。'));
        $('headline').classList.toggle('empty', !headline);
        renderItems('summary-list', 'summary-empty', result.summary);
      }
      renderConcepts(next);
      renderTranslations(next, result, lines);
      const mode = next.session?.source_kind === 'replay' ? '保存音声の逐次再生' : 'Mac マイク · 1ch';
      put('session-detail', next.session ? `${mode} · ${text(next.session.id)}` : '開始すると、原文と解説がここに届きます。');
      renderTranscript(next, lines, forceLatest, previousSession);
    }

    function acceptState(next) {
      if (!next || next.schema_version !== 1 || !next.capture || !next.asr || !next.analysis || !Array.isArray(next.lines)) throw new Error('サーバーの状態形式が対応していません。再読み込みしてください。');
      const previous = state;
      state = next;
      rememberHistory(next, previous);
      connected = true;
      connectionError = '';
      lastReceivedAt = now();
      if (!frozen) renderContent(state);
      renderHistoryNavigation();
      renderStatus();
    }

    async function poll() {
      if (disposed) return;
      if (pollTask) return pollTask;
      const sequence = ++pollSequence;
      pollTask = (async () => {
        try {
          acceptState(await fetchJSON('/api/state'));
          if (options.autoStart !== false && !devicesRequested && state.session?.source_kind !== 'replay') loadDevices();
          if (pending?.phase === 'reconcile' && sequence >= pending.minPoll && isFresh(state, connected, now())) {
            const action = pending.kind;
            const actionError = pending.error;
            const preparationStop = pending.preparationStop;
            const status = state.capture.state;
            pending = null;
            if (action === 'start') message(ACTIVE_CAPTURE.has(status) ? '録音の開始を確認しました。' : (actionError || '開始要求後の状態を取得しました。録音の状態欄を確認してください。'), ACTIVE_CAPTURE.has(status) ? 'good' : 'warning');
            else if (action === 'prepare') message(state.asr_preparation?.state === 'preparing'
              ? '音声認識を準備しています。録音はまだ始まっていません。'
              : (state.asr_preparation?.state === 'ready' ? '音声認識の準備ができました。録音を開始できます。' : actionError || '音声認識の準備状態を確認してください。'));
            else if (preparationStop) message(state.asr_preparation?.state === 'paused'
              ? '音声認識の準備を停止しました。録音は始めていません。'
              : (state.asr_preparation?.stop_requested ? '準備の停止を要求済みです。開始済みの処理の終了を待っています。'
                : (state.asr_preparation?.state === 'ready' ? '音声認識の準備は完了しています。録音は始めていません。' : actionError || '音声認識の準備状態を確認してください。')));
            else if (state.processing_stop_requested === true) message(state.processing_stop_status === 'stopped'
              ? (status === 'failed' ? '処理は停止しました。録音・保存の失敗と未処理分は残っています。' : '録音と新しい処理の停止を確認しました。未処理分は残しています。')
              : '新しい処理を停止しました。音声の保存と開始済みの処理の終了を確認しています。', status === 'failed' ? 'warning' : undefined);
            else if (!ACTIVE_CAPTURE.has(status)) message(actionError || '録音は終了しています。処理の停止を確認できていません。', 'warning');
            else if (status === 'stopping') message('停止を要求しました。音声の保存と処理の状態を確認しています。');
            else message(actionError || '録音はまだ進行中です。停止の完了を確認できていません。', 'warning');
            renderStatus();
          }
        } catch (error) {
          connected = false;
          connectionError = error.name === 'AbortError' ? '状態取得がタイムアウトしました。自動で再接続します。' : `${error.message}。自動で再接続します。`;
          renderStatus();
        }
      })();
      try { await pollTask; } finally { pollTask = null; }
    }

    async function loadDevices() {
      if (devicesLoading) return;
      devicesLoading = true;
      devicesRequested = true;
      $('devices-button').disabled = true;
      try {
        const response = await fetchJSON('/api/devices');
        const selected = $('device-select').value;
        devices = array(response.devices).filter(device => text(device?.id) && text(device?.name));
        const select = $('device-select');
        select.replaceChildren();
        if (!devices.length) {
          const option = element('option', '', '音声入力が見つかりません'); option.value = ''; select.appendChild(option);
        } else {
          for (const device of devices) { const option = element('option', '', text(device.name)); option.value = text(device.id); select.appendChild(option); }
          const previous = devices.find(device => text(device.id) === selected);
          const preferred = previous || (!deviceSelectionInitialized ? (devices.find(device => /macbook/i.test(device.name)) || devices.find(device => /built.in|内蔵/i.test(device.name)) || devices[0]) : null);
          if (preferred) select.value = text(preferred.id);
          else { const placeholder = element('option', '', '音声入力を選択してください'); placeholder.value = ''; select.appendChild(placeholder); select.value = ''; }
          deviceSelectionInitialized = true;
        }
        applyStoredDevice();
        showText('devices-error', text(response.error) || (!devices.length ? '音声入力を再取得してください。Macのマイク接続と録音権限も確認できます。' : ''));
      } catch (error) {
        showText('devices-error', error.name === 'AbortError' || /abort|timeout/i.test(error.message) ? '入力一覧の取得が時間内に終わりませんでした。音声入力を再取得してください。' : `音声入力を取得できませんでした: ${error.message}`);
        if (!devices.length) { $('device-select').replaceChildren(); const option = element('option', '', '音声入力を取得できません'); option.value = ''; $('device-select').appendChild(option); }
      } finally { devicesLoading = false; renderControls(); }
    }

    async function recordAction(kind) {
      if (!['start', 'stop', 'prepare'].includes(kind)) return;
      const control = getControlState(state, connected, now(), !!$('device-select').value, pending);
      if ((kind === 'start' && control.startDisabled) || (kind === 'stop' && control.stopDisabled)) return;
      if (kind === 'prepare' && (pending || !isFresh(state, connected, now()) || !['failed', 'paused'].includes(state?.asr_preparation?.state))) return;
      if (kind === 'start' && $('provider-select').value === 'openai' && state?.capabilities?.cloud_enabled !== true) { message('この起動ではクラウド解析は有効になっていません。', 'warning'); return; }
      if (kind === 'start') { saveIdlePreferences(); goLive(); }
      pending = {kind, phase: 'sending', minPoll: Infinity, preparationStop: kind === 'stop' && state?.asr_preparation?.state === 'preparing'};
      message(kind === 'prepare' ? '音声認識の準備を再試行しています…' : kind === 'start' ? '録音の開始を要求しています…'
        : pending.preparationStop ? '音声認識の準備の停止を要求しています…' : '録音と新しい処理の停止を要求しています…');
      renderStatus();
      const body = kind === 'start' ? {device: $('device-select').value, language: $('language-select').value, provider: $('provider-select').value} : {};
      if (kind === 'start' && $('model-input').value.trim() && body.provider !== 'off') body.model = $('model-input').value.trim();
      try {
        await fetchJSON(`/api/${kind === 'prepare' ? 'prepare-asr' : kind}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
      } catch (error) {
        pending.error = error.confirmed ? `要求に失敗しました: ${error.message}` : '操作の応答を受信できませんでした。実際の状態を確認してください。';
        message(error.confirmed ? pending.error : '操作の応答を受信できませんでした。重複操作を防ぐため、実際の状態を確認しています。', 'warning');
      } finally {
        pending.phase = 'reconcile';
        pending.minPoll = pollSequence + 1;
        renderControls();
        await poll();
        // A request already in flight before POST completion cannot settle that POST.
        if (pending && connected) await poll();
      }
    }

    async function retryAnalysis() {
      if (stopRequested() || retryBusy || !isFresh(state, connected, now()) || state?.analysis?.state !== 'failed' || state?.analysis?.provider === 'off') return;
      retryBusy = true;
      renderControls();
      try {
        await fetchJSON('/api/retry-analysis', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
        message('分析の再試行を要求しました。録音と原文の処理は継続します。');
      } catch (error) { message(`分析の再試行を確認できませんでした: ${error.message}`, 'warning'); }
      finally { retryBusy = false; await poll(); renderControls(); }
    }

    async function retryTranslation() {
      if (stopRequested() || translationRetryBusy || !isFresh(state, connected, now()) || state?.translation?.enabled !== true || state.translation.state !== 'failed' || state.translation.retry_required !== true) return;
      translationRetryBusy = true;
      renderTranslationStatus(true);
      try {
        await fetchJSON('/api/retry-translation', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
        message('翻訳の再試行を要求しました。録音と原文の処理は継続します。');
      } catch (error) { message(`翻訳の再試行を確認できませんでした: ${error.message}`, 'warning'); }
      finally { translationRetryBusy = false; await poll(); renderTranslationStatus(isFresh(state, connected, now())); }
    }

    function freeze() {
      if (!displayedState) return;
      frozen = true;
      renderControls();
    }

    function goLive() {
      frozen = false;
      historySelection = null;
      if (state) renderContent(state, true);
      renderHistoryNavigation();
      renderControls();
    }

    initializeSourceSplitter();
    $('start-button').addEventListener('click', () => recordAction('start'));
    $('preparation-retry-button').addEventListener('click', () => recordAction('prepare'));
    $('stop-button').addEventListener('click', () => recordAction('stop'));
    $('devices-button').addEventListener('click', loadDevices);
    $('retry-button').addEventListener('click', retryAnalysis);
    $('translation-retry-button').addEventListener('click', retryTranslation);
    $('freeze-button').addEventListener('click', () => frozen ? goLive() : freeze());
    $('latest-button').addEventListener('click', goLive);
    $('previous-analysis-button').addEventListener('click', () => navigateHistory(-1));
    $('next-analysis-button').addEventListener('click', () => navigateHistory(1));
    $('concepts-latest-button').addEventListener('click', () => {
      $('concepts-list').scrollTop = $('concepts-list').scrollHeight;
      conceptSeenCount = conceptElements.size; $('concepts-latest-button').hidden = true;
    });
    for (const kind of ['analysis', 'translation']) $(`${kind}-pause-retries`).addEventListener('click', () => pauseRetries(kind));
    $('provider-select').addEventListener('change', providerChanged);
    $('model-input').addEventListener('input', () => { providerSettingsTouched = true; renderProviderDetails(); saveIdlePreferences(); });
    $('device-select').addEventListener('change', () => { deviceSettingsTouched = true; renderControls(); saveIdlePreferences(); });
    $('language-select').addEventListener('change', () => { languageSettingsTouched = true; saveIdlePreferences(); });

    async function pollLoop() {
      await poll();
      if (!disposed) pollTimer = setTimer(pollLoop, 2000);
    }
    function tick() {
      renderStatus();
      if (!disposed) tickTimer = setTimer(tick, 1000);
    }
    if (options.autoStart !== false) { pollLoop(); tick(); }
    return {poll, loadDevices, freeze, goLive, recordAction, retryAnalysis, retryTranslation, pauseRetries, loadOlderHistory, acceptState, renderStatus,
      dispose() { disposed = true; endSourceDrag(null, false); clearTimer(pollTimer); clearTimer(tickTimer); },
    };
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = {formatTime, ageText, isFresh, capturePresentation, getControlState, translationMap, schedulePresentation, createApp};
  if (typeof document !== 'undefined') createApp(document, (...args) => fetch(...args));
})();
