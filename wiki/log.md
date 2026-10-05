# Wiki log

## [2026-10-04] file back | Standalone 0.9 engineering baseline

Created [engineering decisions](engineering-decisions.md) as a new distillation for the standalone repository. Preserved three reusable themes: capture evidence independently of interpretation, separate complete translation from selective understanding support, and validate distinct timing and quality claims separately. Added the operational consequences for shared accounting, process state, and browser authentication.

Added links to the architecture, release boundary, and content-free historical experiment summaries. Existing private wiki pages and session artifacts were not copied. Markdown is the authority; no database-backed wiki migration was introduced. This documentation work performed no new capture, ASR inference, or API experiment.

## [2026-10-04] file back | Canonical development home and collected follow-ups

Made this repository the canonical home for future Mac lecture application code, experiments, feedback, and implementation knowledge. Distinguished development ownership from switching a running session and from device-specific work retained by the originating project. Recorded the policy in AGENTS.md, README.md, and the engineering decisions.

Added [migration follow-ups](migration-follow-ups.md): existing notification-state fixes and ASR comparison research code preserved as unapplied patches, plus lessons about partial/final provenance, missing confidence, confirmed settings, causal timing, and uncertain cost. Added selected [historical handoff measurements](../docs/experiments/development-handoff.md) with their conditions and limits. Updated the wiki index; private artifacts and the active application were not moved. The v0.9.0 baseline remains unchanged.

## [2026-10-04] file back | Live cost attribution and proper-name feedback

Checked the existing partial live-cost aggregate against its private numerical snapshot; the aggregate and end-of-event projections were already collected, so no duplicate experiment was created. Expanded the existing engineering decisions with session-to-ledger attribution, cache-token subset accounting, and next-request reservation headroom. Added the historical model label, cache counts, and translation/analysis breakdown to the existing handoff measurements, preserving their partial-run and forecast limits.

Recorded the qualitative proper-name feedback as an unapplied follow-up: the error stage and correct spellings remain unverified, and source recognition must remain distinct from proposed corrections. Linked the additions from the index and clarified reservation admission in the cloud configuration guide. This documents future investigation, not a change to recognition or runtime settings.

Markdown remains authoritative. No private source content, identifying request hashes, session IDs, machine paths, personal authorization, or ledgers were copied. No capture, inference, application API call, or active-runtime change was performed. Additional application API cost was USD 0; development-assistant usage and electricity were not measured. The v0.9.0 baseline tag is unchanged.

## [2026-10-04] file back | v0.9 experience feedback and product purpose

Added the project's purpose to the English and Japanese READMEs: augment real-time understanding of lectures in another language, reducing the additional cognitive burden so that the listener can attend to the ideas. Recorded the attention constraint as an [engineering principle](engineering-decisions.md#product-purpose-understanding-with-limited-attention).

Extended the existing [field-feedback follow-ups](migration-follow-ups.md#field-feedback-proper-name-recognition) with the user-supplied proper-name examples, a user-facing audio-file experiment path, cost visibility, readable source excerpts, direct access to previously displayed points, and the open distinction between 論点 and 主張. Kept these reports and proposed acceptance criteria separate from measured quality and implemented behavior. Source inspection found developer replay hooks and a collapsed history; the user-facing improvements remain future work.

Added duration/cost tables to both READMEs by extrapolating the already published partial live observation (USD 0.630875 per 30 minutes, or USD 1.26175 per hour). This is a historical illustration with explicit assumptions and exclusions, not a new experiment, current price quote, in-app estimate, or spending guarantee.

Updated topic pages, then the index and this log. Used direct Markdown edits under the repository's explicit Markdown-authority policy; no grasp adoption or database migration was performed. Preserved existing edits. No session artifacts were copied, no runtime or application code was changed, and no capture, inference, or application API request was performed. Additional application API cost was USD 0; development-assistant usage and electricity were not measured. The v0.9.0 baseline tag is unchanged.

## [2026-10-04] file back | Checkout retirement, incomplete processing, and source identity

Compared the current wiki with the later private retirement receipt, stored stage status, and extraction records. Added three missing operational lessons to the existing engineering decisions: an inactive environment can be retired while preserving unsuccessful processing; Git recovery and private runtime-data recovery are separate; and repository HEAD, extracted files, and loaded runtime source need explicit correspondence checks.

Recorded the content-free failure observation without claiming its root cause or a successful long-session run. Updated stale migration wording to reflect the preserved, archived source worktree. The new environment was not set up by that retirement. Aligned AGENTS.md and the operation guide with the distinction between preserving a live session and retiring an inactive, fully preserved checkout. Updated the topic pages, index, then this log.

Existing unrelated edits were retained. Direct Markdown editing follows the repository's explicit authority; no database-backed wiki migration was performed. No private source text, paths, hashes, credentials, manifests, or ledgers were copied into these additions. No application code change, capture, model inference, request retry, or runtime action was performed. Additional application API cost was USD 0; development-assistant usage and electricity were not measured.

## [2026-10-05] experiment | Short recognition and a revisable live buffer

The user explicitly requested recognition at different lengths and clarified that quick text may remain provisional: translation can re-recognize longer audio later. Added an offline probe using the existing local recognizer and inference lock, with new ignored outputs, separate warmup, exact tail coverage, reversed repeat order, causal schedule estimates, and explicit completed/failed/pending states. A rolling option distinguishes the trailing recognition window from new audio coverage and stores provisional snapshots without concatenating them into a false transcript.

Ran the complete authorized Audrey recording (325.567 seconds, matching the published input hash) twice at each of 3/5/10/15/30 seconds, then twice with a 3-second refresh over the previous 15 seconds. All 12 arms completed. On this M1 Pro, independent 3-second recognition took a median 0.654 seconds per call; 15-second recognition took 0.771 seconds; rolling snapshots took 0.777 seconds. Estimated mean audio waits were about 2.15, 8.24 and 2.29 seconds respectively, excluding startup, external lock wait, live coordination and browser rendering. Startup was recorded separately, and other desktop work was not isolated. All fixed-condition repeat texts and all 109 matched rolling snapshot texts agreed after normalization.

Text review found both boundary failures in short independent chunks and a repeated-text burst in the 15-second baseline. Some negation-bearing lines were excluded by current uncertainty rules, so preserving source text did not ensure its meaning reached translation input. Rolling snapshots revised selected failures after 3–6 more seconds of audio, but also showed provisional regressions. Chose 3-second refresh with a revisable 15-second buffer as the next live implementation candidate, with separate translation-time evidence finalization. Caption disagreement is explicitly not WER or verified accuracy; no listening-based ground truth, generated Japanese comparison, or comprehension improvement was measured. Live buffer reconciliation and translation-time re-recognition remain unimplemented, and the running application's defaults were not changed.

Published only [aggregate measurements and method](../docs/experiments/asr-chunk-duration.md), plus machine-readable numbers. Raw audio, recognition, captions, snapshots and text comparisons remain ignored. Updated the pipeline topic, experiment guide, architecture, changelog and experiment links, then the wiki index and this log. Final CPU-only Python validation ran 380 tests successfully (378 passed, two optional native tests skipped); both Node UI checks passed. Additional application API cost was USD 0; development-assistant usage and electricity were unmeasured. No new microphone capture, cloud request, runtime cutover, commit, push or modification to another checkout was performed.

## [2026-10-05] file back | Publish the ASR comparison independently

Filed the short-update and revisable-buffer decision into [provisional ASR](provisional-asr.md), with measured processing time kept separate from simulated waiting and unverified recognition quality. Published the probe guide and aggregate experiment record without audio, captions or recognition text. The repository's Markdown authority takes precedence over database-backed skill defaults; no wiki migration was performed.

Isolated only this experiment's files and additions from the pre-existing working-tree changes. Removed the probe's dependency on the uncommitted general experiment runner by including its standard-library input validation and atomic save helpers; the existing recognizer is unchanged. Preserved the measurement-time source snapshots and did not repeat model inference. The exact staged publication tree passed 330 CPU tests (328 passed, two opt-in native tests skipped), both Node UI checks, local documentation-link checks and whitespace validation. Prior development changes remain outside this commit. Additional application API cost was USD 0; development-assistant usage and electricity were unmeasured.

## [2026-10-05] file back | Preserve meaningful uncertain recognition

After the ASR experiment was committed and pushed, recorded the user's proposal to filter identifiable noise while passing other uncertain text and its reasons to translation. Current code excludes uncertain targets but may include them as context; normalization drops detailed reasons. Kept that current behavior distinct from the proposed policy. The 15-second trial contained five timestamp-only uncertain lines with 79 normalized tokens, illustrating why time uncertainty is not itself proof of unusable text.

Updated [provisional ASR](provisional-asr.md), then the index and this log. Do not use length, a blanket `so` stopword, or a compression-ratio flag as sufficient evidence to discard meaning. Preserve original evidence and exclusion reasons, distinguish timing from textual uncertainty, and align planning, coverage and completion before any runtime adoption. This follow-up changes documentation only; no new recognition, filtering, translation API call or live behavior change occurred. Local links and whitespace were checked. Additional application API cost was USD 0; development-assistant usage and electricity were unmeasured.
