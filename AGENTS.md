# Development instructions

Read README.md, docs/architecture.md, and wiki/index.md before changing behavior.

- v0.9.0 is the extracted baseline. Record later field feedback separately and implement it in subsequent versions.
- Keep capture, ASR, translation, and analysis independently observable. A stop request is not proof of completion.
- Never commit recordings, transcripts, translations, summaries, API payloads, response caches, credentials, token-bearing URLs, or screenshots of real sessions.
- Runtime files belong in ignored data/ and results/. Tests use synthetic fixtures and temporary state directories.
- Do not start real capture, model inference, or paid API requests as part of routine tests. Use explicit user authorization for those operations.
- Do not modify another checkout's running application or shared state while developing this repository.
- Preserve source IDs, uncertain text, failed work, and unresolved budget reservations. Do not turn uncertain state into success.
- Wiki authority is Markdown. Update the topic, wiki/index.md, then wiki/log.md. Keep reusable findings and measurement limits, not private source content.
- Run CPU-only Python tests and Node UI tests after code changes. Keep native synthetic capture tests opt-in while a live recording is running elsewhere.
- Report measured API cost separately from estimates and unmeasured development/energy costs.
