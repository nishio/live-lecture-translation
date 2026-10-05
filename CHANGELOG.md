# Changelog

## Unreleased — subscription experiment branch

- Adds a hash-bound saved-transcript subscription replay and an Audrey lecture
  experiment, with bounded calls, chronological source selection, separate
  planned/actual coverage, and retained failed/pending work.
- Adds an opt-in, synthetic-text Codex subscription probe with isolated CLI
  execution, existing translation/analysis validators, private attempt records,
  and explicit failures/pending work. It does not change the live provider.
- Keeps subscription token reports separate from unmeasured quota/currency
  costs. See the [experiment](docs/experiments/codex-subscription.md).

## 0.9

Initial standalone extraction of the continuous lecture translation application.

- Retains native Mac audio capture, local MLX Whisper recognition, and a local browser dashboard.
- Retains the persistent untranslated-source queue and separate understanding-support scheduler, with nominal intervals of 60 and 120 seconds.
- Retains source links, recognition uncertainty, accumulated translations, historical views, and a stop sequence that tracks remaining processing.
- Separates installation and launch entry points from the original project.
- Adds newly written architecture, operation, data-handling, release-boundary, and development-knowledge documentation.
- Adds content-free summaries of historical measurements, with measured values separated from extrapolations and unmeasured costs.

The original recordings, recognized speech, generated translations and notes, API payloads and responses, and screenshots are excluded. Historical test counts and experiment results do not certify that this extracted release has been independently reproduced on a clean machine; release validation is tracked separately.

Extraction validation: 315 Python tests passed and two Swift synthetic-audio tests were skipped, with no failures; both Node UI checks passed. No real recording, ASR inference, paid API request, or clean installation was tested.

There is no new automatic restart recovery or established long-duration microphone-performance guarantee in 0.9.
