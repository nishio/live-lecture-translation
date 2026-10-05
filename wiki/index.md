# Development wiki

This wiki preserves reusable engineering decisions for `live-lecture-translation`. Markdown is the editing authority. Keep source audio, recognized speech, generated lecture content, keys, and machine-specific private data outside the wiki.

- [Provisional live recognition](provisional-asr.md) and [measured ASR chunk comparison](../docs/experiments/asr-chunk-duration.md): fresh recognition of one public lecture at 3/5/10/15/30 seconds, a 3-second refresh of the latest 15 seconds, measured processing versus estimated waiting, observed revisions and limits; selects a provisional-buffer implementation candidate while preserving the current runtime. Includes the proposed separation of uncertainty from translation eligibility, with explicit filtering reasons and protection of meaningful clauses.
- [Engineering decisions](engineering-decisions.md): the purpose of supporting understanding with limited attention, canonical development ownership, runtime continuity, independent evidence capture, persistent unfinished work, honest timing and quality claims, and session cost attribution without double counting.
- [Collected migration follow-ups](migration-follow-ups.md): unapplied source patches, streaming integration lessons, v0.9 proper-name examples, user-supplied audio experiments and cost visibility, reference/history usability, the 論点/主張 design question, and additional historical measurements.
- [Architecture](../docs/architecture.md): current components and lifecycle.
- [Historical experiments](../docs/experiments/README.md): measured values, conditions, estimates, and limits without source content.
- [Version 0.9 boundary](../docs/release-0.9.md): preserved behavior, known limitations, and development questions.
- [Retirement and recovery rules](engineering-decisions.md#retire-a-checkout-without-erasing-unfinished-work): preserve unfinished stages, restore ignored data and shared dependencies separately from Git, and identify the actual runtime source.
- [File-back log](log.md): changes to this knowledge base.

When adding a lesson, state the observation, reusable decision, and remaining uncertainty. Update the closest topic page first, this index if needed, and the log last. Do not copy an entire session narrative when a short design rule and its limits are sufficient. New experiments should distinguish fresh measurements from historical results and unexecuted estimates.
