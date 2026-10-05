# Provisional live recognition and translation evidence

Early recognition need not be append-only or serve as the final translation evidence. A short update interval can refresh a revisable buffer at the live edge; translation can re-recognize a longer span of already captured audio. Evaluate update interval, audio-window length, and finalization policy as separate parameters. A shorter input failing at a sentence boundary is a reason to test retained audio context and revision, rather than a reason by itself to delay every live update.

Keep provisional snapshots separate from the immutable source lines currently consumed by translation and analysis. At translation time, freeze the recognized audio range and source revision used by the request. Do not change the text behind an existing source ID, append overlapping snapshots as duplicate speech, or discard an unsuccessful revision. Preserve publication times so later corrections do not appear early in historical playback. The property “may still change” is distinct from the recognizer's uncertainty flag.

Both quick recognition and longer re-recognition use local inference capacity. Measure their contention as well as recognition time, missing words, revision frequency, and reader effort. Longer context does not automatically establish greater accuracy. A rolling-window probe can test available context and compute cost, but does not by itself implement stable-prefix reconciliation or the two-stage live pipeline.

The [authorized 325.567-second comparison](../docs/experiments/asr-chunk-duration.md) ran independent 3/5/10/15/30-second chunks twice and a 3-second refresh of a trailing 15-second window twice. Measured service medians were about 0.654 seconds for independent 3-second chunks, 0.771 seconds for 15-second chunks, and 0.777 seconds for the rolling snapshots. Simulated mean waits were about 2.15, 8.24, and 2.29 seconds respectively; these exclude startup and browser arrival. Selected rolling snapshots revised repetition and boundary failures when more audio became available, but also showed provisional regressions. Choose 3-second refresh with a revisable 15-second buffer as the next live implementation candidate, with separate translation evidence finalization. This is a measured compute/context experiment, not an implemented live buffer or proof of general accuracy. The live default remains 15 seconds.


The [architecture](../docs/architecture.md) remains authoritative for current live behavior. The [probe guide](../docs/asr-probe.md) describes explicit saved-audio experiments.

## Uncertainty and translation eligibility are separate decisions

After publishing the comparison, the user proposed filtering rule-identifiable junk from uncertain recognition and translating the remaining content with its uncertainty attached. The reason is semantic preservation: excessive uncertainty detection followed by blanket exclusion can remove a negation, condition, quantity, or qualification. This is a proposed policy, not a change to the live application.

The current [translation planner](../audio-array/lecture_translation.py) excludes every uncertain line from translation targets, but can send those lines as nearby context. Its normalized source retains `uncertain` but drops `doubt_reasons`. The analysis selector excludes uncertain lines before building the request. Merely adding uncertain context does not require its meaning to appear in a target translation.

The 15-second experiment had 15 uncertain lines containing 161 normalized tokens. Five lines, containing 79 tokens, were marked uncertain solely because model timestamps extended outside their audio chunk. This is not evidence that their text is wrong. Text review also found excluded negation-bearing lines. Counts describe this one recording and the current heuristics, not validated error rates.

Separate three properties: the original recognition evidence, typed uncertainty, and the derived decision to include or suppress it in a translation request. Preserve source IDs, original text and every exclusion reason. A translation request should be able to include an uncertain target with an allowlisted reason; unsupported scores must not be presented as calibrated probabilities. Timing uncertainty and textual uncertainty should not be conflated. Translation output must retain unresolved wording or gaps rather than turning guesses into confident facts.

| Candidate treatment | Constraint |
| --- | --- |
| Suppress or collapse evident generated loops | Compression ratio is a suspicion signal, not proof of duplicate speech. Define and validate the repetition rule; preserve the original records and distinguish genuine emphasis or repeated speech. |
| Omit isolated nonsemantic fillers when the rule is reliable | Do not globally remove `so`, `thank you`, short responses, numbers, or negation. Context can make them meaningful. |
| Include meaningful uncertain text with its reason | Protect negation, quantities, conditions, names, attribution, and incomplete but informative clauses. Re-recognition may help resolve them. |
| Split or explicitly defer oversized text | Length alone is not a reason to discard content. Retain pending work and report input-limit failures. |

The next implementation should use one eligibility policy for planning, pending counts, coverage, retry and completion, and should update normalized source fingerprints and request validation consistently. First compare candidate rules on saved recognition and synthetic counterexamples, including a negation with invalid timestamps, meaningful uses of `so`, long valid statements, actual repetitions, and hallucinated loops. Measure recovered meaningful content and mistaken exclusions separately. No filter thresholds, new translation calls, or quality improvement were established by this proposal.
