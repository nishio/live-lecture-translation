# Operation

## Prepare a session

Use Python 3.12 or later. Run `./setup.command` for the environment and pinned local recognition model. For continuous cloud translation, prepare [cloud configuration](cloud-configuration.md), then run:

```sh
./start.command --check --cloud --authorization config/authorization.json
./start.command --cloud --authorization config/authorization.json
```

The check and server startup do not start recording. The default dashboard port is 8776. `./start.command` alone uses a local LLM through Ollama for translations and understanding support. Install and run Ollama and the selected model separately; the default is `qwen3:4b`. Evaluation of that model/settings found incorrect translations and relationships between ideas, so the cloud configuration is recommended. Local mode generates recent-window translations within analysis; it does not use the separate continuous cloud translation queue.

Select the microphone and recognition language. Check that the input is the one you intend to use, the local model and native helper are ready, and adequate storage remains. macOS microphone permission is required. Keep the Mac awake and connected to power during a session.

Cloud translation additionally needs configured text-transmission authorization, a supported model, an API key, and an available spending allowance. The allowed cloud models are `gpt-6-luna` and `gpt-6.1-sol`. The recognition model download and cloud text requests are separate network activities. Normal microphone audio is saved locally; cloud translation and analysis send recognized text plus selected transcript context.

Recording readiness and cloud readiness are distinct. A network check should not hide the state of local capture preparation. If cloud work cannot proceed, inspect its status separately from audio reception and saving.

## Compare connections before a lecture

The dashboard's current network check establishes TCP/TLS reachability to `api.openai.com:443`. It does not test API authentication, remaining provider quota, sustained reliability, or generation speed. A green check is therefore insufficient to choose between venue Wi-Fi and tethering.

Before capture, compare both connections on the same Mac at the seat where you will listen. Keep VPN settings the same and record which connection is active. Use the same bounded sampling plan on each, for example 12 checks spaced 10 seconds apart; that small sample is a screening check, not a reliability certification. Repeat if conditions change as the venue fills.

This optional probe sends no API key, lecture text, or audio, and requests no model generation. It bypasses environment-configured HTTP proxies to match the application's direct connection path; VPN routing still applies:

```sh
/usr/bin/curl -q --noproxy '*' --silent --show-error --output /dev/null \
  --connect-timeout 3 --max-time 10 \
  --write-out 'http=%{http_code} dns=%{time_namelookup} tcp=%{time_connect} tls=%{time_appconnect} first_byte=%{time_starttransfer} total=%{time_total}\n' \
  https://api.openai.com/v1/models
```

The timestamps are seconds from the start of the probe, not independent stage durations. Record successful responses, failures/timeouts, and the median and slowest response time for each connection. This request deliberately has no credentials: an authentication rejection can demonstrate HTTP reachability but cannot establish that the application's key or model works. `http=000` means no HTTP response was received; inspect the connection error. Keep TLS verification enabled and do not follow a portal redirect as if it were API success. These probes do not measure long response stability or generation latency. Speed-test download bandwidth alone also does not answer those questions.

Prefer the connection with fewer failures and more consistent latency in this comparison; retain the observation period and uncertainty instead of inferring a universal Wi-Fi-versus-tethering winner. Any actual generation comparison needs separate authorization, a small synthetic input, and a stated budget. No connection comparison has been performed as part of this documentation.

## Distinguish API failure from network failure

Connection errors may involve networking, DNS, proxies, or TLS; timeouts can also involve server load or processing time. HTTP 429 can represent temporary rate limiting or quota/spending exhaustion, so changing networks alone may not resolve it. Check the provider error subtype when available. Authentication/configuration failures need correction; temporary server failures may recover after waiting. See OpenAI's [error codes](https://developers.openai.com/api/docs/guides/error-codes). The current application does not expose all these diagnostic details.

In continuous mode, a failed connectivity precheck before sending a request is rechecked on a nominal 30-second schedule. While the session is running, this connectivity wait has no automatic deadline and is not covered by the request retry limit below. Pressing 「録音・処理を停止」 ends the wait and prevents later requests from that session. It sends no generation request while offline but may send pending text when connectivity returns and scope/budget still allow it. The post-v0.9 continuous cloud coordinator also retries recoverable actual request failures, up to three additional attempts started within five minutes of the first temporary failure. It respects server retry delays and retains failed inputs and cost reservations. Authentication, quota, unexplained 429, budget and invalid-response failures require attention. Use 「自動再試行を保留」 to pause future automatic attempts, or the stage-specific manual retry after resolving a blocking cause. Manual retry does not bypass a remaining server minimum wait; do not clear uncertain cost reservations.

Saved local recognition lets that running session resume translation using text only. It does not guarantee rapid recovery: during capture, the usual translation interval and shared cloud request slot still apply. Check the pending-line count and translation progress as processing resumes. Do not restart the application as a recovery shortcut; automatic restart recovery is not implemented.

## Check actual progress

After pressing the recording start button, confirm incoming audio, increasing saved duration, and then new source utterances. A request-success notification alone is not evidence of continued capture or completed storage.

The first recognition can take longer while the model initializes. The latest text at the end of 「原文」 uses the latest available audio, refreshing nominally every three seconds with up to 15 seconds of context. Canonical source recognition still waits for each default 15-second chunk. A quiet input with valid PCM is different from input that has stopped arriving.

Translations are queued on a nominal 60-second schedule and understanding support on a nominal 120-second schedule. One cloud request runs at a time. These intervals are not end-to-end latency promises. Pending translation counts and failure states are more informative than waiting for a particular wall-clock second.

「文の続き待ち」 means the untranslated tail has no usable row-end boundary yet; it does not consume another 60-second interval. The planner prefers sentence-like row endings after at least two seconds of processed audio beyond them. New recognition normally arrives in 15-second chunks, so this is not a two-second display promise. If no boundary appears after 30 seconds of further processed audio from the oldest pending row's end, or a size limit/source gap prevents joining, a fragment can be translated with its reason saved. An input or recognition stall does not expire that audio-based wait. A recorded-file experiment reaching its natural end waits for remaining recognition before translating final eligible fragments. The dashboard stop button instead leaves unfinished work pending. These heuristics do not guarantee correct sentence boundaries or translation quality.

## Read without losing your place

New source text appears above its contextual Japanese translation in the left column. Understanding support appears on the right. Holding a view or choosing an earlier result does not stop capture or processing.

The highlighted line is the latest line visible at the selected point. Gray recognition is uncertain; meaningful content is still sent to translation with its uncertainty reasons. Only narrow filler/duplicate rules exclude uncertain text, preserving an audit record. Pending counts include meaningful uncertain targets. Japanese source lines need no Japanese translation. Old saved sessions without the new policy marker retain their historical exclusions and counts.

The latest original text is a revisable snapshot at the end of the same 「原文」 scroll area. Earlier rows entirely before its audio window remain above it; overlapping or boundary-crossing rows are kept whole under 「前の認識を見る」. No words are cut to invent a seamless join. When canonical recognition catches up, its accumulated rows replace the temporary view. The preview is not sent directly to translation or analysis: those stages use immutable source lines from default 15-second chunks. Repeated adjacent uncertain canonical lines with identical text apart from case and whitespace are shown once, while their saved source records remain intact.

The three-second refresh is a scheduling interval, not a latency guarantee. Canonical recognition has priority over preview work; the newest preview replaces overdue work when inference falls behind. CLI options `--provisional-refresh-seconds` and `--provisional-window-seconds` control preview cadence and context; a refresh of `0` disables it. Saved-result playback uses actual preview publication times, so old sessions without preview history remain unchanged.

## Finish before closing

Press 「録音・処理を停止」 when the displayed result has reached a useful stopping point. The dashboard requests capture stop and immediately prevents new recognition, translation, analysis and retry work. It does not drain the backlog or create a final analysis. Reconnection cannot start deferred work after this stop.

| Action | Capture | Model requests and pending work |
| --- | --- | --- |
| 「録音・処理を停止」 | Requests capture stop and saving | Blocks new work and retries; preserves unfinished work |
| Close the browser/tab or hold the reading view | Continues | Continues in the application process |
| 「自動再試行を保留」 | Continues | Pauses only the offered automatic retry path |
| `Ctrl-C` in the application terminal | Requests shutdown and capture stop | Blocks new work and retains unfinished work |

The stop request does not wait for the backlog. Audio already received is saved, and a local inference or cloud request already started may finish and save its result. Submitted API requests are not cancelled and can incur charges. The dashboard distinguishes the stop request, ongoing saving/active work, and confirmed stop; stopped does not mean all recognized speech was translated. Queued audio and pending source IDs remain recorded, as do failures and unresolved reservations. Retry controls cannot resume a stopped session.

New model requests check the stop condition again after preparation and shared-lock waits, before dispatch. A reservation cancelled before dispatch is recorded as unsent; sent requests and unknown usage retain normal accounting. Natural completion of a recorded-file experiment still drains remaining work, so an explicitly stopped live lecture is distinct from a completed file-processing run.

The CLI allows up to 130 seconds for shutdown; workers still alive afterward leave shutdown unconfirmed. Closing a terminal window is not proof that saving finished. After the application exits, restarting does not automatically process unfinished recognition or translation. Saved audio and results remain available.

## Breaks and a new lecture

Same-session recording pause/resume is not implemented. 「閲覧を固定」 affects the reading position, and 「自動再試行を保留」 affects an eligible retry; neither pauses a lecture recording. The schedule card is informational and does not stop capture at a break or start another lecture.

To avoid recording a break, press 「録音・処理を停止」 and start again when saving and any already active operation have ended. This creates a separate session, so the earlier lecture context is not automatically carried into the resumed portion. Stopping does not wait for queued translations or network recovery.

For a new lecture, press 「録音を開始」 after all previous capture/source/processing threads have ended and capture saving is confirmed. Each start creates a unique session ID and separate data/result directories, clearing displayed sources, translations and understanding context. Previous files remain saved. Old failed or pending work is not replayed automatically, and the shared daily cost and text-duration allowances are not reset. Start is rejected while the previous work is still active; no concurrent handoff is implemented.

## Preserve a working session

Change code, model configuration, storage roots, or launch configuration between sessions. A candidate instance should use separate recording and result destinations. If two instances share a single authorization or spending allowance, they must use the same authoritative scope and cost ledgers and inference lock.

A saved browser URL can stop authenticating after a restart. Use the current launcher-provided URL. Port-specific cookie names avoid overwriting another local instance's cookie, but cookie delivery itself is not isolated by port.

## Retire or restore a working directory

First confirm that capture and processing are inactive. Preserve ignored session files as well as Git history, verify the private copy against a manifest, and retain shared dependencies referenced by symbolic links. Record the completion, failure, and pending state of each processing stage. A successful backup does not make a failed session successful.

Restoring the Git checkout alone does not restore ignored recordings or results. Verify the private snapshot, path mapping, shared ledger and model dependencies, and current authorization before enabling any processing. Do not overwrite existing evidence or automatically resend pending work. The application has no automatic restart recovery. See the [engineering rules](../wiki/engineering-decisions.md#restore-application-evidence-separately-from-git-history).

## Handle saved files

Session directories can contain audio, recognized speech, translations, analysis, prompts, raw responses, measurements, model metadata, and runtime source records. Treat the whole session as private working data. To share an experiment, create a separate content-free aggregate as described in [data handling](data-handling.md).

Do not delete a session merely because a cloud stage failed. Capture integrity, recognition completion, generation completion, and semantic quality are separate properties worth investigating independently.

## Reading without losing your place

The current development dashboard shows 「文脈付きの日本語訳」 for translations added from pending source, and 「いま伝えていること」 for the current interpretation. Neither label promises correction of an already completed translation.

The left column places 「原文」 above 「文脈付きの日本語訳」. The right column shows 「いま伝えていること」 and 「言葉の補足」. The workspace fills the browser height and each pane scrolls independently. Drag the handle between original speech and translation to change their heights; the setting survives reloads when browser storage is available. With the handle focused, use ↑/↓ to adjust, Home/End for the limits, or double-click to return to an even split. The top 「処理状況」 retains each stage independently and opens when a new problem needs attention. Closing it does not clear the problem.

The reading panes omit per-block times, source-count controls, routine AI labels and diagnostic footnotes. The original-text pane keeps overlapping earlier recognition accessible through 「前の認識を見る」. Source IDs, concept origin, timing and coverage remain in saved data. Recognition uncertainty is still shown by muted original text; omitting a label does not verify the AI output.

「前の整理」 and 「次の整理」 move between interpretations; the previous arrow can load older saved results when the earliest received result is reached. 「最新へ戻る」 returns to current content. These actions do not start new inference.

Concept explanations remain in a scrollable history. New cards do not move the reading position; 「新しい説明へ」 moves explicitly. Changed explanations remain separate versions. Loading earlier saved interpretations also makes their concept history available. A missing or unreadable record is reported, not silently turned into a complete history.

With previews enabled, the small circle beside original speech follows the preview schedule: observed audio remaining until the next update boundary, normally three seconds. It shows 「認識中」 through model initialization and active recognition, without predicting completion. Disabled previews and older saved runs keep the original 15-second chunk schedule. Input stalls, waiting work and failures retain their corresponding states. The circle follows audio progress rather than browser wall time.

The indicators beside understanding and translation show the remaining wait before a start/retry decision. Click a circle or its short label to read the complete timing explanation and, when available, pause automatic retries. At zero, processing may still wait for recognition or the shared request slot. During generation, paused recovery, saved-result viewing or a lost connection, the label states the corresponding condition instead of inventing a countdown. While reading an earlier result, the indicators describe current processing. The normal continuous-mode translation/analysis intervals are 60/120 seconds; a displayed number such as 「5秒」 is the remaining wait, not a five-second processing interval.

For a no-inference input check and explicit isolated saved-audio runs, see [audio experiments](audio-experiments.md).

To observe a completed run from the beginning at 1× with its original audio, use [saved-result playback](audio-experiments.md#保存結果を冒頭から実時間で観察する). The player reuses saved output and publication timings, with pause, restart and seeking controls; it does not start another generation run.
