# Collected migration follow-ups

The standalone repository is the home for future Mac lecture application development. A review of the related development conversations identified the additional work below. It does not change the v0.9.0 tag or the application currently recording elsewhere.

## Existing code awaiting adoption

[Migration patches](../docs/migration/README.md) preserve two existing developments without applying them to the baseline:

- **Capture operation notifications.** The older project contains a correction and regression fixtures that distinguish starting from recording, and failed saving from a successful stop. The running continuous-translation source and the extracted baseline both retain the older success branch. Acknowledging a requested operation must not imply confirmed recording or storage. The patch is a follow-up candidate, not an installed fix.
- **ASR comparison runner.** A separate experiment runner compares local, file-based, and streaming recognition using a fixed input manifest, real-time delivery, content hashes, separate partial/final output, and cost reservations. It and its CPU tests were outside the initial runtime extraction. Preserve it as research code, with its historical assumptions and dependency/budget integration gaps visible. It is not connected to the live recognizer.

Private recordings, input manifests, authorization files, ledgers, requests, responses, transcripts, and semantic reviews are not part of these patches. A historical permission to run one experiment is not permission to run it again or against another source. The comparison runner's audio transmission requires separate authorization from the application's text-only cloud workflow.

## Lessons for a future streaming adapter

These are integration requirements distilled from the comparison experiment, not claims that a streaming adapter exists in this release.

1. Keep provisional partial text separate from final source evidence. Match events through their item identifiers; final arrival order need not be utterance order. Preserve the relationship between deltas, commits, and final items.
2. Do not label chunk timestamps as word timestamps. Measure connection preparation, first partial arrival, chunk completion, and final publication separately.
3. When a provider does not supply a confidence signal, represent it as unknown or not provided. Do not route missing scores through recognizer-specific defaults that silently classify speech as certain.
4. Distinguish requested settings from values confirmed by the server. An omitted echo leaves the effective value unverified.
5. Pace packets against the source clock and avoid future-sample look-ahead when resampling. Keep failed requests and usage-unconfirmed reservations in the accounting state.

## Additional evidence preserved

[Development handoff measurements](../docs/experiments/development-handoff.md) records content-free aggregates from an older pre-event audit and a partial live cost observation. Those measurements are historical evidence of their own source versions. They do not add a clean-install result or a full-event success claim to v0.9.

The next development cycle should use the user's event feedback, choose which collected changes to adopt, and validate them on an isolated candidate. Switching the operational environment remains a separate step after the current session has finished.
