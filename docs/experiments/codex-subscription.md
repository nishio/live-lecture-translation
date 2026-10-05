# Codex subscription text-processing experiment

The subsequent [Audrey lecture trial](audrey-subscription.md) uses real saved ASR
instead of the synthetic fixture described on this page.

**Result:** ChatGPT-authenticated Codex completed translation and understanding
support in this bounded synthetic case. Each workload produced two accepted
outputs. Translation took 11.009–11.378 seconds; analysis took 22.878–23.673
seconds. This is a feasibility result, not a live-performance or general quality
claim. The live application still uses its existing providers.

This experiment is developed on `experiment/codex-subscription`, starting from
the unchanged `v0.9.0` tag (`976c4508d5ee13019f879a5aee8d0a7c9509c767`). It is
isolated from the concurrently edited development checkout and from live capture.

## Reproduce the bounded probe

The probe uses newly authored synthetic English statements about a bus-route
pilot. It tests numbers, negation, conditional approval, an uncertain source
line, a defined concept, and a future survey. It accepts no private transcript
or audio input and does not start capture or ASR.

```sh
python3.12 audio-array/lecture_subscription_probe.py plan
python3.12 audio-array/lecture_subscription_probe.py check
python3.12 audio-array/lecture_subscription_probe.py run --confirm-subscription-use
```

`plan` performs no network or model operation. `check` inspects CLI version and
existing login status without inference. `run` explicitly consumes the signed-in
ChatGPT allowance, making one translation and one analysis request. Optional
`--repetitions 3` caps a run at six requests; `--timeout` bounds each CLI process
(default 120 seconds). The first failure stops the run with later work pending.
Another run is a new explicit trial, not automatic recovery of previous work.
Use `--workload analysis` or `--workload translation` for an independent stage
trial without re-running the other workload.

Codex must already be signed in with ChatGPT. This experiment never signs in,
copies credentials, replaces API authorization files, or falls back to an API
key. It uses the existing supported client authentication mechanism. See the
official [Codex authentication](https://learn.chatgpt.com/docs/auth) and
[non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode)
documentation, checked on 2026-10-05.

## What is being compared with the application

The probe reuses v0.9.0's pure translation planner, translation/analysis prompt
and schema builders, and source-ID response validators. It requests
`gpt-6.1-sol` with low reasoning effort. It does not invoke the API adapter or
write its USD budget ledger. It is an independent experiment, not a selectable
provider in `start.command` or the live dashboard.

Translation targets are six eligible lines in two ordered groups. The seventh,
uncertain line remains marked uncertain in translation context, as in the
baseline planner, but is excluded from targets. Analysis receives only the six
certain lines, with no previous generated prose and no translation targets.

Codex receives the application message list wrapped in a text-transformation
instruction. It is still an agent runtime with its own base instructions, not
an identical Responses API message stack. Matching the source, prompts, schema,
and requested model therefore does not isolate authentication as the only
experimental variable. No matched paid-API control is run here. The model name
records the requested CLI model; observed JSONL does not independently attest
the served model identity.

## Isolation and observable failure

The adapter launches its own CLI process with `--no-daemon`, an empty temporary
working directory, a read-only sandbox, no approvals, no persistent session,
no user config or rules, and project instruction loading disabled. Shell,
connectors, plugins, browser/computer, image, memory, skill discovery, and
subagent features are disabled. The child receives an allowlisted environment
with API keys and authentication overrides removed. Existing login storage is
used by Codex without this application reading or copying it.
The audited CLI is `0.159.0-alpha.12.1`; another version is refused until its
configuration and event contract are reviewed.

Accepted output requires a zero process exit, one successful terminal turn,
no failed turn or tool-action events, parseable JSON, and the application's response
validation. Validated output must be saved before coverage is marked complete.
The exact known disabled-Code-Mode startup advisory is recorded separately;
other error items still fail. The application keeps the original schema for
validation and removes unsupported `uniqueItems` only from the submitted schema,
matching the baseline API adapter. Duplicate source IDs still fail validation.
Timeout terminates the invocation's own process group; remote work/usage may
remain unconfirmed. This does not cancel or stop any other Codex or lecture
session. There is no application-level automatic retry; CLI-internal transport
behavior is not represented as an exact remote request count.

Private prompts, raw events, generated content, and attempt state are retained
under the worktree's ignored `results/subscription-probe/`. Commit only this
methodology and content-free aggregates. Synthetic fake-CLI tests never invoke
the real model.

## Measurement boundaries

Elapsed times cover the adapter/check startup, remote generation, validation,
and local persistence of the supplied short text. They exclude audio capture,
ASR, live scheduling waits, and display. The fixture's timestamps are artificial;
they do not represent a recording or establish speech-to-screen latency.

Token usage, when returned by Codex, is reported separately from API cost.
No paid API-key call is requested, and no API charge is measured by this adapter;
`measured_api_cost_usd` stays null. Do not convert the subscription token count
using API rates or describe it as a measured USD 0 charge. Subscription quota
consumption, subscription fee allocation, development use, and electricity are
not measured. Full-lecture quota capacity and savings remain unverified.

Structural success proves source-reference and format compatibility only.
Semantic spot checks inspect preservation of 24 volunteers/two weeks, wait
reduction from 12 to 9 minutes without a travel-time measurement, limits on
generalization, both conditions for extension (including strictly less than
USD 5,000), the reversible-decision definition, and the not-yet-conducted
survey. This small synthetic case cannot establish translation quality or
comprehension during a real lecture.

## Observations

The first invocation failed during local CLI configuration loading because
this CLI reserves the built-in `openai` provider configuration. Its failed
attempt and unstarted analysis are retained. The incompatible retry overrides
were removed before a separate trial. No completion or zero-usage result is
inferred from the missing usage report.

The second invocation returned a translation and token usage, but the probe
rejected startup advisories as unexpected events. That failed attempt was kept;
it was not treated as unused quota or retroactively promoted to a successful
pipeline run. The third trial completed translation but its analysis schema
was rejected for `uniqueItems`; later requests stayed pending. These findings
led to the narrowly scoped advisory handling and schema adaptation above.

After those fixes, an independent analysis trial completed, followed by a new
paired translation/analysis trial that completed both stages. Including the
earlier accepted translation gives these observations:

| Accepted workload | Observations | Elapsed seconds | Reported input / output tokens |
| --- | ---: | --- | --- |
| Translation | 2 | 11.378, 11.009 | 10,460 / 250; 10,456 / 245 |
| Analysis | 2 | 22.878, 23.673 | 11,167 / 630; 11,169 / 604 |

Both translations cover all six eligible IDs in the required two groups, with
no duplicate/missing target and no uncertain subsidy text added to the targets.
Each analysis contains three summary items and one lecture-grounded concept.
Model-assisted source/output spot checks found the main numbers, negation,
joint conditions, strict cost bound, definition, and future/not-completed state
preserved in translation. Analysis preserved the main facts but repeatedly
interpreted extension as expansion; one headline strengthened a limit on
evidence into a limit on effects. Keep those as quality findings, not a full
semantic pass or a subscription-specific regression proven against an API control.

Across five explicit trial runs there were seven CLI generation invocations:
four accepted workloads and three failed workloads. Four scheduled workloads
in earlier runs remain unstarted; they were not silently resumed. Five
invocations returned token usage, including the advisory-rejected output:
53,706 input and 1,974 output tokens in total. Two failed invocations returned no
usage; that missing usage is not zero. All attempts remain in private results.
The [content-free JSON aggregate](codex-subscription.json) records these counts
and the individual accepted timings without prompts or generated prose.

Validation used an isolated Python 3.12 virtual environment with only the pinned
CPU-test dependency: 345 tests ran, 343 passed and two optional native capture
tests were skipped. Both Node UI checks passed. Fake-CLI coverage includes
non-ChatGPT refusal, unsupported version, timeout and child cleanup, malformed
or failed output, tool-event rejection, the narrow advisory exception, schema
adaptation, source coverage, and persistence failures. Routine tests made no
real capture, ASR, or model call; the opt-in trials above were separate.
