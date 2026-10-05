# Audrey lecture through the Codex subscription path

**Result:** all nine planned calls completed. Six translation calls covered all
47 eligible source rows in 12 blocks, without duplicates or pending rows; three
analysis results were saved. Translation median was 13.818 seconds and analysis
median was 36.564 seconds. The sequential processing loop took 197.406 seconds.
Quality review identified qualification loss in one analysis headline, recurring
term inconsistency, and a misleading fragment risk caused by uncertainty filtering.

This is the real-source follow-up to the [synthetic subscription probe](codex-subscription.md).
The user explicitly requested the existing Audrey data on 2026-10-05. Work stays
on `experiment/codex-subscription`, derived from v0.9.0; the live application
and concurrent development checkout are not switched or edited.

## Input and attribution

The input is the reviewed sample `samples/audrey-plurality-seoul-2023/transcript.json`
from the development checkout, frozen in this worktree's ignored data directory.
Its SHA256 is `78cf9ca665c668e24038bdffa145f3c21ed2e611d6205f2b07c1f587c19edc81`.
It contains 62 saved English ASR rows: 47 eligible for translation and 15 marked
uncertain. The last source row ends at 324.54 seconds; the complete source audio
is 325.567 seconds. No audio recognition is rerun and no audio is uploaded.

Source: Audrey Tang, **Audrey Tang Remarks in Plurality Seoul, 2023 12 02**,
published by Code for Japan on 2023-12-26:
[original video](https://www.youtube.com/watch?v=4_tge6XJhGA).
The saved provenance records a **CC BY** notice without a license version.
The sample was machine-transcribed and remains uncorrected; no speaker or
publisher endorsement is implied. Attribution and transformation notes are
retained with the private input snapshot. New generated output is not added
to the separately reviewed publication sample.

## What the run measures

`lecture_subscription_replay.py` reads one hash-bound transcript and plans
chronological source prefixes. Translation checkpoints are 60, 120, 180, 240,
300 and 325.567 seconds. At each checkpoint, the baseline FIFO planner drains
eligible untranslated lines in its bounded groups. Analysis checkpoints are
120, 240 and 325.567 seconds, using certain nonempty source from the last
180 seconds, at most 100 rows. Previous generated prose, historical API
translations, reference summaries and unrelated repository context are not
sent to the model.

This is an accelerated, sequential **saved-text** experiment. A source row is
available here when its recorded end time is at or before the checkpoint;
this is not an ASR publication-time replay or a live wall-clock scheduler.
The analysis windows also differ from the existing sample's four API-generated
snapshots. They are recent-window understanding, not a whole-lecture synthesis.
The experiment can establish text processing/coverage and measure these calls,
but cannot establish microphone, ASR, speech-to-display or live backlog latency.

The expected plan contains six translation requests covering 47 eligible IDs
in 12 groups, plus three analysis requests. Each result must pass the original
source-ID and response validators and be saved before completion or coverage
advances. A failed attempt stops the run; pending work remains explicit.
The configured call cap is checked before any inference. There is no API-key
fallback, automatic retry or automatic replay of a failed run.

## Reproduce with an authorized local transcript

Prepare the reviewed transcript under ignored data. `plan` reads it without
inference and reports its digest and workload counts; `run` requires that exact
digest and explicit subscription-use confirmation.

```sh
.venv/bin/python audio-array/lecture_subscription_replay.py plan \
  data/subscription-replay/audrey-78cf9ca665c6/transcript.json --duration 325.567
.venv/bin/python audio-array/lecture_subscription_replay.py run \
  data/subscription-replay/audrey-78cf9ca665c6/transcript.json --duration 325.567 \
  --max-calls 9 \
  --expect-sha256 78cf9ca665c668e24038bdffa145f3c21ed2e611d6205f2b07c1f587c19edc81 \
  --confirm-subscription-use
```

The existing audited Codex transport requests `gpt-6.1-sol` with low reasoning
effort and ChatGPT authentication. The CLI JSONL does not independently attest
the served model identity. Private inputs, requests, responses and attempt
records remain under ignored data/results. Only content-free aggregates and
methodology belong in Git.

## Evaluation boundaries

Before generation, source review identified checks for numbers and ASR name
errors, incomplete clauses, the collaborator list, individual versus group
deliberation, the uncertain negation boundary, a possibility versus proven
achievement, and the closing contrasts. Model-assisted comparison with saved
ASR is not a human listening reference. In particular, an uncertain row contains
a negation before a positive-looking eligible fragment; structural completion
does not recover missing evidence or establish the speaker's intended polarity.

The existing sample's successful API run reports USD 0.1113085, separately from
an earlier unresolved USD 0.178775 reservation. Those are historical accounting
facts, not the charge for this new run or a matched cost/quality control.
Subscription token counts are kept separate from API dollars. Quota percentage,
subscription fee allocation, development-assistant usage and electricity are
not measured; missing usage is never treated as zero.

## Measurements from the completed run

One complete experiment ran on 2026-10-05 with the audited CLI
`0.159.0-alpha.12.1`. There were no failed calls or application retries in this run.
Every call returned usage and passed source/format validation before its result
was saved. The [content-free JSON aggregate](audrey-subscription.json) records
each call's cutoff, duration, input size and reported usage.

| Workload | Completed calls | Minimum | Median | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Translation | 6 | 9.758 s | 13.818 s | 20.150 s |
| Analysis | 3 | 34.221 s | 36.564 s | 37.689 s |

The 197.406-second processing loop excludes the initial planning and outer
preflight. Per-call times include the adapter's own startup/auth check,
generation, validation and persistence. Source-clock waits were not performed.
Some CPU regression tests ran concurrently during early calls; this was not a
dedicated performance benchmark. The 325.567-second audio duration and these
processing times must not be interpreted as a measured live latency or
long-session throughput guarantee.

Codex reported 106,654 input tokens, 5,263 output tokens and 389 reasoning output
tokens as separate returned fields; reasoning tokens are not added again to
the output count. These describe nine subscription invocations, not an API cost
estimate. No application API-key calls were made. API charge reconciliation,
subscription allowance percentage and currency allocation remain unmeasured.

## Retrospective comparison with the Audrey API run

The historical successful Audrey run retained per-call `generation_seconds`
in its private result files. These were recovered on 2026-10-05; the earlier
answer that had only checked the public run aggregate missed this evidence.
No new API or subscription request was needed. All ten API results were real
generations, with no application response-cache hits, and matched their saved
publication-measurement entries. The original 62 source rows match the frozen
sample on IDs, text, times and uncertainty.

| Workload | Historical API calls | API median (min–max) | Subscription calls | Subscription median (min–max) | Ratio of medians |
| --- | ---: | ---: | ---: | ---: | ---: |
| Translation | 6 | 9.019 s (5.134–14.341) | 6 | 13.818 s (9.758–20.150) | 1.53× |
| Analysis | 4 | 17.435 s (6.989–24.737) | 3 | 36.564 s (34.221–37.689) | 2.10× |

Translation provides the stronger comparison: all six ordered target-ID lists
and group boundaries match. The last five application message lists and output
schemas are exactly equal. Only the first call has different source context
(seven API rows versus 17 subscription rows). Both paths requested
`gpt-6.1-sol` with low reasoning effort. Codex still adds its own base instructions
and wrapper, so equality of application requests is not equality of complete
model input or every transport parameter.

| Translation call | API seconds | Subscription seconds | Additional seconds |
| --- | ---: | ---: | ---: |
| 1 (different context) | 5.919 | 11.943 | 6.024 |
| 2 | 13.798 | 19.782 | 5.984 |
| 3 | 7.887 | 11.580 | 3.693 |
| 4 | 14.341 | 20.150 | 5.809 |
| 5 | 10.150 | 15.693 | 5.542 |
| 6 | 5.134 | 9.758 | 4.624 |

Every subscription translation took longer. For the five matching application
requests, the added time was 3.693–5.984 seconds, with a median paired difference
of 5.542 seconds. Output lengths were also close: 2,090 API versus 2,091
subscription output tokens across all six translations. This is evidence that
this subscription implementation was slower in these runs, not an isolated
measurement of the cause or a general provider ranking.

Restricting the calculation to those five matching application requests gives
medians of 10.150 seconds for API and 15.693 seconds for subscription (1.55×).
Their reported input-token counts increase by 9,547–9,566 per request through
Codex. Additional runtime instructions/context are a plausible contributor,
but token totals do not measure their share of elapsed time.

Analysis is a descriptive comparison only: its source windows and row counts
differ, and the API run includes a tiny one-row startup analysis. Excluding that
startup gives an API median of 17.591 seconds and a subscription/API ratio of
2.08×, but does not make the remaining inputs identical.

Both timing metrics measure application generation work rather than only the
HTTP exchange. They include provider processing and local overhead; they
exclude the scheduling interval, recognition and browser rendering. The
subscription metric additionally includes CLI startup and authentication checks.
The historical timing code was checked against saved runtime source hashes.
API timing includes ledger/lock work and intermediate artifact persistence but
ends before saving the final result file; subscription timing includes saving
the validated result. Neither path separately records HTTP-only duration.
These runs were not contemporaneous randomized trials, and startup, queueing,
generation and storage were not separately timed. No tool-use loop occurred
in the nine accepted subscription turns. The result supports an observed
slowdown, but cannot assign it specifically to agentic iteration or isolate
how much is CLI/base-instruction overhead versus provider/network variation.

## Source/output review

The review compared all 12 translation blocks and three analyses with their
frozen source selections. Numbers, opaque ASR spellings, the four collaborator
references, individual/group deliberation contrast, the possibility of building
trust, and the closing exhortations/contrasts were preserved in the checked
passages. Incomplete clauses were often left visibly incomplete instead of
silently repaired. These are model-assisted observations about saved text,
not an accuracy score against audio.

Three issues matter for later integration:

1. **Uncertainty filtering can lose a negation.** The uncertain
   `c000012-l0002` contains a negative before eligible `c000013-l0000`, which
   looks positive in isolation. Translation call 5 preserved that selected
   fragment with an ellipsis. It can mislead a reader about the actual speech,
   even though it is faithful to the selected fragment. This is an input/target
   boundary problem as well as an output-reading risk; it is not evidence that
   this provider reversed a complete target sentence.
2. **An analysis headline lost a qualification.** At 240 seconds, the summary
   explicitly recognizes a missing antecedent and uncertain causal link, while
   its headline confidently connects the preceding group-deliberation topic to
   the effects. A headline needs the same evidence constraints as the body.
   An earlier flow item also describes a trend where the source only gives a
   population level.
3. **Recurring terminology varies across calls.** The central concept is
   rendered differently in earlier translation, the conclusion and analysis.
   The reader may not realize those expressions refer to the same concept.
   This suggests evaluating a source-grounded glossary or term consistency
   check, without adding invented lecture facts.

No prompt, recognition, source-filtering or live-display change was made to
conceal these findings. Private `review.md` beside the run report presents the
new Japanese output alongside its selected English source and flags the above
risks. It is not committed or added to the approved public sample.

## Regression validation

Added 15 synthetic, CPU-only replay tests for chronology, multiple FIFO batches,
uncertainty, explicit window omissions, input hashing/drift, request caps,
metadata isolation, persistence failures and pending work. The worktree's own
Python environment ran 360 tests: 358 passed and two optional native capture
tests were skipped. Both Node UI checks passed. The tests did not run capture,
ASR or real inference; the nine opt-in calls above were separate.
