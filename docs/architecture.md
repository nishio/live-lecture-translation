# Architecture

## Capture is the first responsibility

`lecture_capture_native.swift` uses macOS audio APIs to select an input and convert it to 16 kHz, mono, 16-bit PCM. `lecture_capture.py` receives the stream, writes continuous PCM and split WAV files, and records frame and file metadata. The default recognition chunk is 15 seconds.

Capture must continue independently of recognition and text generation. A successful process start or HTTP response is insufficient evidence that useful audio is being stored. The application tracks input reception and confirmed storage progress separately. Zero-valued PCM can be valid silence; a lack of incoming PCM is a different condition.

The native helper is tied to its source through a build identity. Capture verification should check frame count, sequence, timestamps, PCM boundaries, and file hashes. These properties establish storage integrity; they do not establish intelligibility or transcription accuracy.

## Recognition publishes source evidence

`lecture_live.py` maintains a local MLX Whisper worker and reads a manifest identifying the locally available model. The reference workflow uses `large-v3-turbo`. Source utterances retain IDs, recognized text, timing, language, and uncertainty information.

Recognition publishes independently of the cloud worker. A request already generating a translation or analysis must not hold back newly recognized text. Recognition chunks introduce their own waiting time before any inference begins.

The separate [chunk-duration and provisional-buffer experiment](experiments/asr-chunk-duration.md) compares fresh recognition at different lengths and rolling snapshots. It selects a 3-second refresh with a trailing 15-second revisable buffer as a future implementation candidate. The current live source path is still append-only with a default 15-second chunk; provisional display, stable-prefix reconciliation, and translation-time re-recognition are not integrated.

Raw recognized speech remains the evidence layer. A future correction system should preserve the original recognition, the proposed correction, and the evidence for accepting it. A plausible name is not an adequate substitute for missing evidence.

## Translation tracks unfinished work

`lecture_translation.py` plans blocks from the oldest eligible untranslated utterances. Eligibility excludes empty, Japanese, uncertain, and already covered source lines. Blocks do not cross excluded or discontinuous source boundaries.

The extraction's planning limits are at most three groups per request, each at most eight lines, 45 seconds, and 2,000 bytes, with a 6,000-byte total target cap. Up to 45 seconds of already available source context on either side can accompany the selected targets. Future speech is not available to the request.

Returned source IDs must cover the requested targets in their original order. Translation is marked complete only after a successful result is persisted. A failed request leaves its targets pending and exposes a dedicated retry action. Coverage checks prevent silent queue loss; they do not validate the meaning of a translation.

## Analysis answers a different need

`lecture_analysis.py` builds a compact understanding view from recognized speech: the topic, connections between points, key points, and concepts. It uses source utterances and their provenance. Unrelated notes, a reference library, or knowledge of the final lecture must not enter the source-evidence stream.

Translation aims to preserve the eligible speech sequence. Analysis selects and compresses information. One should not serve as an implicit replacement for the other.

Continuous translation has a nominal 60-second interval; analysis has a nominal 120-second interval. They share one cloud request slot and are scheduled so that one workload does not monopolize it. The actual publication time also includes chunk waiting, recognition, earlier queued work, network latency, generation, validation, and persistence.

## State and shutdown are explicit

The coordinator keeps capture, recognition, translation, and analysis state visible. A stale or unsuccessful state query means that current state is unknown. It does not mean recording has stopped.

The dashboard stop action stops new capture and follows the remaining recognition, translation, and final analysis. Application interruption is a different path: remaining work may be left pending. Automatic resumption after process exit is not implemented in 0.9.

Storage locks and state snapshots are separated so that status can expose the last confirmed progress when an I/O operation is slow. The reader's input watchdog shares its thread with I/O; it cannot by itself guarantee recovery from a filesystem operation that never returns. A responsive stop request can therefore report that completion is unconfirmed.

## The reader controls the view

The left column prioritizes continuous source speech. The right column holds accumulated translation and understanding support. A new source line does not require rebuilding the right column, closing an expanded source reference, or moving the reader's position.

Live progress and the currently viewed time are separate concepts. Historical views use publication timestamps so that a later translation cannot appear in an earlier snapshot. Source navigation can hold the view, and returning to live is explicit.

Uncertainty and recency are also separate: gray text indicates uncertain recognition; a pale highlight indicates the most recent source line visible at the chosen time.

## External requests are bounded

Cloud text processing is optional and requires configuration of both permitted text scope and spending limits. Scope accounting and cost accounting have different units and purposes. Failed requests with uncertain usage keep their reservation until it can be reconciled.

When multiple processes are intentionally run under one allowance, their authoritative accounting and inference lock must be shared. Copying a working directory must not silently duplicate that allowance. An application configuration and the already loaded runtime must agree on supported models and limits.

The local HTTP interface uses authenticated access. Browser cookies are scoped by host, not port. Different cookie names prevent one local instance from overwriting another's authentication; they do not make ports a security boundary.
