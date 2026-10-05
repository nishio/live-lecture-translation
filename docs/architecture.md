# Architecture

## Capture is the first responsibility

`lecture_capture_native.swift` uses macOS audio APIs to select an input and convert it to 16 kHz, mono, 16-bit PCM. `lecture_capture.py` receives the stream, writes continuous PCM and split WAV files, and records frame and file metadata. The default recognition chunk is 15 seconds.

Capture must continue independently of recognition and text generation. A successful process start or HTTP response is insufficient evidence that useful audio is being stored. The application tracks input reception and confirmed storage progress separately. Zero-valued PCM can be valid silence; a lack of incoming PCM is a different condition.

The native helper is tied to its source through a build identity. Capture verification should check frame count, sequence, timestamps, PCM boundaries, and file hashes. These properties establish storage integrity; they do not establish intelligibility or transcription accuracy.

## Recognition publishes source evidence

`lecture_live.py` maintains a local MLX Whisper worker and reads a manifest identifying the locally available model. The reference workflow uses `large-v3-turbo`. Source utterances retain IDs, recognized text, timing, language, and uncertainty information.

Recognition publishes independently of the cloud worker. A request already generating a translation or analysis must not hold back newly recognized text. Recognition chunks introduce their own waiting time before any inference begins.

One completed audio chunk can produce several Whisper segments. Each segment becomes a source line, and the chunk's lines are published together when recognition completes. The saved-result player preserves this publication boundary rather than revealing each line at its utterance timestamp.

The separate [chunk-duration and provisional-buffer experiment](experiments/asr-chunk-duration.md) compares fresh recognition at different lengths and rolling snapshots. It selects a 3-second refresh with a trailing 15-second revisable buffer as a future implementation candidate. The current live source path is still append-only with a default 15-second chunk; provisional display, stable-prefix reconciliation, and translation-time re-recognition are not integrated.

Raw recognized speech remains the evidence layer. A future correction system should preserve the original recognition, the proposed correction, and the evidence for accepting it. A plausible name is not an adequate substitute for missing evidence.

## Translation tracks unfinished work

`lecture_translation.py` plans blocks from the oldest eligible untranslated utterances. Eligibility excludes empty, Japanese, uncertain, and already covered source lines. Blocks do not cross excluded or discontinuous source boundaries.

The extraction's planning limits are at most three groups per request, each at most eight lines, 45 seconds, and 2,000 bytes, with a 6,000-byte total target cap. Up to 45 seconds of already available source context on either side can accompany the selected targets. Future speech is not available to the request.

The continuous coordinator now uses a sentence-aware, whole-row planner. Within every bounded group it prefers the last row ending in sentence-like punctuation, excluding common abbreviations, initials, ellipses and dangling connective words. Live candidate boundaries require at least two seconds of processed-ASR audio after that row's end. This is a heuristic lookahead condition, not re-recognition or proof that a sentence is complete; the default 15-second recognition chunks determine when new evidence actually becomes available. A row containing a complete sentence followed by an unfinished clause is held in full because coverage remains source-ID based.

Unfinished tails remain pending. A candidate can be released as a fragment at a capacity limit, a source discontinuity (including a known failed-ASR interval), 30 seconds of processed audio after its oldest remaining row ends, or final input drain. The selected groups retain `sentence`, `limit`, `source_gap`, `timeout`, or `end_of_input` reasons in the fingerprinted plan and saved translation history. The timeout is not wall-clock time: capture progress without completed ASR cannot expire it. These initial thresholds are engineering defaults, not measured optima. Oversized individual rows still fail explicitly without being discarded.

The scheduler distinguishes pending text from a ready request. A boundary-only wait does not consume the 60-second generation interval or occupy the cloud slot; the dashboard shows 「文の続き待ち」 without a completion countdown. Final drain waits for the last in-flight ASR job, retains recognition failures, and processes the remaining eligible fragments. Retries keep their original frozen plan even when later context arrives. The original capacity-only planner remains available to existing callers; provisional ASR integration and character-range coverage are separate follow-ups.

Returned source IDs must cover the requested targets in their original order. Translation is marked complete only after a successful result is persisted. A failed request leaves its targets pending. In continuous cloud mode, typed recoverable failures receive bounded automatic retries of the same request input; other failures expose a dedicated retry action. Coverage checks prevent silent queue loss; they do not validate the meaning of a translation.

## Analysis answers a different need

`lecture_analysis.py` builds a compact understanding view from recognized speech: the topic, connections between points, key points, and concepts. It uses source utterances and their provenance. Unrelated notes, a reference library, or knowledge of the final lecture must not enter the source-evidence stream.

Translation aims to preserve the eligible speech sequence. Analysis selects and compresses information. One should not serve as an implicit replacement for the other.

Continuous translation has a nominal 60-second interval; analysis has a nominal 120-second interval. They share one cloud request slot and are scheduled so that one workload does not monopolize it. The actual publication time also includes chunk waiting, recognition, earlier queued work, network latency, generation, validation, and persistence.

## What the cloud model receives

The standard [cloud launcher](../start.command) explicitly selects `gpt-6.1-sol` for translation and analysis. MLX Whisper performs audio recognition locally. The shared adapter also supports `gpt-6-luna` and uses it when no model is supplied at that layer; this fallback does not override the launcher's explicit choice.

Each request contains fixed system instructions, a JSON user message, and a required JSON output schema. The current [adapter payload](../audio-array/event_insights_cloud.py) is text-only and has no search or other tool configuration. It does not attach audio, repository/wiki content, or a conversation history. The normalized transcript rows contain only `id`, `start_seconds`, `end_seconds`, `text`, `language`, and `uncertain`.

| Workload | User-message fields | Selected source and output |
| --- | --- | --- |
| Continuous translation | `through_seconds`, `transcript`, `target_groups`, `context_source_ids` | Oldest eligible untranslated groups plus selected available context up to 45 seconds on either side; returns translations with source IDs |
| Understanding support | `through_seconds`, `transcript`, `translation_ids`, `new_source_ids`, `previous_through_seconds`, `previous_context` | Recent 180 seconds plus retained prior source evidence, at most 100 lines and reduced to fit the 22,000-byte allowance; returns headline, summary, flow, concepts, and questions in one analysis result |

In normal continuous mode, analysis does not also translate lines: `translation_ids` is empty and block translation is disabled. Previous analysis helps select source evidence and determine new source IDs, but its prose is not sent by default (`use_previous=False`, so `previous_context` is empty). Translation input likewise excludes prior Japanese translations and generated explanations. These describe the code path, not an inspection of a private session's payload.

Concept explanations already permit two origins: `lecture` for what was explained in the source, and `background` for unverified model general knowledge. A background concept's source IDs locate the mention of the term; they do not substantiate the external explanation. There is no retrieval step. The prompt asks for at most two concepts and short explanations. Whether Sol, another model, or retrieval gives adequate explanations requires a matched quality evaluation; the existing Sol trial does not establish Sol-specific necessity.

## What the translation and concept labels currently mean

The dashboard labels continuous translation 「文脈付きの日本語訳」. The v0.9 heading 「文脈で訳し直し」 was misleading: this path appends translations of previously untranslated source groups. It does not revise a published translation when later context arrives; the coordinator rejects overwriting already covered source IDs. 「翻訳を再試行」 retries failed pending work, rather than rewriting a completed translation for quality.

The older non-continuous analysis path can regenerate bounded block translations from excerpts in the latest 60-second window, so the same source can recur in later snapshots. It still translates source text instead of editing prior Japanese prose. For older results without block translations, the dashboard concatenates saved line translations and labels them 「既存の断片訳」 without making a model call. These are separate display paths.

All translation reading paths show the translated prose without per-block timestamps or source-count controls. Source IDs and timing remain in the saved data and still determine historical visibility. The reading view omits source-count disclosures, translation/analysis detail sections, the separate history list and routine explanatory labels. These presentation choices do not change the saved evidence or verify generated text.

The concepts panel accumulates explanations in a scrollable timeline, retaining timestamps in data. Identical term/explanation/basis/source combinations are deduplicated; changed explanations retain earlier versions. Appended cards retain existing DOM nodes, focus and scroll position. An explicit new-explanation action moves to the latest cards. Historical views filter out later interpretations and published translations.

The server retains its recent 60 snapshots in normal state, while the authenticated, read-only `/api/analysis-history` endpoint pages older persisted analyses with a session-bound cursor and fixed generation cutoff. The browser merges those pages with already received history. Only a server-selected result directory is read; a saved view uses the directory explicitly selected by the CLI. Corrupt/incomplete/missing-time entries are reported as skipped. Files larger than 64 MiB require a separate reader. Previous/next controls offer direct access to earlier understanding, and the previous control can load earlier saved history. See [understanding-support feedback](../wiki/understanding-support.md).

## State and shutdown are explicit

The coordinator keeps capture, recognition, translation, and analysis state visible. A stale or unsuccessful state query means that current state is unknown. It does not mean recording has stopped.

The dashboard stop action stops new capture and follows the remaining recognition, translation, and final analysis. Application interruption is a different path: remaining work may be left pending. Automatic resumption after process exit is not implemented in 0.9.

Storage locks and state snapshots are separated so that status can expose the last confirmed progress when an I/O operation is slow. The reader's input watchdog shares its thread with I/O; it cannot by itself guarantee recovery from a filesystem operation that never returns. A responsive stop request can therefore report that completion is unconfirmed.

## The reader controls the view

The reading workspace uses the browser viewport height (`100dvh`, with a `100vh` fallback), and long content scrolls within its pane instead of extending the page. The left column stacks original speech above accumulated contextual translation. Its horizontal divider adjusts their height share from 25% to 75%, retains the setting in browser storage, and supports pointer and keyboard input. The right column prioritizes the current interpretation, its key points, and retained concept explanations. Routine metadata, source-reference buttons and explanatory disclosures are omitted from the reading panes; process controls remain available separately. A new source line does not require rebuilding understanding content or moving the reader's position.

A compact process overview retains the independent stage states. New failures, stalled/unknown states, and unresolved reservations open the process details; dismissing the same problem does not reopen it on each poll. Countdown summaries reserve fixed space for the circle and short wait/state label, so digit count and lifecycle changes cannot move the circle; full timing and retry controls are inside. Uncertain source text remains visually muted. Routine AI disclaimers and concept-origin badges are omitted from the reading screen; generated content remains separate from recognized speech, with its provenance in saved data and documentation. These presentation changes have synthetic UI coverage, not a measured improvement in lecture comprehension.

Live progress and the currently viewed time are separate concepts. Historical views use publication timestamps so that a later translation cannot appear in an earlier snapshot. The reader can hold the view, and returning to live is explicit.

Uncertainty and recency are also separate: gray text indicates uncertain recognition; a pale highlight indicates the most recent source line visible at the chosen time.

Consecutive uncertain source lines with the same language and text are displayed as one row, comparing text with case and whitespace normalized. Punctuation and other text differences remain distinct, and an intervening line ends a group. The first line supplies the displayed text; the row retains all member source IDs and receives the latest highlight if it contains the latest line. This grouping only affects the reading display: saved recognition, timing, uncertainty, translation inputs and source coverage are unchanged. Rewind and held views group only the lines visible at that point.

## External requests are bounded

Cloud text processing is optional and requires configuration of both permitted text scope and spending limits. Scope accounting and cost accounting have different units and purposes. Failed requests with uncertain usage keep their reservation until it can be reconciled.

When multiple processes are intentionally run under one allowance, their authoritative accounting and inference lock must be shared. Copying a working directory must not silently duplicate that allowance. An application configuration and the already loaded runtime must agree on supported models and limits.

The local HTTP interface uses authenticated access. Browser cookies are scoped by host, not port. Different cookie names prevent one local instance from overwriting another's authentication; they do not make ports a security boundary.

## Observable schedules and recovery

The original-speech indicator reports the observed audio remaining until the next recognition chunk boundary (15 seconds by default). Its clock advances with confirmed audio progress, not browser wall time, so accelerated replay and input stalls cannot create a false countdown. Recognition in progress, queued chunks, failed work, saved views, and confirmed completion have separate states; this indicator does not predict when recognized text will appear.

Each translation/analysis snapshot includes a schedule derived from the same eligibility conditions as its coordinator: the wait interval and remaining seconds, busy/blocked/idle/completed status, and bounded retry information. Circular UI indicators decrease only for a confirmed wait; they show no completion ETA while a request or another stage is running. Stale/disconnected observations show unknown. The indicators continue to describe current processing while the content view is held.

In continuous cloud mode, transport failures, HTTP 408/5xx, and explicitly identified temporary rate limits can trigger at most three additional automatic attempts, started within five minutes of the first temporary failure. Each attempt uses the frozen failed input and passes the existing scope, budget, and lock checks. Valid server retry delays are minimum waits; authentication, quota, unknown 429 subtypes, budget and validation failures require attention. Waiting does not occupy the cloud worker. Automatic attempts can be paused; manual retry starts a fresh bounded cycle without bypassing a remaining server minimum. The provider itself makes one attempt per call and retains every failed/unknown cost reservation.

These changes do not implement recovery after process exit or a reconnect catch-up burst. [Audio experiments](audio-experiments.md) provide an isolated saved-file entry point for comparing pipeline settings; microphone and live comprehension evaluation remain separate.

## Observe a recorded run at its original pace

`lecture_demo.py` reconstructs a saved run using recorded publication events for ASR, continuous translation and analysis. Its default cursor is zero and its browser player advances at 1×. Optional audio comes from one explicitly selected local PCM WAV; authenticated loopback range requests allow normal media seeking. No capture, recognition, cloud client, authorization ledger or inference process is started.

Audio playback drives the cursor while sound is playing; the final processing tail continues at real-time pace after audio ends. Pausing, seeking and restarting update that clock and reset the reading history when needed. An older in-flight state response cannot overwrite a later seek. Output appears at publication time, including generation delay; utterance timestamps alone do not make text available early. The recording's final publication, which may follow the audio end, determines replay duration. See [saved-result playback](audio-experiments.md#保存結果を冒頭から実時間で観察する) for usage and measurement limits.
