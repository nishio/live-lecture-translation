# Data handling and publication

## The application creates private working data

The normal microphone workflow records audio locally and runs recognition locally. Optional cloud translation and analysis send recognized text and selected transcript context. The application also preserves generated text and evidence needed to diagnose a request.

Revisable preview recognition copies a trailing window of saved PCM to a temporary WAV. The WAV is removed once its recognition returns or fails. Its frame range and PCM hash stay in `provisional-history.jsonl`, so the window can be re-derived from `raw.pcm`. Sessions recorded with 1.0.1 or earlier keep their preview WAVs; nothing deletes existing session files.

Private recordings are not distributed. The user explicitly authorized the [Audrey public demo](../samples/audrey-plurality-seoul-2023/demo/README.md), including its public lecture WAV and generated playback export, on 2026-10-06. It retains source attribution and the CC BY notice. Historical private-session measurement summaries remain separate from their original artifacts.

## Use a publication allowlist

Publish code, newly authored documentation, synthetic fixtures, and reviewed numerical aggregates. Keep generated session directories outside that allowlist. An ignore rule helps prevent accidental staging; it does not remove data already committed or prove that other files are safe.

The Audrey sample is a narrow user-authorized exception: export only recognized lines, translation blocks, analysis content and reviewed measurements into `samples/audrey-plurality-seoul-2023/`. Preserve uncertain recognition, source IDs and the unedited generated wording. Include the original video, speaker, publisher, observed license notice, and a description of the recognition/translation/summarization changes. For the bundled `demo/`, also export the exact public lecture WAV and allowlisted publication times, stage outcomes and aggregate costs required for playback. Keep recognition uncertainty and historical failures. Exclude publisher subtitles, raw API exchanges, raw runtime snapshots, machine paths, account identifiers, authorizations and shared cost/scope ledgers. Do not copy a session directory wholesale. This does not authorize publication of other sessions.

Files that can contain source content include:

- Audio, PCM, WAV, and multipart request bodies.
- Recognition reports and transcript, translation, or analysis histories.
- API requests, prompts, payloads, responses, generated results, server-sent events, and streaming event logs.
- Semantic reviews, disagreement reports, display fixtures, HTML exports, demos, and screenshots.
- Status files, manifests, receipts, runtime snapshots, and logs with embedded text or request details.

README files and experiment narratives can quote the material being evaluated. File extensions and apparently administrative filenames are not sufficient publication criteria.

Private configuration may also include API credentials, authenticated local URLs, machine-specific absolute paths, account identifiers, or scope and cost ledgers. A public configuration example should use placeholders and conservative defaults; it must not copy a working authorization or key.

## A public experiment record

The aggregate should state what was measured, the observation count, units, timing boundaries, model or method, input duration, and whether input was real-time, accelerated, synthetic, or replayed. State which components were reused. Record the difference between a measured expense, an unresolved reservation, and an extrapolation.

For private-session aggregates, do not include source text, individual API payloads, audio fingerprints tied to a private source, authenticated URLs, absolute machine paths, or source screenshots. Use a neutral experiment identifier and report the method without identifying a speaker or session. The approved public-video sample instead identifies its public source and includes its audio hash for reproduction; its explicitly approved demo audio and allowlisted playback records may be distributed, while raw runtime artifacts remain excluded.

Where the source material cannot be distributed, say so. Public aggregates allow readers to inspect the claim and its limits; they do not enable independent reproduction of a content-sensitive quality judgment. Future reproducible benchmarks should use newly authored, synthetic, or explicitly publishable source material.

## Corrections keep provenance

Keep raw recognition separate from proposed corrections and accepted edits. Record the reason and source for a correction. A name supplied by a user can justify that spelling; it does not confirm other missing or ambiguous speech. Generated explanations must retain source links and avoid presenting outside background knowledge as something the speaker said.
