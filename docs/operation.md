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

## Check actual progress

After pressing the recording start button, confirm incoming audio, increasing saved duration, and then new source utterances. A request-success notification alone is not evidence of continued capture or completed storage.

The first recognition can take longer while the model initializes. The default 15-second chunk must also finish before its recognition result is available. A quiet input with valid PCM is different from input that has stopped arriving.

Translations are queued on a nominal 60-second schedule and understanding support on a nominal 120-second schedule. One cloud request runs at a time. These intervals are not end-to-end latency promises. Pending translation counts and failure states are more informative than waiting for a particular wall-clock second.

「文の続き待ち」 means the untranslated tail has no usable row-end boundary yet; it does not consume another 60-second interval. The planner prefers sentence-like row endings after at least two seconds of processed audio beyond them. New recognition normally arrives in 15-second chunks, so this is not a two-second display promise. If no boundary appears after 30 seconds of further processed audio from the oldest pending row's end, or a size limit/source gap prevents joining, a fragment can be translated with its reason saved. An input or recognition stall does not expire that audio-based wait. Stop waits for remaining recognition before translating the final eligible fragments. These heuristics do not guarantee correct sentence boundaries or translation quality.

## Read without losing your place

New source text appears in the left column. Translation and understanding support remain on the right. Follow a source reference to inspect its recognized speech and time. Holding a view or choosing an earlier result does not stop capture or processing.

The highlighted line is the latest line visible at the selected point. Gray recognition is uncertain and may be excluded from translation. Untranslated eligible lines, excluded uncertain lines, and Japanese source lines are different categories.

## Finish before closing

Use the dashboard stop action and wait for capture saving and the remaining recognition, translation, and final analysis. Check their completion states before closing the application. Retain the session data when a stage fails or its completion cannot be confirmed.

Use the dedicated translation retry for failed pending work after the underlying issue is resolved. Do not infer successful completion from a disappeared terminal or a closed browser. Interrupting the process may leave pending work; 0.9 does not automatically resume it after a restart.

## Preserve a working session

Change code, model configuration, storage roots, or launch configuration between sessions. A candidate instance should use separate recording and result destinations. If two instances share a single authorization or spending allowance, they must use the same authoritative scope and cost ledgers and inference lock.

A saved browser URL can stop authenticating after a restart. Use the current launcher-provided URL. Port-specific cookie names avoid overwriting another local instance's cookie, but cookie delivery itself is not isolated by port.

## Retire or restore a working directory

First confirm that capture and processing are inactive. Preserve ignored session files as well as Git history, verify the private copy against a manifest, and retain shared dependencies referenced by symbolic links. Record the completion, failure, and pending state of each processing stage. A successful backup does not make a failed session successful.

Restoring the Git checkout alone does not restore ignored recordings or results. Verify the private snapshot, path mapping, shared ledger and model dependencies, and current authorization before enabling any processing. Do not overwrite existing evidence or automatically resend pending work. Version 0.9 has no automatic restart recovery. See the [engineering rules](../wiki/engineering-decisions.md#restore-application-evidence-separately-from-git-history).

## Handle saved files

Session directories can contain audio, recognized speech, translations, analysis, prompts, raw responses, measurements, model metadata, and runtime source records. Treat the whole session as private working data. To share an experiment, create a separate content-free aggregate as described in [data handling](data-handling.md).

Do not delete a session merely because a cloud stage failed. Capture integrity, recognition completion, generation completion, and semantic quality are separate properties worth investigating independently.
