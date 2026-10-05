# Development instructions

Read README.md, docs/architecture.md, and wiki/index.md before changing behavior.

This repository is the canonical home for future Mac lecture application code, experiments, field feedback, and reusable implementation knowledge. Keep new development here. Device-specific Android recording, ADB/Wi-Fi retrieval, multichannel processing, and the original reading/Vision project remain in the originating project.

Development migration does not authorize a runtime cutover. Preserve a running session. Before retiring a stopped checkout, verify that no capture or processing is active, preserve its private files and shared dependencies, and record completed, failed, and pending stages explicitly. Preserving unfinished work does not mean it succeeded or authorize its replay. The v0.9.0 tag remains the baseline; later changes belong to later commits. See wiki/migration-follow-ups.md for collected work that has not been applied.

- v0.9.0 is the extracted baseline. Record later field feedback separately and implement it in subsequent versions.
- Keep capture, ASR, translation, and analysis independently observable. A stop request is not proof of completion.
- Never commit recordings, transcripts, translations, summaries, API payloads, response caches, credentials, token-bearing URLs, or screenshots of real sessions.
- Explicit publication exception: the user approved the reviewed text sample in `samples/audrey-plurality-seoul-2023/` from the public Audrey Tang video. Keep its source attribution, CC BY notice, uncertainty and transformation notes. This exception covers only the allowlisted text/JSON sample and aggregate measurements, not audio, publisher subtitles, runtime state, API payloads/responses, credentials, ledgers or screenshots. Other session data stays private.
- Runtime files belong in ignored data/ and results/. Tests use synthetic fixtures and temporary state directories.
- Do not start real capture, model inference, or paid API requests as part of routine tests. Use explicit user authorization for those operations.
- Do not modify another checkout's running application or shared state while developing this repository.
- Preserve source IDs, uncertain text, failed work, and unresolved budget reservations. Do not turn uncertain state into success.
- Wiki authority is Markdown. Update the topic, wiki/index.md, then wiki/log.md. Keep reusable findings and measurement limits, not private source content.
- Run CPU-only Python tests and Node UI tests after code changes. Keep native synthetic capture tests opt-in while a live recording is running elsewhere.
- Report measured API cost separately from estimates and unmeasured development/energy costs.
