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
    const processing = state?.processing_active === true || BUSY_WORK.has(state?.asr?.state) || BUSY_WORK.has(state?.analysis?.state);
    return {
      startDisabled: !isFresh(state, connected, now) || !hasDevice || active || processing || !!pending || ['checking', 'blocked'].includes(state?.preflight?.state),
      stopDisabled: !active || state?.capture?.state === 'stopping' || !!pending,
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

  function contentSignature(state) {
    return JSON.stringify([state?.session?.id, state?.analysis?.generated_at, state?.analysis?.through_seconds, state?.analysis?.result, state?.lines,
      state?.translation?.enabled, state?.translation?.blocks]);
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
    let failuresSignature = '';
    let pending = null;
    let retryBusy = false;
    let translationRetryBusy = false;
    let frozen = false;
    let frozenLiveSignature = '';
    let historySelection = null;
    let renderedHistorySignature = '';
    let historyButtons = new Map();
    let displayedState = null;
    let renderedSignature = '';
    let lineElements = new Map();
    let renderedTranscriptSignature = '';
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
      const footerProvider = state?.session ? state.analysis?.provider : $('provider-select').value;
      put('processing-caption', footerProvider === 'openai' ? '原音はこのMacに保存 · 文字のみOpenAIで解析' : '音声と文字起こしはこのMacに保存 · ローカル処理');
      put('model-caption', ['local', 'openai'].includes(footerProvider) ? (state?.session ? text(state.analysis?.model) : $('model-input').value.trim()) : '');
      put('start-button', pending?.kind === 'start' ? (pending.phase === 'sending' ? '開始を要求中…' : '開始結果を確認中…') : '● 録音を開始');
      put('stop-button', pending?.kind === 'stop' || state?.capture?.state === 'stopping' ? '停止・保存を確認中…' : '録音を停止');
      $('retry-button').hidden = state?.analysis?.state !== 'failed' || state?.analysis?.provider === 'off';
      $('retry-button').disabled = retryBusy || !isFresh(state, connected, now());
      put('retry-button', retryBusy ? '分析の再試行を要求中…' : '分析を再試行');
      $('freeze-button').disabled = !displayedState;
      $('freeze-button').setAttribute('aria-pressed', String(frozen));
      put('freeze-button', frozen ? '表示を固定中' : '閲覧を固定');
      $('latest-button').hidden = !frozen;
      $('freeze-notice').hidden = !frozen;
      if (historySelection) {
        const hasLaterSpeech = array(displayedState?.lines).some(line => number(line.end_seconds) && line.end_seconds > historySelection.through_seconds);
        put('freeze-notice', `${formatTime(historySelection.through_seconds)} までの分析を固定表示しています。録音と処理は継続します。${hasLaterSpeech ? '原文一覧には、この分析より後の発言も含まれます。' : '原文一覧は、履歴を選んだときの内容です。'}`);
      } else put('freeze-notice', '表示を固定しています。録音と処理は継続します。');
      if (frozen) {
        const newLines = Math.max(0, array(state?.lines).length - array(displayedState?.lines).length);
        const changed = state && contentSignature(state) !== (frozenLiveSignature || renderedSignature);
        showText('new-count', newLines ? `原文 ${newLines}件の新着` : (changed ? '新しい訳・分析があります' : ''));
      } else $('new-count').hidden = true;
    }

    function renderStatus() {
      const current = now();
      const fresh = isFresh(state, connected, current);
      $('connection-dot').dataset.tone = fresh ? 'good' : 'warning';
      put('connection-text', fresh ? 'ローカルサーバーに接続' : '状態を確認できません');
      put('observed-at', lastReceivedAt ? `最終受信 ${ageText(lastReceivedAt, current)}` : 'まだ状態を受信していません');
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
        const label = name === 'analysis' && stage.provider === 'off' ? 'オフ' : (partial ? '一部未認識' : (stageLabels[stage.state] || '不明'));
        pill(`${name}-state`, fresh ? label : '不明', !fresh || partial || stage.state === 'paused' ? 'warning' : (stage.state === 'failed' ? 'error' : (stage.state === 'running' ? 'good' : 'muted')));
        put(`${name}-time`, formatTime(stage.through_seconds));
        showText(`${name}-error`, text(stage.error));
      }
      if (analysis.provider === 'openai' && analysis.state === 'failed') {
        const continues = captureView.tone === 'good' && capture.state === 'recording' && state?.session?.source_kind !== 'replay';
        const note = continues ? 'API解析が利用できなくても、録音・保存は継続しています。' : 'API解析と録音・保存は独立しています。録音・保存欄で現在の状態を確認してください。';
        showText('analysis-error', [text(analysis.error), note].filter(Boolean).join('\n'));
      }
      put('asr-detail', (asr.state === 'paused' ? '認識を保留 · ' : '') + (number(asr.queue_seconds) ? `認識待ち ${formatTime(asr.queue_seconds)}${!fresh ? '（前回観測）' : ''}` : '認識した音声の位置'));
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
      const waitingForWorker = state?.processing_active === true && !ACTIVE_CAPTURE.has(capture.state);
      const preflightWait = ['checking', 'blocked'].includes(state?.preflight?.state) && !ACTIVE_CAPTURE.has(capture.state);
      put('setup-hint', waitingForWorker ? '前のセッションの処理終了を確認するまで、新しい録音は開始できません。録音・認識・分析の状態を確認してください。' : (preflightWait ? '下の準備確認が終わると録音を開始できます。問題がある項目を確認してください。' : (session?.source_kind === 'replay' ? '保存済み音声の逐次再生です。Macのマイクは使用していません。' : 'Macのマイクから音声を保存します。録音はボタンを押してから始まります。')));
      renderPreflight(fresh, current);
      renderAgenda();
      renderControls();
      renderTranslationStatus(fresh);
    }

    function renderTranslationStatus(fresh) {
      const translation = state?.translation;
      const enabled = translation?.enabled === true;
      $('translation-status').hidden = !enabled;
      $('translation-retry-button').hidden = !enabled || translation.state !== 'failed' || translation.retry_required !== true;
      $('translation-retry-button').disabled = translationRetryBusy || !fresh;
      put('translation-retry-button', translationRetryBusy ? '翻訳の再試行を要求中…' : '翻訳を再試行');
      showText('translation-error', enabled ? text(translation.error) : '');
      if (!enabled) return;
      const labels = {idle: '待機', waiting: '待機', running: '翻訳中', completed: '処理済み', failed: '失敗・要確認', paused: '保留'};
      const continuation = translation.state === 'waiting' && translation.wait_reason === 'continuation' && !translation.worker_alive;
      const status = fresh ? (continuation ? '文の続き待ち' : (labels[translation.state] || '不明')) : '状態不明';
      const counts = [];
      for (const [key, label] of [['pending_lines', '未訳'], ['excluded_uncertain_lines', '不確か除外'], ['native_lines', '日本語']]) {
        if (number(translation[key])) counts.push(`${label} ${Math.max(0, translation[key])}行`);
      }
      put('translation-status', [`現在の訳: ${status}`, ...counts].join(' · '));
      $('translation-status').dataset.tone = !fresh || ['failed', 'paused'].includes(translation.state) ? 'warning' : 'muted';
    }

    function renderPreflight(fresh, current) {
      const preflight = state?.preflight;
      $('preflight-details').hidden = !preflight;
      if (!preflight) return;
      const warnings = array(preflight.checks).filter(check => check.state === 'warning').length;
      const label = !fresh ? '現在の準備状態は不明' : ({ready: warnings ? '録音可能・解析の注意あり' : '録音の準備ができています', checking: '録音の準備を確認中', blocked: '録音の準備に問題があります'})[preflight.state] || '準備状態は不明';
      put('preflight-summary', `準備確認: ${label}`);
      $('preflight-summary').dataset.tone = !fresh || preflight.state !== 'ready' || warnings ? 'warning' : 'good';
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

    function sourceButtons(parent, sourceIds, lineIndex, collapsed = true) {
      const ids = [...new Set(array(sourceIds).map(text).filter(Boolean))];
      if (!ids.length) return;
      let destination = parent;
      if (collapsed) {
        const details = element('details', 'source-disclosure');
        const summary = element('summary', '', `出典 ${ids.length}`);
        summary.title = '根拠となる原文を表示';
        details.appendChild(summary);
        destination = element('div', 'source-chips'); details.appendChild(destination); parent.appendChild(details);
      }
      for (const id of ids) {
        const line = lineIndex.get(id);
        const button = element('button', 'source-button', line ? `↗ ${formatTime(line.start_seconds)}` : `出典 ${id}（未取得）`);
        button.type = 'button';
        button.disabled = !line;
        button.title = line ? `${formatTime(line.start_seconds)} の原文を見る` : '対応する原文がこの表示にありません';
        if (line) button.addEventListener('click', () => {
          freeze();
          const target = lineElements.get(id);
          if (target) { target.scrollIntoView({block: 'nearest', behavior: 'auto'}); target.focus({preventScroll: true}); }
        });
        destination.appendChild(button);
      }
    }

    function sourceRange(ids, lineIndex) {
      const sources = ids.map(id => lineIndex.get(id)).filter(line => line && number(line.start_seconds) && number(line.end_seconds));
      return sources.length ? `${formatTime(Math.min(...sources.map(line => line.start_seconds)))}–${formatTime(Math.max(...sources.map(line => line.end_seconds)))}` : '';
    }

    function renderTranslations(next, result, lines, lineIndex) {
      if (next.translation?.enabled === true) {
        renderContinuousTranslations(next, lineIndex);
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
      put('translation-heading', hasLegacyTranslations ? '既存の断片訳' : '文脈で訳し直し');
      const ids = [...new Set(blocks.flatMap(block => block.source_ids))];
      put('translation-range', sourceRange(ids, lineIndex));
      put('translation-caption', blocks.length ? (hasBlocks ? '直近の対象発言をまとめて訳したものです。全講演・全区間を訳し直したものではありません。' : (historySelection ? 'この履歴に保存された断片訳です。現在の訳を当時の訳として補っていません。' : '保存済みの断片訳を発言順につなげて表示しています。文脈で訳し直したものではありません。')) : '');
      $('translation-caption').hidden = !blocks.length;
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
        const evidence = element('div', 'block-evidence');
        const range = sourceRange(block.source_ids, lineIndex);
        if (range) evidence.appendChild(element('span', 'block-range', range));
        sourceButtons(evidence, block.source_ids, lineIndex);
        article.appendChild(evidence); panel.appendChild(article);
      }
      panel.scrollTop = firstRender || nearBottom ? panel.scrollHeight : scrollTop;
    }

    function renderContinuousTranslations(next, lineIndex) {
      const cutoff = historySelection?.generated_at;
      const hasCutoff = number(cutoff) && cutoff > 0;
      const blocks = array(next.translation.blocks).filter(block => text(block?.id) && text(block?.text)
        && (!historySelection || (hasCutoff && number(block.published_at) && block.published_at <= cutoff)));
      const ids = [...new Set(blocks.flatMap(block => array(block.source_ids).map(text)))];
      put('translation-heading', '文脈で訳し直し');
      put('translation-range', blocks.length ? `${sourceRange(ids, lineIndex)} · ${blocks.length}ブロック` : '');
      put('translation-caption', historySelection ? (hasCutoff ? '選んだ分析の生成時刻までに公開された訳です。' : 'この分析の生成時刻が未記録のため、当時の訳は表示できません。') : '古い未訳から順に追加します。認識が不確かな原文は除外します。');
      $('translation-caption').hidden = false;
      put('translation-empty', historySelection ? 'この時点に対応する公開済みの訳はありません。' : '原文のまとまりから順に訳を追加します。');
      $('translation-empty').hidden = blocks.length > 0;
      const signature = JSON.stringify([next.session?.id, blocks, ids.map(id => [id, lineIndex.has(id), lineIndex.get(id)?.start_seconds, lineIndex.get(id)?.end_seconds])]);
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
        const sources = array(block.source_ids).map(text);
        const article = translationElements.get(id) || element('article', 'translation-block');
        const rowSignature = JSON.stringify([block, sources.map(source => [source, lineIndex.has(source), lineIndex.get(source)?.start_seconds, lineIndex.get(source)?.end_seconds])]);
        article.dataset.translationId = id;
        if (article.dataset.translationSignature !== rowSignature) {
          article.dataset.translationSignature = rowSignature;
          const evidence = element('div', 'block-evidence');
          const range = number(block.start_seconds) && number(block.end_seconds) ? `${formatTime(block.start_seconds)}–${formatTime(block.end_seconds)}` : sourceRange(sources, lineIndex);
          if (range) evidence.appendChild(element('span', 'block-range', range));
          sourceButtons(evidence, sources, lineIndex);
          article.replaceChildren(element('p', 'block-translation-text', text(block.text)), evidence);
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

    function renderTranscript(next, lines, forceLatest, previousSession) {
      const signature = JSON.stringify([next.session?.id, lines.map(line => [line.id, line.text, line.start_seconds, line.end_seconds, line.language, line.uncertain])]);
      if (signature === renderedTranscriptSignature) return;
      renderedTranscriptSignature = signature;
      const transcript = $('transcript');
      const scrollTop = transcript.scrollTop;
      const nearBottom = transcript.scrollHeight - transcript.clientHeight - scrollTop < 60;
      const oldIds = [...lineElements.keys()];
      const focusedId = oldIds.find(id => lineElements.get(id) === doc.activeElement);
      const nextElements = new Map();
      const rows = [];
      const latestId = text(lines[lines.length - 1]?.id);
      for (const line of lines) {
        const id = text(line.id);
        const row = lineElements.get(id) || element('article', 'transcript-line');
        const rawSignature = JSON.stringify([line.text, line.start_seconds, line.end_seconds, line.language, line.uncertain]);
        row.tabIndex = -1; row.dataset.sourceId = id;
        row.classList.toggle('latest-source', id === latestId);
        row.setAttribute('aria-current', id === latestId ? 'true' : 'false');
        if (row.dataset.rawSignature !== rawSignature) {
          row.dataset.rawSignature = rawSignature;
          row.title = `${formatTime(line.start_seconds)}–${formatTime(line.end_seconds)} · ${id}${text(line.language) ? ` · ${text(line.language).toUpperCase()}` : ''}${line.uncertain ? ' · 認識が不確か' : ''}`;
          row.classList.toggle('uncertain-source', !!line.uncertain);
          row.replaceChildren(element('p', 'line-original', text(line.text)));
        }
        nextElements.set(id, row); rows.push(row);
      }
      const appendOnly = oldIds.length > 0 && oldIds.length <= rows.length && oldIds.every((id, index) => id === text(lines[index]?.id));
      if (appendOnly) for (const row of rows.slice(oldIds.length)) transcript.appendChild(row);
      else transcript.replaceChildren(...rows);
      lineElements = nextElements;
      if (!lines.length) transcript.appendChild(element('p', 'transcript-empty', 'まだ原文はありません'));
      put('line-count', `${lines.length}件`);
      transcript.scrollTop = forceLatest || previousSession !== next.session?.id || nearBottom ? transcript.scrollHeight : scrollTop;
      if (focusedId && lineElements.has(focusedId) && doc.activeElement !== lineElements.get(focusedId)) lineElements.get(focusedId).focus({preventScroll: true});
    }

    function renderItems(id, emptyId, items, lineIndex) {
      const parent = $(id);
      parent.replaceChildren();
      let count = 0;
      for (const item of array(items)) {
        const value = text(item?.text);
        if (!value) continue;
        const li = element('li');
        li.appendChild(element('p', '', value));
        const sources = element('div', 'sources');
        sourceButtons(sources, item.source_ids, lineIndex);
        li.appendChild(sources);
        parent.appendChild(li);
        count++;
      }
      $(emptyId).hidden = count > 0;
    }

    function historyKey(item) {
      return JSON.stringify([state?.session?.id, item.through_seconds, item.generated_at, item.headline]);
    }

    function selectHistory(item) {
      if (!state || !number(item?.through_seconds) || !text(item?.headline?.text)) return;
      historySelection = {key: historyKey(item), through_seconds: item.through_seconds, generated_at: item.generated_at};
      frozen = true;
      frozenLiveSignature = contentSignature(state);
      // Keep the evidence IDs exactly as generated then. Current ASR remains
      // available for source jumps, without becoming input to that interpretation.
      const selected = {...state, analysis: {...state.analysis, through_seconds: item.through_seconds,
        generated_at: item.generated_at, result: item}};
      renderContent(selected, true);
      renderHistory();
      renderControls();
    }

    function renderHistory() {
      const history = array(state?.analysis_history).filter(item => number(item?.through_seconds) && text(item?.headline?.text)).slice(-60);
      $('analysis-history').hidden = !history.length;
      const signature = JSON.stringify([state?.session?.id, history]);
      if (signature === renderedHistorySignature) {
        for (const [key, button] of historyButtons) button.setAttribute('aria-pressed', String(historySelection?.key === key));
        return;
      }
      const focusedKey = [...historyButtons.entries()].find(([, button]) => button === doc.activeElement)?.[0];
      renderedHistorySignature = signature;
      put('history-heading', `これまでの分析（${history.length}件）`);
      $('history-list').replaceChildren();
      historyButtons = new Map();
      for (const item of history.slice().reverse()) {
        const row = element('li');
        const button = element('button', 'history-button');
        button.type = 'button';
        button.setAttribute('aria-pressed', String(historySelection?.key === historyKey(item)));
        button.appendChild(element('span', 'history-time', formatTime(item.through_seconds)));
        button.appendChild(element('span', 'history-headline', text(item.headline.text)));
        button.title = `${formatTime(item.through_seconds)} までの発言に基づく分析を固定表示`;
        button.addEventListener('click', () => selectHistory(item));
        historyButtons.set(historyKey(item), button);
        row.appendChild(button);
        $('history-list').appendChild(row);
      }
      if (focusedKey && historyButtons.has(focusedKey)) historyButtons.get(focusedKey).focus({preventScroll: true});
    }

    function renderContent(next, forceLatest = false) {
      const signature = contentSignature(next);
      if (!forceLatest && signature === renderedSignature) return;
      const previousSession = displayedState?.session?.id;
      displayedState = next;
      renderedSignature = signature;
      const lines = array(next.lines);
      const lineIndex = new Map(lines.map(line => [text(line.id), line]));
      const result = next.analysis?.result || {};
      const evidenceIds = [...new Set([result.headline, ...array(result.flow), ...array(result.summary), ...array(result.concepts)]
        .flatMap(item => array(item?.source_ids)).concat(array(result.source_ranges).map(range => range.source_id)).map(text).filter(Boolean))];
      const interpretationSignature = JSON.stringify([next.session?.id, historySelection?.key, next.analysis?.provider, next.analysis?.through_seconds,
        result.headline, result.flow, result.summary, result.concepts, result.source_ranges,
        evidenceIds.map(id => [id, lineIndex.has(id), lineIndex.get(id)?.start_seconds])]);
      // New raw speech must not collapse evidence a reader has opened on the right.
      if (interpretationSignature !== renderedInterpretationSignature) {
        renderedInterpretationSignature = interpretationSignature;
        const headline = text(result.headline?.text);
        put('focus-heading', historySelection ? 'その時点の論点' : 'いまの論点');
        put('summary-heading', historySelection ? '当時の要点' : '直近の要点');
        put('headline', headline || (next.analysis?.provider === 'off' ? '理解支援はオフです。原文を表示しています。' : '話のまとまりが届くと、いまの論点を表示します。'));
        $('headline').classList.toggle('empty', !headline);
        $('headline-sources').replaceChildren();
        sourceButtons($('headline-sources'), result.headline?.source_ids, lineIndex);
        renderItems('flow-list', 'flow-empty', result.flow, lineIndex);
        renderItems('summary-list', 'summary-empty', result.summary, lineIndex);
        $('concepts-list').replaceChildren();
        let conceptCount = 0;
        for (const concept of array(result.concepts)) {
          if (!text(concept?.term) || !text(concept?.explanation)) continue;
          const card = element('article', 'concept');
          const header = element('div', 'concept-header');
          header.appendChild(element('h3', '', text(concept.term)));
          const label = concept.basis === 'lecture' ? '講演に基づく説明' : (concept.basis === 'background' ? '背景補足' : '由来未確認');
          const basis = element('span', 'basis', label);
          basis.dataset.background = String(concept.basis !== 'lecture');
          header.appendChild(basis);
          card.appendChild(header);
          card.appendChild(element('p', '', text(concept.explanation)));
          const sources = element('div', 'sources');
          sourceButtons(sources, concept.source_ids, lineIndex);
          card.appendChild(sources);
          $('concepts-list').appendChild(card);
          conceptCount++;
        }
        $('concepts-empty').hidden = conceptCount > 0;
        const through = next.analysis?.through_seconds;
        put('analysis-caption', headline ? `${historySelection ? '履歴 · ' : ''}音声位置 ${formatTime(through)} 時点のAIの整理です。講演全体の振り返りは「これまでの分析」から確認できます。` : 'その時点までの発言をもとに更新します。');
        $('analysis-coverage').hidden = !headline;
        const ranges = array(result.source_ranges).filter(range => text(range?.source_id) && number(range?.start_seconds) && number(range?.end_seconds));
        $('coverage-list').replaceChildren();
        if (ranges.length) {
          const first = Math.min(...ranges.map(range => range.start_seconds));
          const last = Math.max(...ranges.map(range => range.end_seconds));
          put('coverage-summary', `参照した発言 ${formatTime(first)}〜${formatTime(last)} · ${ranges.length}行（抜粋）`);
          put('coverage-explanation', '範囲内の全発言を参照した意味ではありません。直近の発言に、必要な過去の根拠を加えた抜粋です。');
          for (const range of ranges) {
            const row = element('li');
            row.appendChild(element('span', '', `${formatTime(range.start_seconds)}–${formatTime(range.end_seconds)}`));
            sourceButtons(row, [range.source_id], lineIndex, false);
            $('coverage-list').appendChild(row);
          }
        } else {
          put('coverage-summary', '参照した発言の範囲は未記録');
          put('coverage-explanation', 'この保存結果には参照範囲の記録がありません。各解説の出典ボタンから原文を確認してください。');
        }
      }
      renderTranslations(next, result, lines, lineIndex);
      put('session-title', text(next.session?.title) || 'ライブの理解支援');
      const mode = next.session?.source_kind === 'replay' ? '保存音声の逐次再生' : 'Mac マイク · 1ch';
      put('session-detail', next.session ? `${mode} · ${text(next.session.id)}` : '開始すると、原文と解説がここに届きます。');
      renderTranscript(next, lines, forceLatest, previousSession);
    }

    function acceptState(next) {
      if (!next || next.schema_version !== 1 || !next.capture || !next.asr || !next.analysis || !Array.isArray(next.lines)) throw new Error('サーバーの状態形式が対応していません。再読み込みしてください。');
      state = next;
      connected = true;
      connectionError = '';
      lastReceivedAt = now();
      if (!frozen) renderContent(state);
      renderHistory();
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
            const status = state.capture.state;
            pending = null;
            if (action === 'start') message(ACTIVE_CAPTURE.has(status) ? '録音の開始を確認しました。' : (actionError || '開始要求後の状態を取得しました。録音の状態欄を確認してください。'), ACTIVE_CAPTURE.has(status) ? 'good' : 'warning');
            else if (!ACTIVE_CAPTURE.has(status)) message('録音の終了を確認しました。残りの認識・分析は続きます。');
            else if (status === 'stopping') message('停止を要求しました。保存完了まで状態を確認します。');
            else message(actionError || '録音はまだ進行中です。停止の完了を確認できていません。', 'warning');
            renderControls();
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
      const control = getControlState(state, connected, now(), !!$('device-select').value, pending);
      if ((kind === 'start' && control.startDisabled) || (kind === 'stop' && control.stopDisabled)) return;
      if (kind === 'start' && $('provider-select').value === 'openai' && state?.capabilities?.cloud_enabled !== true) { message('この起動ではクラウド解析は有効になっていません。', 'warning'); return; }
      if (kind === 'start') { saveIdlePreferences(); goLive(); }
      pending = {kind, phase: 'sending', minPoll: Infinity};
      message(kind === 'start' ? '録音の開始を要求しています…' : '停止と音声の保存を要求しています…');
      renderControls();
      const body = kind === 'start' ? {device: $('device-select').value, language: $('language-select').value, provider: $('provider-select').value} : {};
      if (kind === 'start' && $('model-input').value.trim() && body.provider !== 'off') body.model = $('model-input').value.trim();
      try {
        await fetchJSON(`/api/${kind}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
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
      if (retryBusy || !isFresh(state, connected, now()) || state?.analysis?.state !== 'failed' || state?.analysis?.provider === 'off') return;
      retryBusy = true;
      renderControls();
      try {
        await fetchJSON('/api/retry-analysis', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
        message('分析の再試行を要求しました。録音と原文の処理は継続します。');
      } catch (error) { message(`分析の再試行を確認できませんでした: ${error.message}`, 'warning'); }
      finally { retryBusy = false; await poll(); renderControls(); }
    }

    async function retryTranslation() {
      if (translationRetryBusy || !isFresh(state, connected, now()) || state?.translation?.enabled !== true || state.translation.state !== 'failed' || state.translation.retry_required !== true) return;
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
      if (!frozen) frozenLiveSignature = contentSignature(state);
      frozen = true;
      renderControls();
    }

    function goLive() {
      frozen = false;
      historySelection = null;
      frozenLiveSignature = '';
      if (state) renderContent(state, true);
      renderHistory();
      renderControls();
    }

    $('start-button').addEventListener('click', () => recordAction('start'));
    $('stop-button').addEventListener('click', () => recordAction('stop'));
    $('devices-button').addEventListener('click', loadDevices);
    $('retry-button').addEventListener('click', retryAnalysis);
    $('translation-retry-button').addEventListener('click', retryTranslation);
    $('freeze-button').addEventListener('click', () => frozen ? goLive() : freeze());
    $('latest-button').addEventListener('click', goLive);
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
    return {poll, loadDevices, freeze, goLive, recordAction, retryAnalysis, retryTranslation, acceptState, renderStatus,
      dispose() { disposed = true; clearTimer(pollTimer); clearTimer(tickTimer); },
    };
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = {formatTime, ageText, isFresh, capturePresentation, getControlState, translationMap, createApp};
  if (typeof document !== 'undefined') createApp(document, (...args) => fetch(...args));
})();
