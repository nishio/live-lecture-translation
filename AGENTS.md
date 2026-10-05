# Development instructions

Read README.md, docs/architecture.md, and wiki/index.md before changing behavior.

This repository is the canonical home for future Mac lecture application code, experiments, field feedback, and reusable implementation knowledge. Keep new development here. Device-specific Android recording, ADB/Wi-Fi retrieval, multichannel processing, and the original reading/Vision project remain in the originating project.

Development migration does not authorize a runtime cutover. Preserve a running session. Before retiring a stopped checkout, verify that no capture or processing is active, preserve its private files and shared dependencies, and record completed, failed, and pending stages explicitly. Preserving unfinished work does not mean it succeeded or authorize its replay. The v0.9.0 tag remains the baseline; later changes belong to later commits. See wiki/migration-follow-ups.md for collected work that has not been applied.

- Before proposing, repeating or implementing a Codex subscription provider, read the [tested route and adoption decision](wiki/live-and-review-pipeline.md#subscription-provider-decision). The Sol/Luna route was already evaluated and is not the preferred live replacement. Do not rerun unchanged conditions as an untested idea; reconsider only with a concrete changed factor and latency/quality acceptance criteria.
- v0.9.0 is the extracted baseline. Record later field feedback separately and implement it in subsequent versions.
- Keep capture, ASR, translation, and analysis independently observable. A stop request is not proof of completion.
- Keep private recordings, transcripts, translations, summaries, API payloads, response caches, credentials, token-bearing URLs, and screenshots out of Git. The explicitly approved Audrey public demo below is the only audio/playback exception.
- Explicit publication exception: the user approved the reviewed text sample in `samples/audrey-plurality-seoul-2023/` and, on 2026-10-06, explicitly requested bundled audio and generated playback data. Its `demo/` directory may contain the attributed public Audrey WAV and an allowlisted export of source/translation/analysis text, uncertainty, IDs, recorded publication timing, stage outcomes and aggregate costs. Keep source attribution, CC BY notice and transformation notes. Exclude publisher subtitles, raw runtime snapshots, machine paths, API payloads/responses, caches, credentials, ledgers and authenticated URLs. Do not copy private session directories wholesale. This exception does not authorize publication of other sessions.
- Runtime files belong in ignored data/ and results/. Tests use synthetic fixtures and temporary state directories.
- Do not start real capture, model inference, or paid API requests as part of routine tests. Use explicit user authorization for those operations.
- Do not modify another checkout's running application or shared state while developing this repository.
- Preserve source IDs, uncertain text, failed work, and unresolved budget reservations. Do not turn uncertain state into success.
- Wiki authority is Markdown. Update the topic, wiki/index.md, then wiki/log.md. Keep reusable findings and measurement limits, not private source content.
- Run CPU-only Python tests and Node UI tests after code changes. Keep native synthetic capture tests opt-in while a live recording is running elsewhere.
- Report measured API cost separately from estimates and unmeasured development/energy costs.
