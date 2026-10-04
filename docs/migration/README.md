# Preserved development patches

These patches collect existing work discovered during the repository handoff. They are **not applied** to this repository's application files, are not loaded at runtime, and do not alter the v0.9.0 baseline. Adopt them as separate, reviewed follow-up changes when development resumes.

| Patch | Preserved work | Validation on a temporary copy |
| --- | --- | --- |
| [notification-state.patch](notification-state.patch) | Minimal dashboard notification fix and two synthetic regression fixtures | Applies to the extracted source; both Node UI and demo UI suites pass |
| [asr-comparison.patch](asr-comparison.patch) | Separate ASR research runner and 14 CPU tests | Applies to the extracted source; 14 tests pass with no skips |

The notification regressions also fail against the unpatched baseline. The patch confirms start only after fresh audio reception in recording state and confirms a successful stop only in completed state. It preserves the continuous-translation dashboard rather than replacing it with the earlier project's entire UI.

The comparison patch adds two files under audio-array/ and does not wire online recognition into the application. The runner source is preserved; only synthetic test dates were changed. Its model identifiers, endpoint assumptions, pricing date, and rates are historical research configuration, not a verification of current API availability or pricing.

## Comparison runner integration limits

- The fixed experiment uses English, 16 kHz mono input in complete 15-second chunks, a maximum of 40 chunks, 100 ms streaming packets, and a maximum experiment allowance of USD 0.50. It is not a general-purpose streaming backend.
- The runner uses internal event_insights_cloud helpers and its atomic ledger, plus lecture_live.LocalTranscriber for local recognition. Keep that dependency explicit when adopting it; a provider refactor must recheck accounting compatibility.
- Optional experiment dependencies are SciPy for causal resampling and WebSockets for streaming. Neither was added to the baseline requirements. CPU checks used NumPy 2.5.3 and SciPy 1.18.1. WebSockets was not installed in that test environment; no connection-path verification is claimed.
- CPU tests use synthetic WAV files and fabricated text, temporary state, and a mocked daily budget. They establish selected pacing, manifest, confirmation, duplication, and reservation properties. They do not establish end-to-end budget authorization or current provider behavior.
- Online execution needs a source manifest and its pinned hash, a separate raw-audio authorization, a separate daily-budget authorization, and an explicit key file. Local recognition needs an installed model manifest. No private manifest, approval, ledger, key, audio, response, or recognized text is included here.
- Failure or unknown usage retains its cost reservation. A deliberate retry needs an explicit attempt identity; the runner does not automatically resend audio.

Both patches were checked and tested on disposable copies. No real capture, model inference, audio playback, network request, or paid API experiment was performed. Applying a patch is distinct from authorizing an experiment or switching an existing recording session.

See [migration follow-ups](../../wiki/migration-follow-ups.md) for the reusable lessons and remaining adoption decisions.
