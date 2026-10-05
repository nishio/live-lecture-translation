# Collected migration follow-ups

The standalone repository is the home for future Mac lecture application development. A review of the related development conversations identified the additional work below. It does not change the v0.9.0 tag or the preserved results of the source application.

The later [1.0 release candidate](../docs/release-1.0.md) implements part of the field-feedback queue: readable source excerpts, direct history navigation, stable concept history with older loading, honest labels and scheduling countdowns, a refined explanation prompt, bounded continuous-cloud retries, and an isolated [audio experiment CLI](../docs/audio-experiments.md). Baseline observations below remain historical evidence; they are not a claim that all proposed improvements are still absent or now complete. Search, model/ASR optimization, post-lecture revision and diarization, and interview support remain follow-ups. The proposed Codex subscription replacement was subsequently evaluated and [deferred](live-and-review-pipeline.md#subscription-provider-decision); it is not an untested item to rerun unchanged.

## Existing code awaiting adoption

[Migration patches](../docs/migration/README.md) preserve two existing developments without applying them to the baseline:

- **Capture operation notifications.** The older project contains a correction and regression fixtures that distinguish starting from recording, and failed saving from a successful stop. The source used for the historical continuous-translation run and the extracted baseline both retain the older success branch. Acknowledging a requested operation must not imply confirmed recording or storage. The patch is a follow-up candidate, not an installed fix.
- **ASR comparison runner.** A separate experiment runner compares local, file-based, and streaming recognition using a fixed input manifest, real-time delivery, content hashes, separate partial/final output, and cost reservations. It and its CPU tests were outside the initial runtime extraction. Preserve it as research code, with its historical assumptions and dependency/budget integration gaps visible. It is not connected to the live recognizer.

Private recordings, input manifests, authorization files, ledgers, requests, responses, transcripts, and semantic reviews are not part of these patches. A historical permission to run one experiment is not permission to run it again or against another source. The comparison runner's audio transmission requires separate authorization from the application's text-only cloud workflow.

## Acceptance checks for the notification patch

The follow-up review confirmed that the source correction and synthetic regression cases are preserved in `docs/migration/notification-state.patch`, while the application source still contains the older branch. Collected code is not applied code, and neither establishes which source an existing process loaded. This file-back does not apply the patch.

When adopting it in a later version, retain these checks:

- A reconciled start request in `starting` without received audio must not show confirmed recording. `stalled` or stale reception must not be a green success either.
- A reconciled stop request in `failed` must retain a saving-failure warning; only `completed` confirms capture and storage completion. Remaining recognition and translation are separate stages.
- Validate the actual launcher and browser entry as well as unit tests. A tab retaining an old authenticated URL can remain unusable even when the server is healthy; obtain the current URL through the launcher and verify the input, model, and controls without starting capture.

## Lessons for a future streaming adapter

These are integration requirements distilled from the comparison experiment, not claims that a streaming adapter exists in this release.

1. Keep provisional partial text separate from final source evidence. Match events through their item identifiers; final arrival order need not be utterance order. Preserve the relationship between deltas, commits, and final items.
2. Do not label chunk timestamps as word timestamps. Measure connection preparation, first partial arrival, chunk completion, and final publication separately.
3. When a provider does not supply a confidence signal, represent it as unknown or not provided. Do not route missing scores through recognizer-specific defaults that silently classify speech as certain.
4. Distinguish requested settings from values confirmed by the server. An omitted echo leaves the effective value unverified.
5. Pace packets against the source clock and avoid future-sample look-ahead when resampling. Keep failed requests and usage-unconfirmed reservations in the accounting state.

## Acceptance checks for an ASR adapter

The preserved research runner is already collected as a patch. Adopting it requires an adapter and application tests beyond its existing CPU checks:

1. **Convert the evidence contract explicitly.** The current `catchup_page.build_lines` reads `raw_result.segments`; a provider response containing only whole-transcript text produces no source lines. Map finalized provider items to stable application source IDs, their commits, and the saved audio frame range or hash. Declare chunk-level bounds when finer timing is unavailable. Verify out-of-order final delivery without reordering the source or promoting unfinished partials into translation history.
2. **Test missing confidence and setting confirmation separately.** Whisper-specific missing-score defaults can leave a line without uncertainty reasons, while setting every unsupported score to `uncertain` excludes those lines from translation. Define a policy for confidence that was not provided, and test both its display and eligibility behavior. For the historical streaming configuration, an absent delay echo was recorded as unconfirmed; an explicit null or conflicting value remained an error, as did mismatched model, language, format, or turn detection. Keep those cases distinct when revisiting provider compatibility.
3. **Provide a path for early output and measure its actual benefit.** The existing recognition worker waits for a completed audio chunk and a returned transcript before publishing lines. Replacing that synchronous recognizer alone does not expose streaming partials. A future streaming receive/display path must preserve independent recording and promote only final items to durable source evidence. Translation scheduling and generation remain separate contributors to Japanese publication time; an earlier English partial is not a measured reduction in that total.

These are adoption criteria, not implemented features. Use publishable synthetic fixtures and retain the existing boundary between code collection, experiment authorization, and runtime cutover.

## Field feedback: proper-name recognition

An operator initially reported frequent proper-name misrecognitions during a live event and explicitly prioritized keeping the existing system running. That initial report supplied no affected name, correct spelling, source position, or error count. In subsequent v0.9 experience feedback on 2026-10-04, the user reported failures in proper-name translation and supplied the example spellings **Nameraka, Glen Weyl, Audrey Tang, and Keio**. These are user-supplied investigation targets, not spellings verified against recorded speech or accepted corrections to a transcript. No source positions, reference audio, or error counts were supplied. Treat the reports as qualitative evidence requiring investigation, not a measured error rate or proof that ASR rather than translation introduced the error. They did not trigger a recognizer change, retranscription, or automatic correction.

A future evaluation should distinguish candidate vocabulary supplied before recognition from correction after recognition. Establish examples and authoritative spellings first. Keep the original ASR, proposed correction, and evidence for any accepted correction separately; do not silently map an unclear word to a plausible familiar name. Use publishable fixtures for public regression tests. No correction feature or quality improvement is claimed here.

## Field feedback: audio experiments and cost visibility

The same 2026-10-04 v0.9 feedback requested a way for users to supply their own audio and experiment on their Mac, together with an estimate of cost by audio duration. A reusable experiment path should make it possible to compare settings on the same input without recording a new lecture each time. Existing research and replay hooks are not by themselves evidence of a supported end-user file workflow.

The current [live coordinator](../audio-array/lecture_live.py) has a developer CLI with `--replay`, `--duration`, `--pace`, and `--exit-after-replay`; replay requires 16 kHz mono PCM16 WAV. The regular launcher and dashboard do not expose a file-selection workflow. Build on this distinction rather than treating replay as either absent or ready for general use.

Proposed acceptance criteria for a later version:

- Let the user select a local audio file, see its duration and supported format requirements, choose local recognition alone or opt into cloud text processing, and inspect the estimate before starting. Preserve the input and store private input and output in ignored working directories, with separate destinations from any live session.
- Distinguish fresh local recognition from reuse of saved recognition, and real-time replay from accelerated processing. Record which stages ran, model and settings, source duration, elapsed time, failures, and completion separately. A file experiment does not validate microphone capture or live speech-to-display latency.
- Show estimated translation and analysis cost before a cloud run and usage-confirmed cost afterward, keeping unresolved reservations visible. State the rate's source, model, observation window, and exclusions; duration alone does not determine token usage. Respect text-transmission scope and shared budget accounting. Cloud audio recognition remains a separately authorized experiment.

The English and Japanese READMEs now give a historical duration-based illustration from the [partial live observation](../docs/experiments/development-handoff.md#partial-live-cost-observation): USD 0.630875 per observed 30 minutes, extrapolated at USD 1.26175 per hour. The table is arithmetic using existing evidence, not a new benchmark, current pricing, a file-specific quote, or an implemented in-app estimator. Development-assistant usage and electricity remain unmeasured.

## Field feedback: understanding with limited attention

The user reported that opening the current point's references shows a list of IDs and requires clicking them individually to read the evidence. During a lecture, attention is limited; the user also needs easy access to the point that was displayed immediately before. The presence of references or a general time-navigation control does not establish that these tasks are easy while listening.

In the current [dashboard code](../audio-array/lecture-dashboard/app.js), individual source buttons normally display timestamps and fall back to IDs when the source is unavailable. A collapsed 「これまでの分析」 history already offers earlier headlines and times, retaining at most 60 recent snapshots in the UI and coordinator state. This source inspection does not establish which display the user saw; it identifies improvements to the existing reference and history interactions rather than a need to invent history storage from scratch.

Proposed acceptance criteria for a later version:

- Opening a point's references should show readable source excerpts together, in source order, with timestamps and recognition uncertainty. Keep source IDs for provenance and optional deeper navigation, without making individual ID clicks the main reading path.
- Make the previously displayed point reachable directly, with enough recent history to understand the transition and an explicit return to the current view. New results should preserve the reader's chosen position and expanded evidence. Check ease of use while listening, using synthetic or publishable material.
- Reconsider what the summary unit represents. **論点** describes the subject or question under discussion; **主張** describes what the speaker is asserting. If the intended aid is “what is the speaker saying?”, evaluate **話者の主張** as a candidate. Define the extraction and evidence rules before relabeling: questions, examples, tentative statements, and the model's own background explanations must not become assertions attributed to the speaker. The user raised this as an open design question; no label or prompt change is adopted here.

These criteria apply the project's purpose of augmenting understanding in real time. They record v0.9 field feedback separately from the extracted baseline; no claim is made that the interaction changes are implemented or have reduced cognitive load in a measured trial.

## Field feedback: network failures and recovery

In further v0.9 feedback on 2026-10-04, the user reported intermittent OpenAI API failures, uncertainty about choosing venue Wi-Fi or personal tethering, and the burden of manual retries. Network quality is a hypothesis, not an established cause. The separate [retirement observation](engineering-decisions.md#retire-a-checkout-without-erasing-unfinished-work) includes HTTP 429, but does not identify its provider error subtype or explain every failure reported here.

Current behavior, from source inspection rather than a new network trial:

- [Readiness](../audio-array/lecture_readiness.py) checks TCP/TLS reachability to the API host; it does not measure sustained connection quality or establish authentication, quota, or generation success. The [HTTP adapter](../audio-array/event_insights_cloud.py) reports HTTP status but reduces other transport failures to a shared error message. It does not expose the provider error subtype or retry headers needed for fuller diagnosis.
- In continuous mode, a failure of the connectivity precheck before an API request leaves pending text intact and is rechecked on a nominal 30-second schedule. An actual API request failure, including a transport timeout, instead requires an explicit retry for the affected translation or analysis stage. These are different paths in the [coordinator](../audio-array/lecture_live.py); restoring connectivity alone does not unblock every failure.
- Local capture and recognition proceed independently of cloud processing. Within the same running session, pending translations can use saved recognized text without uploading audio or repeating recognition. During capture, translation retains its nominal 60-second interval and shares one request slot with analysis. There is no special reconnect catch-up mode. After capture and recognition finish, pending work can drain without waiting for the normal interval, subject to failures, authorization, and budget.

Proposed acceptance criteria for a later version:

1. **Make diagnosis useful for choosing a connection.** Provide a bounded comparison of venue Wi-Fi and tethering on the same Mac and at the actual seat. Show connection success/failure counts, DNS/TCP/TLS timing, HTTP response timing, and variability, with the observation period and sample count. Keep network reachability, provider errors, and generation latency distinct. Preserve sanitized error codes, failure phase, retry hints, and private request correlation separately from lecture content. The [manual connection-check procedure](../docs/operation.md#compare-connections-before-a-lecture) is available now; an in-app comparison is not implemented.
2. **Recover without repeated manual attention.** For retryable transport/server failures and temporary rate limits, use bounded automatic retries with backoff and jitter, respecting a valid server retry delay. Keep authentication, invalid requests, exhausted quota, and local authorization/budget blocks actionable instead of retrying indefinitely. Show the next attempt, attempt count, pending work, and a cancel/pause action while capture and ASR continue. OpenAI's [error guide](https://developers.openai.com/api/docs/guides/error-codes) distinguishes these error classes; its [rate-limit guidance](https://developers.openai.com/api/docs/guides/rate-limits) describes bounded retries and server delay handling. These references were checked on 2026-10-04; no retry policy is changed here.
3. **Preserve accounting and measure catch-up.** Retain the original failed attempt and any unresolved usage reservation; a lost response does not prove that generation or billing never occurred. Admit each retry against the shared budget and preserve source-ID coverage so repeated responses do not duplicate published translation. Measure outage duration, pending eligible source coverage, oldest pending age, time to the first recovered translation, and time to drain the backlog while new speech arrives. Faster text upload alone is not a catch-up result: model generation, request limits, batch size, and analysis competition can dominate.

Catch-up should process the oldest eligible translation targets without silently discarding failed work or uncertain source lines; uncertain lines keep their explicit exclusion state. Analysis currently selects a recent source window and retained prior evidence, so an updated analysis is not proof that every missed interval was summarized. Same-process reconnection and recovery after application exit also remain separate: automatic restart recovery is not implemented in v0.9. No real-network comparison, automatic-retry implementation, or recovery-throughput measurement was performed for this feedback.

## Field feedback: explanation granularity and speaker claims

The user questioned whether the current explanation granularity is appropriate, suggested rereading the output, and again asked whether 「論点」 should be 「主張」. The repeated question reinforces the existing [understanding-support design issue](#field-feedback-understanding-with-limited-attention); it does not settle the label or establish that the current output is adequate.

The [analysis prompt and schema](../audio-array/lecture_analysis.py) ask for a one-sentence current point, up to four summary items, four flow items, two concepts, and three questions, with short prose. These are generation instructions and structural limits, not evidence of readable semantic units. The normal 120-second schedule and recent-source selection also do not define where a speaker's claim begins and ends.

Proposed review: read two or three consecutive saved displays alongside the source actually selected for each request. Evaluate only information available then; later speech must not supply missing evidence. Use a local, authorized review for private material and synthetic or publishable examples for public fixtures. Do not regenerate content or send it to a model merely to conduct the review.

| Review dimension | What to check |
| --- | --- |
| Meaning and attribution | Preserve the speaker, negation, conditions, and uncertainty. Do not turn a question, example, or model-supplied background into a claim by the speaker. |
| Granularity | Does one item express one useful unit of understanding? Flag vague abstractions, an example substituted for the central claim, and unrelated claims packed into one item. |
| Roles and repetition | Do the headline, summary, flow, and concept explanations each help, or repeat the same content and add reading effort? |
| Continuity | Can the reader see what changed from the previous display and how a reason, example, objection, or qualification relates to the earlier claim? |
| Reading effort | Record time to restate the main idea, rereads, reference-opening actions, and missing explanation the reader needed. |

Mark each dimension as acceptable, needs revision, or unsupported, with a short reason. Meaning reversal or false attribution fails regardless of brevity. Keep recognition errors separate from errors introduced by the explanation. Rereading can assess content and reveal excess detail, but reduced burden while listening still needs a separate listening task.

For this product's purpose, 「話者の主張」 is a useful candidate when the speaker makes an assertion; a broader label such as 「いま伝えていること」 could also accommodate questions and examples. Compare the extraction contracts on the same available evidence rather than only replacing a heading. Preserve cases where no complete claim is yet supported. The present feedback adds the review method; no private output was reread, no quality score was measured, and no prompt or label was changed.

## Field feedback: live optimization and post-lecture review

On 2026-10-05, the user proposed using Astra to reconsider recognition units, overlap, translation and summary units, and pipeline scheduling. They distinguished understanding while listening from understanding during later review, suggested selective re-recognition of uncertain passages and post-lecture speaker diarization, and asked whether subscription access could replace some API usage after reporting approximately USD 10 in spending.

The [live and review pipeline proposal](live-and-review-pipeline.md) records these as separate objectives, a small staged comparison plan informed by a read-only Astra review, revision provenance, sampling beyond flagged uncertainty, speaker-attribution checks, and the initial Codex subscription-provider proposal. The later [measured decision](live-and-review-pipeline.md#subscription-provider-decision) supersedes that candidate status and defers adoption of the tested route. The reported cost was not reconciled against a ledger, and no current default, provider, permission, or runtime was changed. The existing recent-window analysis is not presented as a completed whole-lecture review feature.

## Field feedback: useful explanations and interview support

Further 2026-10-05 feedback questioned 「訳し直し」 and what the focus area should show, reported unhelpful concept non-explanations and disappearing cards, proposed AI-assisted keyword research, asked what Sol receives and whether it is necessary, and noted personal demand for interview support.

The [understanding-support proposal](understanding-support.md) preserves these observations and acceptance criteria. The [current input/display contract](../docs/architecture.md#what-the-cloud-model-receives) clarifies that continuous translation appends pending targets rather than revising completed translations, concept background already permits unverified model knowledge, analysis updates replace the concepts view, and normal cloud calls send selected text without search tools. These facts do not establish which output the user saw or a measured need for Sol. No runtime change, research request, private-output inspection, or interview prototype was performed.

## Additional evidence preserved

[Development handoff measurements](../docs/experiments/development-handoff.md) records content-free aggregates from an older pre-event audit and a partial live cost observation. Those measurements are historical evidence of their own source versions. They do not add a clean-install result or a full-event success claim to v0.9.

The next development cycle should use the user's event feedback, choose which collected changes to adopt, and validate them on an isolated candidate. The source worktree has since been archived after its stopped state and private evidence were preserved. Some generation work remains unfinished; retirement did not retry it. The standalone operational environment was not set up as part of that retirement. Runtime preparation and recovery of pending work remain separate follow-ups; see [retirement and recovery rules](engineering-decisions.md#retire-a-checkout-without-erasing-unfinished-work).
