# Collected migration follow-ups

The standalone repository is the home for future Mac lecture application development. A review of the related development conversations identified the additional work below. It does not change the v0.9.0 tag or the preserved results of the source application.

## Existing code awaiting adoption

[Migration patches](../docs/migration/README.md) preserve two existing developments without applying them to the baseline:

- **Capture operation notifications.** The older project contains a correction and regression fixtures that distinguish starting from recording, and failed saving from a successful stop. The source used for the historical continuous-translation run and the extracted baseline both retain the older success branch. Acknowledging a requested operation must not imply confirmed recording or storage. The patch is a follow-up candidate, not an installed fix.
- **ASR comparison runner.** A separate experiment runner compares local, file-based, and streaming recognition using a fixed input manifest, real-time delivery, content hashes, separate partial/final output, and cost reservations. It and its CPU tests were outside the initial runtime extraction. Preserve it as research code, with its historical assumptions and dependency/budget integration gaps visible. It is not connected to the live recognizer.

Private recordings, input manifests, authorization files, ledgers, requests, responses, transcripts, and semantic reviews are not part of these patches. A historical permission to run one experiment is not permission to run it again or against another source. The comparison runner's audio transmission requires separate authorization from the application's text-only cloud workflow.

## Lessons for a future streaming adapter

These are integration requirements distilled from the comparison experiment, not claims that a streaming adapter exists in this release.

1. Keep provisional partial text separate from final source evidence. Match events through their item identifiers; final arrival order need not be utterance order. Preserve the relationship between deltas, commits, and final items.
2. Do not label chunk timestamps as word timestamps. Measure connection preparation, first partial arrival, chunk completion, and final publication separately.
3. When a provider does not supply a confidence signal, represent it as unknown or not provided. Do not route missing scores through recognizer-specific defaults that silently classify speech as certain.
4. Distinguish requested settings from values confirmed by the server. An omitted echo leaves the effective value unverified.
5. Pace packets against the source clock and avoid future-sample look-ahead when resampling. Keep failed requests and usage-unconfirmed reservations in the accounting state.

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

## Additional evidence preserved

[Development handoff measurements](../docs/experiments/development-handoff.md) records content-free aggregates from an older pre-event audit and a partial live cost observation. Those measurements are historical evidence of their own source versions. They do not add a clean-install result or a full-event success claim to v0.9.

The next development cycle should use the user's event feedback, choose which collected changes to adopt, and validate them on an isolated candidate. The source worktree has since been archived after its stopped state and private evidence were preserved. Some generation work remains unfinished; retirement did not retry it. The standalone operational environment was not set up as part of that retirement. Runtime preparation and recovery of pending work remain separate follow-ups; see [retirement and recovery rules](engineering-decisions.md#retire-a-checkout-without-erasing-unfinished-work).
