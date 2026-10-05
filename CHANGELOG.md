# Changelog

## Unreleased

- Classify cloud failures and add up to three automatic retries within a five-minute start window for recoverable failures in continuous mode. Respect server retry delays, permit pausing, preserve frozen request input and every unresolved reservation, and keep other stages independent.

- Prefer sentence-like source-row endings in every continuous translation block and retain unfinished tails with a distinct continuation-wait state. Preserve explicit reasons for fragments forced by limits, source gaps, processed-audio timeout or final drain; boundary waits consume no generation interval and retries retain frozen inputs. This first stage keeps whole-row coverage and does not integrate provisional ASR or revise published translations.
- Add an explicit offline ASR probe comparing 2–30-second chunks and revisable rolling windows, with separate warmup, processing measurements, estimated publication timing, and retained incomplete work. Record the authorized public-audio comparison without changing live defaults.

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
