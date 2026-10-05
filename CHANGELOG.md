# Changelog

## Unreleased

- Prefer sentence-like source-row endings in every continuous translation block and retain unfinished tails with a distinct continuation-wait state. Preserve explicit reasons for fragments forced by limits, source gaps, processed-audio timeout or final drain; boundary waits consume no generation interval and retries retain frozen inputs. This first stage keeps whole-row coverage and does not integrate provisional ASR or revise published translations.
- Add an explicit offline ASR probe comparing 2–30-second chunks and revisable rolling windows, with separate warmup, processing measurements, estimated publication timing, and retained incomplete work. Record the authorized public-audio comparison without changing live defaults.
- Collapse adjacent repeated uncertain recognition into one reading row, allowing case/whitespace differences while preserving every source record, uncertainty flag and publication time.
- Replay saved runs from the beginning at 1×, optionally synchronized to a local WAV, with pause, restart and seeking. Reveal continuous translations at their recorded publication times and retain the final processing tail after the audio ends.
- Remove routine reading-pane metadata, source-count controls, AI disclaimer labels, detail disclosures and the separate history list. Keep original speech, generated content, direct history navigation, timers and actionable errors.
- Permit replay-only cloud authorization with a zero microphone allowance; reject every microphone-text request while retaining past reservations.
- Add an original-speech circle showing observed audio remaining until the next recognition chunk, with distinct recognition, queue, input-stall, failure and completion states.
- Fit the reading workspace to the viewport, with independent scrolling and a draggable, keyboard-accessible original/translation height divider. Preserve the height preference and fix countdown positions across changing labels.
- Stack source speech and contextual Japanese translation in the left column; prioritize the current interpretation and concept explanations on the right. Keep process details available for new problems, with compact countdowns and explanations on demand.
- Add circular, per-stage countdowns for the next start/retry decision, with separate busy, blocked, saved-view, and unknown states. They are not generation-completion estimates.
- Add direct previous/next understanding navigation and retain concept versions without scrolling away from a reader. Load older persisted analysis/concept history beyond the recent 60-snapshot window while retaining source links in saved data.
- Rename contextual translation and focus headings to describe their actual tasks. Refine the existing analysis prompt to preserve questions and qualifications, avoid repetition, and provide useful concept explanations. Comprehension gains remain unmeasured.
- Classify cloud failures and add up to three automatic retries within a five-minute start window for recoverable failures in continuous mode. Respect server retry delays, permit pausing, preserve frozen request input and every unresolved reservation, and keep other stages independent.
- Add a user-audio experiment CLI: a read-only WAV check with historical cost illustration, and explicit isolated ASR/authorized cloud runs with input identity, settings, completion, and cost records.

The v0.9.0 tag and existing recording sessions are unchanged. No new model-quality, real-network recovery, or live-listening benchmark is implied.

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
