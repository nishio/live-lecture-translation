# Data handling and publication

## The application creates private working data

The normal microphone workflow records audio locally and runs recognition locally. Optional cloud translation and analysis send recognized text and selected transcript context. The application also preserves generated text and evidence needed to diagnose a request.

No original recording or lecture-derived text is distributed with this repository. The public measurement summaries were written separately from the original session artifacts.

## Use a publication allowlist

Publish code, newly authored documentation, synthetic fixtures, and reviewed numerical aggregates. Keep generated session directories outside that allowlist. An ignore rule helps prevent accidental staging; it does not remove data already committed or prove that other files are safe.

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

Do not include source text, individual API payloads, audio fingerprints tied to a private source, authenticated URLs, absolute machine paths, or source screenshots. Use a neutral experiment identifier and report the method without identifying a speaker or session.

Where the source material cannot be distributed, say so. Public aggregates allow readers to inspect the claim and its limits; they do not enable independent reproduction of a content-sensitive quality judgment. Future reproducible benchmarks should use newly authored, synthetic, or explicitly publishable source material.

## Corrections keep provenance

Keep raw recognition separate from proposed corrections and accepted edits. Record the reason and source for a correction. A name supplied by a user can justify that spelling; it does not confirm other missing or ambiguous speech. Generated explanations must retain source links and avoid presenting outside background knowledge as something the speaker said.
