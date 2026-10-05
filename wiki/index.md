# Development wiki

This wiki preserves reusable engineering decisions for `live-lecture-translation`. Markdown is the editing authority. Keep source audio, recognized speech, generated lecture content, keys, and machine-specific private data outside the wiki.

- [Subscription provider experiment](subscription-provider-experiment.md): isolated v0.9.0-based Codex trials, completed Audrey Sol replay, historical API timings, and faster Luna responses with one rejected translation and semantic/source-reference failures; separate usage evidence and remaining live-integration work.
- [Engineering decisions](engineering-decisions.md): independent evidence capture, persistent unfinished work, recovering per-call measurements, matching provider comparisons, separating observed latency from causal explanations, and their operational consequences.
- [Architecture](../docs/architecture.md): current components and lifecycle.
- [Historical experiments](../docs/experiments/README.md): measured values, conditions, estimates, and limits without source content.
- [Version 0.9 boundary](../docs/release-0.9.md): preserved behavior, known limitations, and development questions.
- [File-back log](log.md): changes to this knowledge base.

When adding a lesson, state the observation, reusable decision, and remaining uncertainty. Update the closest topic page first, this index if needed, and the log last. Do not copy an entire session narrative when a short design rule and its limits are sufficient. New experiments should distinguish fresh measurements from historical results and unexecuted estimates.
