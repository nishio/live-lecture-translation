# Live understanding and post-lecture review

This page records 2026-10-05 field feedback, a read-only Astra design review, and the subsequent authorized ASR experiments. The design review itself ran no experiments. The [architecture](../docs/architecture.md) describes the current implementation, and [migration follow-ups](migration-follow-ups.md) preserve the earlier feedback.

The [user-audio experiment entry point](../docs/audio-experiments.md) provides a read-only WAV check and isolated saved-audio runs. The later [chunk-duration and revisable-buffer experiment](../docs/experiments/asr-chunk-duration.md) performs fresh local recognition and separates measured processing time from estimated publication delay. The existing live chunking, overlap, model choices, and review capabilities below remain unchanged. Bounded retries and visible scheduling are described in the current architecture.

## Two objectives, two evaluation paths

The user distinguished maximizing understanding at the venue from maximizing understanding during later review. Both belong to the product purpose, but they impose different constraints.

| Dimension | While listening | Later review |
| --- | --- | --- |
| Main objective | Follow the speaker with little delay or diverted attention | Recover meaning, attribution, and the lecture's overall structure |
| Available evidence | Only audio and source text already available at that point | The completed recording and explicitly identified supporting material |
| Useful units to evaluate | Stable utterances or clauses, short translations, current claim with its reason | Topic sections, claims and evidence across sections, questions and answers |
| Candidate quality work | Visible uncertainty and stable output; defer costly repairs | Selective re-recognition, speaker attribution, revised translation, section and whole-lecture synthesis |
| Success measures | Speech-to-useful-Japanese delay, comprehension while listening, reference actions, backlog | Answering review questions, retaining qualifications, tracing claims to speakers and sources, review effort |

Keep the original live display history separate from later revisions. A review result may use later context, but must not appear in a replay of what was available to the listener earlier. A recent-window analysis is not a whole-lecture review.

## Separate processing units before optimizing

Current source inspection establishes this baseline:

- Capture saves continuous audio and default 15-second chunks. The [local recognizer](../audio-array/lecture_live.py) processes non-overlapping chunks with `condition_on_previous_text=False` and no initial prompt. Current source IDs depend on chunk and line positions.
- [Translation](../audio-array/lecture_translation.py) accepts up to three groups per request, each bounded by eight source lines, 45 seconds, and 2,000 bytes, with 6,000 target bytes total. Context can include up to 45 seconds of already recognized text on either side. This does not mean waiting for 45 seconds of future speech.
- Analysis normally prefers the recent 180 seconds and retained prior evidence, up to 100 lines, then reduces input to fit a 22,000-byte request allowance. Uncertain lines are excluded from normal analysis as well as translation targets.
- In continuous mode, nominal 60-second translation and 120-second analysis schedules share one cloud request slot. Scheduling intervals, selected context, semantic units, and publication latency are different quantities.

Audio overlap re-recognizes some of the same sound and needs timestamp-aware reconciliation. Repeated text context helps interpret a translation target but cannot recover unheard audio. Neither should create duplicate published source lines or translated coverage. Preserve actual repeated speech: string equality alone is not sufficient evidence that two recognized spans are duplicates. Live overlap must use past audio or explicitly incur a bounded look-ahead delay; an offline trial must not hide that delay.

Use Astra first to review these contracts, design experiments, and examine text-level differences. The official [GPT-6 Astra model description](https://developers.openai.com/api/docs/models/gpt-6-astra), checked on 2026-10-05, lists no audio input support. A transcription experiment therefore needs an audio recognizer; selecting Astra as a design reviewer is not an ASR replacement or a decision to use it for every live generation.

## A small comparison plan

Choose publishable or authorized audio containing complete statements, boundary-crossing sentences, names, numbers, negation, and speaker changes. Establish a listening-based reference; model agreement is not ground truth. Change one factor at a time, retain the baseline, and state authorization and cost limits before actual recognition or API runs.

| Step | Comparison | Keep fixed and measure |
| --- | --- | --- |
| 1. Locate delay | Current pipeline with stage timestamps | Separate chunk wait, recognition, queue/slot wait, generation, and display; measure comprehension as well as p50/p95 latency |
| 2. Test ASR boundaries | Non-overlapping 15-second baseline versus one bounded overlap candidate; test chunk length separately afterward | Same recognizer and audio; boundary omissions/duplicates, name/number/negation errors, elapsed processing and resource use |
| 3. Test translation units | Current groups versus clause/sentence-aware groups with a maximum wait | Same recognized text and availability times; meaning preservation, unfinished statements, reading effort, latency and cost |
| 4. Test scheduling | Current fair alternation versus an oldest-pending-age policy | First use saved events and a fake provider; measure backlog and analysis starvation before selecting a real-generation trial |
| 5. Test review structure | Recent-window summaries versus section-level claims/evidence followed by whole-lecture synthesis | Same reviewed source revision; source-backed coverage, qualifications, attribution, and answers to review questions |

Overlap size, chunk length, and maximum wait are experiment parameters, not established optimal values. Do not change chunking, grouping, schedules, and models together and attribute the result to one of them. Saved-ASR trials answer scheduling or text-processing questions, not recognition-quality questions. Use the [granularity rubric](migration-follow-ups.md#field-feedback-explanation-granularity-and-speaker-claims) for explanation review.

## Provisional live text and translation-time recognition

See [provisional live recognition and translation evidence](provisional-asr.md) for the user clarification, measured comparison, and the next implementation candidate.

## Selective re-recognition and speaker attribution

Selective post-lecture ASR is a promising hypothesis: retry uncertain spans, boundary problems, names/numbers, and user-marked passages with surrounding audio. Include a random sample of apparently confident spans to detect errors missed by the current uncertainty rule. Fix selection rules before looking at results; report difficult-span improvements, regressions, and the random sample separately. A second recognizer or a longer window is not automatically more accurate.

Before implementing repair, define revision provenance. Keep saved audio ranges and hashes, original recognition, new recognition, parent/revision IDs, and the evidence for acceptance. Changed segmentation may split or merge utterances, so do not overwrite old source IDs with different text. Map revised spans to the original audio and preserve old-to-new relationships. Record which revision each translation or analysis used; invalidate or recompute affected interpretations explicitly, retaining their history. Disagreements without sufficient evidence remain unresolved.

Evaluate speaker diarization first for Q&A, interviews, panels, and other material where confusing participants changes the meaning. Use anonymous labels such as Speaker A/B and retain unknown, overlap, and boundary uncertainty. Diarization assigns who spoke when; it is distinct from separating overlapping audio into clean tracks or identifying a person's real name. Keep the speaking voice separate from a person being quoted. Name mapping requires separate evidence, and diarization labels should not turn uncertain attribution into fact.

The official [file-transcription guide](https://developers.openai.com/api/docs/guides/speech-to-text), checked on 2026-10-05, documents speaker-labeled output via `gpt-4o-transcribe-diarize`. This is one possible evaluation backend, not an adopted dependency or a quality ranking. Compare attribution errors against leaving speakers unknown, including turn boundaries and overlapping speech. A single-speaker lecture does not automatically benefit from an extra labeling pass. Local processing and explicitly authorized cloud audio processing remain separate choices; existing text-only cloud permission does not authorize audio upload.

## Subscription provider decision

**2026-10-05 decision: the tested Codex subscription route is not the preferred
replacement for the current lecture pipeline. It has already been evaluated;
do not restart the same trial merely because subscription access is available.**
Keep the current live provider selection unchanged. This decision concerns the
automated translation/analysis path, not asking an agent questions about saved
transcripts or a blanket prohibition on Luna or subscription use.

An isolated worktree based on v0.9.0 processed the saved Audrey lecture text
through ChatGPT-authenticated Codex. The
[Sol/API comparison](https://github.com/nishio/live-lecture-translation/blob/6f4bfda1860ff3464cc79a44beb089a0b9b77f82/docs/experiments/audrey-subscription.md#retrospective-comparison-with-the-audrey-api-run)
and [Luna evaluation](https://github.com/nishio/live-lecture-translation/blob/6f4bfda1860ff3464cc79a44beb089a0b9b77f82/docs/experiments/audrey-luna-subscription.md)
are pinned to the published experiment commit `6f4bfda`. Its code remains on
`experiment/codex-subscription`; it has not been integrated into main or the
running application.

- With `gpt-6.1-sol` / low, five matching application translation requests had
  API/Codex medians of 10.150/15.693 seconds (Codex 1.55× as long). The Codex path
  also reported roughly 9,550 additional input tokens per matching request.
  No tool-use loop occurred, and the contribution of startup/authentication,
  extra instructions, provider and network variation was not separately timed.
- `gpt-6-luna` / low returned faster, but one of six translations failed group
  and target validation. Other outputs passed structural checks while omitting
  meaning, moving text under incorrect source IDs, or adding unsupported
  interpretation. The normal replay stopped; the remaining seven requests were
  separate diagnostics, not a successfully resumed lecture run.
- For the five translations accepted by both subscription models, Sol/Luna
  medians were 11.943/8.525 seconds (28.6% shorter); analysis medians were
  36.564/19.693 seconds (46.1% shorter). This is a different subset from the
  Sol/API comparison. Speed did not establish equivalent semantic quality.

These are single saved-ASR trials using v0.9.0-derived prompts and selection,
not audio-ground-truth accuracy scores, current-main quality measurements,
long-session capacity tests or speech-to-display latency. Subscription allowance
percentage and fee allocation were not measured. The earlier user-reported
approximately USD 10 API spend was not reconciled and is not a measured saving.
Keep subscription usage separate from the application's USD ledger.

Before reconsidering this route, read those results and state a concrete changed
factor, the new hypothesis, and the latency/quality criteria that could overturn
this decision. Examples are a different integration that reduces startup overhead
or a revised source/prompt/schema contract addressing the known failures. Do not
repeat unchanged conditions as if the idea were untested, and do not automatically
switch providers or replay preserved failed work. Exact source-ID coverage alone
does not establish faithful meaning-to-source attribution. New inference retains
the repository's existing authorization requirements.
