# Audrey subscription comparison: Luna versus Sol

**Result:** Luna returned faster, but it was not a reliable drop-in replacement
with the unchanged prompt. One of six translation responses failed the existing
group/coverage validator. Several accepted results also omitted content or
misstated its source support. This is one saved-source comparison, not a general
accuracy or latency ranking.

The user authorized `gpt-6-luna` on 2026-10-05. The experiment used the existing
v0.9.0-derived worktree, code at `526a927`, the same audited Codex CLI
`0.159.0-alpha.12.1`, ChatGPT authentication, and low reasoning effort. Only the
requested model changed from the [Sol trial](audrey-subscription.md). All nine
application requests, source selections and output schemas match that trial.
The CLI does not independently attest the served model identity.

## Source and boundaries

The frozen transcript SHA256 remains
`78cf9ca665c668e24038bdffa145f3c21ed2e611d6205f2b07c1f587c19edc81`.
There are 62 saved English ASR rows from 325.567 seconds of audio: 47 eligible
translation targets and 15 uncertain rows excluded as targets.

Source: Audrey Tang, Code for Japan,
[Audrey Tang Remarks in Plurality Seoul, 2023 12 02](https://www.youtube.com/watch?v=4_tge6XJhGA),
published 2023-12-26. Saved provenance records CC BY without a license version.
The input is an uncorrected machine transcription. No speaker/publisher
endorsement is implied. New translations, analyses, requests and raw responses
stay in ignored storage and are not added to the approved public sample.

This trial processes saved source-end-time prefixes. It does not capture audio,
rerun ASR, wait on a live source clock, or measure speech-to-display latency.
Translation checkpoints are 60/120/180/240/300/325.567 seconds; analysis uses
120/240/325.567-second checkpoints with the same 180-second windows as Sol.

## Failure and subsequent diagnostics

The normal replay was invoked with the previous command plus
`--model gpt-6-luna`, a maximum of nine calls and the verified source hash.
It stopped at request 2, as designed: request 1 completed, request 2 returned
an invalid translation, and seven requests remained unstarted. Its original
report remains **failed**, with six accepted source rows and 41 pending rows.

Request 2 was required to return two groups containing seven and two source
IDs. Instead it returned groups of three and four IDs and omitted the last two
targets. The entire response was rejected; none of its nine requested targets
was marked translated. It was not retried or silently repaired.

To finish evaluating the selected model, the remaining seven frozen requests
were then issued once each as a separate diagnostic batch. The batch recorded
per-request status, time, usage and validation, preserving the original failure.
It accepted all seven responses. Thus all nine planned inputs were tested
exactly once across the two executions, but this was **not a completed replay**.
Across the combined observations, structurally accepted translations cover
38 of 47 eligible rows in ten blocks; one nine-row request remains rejected.
Three analysis responses passed structural validation. No retry or API-key
fallback occurred.

## Timing and usage

The [content-free aggregate](audrey-luna-subscription.json) includes every
invocation, including the rejected response and its token usage.

| Comparison | Sol median | Luna median | Observed reduction |
| --- | ---: | ---: | ---: |
| All six translation responses, including one Luna rejection | 13.818 s | 8.420 s | 39.1% |
| Five translations accepted by both models, same subset | 11.943 s | 8.525 s | 28.6% |
| Three analyses, all structurally accepted | 36.564 s | 19.693 s | 46.1% |

Luna translation attempts ranged from 6.787 to 11.254 seconds; analysis ranged
from 17.976 to 22.559 seconds. Every corresponding Luna invocation took less
time than Sol, but time to a rejected output is not time to a usable translation.
Shorter or omitted content can also reduce generation time. The second row
therefore reports the matching accepted subset separately; structural acceptance
still does not establish equal semantic quality.

The nine per-call times sum to 113.938 seconds. This excludes the manual gap
between the stopped replay and the diagnostic batch, and must not be presented
as successful whole-lecture completion time. Per-call timing includes startup,
authentication, remote generation, validation and local persistence. The small
diagnostic coordinator differs from the original replay loop; transport and
validators are unchanged. These are single, noncontemporaneous model trials,
not randomized repeated measurements. This task ran no CPU regression tests
concurrently with Luna generation; other desktop work was not isolated.

Luna reported 100,422 input tokens and 4,199 output tokens across all nine
invocations, including the rejection; the returned reasoning-output field was
zero. No application API-key calls were made. Subscription allowance percentage,
fee allocation, API charge reconciliation, development use and energy were not
measured. These token counts are not an API-dollar estimate.

## Meaning and source-reference review

Both the main agent and an independent reviewer compared all ten accepted
translation blocks, the two rejected candidate blocks and three analyses with
the saved source. The original pre-generation evaluation checklist was reused.
Findings refer to request sequence numbers, including interleaved analyses.

- Request 2, rejected: besides its structural failure, the response removed
  opaque recognition text and smoothed an incomplete clause. The omission of
  two target IDs also omitted their corresponding content.
- Request 4, accepted: an explicit source concept in `c000010-l0001` was omitted.
- Request 5, accepted: content from `c000014-l0000` moved into the next block,
  which cited different source IDs. Content from `c000015-l0000` then disappeared.
  Exact ID coverage did not detect the meaning-to-ID mismatch.
- Request 6, accepted: analysis confidently supplied a negation absent from
  its selected input and strengthened a causal connection across incomplete
  clauses. The uncertain row containing a negative was excluded from this
  analysis input. Whether the reconstructed claim matches the spoken intent
  is not established by this saved-text evaluation.
- Request 9, accepted: a general definition was labeled as lecture-derived
  despite the current source window not providing that definition. The same
  Sol window had explicitly left the definition unresolved.
- Recurring terminology varied across requests. This was also a Sol finding;
  the smaller model did not resolve it.

Numbers in the checked passage, the four collaborator references, the
individual/group-deliberation contrast, the possibility of building trust and
the four closing exhortations were preserved in the relevant reviewed passages.
These strengths do not cancel the omissions, source-reference errors or rejected
work. The evaluation is model-assisted against saved ASR, not a human/audio
ground-truth score.

The practical conclusion is to retain Luna as a speed candidate, not enable it
as an equivalent live replacement yet. Any later prompt/schema change must be
evaluated separately for both structural compliance and semantic fidelity.
The current trial did not alter prompts, validators, production model selection
or the live application to hide its failures.

**Adoption decision (2026-10-05):** the unchanged Luna/low configuration is not
recommended for replacing the current lecture path. The user agreed that the
result was not compelling. Keep live selection unchanged; a future candidate
must pass the known coverage and meaning-to-source failures before its speed
gain supports adoption. This is specific to the tested configuration, not a
general rejection of Luna or subscription access.

## Validation of the record

No application code changed. Source digest and all nine request hashes were
checked against Sol; saved timing/status/usage were reconciled with the numeric
aggregate. Private review files remain ignored. Markdown links and whitespace
were checked. The earlier CPU/Node regression results describe unchanged code;
no fresh regression run or new live-runtime validation is claimed here.
