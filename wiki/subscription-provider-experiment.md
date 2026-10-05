# Subscription provider experiment

On 2026-10-05 the user requested an isolated experiment from the stable release
while other development continued. The `experiment/codex-subscription` worktree
starts at the unchanged `v0.9.0` tag. Its standalone synthetic-text probe is
implemented; the live coordinator, launcher, API provider, and budget ledger
have not been switched. See [method, results, and limits](../docs/experiments/codex-subscription.md).

The probe accepted two translation outputs (11.009–11.378 seconds) and two
analysis outputs (22.878–23.673 seconds). Source coverage and format checks
passed. Analysis retained small interpretation shifts, so structural success
is not presented as complete semantic fidelity. Failed attempts and their
reported/unknown usage remain separate from these accepted observations.

## Integration contract

Use the supported ChatGPT-authenticated Codex client, with a positive auth-mode
check, explicit subscription-use invocation, and no API-key fallback. Run each
trial from empty temporary context with unrelated configuration and capabilities
disabled. Preserve the existing user login; never extract its tokens into an
application API-key field. Pin the CLI version audited for isolation and event
handling until a later version is reviewed.

Reuse the application's source selection, prompt, schema, and source-ID validators.
Agent execution still adds base instructions and protocol behavior. A matching
model name and source text do not establish identical API and Codex conditions.
Normalize unsupported schema keywords only for the provider, retaining the
original contract and checking uniqueness/coverage locally.

Require terminal-turn completion, valid structured output, source validation,
and successful persistence before recording a workload as complete. Keep
process start, remote completion, accepted output, and unfinished work distinct.
A timed-out subprocess or missing usage cannot establish that remote generation
stopped or that no allowance was consumed. Never discard a completed translation
because the independent analysis subsequently fails.

## Lessons from the probe

- The audited CLI rejects overrides of its reserved built-in `openai` provider.
  Do not assume configuration examples from another CLI version are accepted.
- CLI item-level error-shaped messages include a known startup advisory for
  deliberately disabled Code Mode. Handle only the exact reviewed advisory;
  unknown error/tool events still prevent acceptance.
- The analysis schema contains `uniqueItems`, which the remote structured-output
  subset rejects. The baseline API adapter already compensates for this; the
  subscription transport needs equivalent adaptation and unchanged local checks.
- An adapter failure may occur after the model returned usage. Preserve and
  include such usage separately instead of counting only accepted results.

## What remains

The synthetic experiment and the real-source follow-up below evaluate feasibility
and bounded meaning-preservation cases. They do not measure microphone/ASR latency, listening comprehension,
long-session subscription capacity, cancellation of remote work, or behavior
after real allowance exhaustion/authentication expiry. Production integration
still needs user-facing provider selection, authorized transcript scope,
persistent pending work, and live scheduling/recovery tests.

Record Codex token reports separately from API dollars. No API-key operation is
requested here; API charge reconciliation, subscription quota consumption,
subscription-fee allocation, development-assistant use, and electricity are not
measured. Keep all generated content and invocation artifacts in ignored
results; publish only content-free aggregates and methodology.

## Real-source follow-up: Audrey lecture

The user requested the existing Audrey source instead of synthetic data. A
hash-bound snapshot of the reviewed public sample's 62 saved ASR rows was
processed through a new offline prefix runner. The [Audrey experiment record](../docs/experiments/audrey-subscription.md)
and content-free aggregate separate it from the earlier synthetic trials.

All nine calls completed: six translation calls covered 47 eligible rows in
12 blocks with no missing/duplicate/pending targets, and three recent-window
analyses were saved. Median translation/analysis times were 13.818/36.564 seconds;
sequential processing took 197.406 seconds for a source recording of 325.567
seconds. No audio/ASR or wall-clock source replay was performed. Historical API
analysis inputs differ, so the complete workload is not a matched comparison.

The main reusable findings are an uncertain-negation boundary that leaves a
misleading positive-looking target fragment, an analysis headline that drops
its own body's causal qualification, and inconsistent rendering of a recurring
term across calls. Complete coverage alone cannot detect these problems.
Preserve the observations; do not silently repair source text or infer that
subscription authentication caused them. Keep new generated output private.

The nine calls reported 106,654 input and 5,263 output tokens. No application
API-key calls were made; subscription allowance percentage and currency
allocation were not measured. CPU regression validation passed 358 tests,
with two optional native tests skipped, and both Node UI checks passed.

## Recovered historical API call timings

Read the private per-call results before concluding that only whole-run timing
exists. The successful Audrey API run retained six translation generation times
(median 9.019 seconds) and four analysis times (median 17.435 seconds). Compared
with subscription medians of 13.818/36.564 seconds, these are ratios of 1.53/2.10.
All ten API results match saved publication measurements and have no application
cache hits. No new inference was performed to recover this evidence.

Five of the six translation application messages and output schemas match
exactly, with the same requested model and low reasoning setting. These five
API/subscription pairs have medians 10.150/15.693 seconds (1.55×), and every
subscription call took 3.693–5.984 seconds longer. The first translation uses
different context but the same targets. Analysis windows differ, including one
tiny startup API request, so analysis is a reference comparison only.

This establishes an observed slowdown for this implementation and these runs.
It does not attribute it to agentic iteration: accepted calls had no tool loop.
The extra roughly 9,600 reported input tokens per matching translation and
per-call CLI/auth startup are candidate contributors. Timing includes local
overhead on both paths, not just HTTP; no stage breakdown or contemporaneous
randomized comparison isolates the cause. Keep causal claims narrower than the
observed timing comparison. See the [comparison tables](../docs/experiments/audrey-subscription.md#retrospective-comparison-with-the-audrey-api-run).

The reusable [comparison discipline](engineering-decisions.md#4-recover-and-match-evidence-before-comparing-providers)
is to search retained per-call evidence before claiming it is missing, compare
the best-matched requests before relying on whole-run totals, and distinguish
an observed runtime slowdown from an unmeasured causal explanation. Further
optimization should measure startup/auth, response wait and generation
separately; it should not assume that removing agentic iteration helps when
the accepted trials contain no such iteration.

## Luna model comparison

The authorized [Luna comparison](../docs/experiments/audrey-luna-subscription.md)
changed only the requested model to `gpt-6-luna`, retaining low reasoning and all
nine frozen application requests. The normal replay stopped on its second
translation: it split prescribed groups and dropped two target IDs. Its failed
status and pending work remain unchanged. Seven unstarted inputs were tested
once in a separate diagnostic batch; none of the original attempts was retried.

Across both executions, five of six translation responses and all three
analyses passed structural validation. Accepted translations cover 38 of 47
eligible rows; the nine-row rejected request is still unaccepted. All-response
translation median was 8.420 seconds versus Sol 13.818, but includes the rejection.
For the same five accepted requests, medians were 8.525 versus 11.943 seconds
(28.6% shorter); analysis medians were 19.693 versus 36.564 (46.1% shorter).

Speed did not establish an equivalent replacement. Review found omitted source
content, text moved under the wrong source IDs, an unsupported negative/causal
connection, and a background definition labeled as lecture evidence. Some of
these passed the existing validator. Keep structural compliance and semantic
fidelity as separate acceptance criteria, and never treat a rejected short
response as faster successful completion. Future prompt/schema experiments
must preserve this baseline and its failure evidence. No live model was switched.
