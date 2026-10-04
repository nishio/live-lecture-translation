/* Read-only controller; the lecture renderer and its source/history interactions are reused unchanged. */
(() => {
  'use strict';
  function formatTime(seconds) {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
  }
  function nextPosition(position, elapsed, speed, duration) {
    return Math.min(duration, Math.max(0, position + Math.max(0, elapsed) * speed));
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {formatTime, nextPosition};
  if (typeof document === 'undefined') return;

  const $ = id => document.getElementById(id);
  let meta, app, position = 0, shownPosition = 0, playing = false, speed = 4;
  let lastTick = performance.now(), requestId = 0, requesting = false, lastSnapshot = null, queuedRequest = null;
  const landmarkButtons = [];
  const put = (id, value) => { $(id).textContent = value; };
  const error = value => { put('demo-error', value); $('demo-error').hidden = !value; };
  const readOnlyTransport = async () => { throw new Error('このデモでは操作要求を送信しません。'); };

  function updateControls() {
    if (!meta) return;
    $('demo-slider').value = position;
    put('demo-position', lastSnapshot ? `${formatTime(shownPosition)} / ${formatTime(meta.duration_seconds)}` : '取得中');
    put('demo-loading', requesting ? `${formatTime(position)} へ移動中（内容は表示時点のまま）` : '');
    $('demo-loading').hidden = !requesting;
    $('demo-slider').setAttribute('aria-valuetext', `移動先 ${formatTime(position)}、表示済み ${formatTime(shownPosition)}`);
    put('demo-play', playing ? 'Ⅱ 表示を一時停止' : '▶ 表示を再生');
    $('demo-play').setAttribute('aria-pressed', String(playing));
    for (const [button, value] of landmarkButtons) button.setAttribute('aria-pressed', String(Math.abs(position - value) < .5));
  }

  function decorate(snapshot) {
    put('capture-label', '当時の音声進行');
    put('capture-state', snapshot.demo.audio_input_ended ? '入力終了の記録' : '記録の表示');
    $('capture-state').dataset.tone = 'muted';
    put('capture-detail', `記録上 ${formatTime(snapshot.capture.audio_seconds)} · 現在の録音ではありません`);
    put('session-detail', '保存済みの原文・解析を公開順に表示 · 音声再生なし');
    put('processing-caption', '保存結果の閲覧 · 現在の録音・認識・API送信なし');
    put('demo-progress', `原文 ${snapshot.demo.published_lines}/${meta.line_count} · 分析 ${snapshot.demo.published_analyses}/${meta.analysis_count} · 和訳未作成 ${snapshot.demo.untranslated_lines}`);
    if ($('freeze-button').getAttribute('aria-pressed') === 'true') put('freeze-notice', '解説の表示を固定しています。デモの時間移動でその時点の表示へ戻ります。');
  }

  function renderAt(value, resetReading = false) {
    queuedRequest = {value, resetReading, id: ++requestId};
    if (requesting) { updateControls(); return; }
    return readQueued();
  }

  async function readQueued() {
    const {value, resetReading, id: thisRequest} = queuedRequest;
    queuedRequest = null;
    requesting = true;
    updateControls();
    try {
      const response = await fetch(`/api/state?at=${encodeURIComponent(value)}`, {cache: 'no-store'});
      const snapshot = await response.json();
      if (!response.ok) throw new Error(snapshot.error || '保存状態を取得できませんでした。');
      if (thisRequest !== requestId) return;
      shownPosition = value;
      lastSnapshot = snapshot;
      app.acceptState(snapshot);
      if (resetReading) app.goLive();
      decorate(snapshot);
      error('');
    } catch (failure) {
      if (thisRequest !== requestId) return;
      playing = false;
      position = shownPosition;
      error(`デモ表示を確認できませんでした: ${failure.message}`);
      updateControls();
    } finally {
      requesting = false;
      updateControls();
      if (queuedRequest) readQueued();
    }
  }

  function seek(value) {
    playing = false;
    position = Math.max(0, Math.min(meta.duration_seconds, value));
    lastTick = performance.now();
    updateControls();
    renderAt(position, true);
  }

  async function start() {
    try {
      const response = await fetch('/api/demo', {cache: 'no-store'});
      meta = await response.json();
      if (!response.ok) throw new Error(meta.error || '保存記録を取得できませんでした。');
      // A different cookie name keeps the production app's authentication intact.
      history.replaceState(null, '', location.pathname);
      document.querySelector('.setup').inert = true;
      $('start-button').disabled = true;
      $('stop-button').disabled = true;
      $('retry-button').disabled = true;
      app = window.LectureDemoRenderer.createApp(document, readOnlyTransport, {autoStart: false, storage: null, now: () => meta.started_at + shownPosition});
      put('demo-source', `${meta.source_label} · ${meta.model} / ${meta.configuration.analysis_interval}秒間隔`);
      put('demo-heading', meta.display_simulation ? '改善版の表示シミュレーション · 再表示API $0' : '保存結果の表示再現 · 再表示API $0');
      put('demo-cost', meta.display_simulation
        ? `再表示のAPI料金は $0。今回の新しい解析を準備した料金: ${Number.isFinite(meta.preparation_api_usd) ? `$${meta.preparation_api_usd.toFixed(6)}` : '未確定'}。Codex利用・電力は未計測です。`
        : `今回の再表示: 追加API $0。元の試験の確定API料金: ${Number.isFinite(meta.recorded_api_usd) ? `$${meta.recorded_api_usd.toFixed(6)}` : '未確定'}。記録全体 ${meta.analysis_count}解析のうち${meta.cache_hits}件はキャッシュ利用で、その実際の待ち時間を含みます。`);
      put('demo-timing', meta.timing_note);
      $('demo-slider').max = meta.duration_seconds;
      $('demo-slider').disabled = false;
      $('demo-play').disabled = false;
      for (const point of meta.landmarks) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = `${point.label} ${formatTime(point.seconds)}`;
        button.addEventListener('click', () => seek(point.seconds));
        $('demo-landmarks').appendChild(button);
        landmarkButtons.push([button, point.seconds]);
      }
      $('demo-slider').addEventListener('input', () => seek(Number($('demo-slider').value)));
      $('demo-speed').addEventListener('change', () => { speed = Number($('demo-speed').value); lastTick = performance.now(); });
      $('demo-play').addEventListener('click', () => {
        if (position >= meta.duration_seconds) { position = 0; renderAt(position, true); }
        playing = !playing; lastTick = performance.now(); updateControls();
      });
      document.addEventListener('click', () => { if (lastSnapshot) decorate(lastSnapshot); });
      position = meta.initial_seconds;
      updateControls();
      await renderAt(position, true);
      // Source/history freeze still works. The demo control only advances the clock.
      setInterval(() => {
        const current = performance.now();
        if (playing) {
          position = nextPosition(position, (current - lastTick) / 1000, speed, meta.duration_seconds);
          if (position >= meta.duration_seconds) playing = false;
          updateControls();
        }
        // Also commit the final frame if the previous request was still in flight
        // when playback reached the end. Old responses cannot win a later seek.
        if (!requesting && Math.abs(position - shownPosition) > .000001) renderAt(position);
        lastTick = current;
      }, 300);
    } catch (failure) { error(`デモを準備できませんでした: ${failure.message}`); }
  }
  start();
})();
