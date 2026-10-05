/* Saved publications use the same reading renderer; playback sends only GET requests. */
(() => {
  'use strict';
  function formatTime(seconds) {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
  }
  function nextPosition(position, elapsed, speed, duration) {
    return Math.min(duration, Math.max(0, position + Math.max(0, elapsed) * speed));
  }
  function audioPosition(seconds, start, duration) {
    return Math.min(duration, Math.max(0, start + Math.max(0, Number(seconds) || 0)));
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {formatTime, nextPosition, audioPosition};
  if (typeof document === 'undefined') return;

  const $ = id => document.getElementById(id);
  let meta, app, audio, position = 0, shownPosition = 0, playing = false, starting = false;
  let lastTick = performance.now(), generation = 0, playGeneration = 0, requesting = false, queuedRequest = null;
  let audioFinished = false, audioStarted = false, stateError = '', audioError = '';
  const put = (id, value) => { $(id).textContent = value; };
  const showError = () => { put('demo-error', [audioError, stateError].filter(Boolean).join('\n')); $('demo-error').hidden = !audioError && !stateError; };
  const readOnlyTransport = async () => { throw new Error('保存結果の再生では処理要求を送信しません。'); };
  const audioEnd = () => Math.min(meta.duration_seconds, meta.audio_start_seconds + meta.audio_seconds);

  function updateControls() {
    if (!meta) return;
    $('demo-slider').value = position;
    put('demo-position', `${formatTime(position)} / ${formatTime(meta.duration_seconds)}`);
    $('demo-slider').setAttribute('aria-valuetext', `${formatTime(position)} / ${formatTime(meta.duration_seconds)}`);
    $('demo-slider').setAttribute('aria-busy', String(requesting));
    put('demo-play', starting ? '準備中…' : playing ? 'Ⅱ 一時停止' : '▶ 再生');
    $('demo-play').setAttribute('aria-pressed', String(playing));
    $('demo-play').setAttribute('aria-label', starting ? '再生の準備を中止' : playing ? '一時停止' : '再生');
  }

  function placeAudio() {
    if (!audio) return;
    const target = Math.min(meta.audio_seconds, Math.max(0, position - meta.audio_start_seconds));
    // Some browsers defer the initial seek until metadata is available.
    try { audio.currentTime = target; } catch (_) { /* loadedmetadata retries below. */ }
    audioFinished = position >= audioEnd();
    audioStarted = false;
  }

  function pause() {
    if (playing && audio && audioStarted && !audioFinished) position = audioPosition(audio.currentTime, meta.audio_start_seconds, meta.duration_seconds);
    playing = false; starting = false; playGeneration++;
    if (audio) { audio.pause(); audio.muted = false; }
    lastTick = performance.now();
    updateControls();
  }

  function renderAt(value, resetReading = false) {
    if (resetReading) generation++;
    queuedRequest = {value, resetReading, generation};
    if (requesting) return;
    return readQueued();
  }

  async function readQueued() {
    const request = queuedRequest;
    queuedRequest = null; requesting = true; updateControls();
    try {
      const response = await fetch(`/api/state?at=${encodeURIComponent(request.value)}`, {cache: 'no-store'});
      const snapshot = await response.json();
      if (!response.ok) throw new Error(snapshot.error || '保存状態を取得できませんでした。');
      if (request.generation !== generation) return;
      shownPosition = request.value;
      app.acceptState(snapshot);
      if (request.resetReading) app.goLive();
      stateError = ''; showError();
    } catch (failure) {
      if (request.generation !== generation) return;
      pause(); position = shownPosition; placeAudio();
      stateError = `表示を取得できませんでした。再生または時間の選択で再試行できます: ${failure.message}`;
      showError();
    } finally {
      requesting = false; updateControls();
      if (queuedRequest) readQueued();
    }
  }

  function seek(value) {
    pause();
    position = Math.max(0, Math.min(meta.duration_seconds, Number(value) || 0));
    placeAudio(); updateControls();
    return renderAt(position, true);
  }

  async function play() {
    if (playing || starting) { pause(); return; }
    if (position >= meta.duration_seconds) seek(0);
    const token = ++playGeneration;
    audioError = ''; showError();
    const beforeAudio = audio && position < meta.audio_start_seconds;
    if (audio && !audioFinished) {
      starting = true; updateControls();
      // Prime playback in the click gesture even when recording had a short startup gap.
      audio.muted = !!beforeAudio;
      try {
        await audio.play();
        if (token !== playGeneration) { if (!playing && !starting) audio.pause(); return; }
        if (beforeAudio) { audio.pause(); placeAudio(); }
        else audioStarted = true;
        audio.muted = false;
      } catch (failure) {
        if (token !== playGeneration) return;
        audio.pause(); audio.muted = false; starting = false; playing = false;
        audioError = `音声を再生できませんでした。再生ボタンで再試行してください: ${failure.message}`;
        showError(); updateControls(); return;
      }
    }
    if (token !== playGeneration) return;
    starting = false; playing = true; lastTick = performance.now(); updateControls();
    if (stateError) renderAt(position, true);
  }

  function tick() {
    const current = performance.now();
    if (playing) {
      if (audio && audioStarted && !audioFinished) {
        position = audioPosition(audio.currentTime, meta.audio_start_seconds, meta.duration_seconds);
      } else {
        position = nextPosition(position, (current - lastTick) / 1000, 1, meta.duration_seconds);
        if (audio && !audioFinished && position >= meta.audio_start_seconds) {
          position = meta.audio_start_seconds;
          playing = false;
          play();
        }
      }
      if (position >= meta.duration_seconds) pause();
      updateControls();
    }
    // Playback ticks do not invalidate an in-flight read. Slow reads may publish an
    // intermediate frame; explicit seeks alone invalidate an older response.
    if (!requesting && !queuedRequest && Math.abs(position - shownPosition) > .000001) renderAt(position);
    lastTick = current;
  }

  async function start() {
    try {
      const response = await fetch('/api/demo', {cache: 'no-store'});
      meta = await response.json();
      if (!response.ok) throw new Error(meta.error || '保存記録を取得できませんでした。');
      if (!Number.isFinite(meta.started_at) || !Number.isFinite(meta.duration_seconds) || meta.duration_seconds < 0) throw new Error('再生時間を確認できません。');
      meta.audio_start_seconds = Math.max(0, Number(meta.audio_start_seconds) || 0);
      meta.audio_seconds = Math.max(0, Number(meta.audio_seconds) || 0);
      history.replaceState(null, '', location.pathname);
      document.querySelector('.setup').inert = true;
      app = window.LectureDemoRenderer.createApp(document, readOnlyTransport, {autoStart: false, storage: null, now: () => meta.started_at + shownPosition});
      if (meta.audio_url) {
        audio = $('demo-audio'); audio.src = meta.audio_url; audio.playbackRate = 1;
        audio.addEventListener('loadedmetadata', () => { if (!playing && !starting) placeAudio(); });
        audio.addEventListener('ended', () => {
          if (!playing || !audioStarted) return;
          position = audioEnd(); audioFinished = true; audioStarted = false; lastTick = performance.now();
          if (position >= meta.duration_seconds) pause();
          updateControls(); renderAt(position);
        });
        audio.addEventListener('error', () => {
          pause(); audioError = '音声を読み込めませんでした。ページを再読み込みして再試行してください。'; showError();
        });
      }
      $('demo-slider').max = meta.duration_seconds;
      $('demo-slider').disabled = false; $('demo-play').disabled = false; $('demo-restart').disabled = false;
      $('demo-slider').addEventListener('input', () => seek($('demo-slider').value));
      $('demo-play').addEventListener('click', play);
      $('demo-restart').addEventListener('click', () => seek(0));
      position = Math.max(0, Math.min(meta.duration_seconds, Number(meta.initial_seconds) || 0));
      placeAudio(); updateControls(); await renderAt(position, true);
      setInterval(tick, 300);
    } catch (failure) { stateError = `再生を準備できませんでした: ${failure.message}`; showError(); }
  }
  start();
})();
