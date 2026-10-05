# Operation

## Prepare a session

Use Python 3.12 or later. Run `./setup.command` for the environment and pinned local recognition model. For continuous cloud translation, prepare [cloud configuration](cloud-configuration.md), then run:

```sh
./start.command --check --cloud --authorization config/authorization.json
./start.command --cloud --authorization config/authorization.json
```

The check and server startup do not start recording. The default dashboard port is 8776. `./start.command` alone selects local processing; the retained `qwen3:4b` analysis path has not established acceptable semantic quality and does not provide the continuous cloud translation workflow.

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

In continuous mode, a failed connectivity precheck before sending a request is rechecked on a nominal 30-second schedule. The post-v0.9 continuous cloud coordinator also retries recoverable actual request failures, up to three additional attempts started within five minutes of the first temporary failure. It respects server retry delays and retains failed inputs and cost reservations. Authentication, quota, unexplained 429, budget and invalid-response failures require attention. Use 「自動再試行を保留」 to pause future automatic attempts, or the stage-specific manual retry after resolving a blocking cause. Manual retry does not bypass a remaining server minimum wait; do not clear uncertain cost reservations.

Saved local recognition lets that running session resume translation using text only. It does not guarantee rapid recovery: during capture, the usual translation interval and shared cloud request slot still apply. Check the pending-line count and translation progress as processing resumes. Do not restart the application as a recovery shortcut; automatic restart recovery is not implemented in v0.9.

## Check actual progress

After pressing the recording start button, confirm incoming audio, increasing saved duration, and then new source utterances. A request-success notification alone is not evidence of continued capture or completed storage.

The first recognition can take longer while the model initializes. The default 15-second chunk must also finish before its recognition result is available. A quiet input with valid PCM is different from input that has stopped arriving.

Translations are queued on a nominal 60-second schedule and understanding support on a nominal 120-second schedule. One cloud request runs at a time. These intervals are not end-to-end latency promises. Pending translation counts and failure states are more informative than waiting for a particular wall-clock second.

「文の続き待ち」 means the untranslated tail has no usable row-end boundary yet; it does not consume another 60-second interval. The planner prefers sentence-like row endings after at least two seconds of processed audio beyond them. New recognition normally arrives in 15-second chunks, so this is not a two-second display promise. If no boundary appears after 30 seconds of further processed audio from the oldest pending row's end, or a size limit/source gap prevents joining, a fragment can be translated with its reason saved. An input or recognition stall does not expire that audio-based wait. Stop waits for remaining recognition before translating the final eligible fragments. These heuristics do not guarantee correct sentence boundaries or translation quality.

## Read without losing your place

New source text appears above its contextual Japanese translation in the left column. Understanding support appears on the right. Holding a view or choosing an earlier result does not stop capture or processing.

The highlighted line is the latest line visible at the selected point. Gray recognition is uncertain and may be excluded from translation. Untranslated eligible lines, excluded uncertain lines, and Japanese source lines are different categories.

Recognition processes a 15-second audio chunk at a time by default, so several source lines can appear together. Repeated adjacent uncertain lines with identical text apart from case and whitespace are shown once. Their original source records remain intact; ordinary source lines and different text are not merged.

## Finish before closing

Use the dashboard stop action and wait for capture saving and the remaining recognition, translation, and final analysis. Check their completion states before closing the application. Retain the session data when a stage fails or its completion cannot be confirmed.

If automatic attempts have been paused or exhausted, or the error is not retryable, use the dedicated translation retry after the underlying issue is resolved. Do not infer successful completion from a disappeared terminal or a closed browser. Interrupting the process may leave pending work; 0.9 does not automatically resume it after a restart.

## Preserve a working session

Change code, model configuration, storage roots, or launch configuration between sessions. A candidate instance should use separate recording and result destinations. If two instances share a single authorization or spending allowance, they must use the same authoritative scope and cost ledgers and inference lock.

A saved browser URL can stop authenticating after a restart. Use the current launcher-provided URL. Port-specific cookie names avoid overwriting another local instance's cookie, but cookie delivery itself is not isolated by port.

## Retire or restore a working directory

First confirm that capture and processing are inactive. Preserve ignored session files as well as Git history, verify the private copy against a manifest, and retain shared dependencies referenced by symbolic links. Record the completion, failure, and pending state of each processing stage. A successful backup does not make a failed session successful.

Restoring the Git checkout alone does not restore ignored recordings or results. Verify the private snapshot, path mapping, shared ledger and model dependencies, and current authorization before enabling any processing. Do not overwrite existing evidence or automatically resend pending work. Version 0.9 has no automatic restart recovery. See the [engineering rules](../wiki/engineering-decisions.md#restore-application-evidence-separately-from-git-history).

## Handle saved files

Session directories can contain audio, recognized speech, translations, analysis, prompts, raw responses, measurements, model metadata, and runtime source records. Treat the whole session as private working data. To share an experiment, create a separate content-free aggregate as described in [data handling](data-handling.md).

Do not delete a session merely because a cloud stage failed. Capture integrity, recognition completion, generation completion, and semantic quality are separate properties worth investigating independently.

## Reading without losing your place

The current development dashboard shows 「文脈付きの日本語訳」 for translations added from pending source, and 「いま伝えていること」 for the current interpretation. Neither label promises correction of an already completed translation.

The left column places 「原文」 above 「文脈付きの日本語訳」. The right column shows 「いま伝えていること」 and 「言葉の補足」. The workspace fills the browser height and each pane scrolls independently. Drag the handle between original speech and translation to change their heights; the setting survives reloads when browser storage is available. With the handle focused, use ↑/↓ to adjust, Home/End for the limits, or double-click to return to an even split. The top 「処理状況」 retains each stage independently and opens when a new problem needs attention. Closing it does not clear the problem.

The reading panes show lecture content without per-block times, source-count controls, routine AI labels, diagnostic footnotes or a separate history list. Source IDs, concept origin, timing and coverage remain in saved data. Recognition uncertainty is still shown by muted original text; omitting a label does not verify the AI output.

「前の整理」 and 「次の整理」 move between interpretations; the previous arrow can load older saved results when the earliest received result is reached. 「最新へ戻る」 returns to current content. These actions do not start new inference.

Concept explanations remain in a scrollable history. New cards do not move the reading position; 「新しい説明へ」 moves explicitly. Changed explanations remain separate versions. Loading earlier saved interpretations also makes their concept history available. A missing or unreadable record is reported, not silently turned into a complete history.

The small circle beside original speech shows the observed audio remaining until the next recognition chunk boundary (normally 15 seconds). During recognition it shows 「認識中」 without predicting completion. Input stalls, waiting chunks and failed work retain their corresponding states. This circle follows audio progress rather than browser wall time.

The indicators beside understanding and translation show the remaining wait before a start/retry decision. Click a circle or its short label to read the complete timing explanation and, when available, pause automatic retries. At zero, processing may still wait for recognition or the shared request slot. During generation, paused recovery, saved-result viewing or a lost connection, the label states the corresponding condition instead of inventing a countdown. While reading an earlier result, the indicators describe current processing. The normal continuous-mode translation/analysis intervals are 60/120 seconds; a displayed number such as 「5秒」 is the remaining wait, not a five-second processing interval.

For a no-inference input check and explicit isolated saved-audio runs, see [audio experiments](audio-experiments.md).

To observe a completed run from the beginning at 1× with its original audio, use [saved-result playback](audio-experiments.md#保存結果を冒頭から実時間で観察する). The player reuses saved output and publication timings, with pause, restart and seeking controls; it does not start another generation run.
