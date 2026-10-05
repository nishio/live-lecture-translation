# Wiki log

## [2026-10-05] experiment | Audrey with Luna low

At the user's request, evaluated `gpt-6-luna` with the same frozen nine
application requests and low reasoning used for Sol. The ordinary replay
stopped after one accepted and one rejected translation. Preserved that failed
report and its 41 pending rows. Tested the other seven inputs once each in a
separate diagnostic batch; no original attempt was retried or relabeled.

Five translations and three analyses passed structural validation; one
nine-target translation was rejected for group changes and missing IDs.
Accepted translation coverage is 38/47. The same five accepted translation
requests had Luna/Sol medians 8.525/11.943 seconds; three analyses had medians
19.693/36.564 seconds. Recorded the rejection's time/usage separately from valid
completion and retained semantic omissions, wrong source attribution, unsupported
negation and mislabeled concept provenance found by two reviewers.

All nine invocations reported usage: 100,422 input and 4,199 output tokens,
including the rejection. No application API-key requests were made; currency,
subscription allowance percentage, development and energy are unmeasured.
Updated experiment documents, the topic, index and log. No application code,
main checkout, live model selection, capture or ASR changed. Only aggregate
measurements and reusable findings enter Git; generated outputs stay ignored.

## [2026-10-05] file back | Evidence before latency explanations

Distilled the subscription/API conversation into three reusable rules in
[engineering decisions](engineering-decisions.md#4-recover-and-match-evidence-before-comparing-providers):
inspect retained per-call artifacts before declaring data unavailable, compare
matching requests and measurement boundaries, and separate an observed slowdown
from an unmeasured explanation. Linked the rules from the subscription topic
and updated the index. The existing canonical experiment document already
records the timings, matching five-call subset, token increase and limitations;
no runtime or configuration change follows from this file back.

Used direct Markdown edits because this repository explicitly makes Markdown
authoritative and is not grasp-write ready. Kept the work in the isolated
experiment worktree, preserving concurrent development in the main checkout.
Only reusable findings enter Git; no source/generated content, new capture,
model inference or API request was involved.

## [2026-10-05] comparison | Recovered Audrey API call timings

Corrected the earlier incomplete latency answer by inspecting retained per-call
API results, their publication measurements and source metadata. Recovered
translation/analysis medians of 9.019/17.435 seconds, versus subscription
13.818/36.564 seconds. Five translation application messages and schemas match
exactly; every one was slower through subscription, with paired differences
3.693–5.984 seconds. The first translation context and analysis windows differ.
Recorded raw numerical timings and comparison limits in the content-free
experiment aggregate, then updated the topic, index and log. No inference,
capture, runtime change or application code change was performed. Retained
source and generated text stay private. No timing breakdown establishes whether
CLI startup, extra instructions, provider/network variation or another factor
caused the observed slowdown.

## [2026-10-05] experiment | Audrey saved ASR through the subscription path

At the user's request, ran the existing reviewed Audrey lecture transcript
through the ChatGPT-authenticated Codex adapter in the isolated v0.9.0-based
worktree. Added a hash-bound, call-capped saved-text replay runner with explicit
completed, failed and pending states. All nine requests completed: six
translations covered all 47 eligible source rows in 12 blocks and three
analyses were saved. Fifteen uncertain source rows remained excluded as targets.

The sequential processing loop took 197.406 seconds; median translation and
analysis calls took 13.818 and 36.564 seconds. This accelerated source-end-time
replay does not measure ASR publication time, live latency or long-session
capacity. Reviewed all generated blocks and analyses against their saved source
selections. Recorded a negation/fragment risk at an uncertainty boundary,
qualification loss in an analysis headline, and recurring terminology variation.
This review is not an audio-based accuracy score or a matched API comparison.

Codex reported 106,654 input and 5,263 output tokens, plus a separately reported
389 reasoning-output field that is not added again to output tokens. No
application API-key requests were made. Subscription allowance percentage,
currency allocation, development usage and electricity remain unmeasured.
Private source snapshots, requests and generated results stay ignored; only
methodology and content-free aggregates enter Git.

Validation ran 360 CPU tests: 358 passed and two optional native capture tests
were skipped. Both Node UI checks passed. Updated the topic, canonical experiment
and architecture/configuration documents, this index, then the log. Concurrent
development, live runtime and the v0.9.0 baseline remain unchanged.

## [2026-10-05] experiment | Codex subscription from stable v0.9.0

Created the user-requested isolated `experiment/codex-subscription` worktree from
v0.9.0 and added an opt-in synthetic-text probe. Reused baseline translation and
analysis contracts through a ChatGPT-authenticated, isolated Codex CLI adapter.
Preserved the concurrent development checkout, live runtime, API authorization,
USD ledger, and baseline tag.

Observed two accepted translations at 11.009–11.378 seconds and two accepted
analyses at 22.878–23.673 seconds. Recorded three earlier failed workloads and
four unstarted workloads without relabeling them as completed. Fixed reserved
provider configuration incompatibility, narrow startup-advisory handling, and
structured-schema adaptation. Kept all prompts, outputs, events and state under
ignored results; published only methodology and content-free measurements.

Model-assisted spot checks retained the main translation facts/qualifiers;
analysis still showed small interpretation shifts. This does not establish
live latency, lecture comprehension, quota capacity, or savings. No API-key
calls were requested. Codex reported 53,706 input and 1,974 output tokens across
five invocations, including one rejected result; two failed invocations lacked
usage. API charge reconciliation, subscription quota/fee allocation, development
usage and electricity remain unmeasured.

Validation in the worktree's own test environment ran 345 CPU tests: 343 passed,
two optional native capture tests skipped. Both Node UI checks passed. No routine
test performed capture, ASR or real model inference. Updated the topic and
canonical experiment/configuration/architecture documents, the index, then this
log. The live provider remains unchanged; later integration is separate work.

## [2026-10-04] file back | Standalone 0.9 engineering baseline

Created [engineering decisions](engineering-decisions.md) as a new distillation for the standalone repository. Preserved three reusable themes: capture evidence independently of interpretation, separate complete translation from selective understanding support, and validate distinct timing and quality claims separately. Added the operational consequences for shared accounting, process state, and browser authentication.

Added links to the architecture, release boundary, and content-free historical experiment summaries. Existing private wiki pages and session artifacts were not copied. Markdown is the authority; no database-backed wiki migration was introduced. This documentation work performed no new capture, ASR inference, or API experiment.
