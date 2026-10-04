# Engineering decisions

This page distills reusable lessons from development. It contains no source speech or generated lecture content. Historical measurements are in [the experiment notes](../docs/experiments/README.md); the current implementation is described in [architecture](../docs/architecture.md).

## Development ownership and runtime continuity

The standalone repository owns future Mac lecture application code and reusable engineering knowledge. Device-specific recording, retrieval, multichannel work, and reading/Vision development stay in the originating project. File new lessons into this wiki rather than maintaining parallel copies in the old wiki.

A source repository migration and an operational cutover are separate actions. Do not remove or relocate a live recording session. Once execution has stopped, preserve the private evidence and establish which stages completed, failed, or remain pending before retiring the checkout. A preserved failure need not be relabeled as completion to retire an inactive environment. Keep v0.9.0 as the extracted comparison baseline. Preserve additional work as explicit follow-ups without implying that it is already active; see [collected migration follow-ups](migration-follow-ups.md).

## Product purpose: understanding with limited attention

The purpose is to augment real-time understanding of lectures in another language. Following an English lecture in a non-native language consumes cognitive resources beyond understanding its ideas. AI support should reduce that language burden and leave attention available for the speaker. The English and Japanese READMEs state this purpose.

Evaluate the listener's effort as well as processing throughput: can they see the supporting text without opening each source ID, revisit the previously displayed point, and return to the current view without losing their place? Stable source IDs remain necessary for provenance, but the reading interface should surface their content. Distinguish a topic or question under discussion (論点) from a proposition attributed to the speaker (主張); changing a heading alone does not establish that the generated content meets the latter contract. See the [v0.9 usability feedback](migration-follow-ups.md#field-feedback-understanding-with-limited-attention) for proposed checks, which have not yet been validated in use.

## 1. Preserve evidence independently of interpretation

Audio storage, speech recognition, translation, and understanding support are independent responsibilities. A slow or failed downstream request must not withhold new source speech or intentionally stop capture. Verify input reception and confirmed storage, rather than interpreting a running process or a successful start request as evidence of a good recording.

Use frame counts, timestamp continuity, chunk boundaries, and hashes for storage integrity. Keep semantic accuracy as a separate question. A filesystem-blocked reader may also block its own watchdog; status and manual-stop behavior must be explicit about unconfirmed completion.

The same rule applies to meaning: recognized text is evidence, while a correction or generated explanation is an interpretation. Keep their provenance separate. Unrelated reference notes must not silently become claims attributed to the source speech.

## 2. Make completeness and understanding separate workloads

A rolling summary can be useful while repeatedly dropping speech outside its current window. To support continuous translation, maintain unfinished targets explicitly, process older eligible targets first, validate target order and coverage, and mark completion only after saving the result.

Understanding support has a different purpose: select a compact topic, flow, key points, and concepts. Give it a separate schedule while sharing a bounded request slot. Failure should retain pending targets and expose a retry, not quietly skip to newer speech.

Keep source exclusions visible. Complete coverage of eligible ASR lines is not complete coverage of the speaker's words. Preserve uncertainty and grouping boundaries even when a smoother-looking translation would hide them.

## 3. Design observation and validation around distinct claims

The reader's position, the latest processing position, the time of an API request, and the publication time of a result are different. Preserve the reading position as source text arrives. Replay results according to their actual publication times, including on rewind, so that later knowledge cannot leak into an earlier view.

Measure the boundaries that answer the question: chunk completion to recognition publication, first partial text, full final text, request duration, or speech to screen. Do not collapse them into one “latency” figure. A saved-ASR replay can validate queue behavior and UI publication without measuring a new recognizer.

Likewise, keep structural validity, source coverage, semantic fidelity, capture integrity, cost, and duration endurance separate. A result can succeed in one and fail in another. Model agreement is not ground truth; rapid partial text is not proof of a correct final transcript.

## Operational consequences

Changes should be developed in an isolated candidate while a working session continues. Recording and result destinations can be separate, but one shared authorization or budget needs one authoritative ledger and inference lock. A copied checkout must not multiply the allowance.

Keep usage-confirmed cost, uncertain reservations, and estimates distinct. A failure with no usage receipt does not justify erasing its reservation. A configuration file and an already loaded process must agree on limits; changing the file alone may not change supported runtime behavior.

Browser authentication also has state. Cookies can be sent to different ports on the same host. Port-specific cookie names prevent accidental overwrites, while offering no port-based secrecy guarantee. Validate actual launch paths and browser access in addition to reading source code.

## Attribute and forecast cost without changing the running session

A shared daily ledger is authoritative for admission, but its change since startup is not necessarily the cost of one lecture: another experiment may share it. Save the request keys present at session start, match the session's saved payload fingerprints to subsequent ledger entries, and retain retry entries rather than counting only the successful history. Distinguish usage-confirmed expense, unresolved reservations, and reused cached results. Use generation completion timestamps to define a recent-rate window; state the window and remaining duration alongside a forecast.

API input totals include repeated instructions and context, so they are not the number of unique words spoken. For the recorded provider's usage schema, cache-read and cache-write counts are subsets of total input. Do not add those counts to input again; apply their respective rates to their portions. Keep translation and analysis costs separate: update frequency alone does not identify the more expensive workload. See the [partial live cost observation](../docs/experiments/development-handoff.md#partial-live-cost-observation).

Admission also needs room for the next request's conservative reservation. A request can be blocked while confirmed expense is still below the configured limit. Report that condition separately from uncertain usage or an authorization error, and do not clear reservations to manufacture headroom. Compare a recent-rate projection with an explicitly described planning scenario; a chosen margin is not a statistical upper bound or proof that a session will finish within budget.

## Retire a checkout without erasing unfinished work

Process absence, successful storage, completed recognition, and completed generation are separate facts. In a preserved field run, capture reported input loss after saving about 5 hours 17 minutes; recognition reached the saved audio end, while translation and analysis reported HTTP 429 and 131 eligible source lines remained untranslated. The retirement record establishes these stored states, not a fresh operational observation or an end-to-end success. It does not establish the cause of the missing PCM input or the particular provider-side condition behind HTTP 429.

An inactive checkout can be retired after its files, failure states, pending targets, and cost reservations are verifiably preserved. Confirm absence of the relevant processes and listener as well as consulting saved status; neither alone proves full processing success. Record the unresolved stages and a recovery location before removing the working copy. Archiving does not authorize retrying failed requests or starting the replacement application. A recovery design must reconcile saved target IDs with durable results and request reservations, retain failed attempts, and validate current authorization before any new request.

## Restore application evidence separately from Git history

A Git snapshot or bundle preserves the included source history; it does not establish that ignored recordings, results, caches, or runtime state can be recovered. Inventory tracked, untracked, and ignored files, plus symbolic links and their external targets, before retirement. After stopping writes, preserve private files with a manifest and verify file count, sizes, and per-file hashes. Preserve Git history and uncommitted changes separately. Hash agreement verifies copying, not the semantic correctness or successful completion of a session.

Saving a symbolic link does not save its target. Retain or explicitly account for shared model environments, authorization state, cost ledgers, and inference locks. Use a private old-to-new path map to resolve historical references without rewriting original provenance. A restored Git checkout and a preserved evidence snapshot are not automatically a runnable environment: compare with existing files before copying, verify shared dependencies and time-limited authorization, and inspect pending work before enabling processing. Public documentation contains the method; private manifests, paths, source content, and working configuration remain private.

## Identify the source that produced a result

Repository HEAD, the files selected for extraction, and the source loaded by a running process can differ. Freeze the selected file inventory including dirty and untracked source, record hashes, and compare before and after extraction. Relate it to a session through its saved `source-at-start` files and `runtime-manifest`, where available; do not infer runtime identity from a branch name or current HEAD alone. Keep that private evidence separate from the public file allowlist. Matching hashes establishes source correspondence, not a clean installation, working microphone, correct model output, or successful cloud execution; report those validation layers separately.

## Open questions

- What fraction of eligible speech can be translated promptly under sustained high speech density?
- How should incomplete sentences be carried across groups without losing stable source coverage?
- Can name correction improve recognition without inventing plausible names?
- What restart protocol can reconcile saved results, pending work, failed requests, and cost reservations?
- Which end-to-end delays matter most while a person is simultaneously listening and reading?

Answer these with publishable fixtures or newly documented trials. Do not promote a historical short-run result into a general performance guarantee.
