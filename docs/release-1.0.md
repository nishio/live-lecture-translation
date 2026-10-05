# Version 1.0 release candidate

**Status: 1.0.0-rc.1, 2026-10-05. Installation and synthetic immediate-stop checks passed. Same-session pause/resume and candidate-specific microphone/cloud acceptance remain pending.** These notes describe the release candidate and its evidence, not a completed stable 1.0 release. The `v0.9.0` baseline remains unchanged, and preparing this candidate does not switch an existing recording session to new code.

## Scope

An Apple Silicon Mac application for following English lectures with local transcription, Japanese translation, key points and concept explanations. Input can come from a selected microphone or an existing local recording. The dashboard is in Japanese.

Recording and MLX Whisper recognition run locally. Cloud translation and understanding support require an OpenAI API key, paid API access, and explicit text-transmission and spending settings. Audio stays on the Mac; selected recognized text is sent to the cloud. See [setup and use](../README.md), [cloud configuration](cloud-configuration.md), and [data handling](data-handling.md).

The project uses [AGPL-3.0-only](../LICENSE), except where otherwise noted. The approved [Audrey Tang text sample](../samples/audrey-plurality-seoul-2023/README.md) retains its separate source attribution, CC BY notice, and transformation and uncertainty notes.

## Changes since 0.9

- Stop recording and new recognition, translation, analysis and retries from the dashboard without draining the backlog. Save received audio and already started operations separately, retaining unfinished work. Recorded-file experiments still process their remaining work on natural completion.
- Read source speech above accumulated Japanese translation in a viewport-sized workspace, with an adjustable split and independent scrolling. Concept explanations persist, and previous understanding results remain accessible without losing the reading position.
- See independent recording, recognition, translation and analysis states, with countdowns for known waits and explicit busy, failed or unknown states. Recoverable cloud failures receive bounded automatic retries with preserved inputs and cost reservations.
- Keep meaningful uncertain recognition in translation and analysis, carrying its uncertainty reasons. Narrow filler and duplicate rules preserve exclusion records and original source evidence. Older saved sessions retain their original policy.
- Validate the new uncertainty metadata when auditing saved analysis inputs, while retaining the old saved-request format. Missing or altered reasons fail verification. This audit does not independently verify the continuous translation history or its full cost total; acceptance also checks each stage and translation coverage.
- Wait for sentence-like source-row boundaries before translation where possible, retaining unfinished tails and recording why a fragment was released. Final processing includes remaining eligible fragments.
- Import local MP3, M4A, WAV or video audio, convert and trim inside the application, and replay the exact prepared audio with saved results at their original publication times. Playback supports pause, seek and restart without another inference run.
- Provide an attributed public text sample, recorded-audio walkthrough, and experiments with explicit conditions, measured costs and limitations.

The [changelog](../CHANGELOG.md) contains the complete change list.

## Evidence and candidate validation

| Check or experiment | Observed result | What it establishes |
| --- | --- | --- |
| Immediate-stop candidate regression | 485 Python tests in 106.950 seconds: 483 passed, two opt-in native capture tests skipped; both Node UI suites passed | CPU-only synthetic checks on Python 3.12.8, including preparation/lock-wait cancellation, offline/retry stop, in-flight completion, accounting, saved-view compatibility and natural file completion. No real capture, model inference or paid API request. |
| Earlier frozen candidate environment | 462 Python tests in 91.937 seconds: 460 passed, two opt-in native capture tests skipped; both Node UI suites passed | CPU-only and synthetic integration checks before the immediate-stop change, on Python 3.14.7. No real capture, model inference or paid API request. |
| Fresh installation on the same Mac | `setup.command`, `pip check`, MLX Whisper import and `start.command --check` passed | Empty virtual environment and model cache, new dependency installation and a new download of the pinned model. No existing virtual environment or model files were copied. Microphone permissions and cloud access were not exercised. |
| Prior development-tree review | 459 Python tests: 457 passed, two opt-in native capture tests skipped; both Node UI suites passed | Synthetic behavior and regression checks on that reviewed tree. Candidate-specific validation must be recorded separately after preparation. |
| Historical Mac microphone field use | 5 hours 16 minutes 36.864 seconds saved in 1,267 chunks; ASR completed to the saved audio end; capture ended on input loss; translation/analysis failed with HTTP 429 and 131 eligible lines remained untranslated | Actual multi-hour microphone use on the earlier version, with unresolved failures. This is neither an absent long-session test nor a successful end-to-end run of the candidate. See the [preserved stage outcomes](experiments/development-handoff.md#long-microphone-field-use). |
| Historical six-hour synthetic storage trial | 21,600.123 seconds of audio stored in 47.130 seconds; all 1,441 chunks passed hash/frame/continuity checks | Accelerated storage integrity, without a microphone, real-time endurance or cloud processing. See [conditions](experiments/README.md#accelerated-capture-storage). |
| Public lecture replay with fresh local ASR and cloud processing | 325.567 seconds of saved audio completed in 350.989 seconds; 22 ASR chunks, 47 eligible source lines translated once, four understanding results; usage-confirmed API cost USD 0.1113085 | A completed saved-audio pipeline run on the earlier uncertainty policy. Its 15 uncertain lines were excluded. It did not test microphone capture or the final candidate. |
| Matched uncertainty-policy translation comparison | 12 completed requests; 47 old-policy versus 59 candidate-policy target IDs; two negation cases lost or inverted under the old policy were retained under the new policy; usage-confirmed API cost USD 0.097502 | A targeted meaning-preservation observation on one lecture's saved ASR, with one output per condition. It does not establish general accuracy or listening comprehension. |

See the [development validation record](../wiki/log.md), [replay measurements](../samples/audrey-plurality-seoul-2023/measurements.json), and [uncertainty comparison](experiments/uncertain-translation-audrey.md). The public sample preserves its original generated output; it is not a sample of the candidate's new selection policy.

The experiments' confirmed API costs are separate from the earlier USD 0.178775 unresolved reservation, which remains unconfirmed. Development-assistant usage and electricity were not measured. The [duration-based cost table](../README.md#cost-by-lecture-duration) is a historical extrapolation, not a current quote or a completion guarantee.

## Acceptance before a stable 1.0 announcement

Historical multi-hour use is already recorded above. Candidate acceptance verifies the changed behavior rather than claiming there has never been live testing. The user-defined live stop ends new work at a useful reading boundary; it must preserve pending work instead of requiring final translation and analysis. Same-session pause/resume and real microphone/cloud acceptance remain pending. A successful stop request alone does not confirm capture saving or the outcome of a previously dispatched request.

| Status | Check | Evidence required |
| --- | --- | --- |
| Passed | Freeze the candidate | Commit or equivalent immutable source identity includes the source-policy module, tests, license and release metadata; verify the publication contains only permitted files and preserves the `v0.9.0` tag. |
| Passed | Candidate regression checks | 485 Python tests: 483 passed, two optional native tests skipped; both Node UI suites passed on the current immediate-stop code identity below. |
| Passed, same Mac | Clean-environment installation | Apple M1 Pro (MacBookPro18,1), macOS 15.1.1, Python 3.14.7; independent fresh checkout, virtual environment and caches. Setup, dependency check, model download, decoder, module imports, native-helper compilation and local startup preflight passed. This is not a second-Mac validation or a cloud-authentication test. |
| Passed, synthetic | Immediate live stop | Regression checks cover no new ASR/cloud work after stop during preparation, lock waits, offline waiting and retry delay; saved output, pending targets and existing request accounting survive. Already active work may finish; natural file completion still drains. Real microphone acceptance remains separate. |
| Pending | Same-session breaks | Recording pause/resume with retained lecture context remains unimplemented. Current stop/start creates separate sessions. |
| Pending | Real microphone pipeline | With explicit recording and cloud authorization, confirm microphone permission, incoming audio and increasing saved duration, new ASR, Japanese translation and understanding output. Run long enough to exercise both cloud workloads; retain stage outcomes, publication timing, confirmed costs and unresolved reservations separately. |
| Pending | Stop and inspect the saved result | Stop through the dashboard after useful output appears. Verify capture saving, no new model work, and the separate outcome of any active operation. Open the saved result and verify retained audio/results, pending work and representative names, quantities and negation. Do not require a final analysis after explicit stop or relabel intentionally pending work as completed. |

Current immediate-stop candidate code identity: `e968031475bdc27fc3363c5052d10f69c4366bf715da075a3eaf13fd35dc06b0`, using the same sorted-record method below across 60 source/test/setup files. This is the source checked by the 485-test run; it includes `processing_control.py` and its tests.

Earlier frozen candidate code identity, before immediate-stop changes: `665d30db9320d3124acccbe086564122cdaf26933eebbd5acdaf9de08a745b41`. This is SHA-256 of sorted records `relative_path + NUL + file_sha256 + LF` for the 58 tracked files under `audio-array/` and `scripts/`, plus `VERSION`, `setup.command`, `start.command`, `requirements.txt` and `requirements-test.txt`. At that earlier validation, the test checkout and candidate had identical bytes for every included file. The immediate-stop changes have a separate source identity and regression result above; installation dependencies are unchanged.

Direct dependencies installed were NumPy 2.5.3, MLX 0.32.2, MLX Whisper 0.4.3, Hugging Face Hub 2.0.0 and imageio-ffmpeg 0.6.0. The decoder reports FFmpeg 7.1. The freshly downloaded model used revision `a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb`; its 1,613,977,612-byte weights have SHA-256 `951ed3fc1203e6a62467abb2144a96ce7eafca8fa77e3704fdb8635ff3e7f8a6`. Setup did not load the model for recognition. Additional application API cost for these candidate checks was USD 0; development-assistant usage and electricity were not measured.

Perform acceptance in an isolated candidate instance between sessions, preserving existing applications, recordings, results, shared ledgers and inference locks. Keep private evidence under ignored `data/` or `results/`; publish only permitted aggregates. Failed or unfinished checks stay recorded as such. Add the final source identity and actual results here before promoting the candidate to stable 1.0.

## Known limits

- Recognition, translation and explanations can be wrong. Source-ID coverage confirms which recognized rows were processed; it does not prove that speech was recognized or interpreted correctly. Some repetitive noise remains even with the new policy.
- Recognition still uses append-only 15-second chunks by default. Translation and analysis have nominal 60- and 120-second intervals; chunk waiting, queueing and generation add delay. These are not immediate subtitles or speech-to-screen latency guarantees.
- Provisional recognition, automatic correction using later audio, and revision of already published translations are not included. Sentence-boundary waiting is a heuristic over complete source rows.
- Same-session recording pause/resume is not implemented. The dashboard stop action blocks new work, including delayed retries and connectivity recovery; an already started operation may finish and incur a charge. Browser closure does not stop the backend. See [stop and lecture transitions](operation.md#finish-before-closing).
- Automatic resumption after application exit is not implemented. Dashboard stop, process interruption, and confirmed completion have different meanings. Retries within a running session do not guarantee rapid catch-up after an outage.
- Historical multi-hour Mac microphone use exposed input loss and unfinished cloud work. Reliable completion of long sessions remains unresolved; the candidate's short acceptance run cannot establish it. Mac battery endurance, lid-closed operation, accuracy in difficult acoustics, recovery performance and improved comprehension while listening remain unmeasured.
- Windows, Linux and Intel Macs are outside the validated platform scope. Local LLM translation and analysis are available through Ollama, but the evaluated default `qwen3:4b` configuration did not provide adequate semantic quality for the reference workflow.

The final release description must retain the limits that remain after acceptance. See [operation](operation.md) and [architecture](architecture.md) for processing and recovery details.
