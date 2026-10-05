# Engineering decisions

This page distills reusable lessons from development. It contains no source speech or generated lecture content. Historical measurements are in [the experiment notes](../docs/experiments/README.md); the current implementation is described in [architecture](../docs/architecture.md).

## Development ownership and runtime continuity

The standalone repository owns future Mac lecture application code and reusable engineering knowledge. Device-specific recording, retrieval, multichannel work, and reading/Vision development stay in the originating project. File new lessons into this wiki rather than maintaining parallel copies in the old wiki.

A source repository migration and an operational cutover are separate actions. Do not remove or relocate a live recording session. Once execution has stopped, preserve the private evidence and establish which stages completed, failed, or remain pending before retiring the checkout. A preserved failure need not be relabeled as completion to retire an inactive environment. Keep v0.9.0 as the extracted comparison baseline. Preserve additional work as explicit follow-ups without implying that it is already active; see [collected migration follow-ups](migration-follow-ups.md).

## Publish completed work without absorbing parallel edits

A feedback status report must distinguish implementation in the working tree, validation of a particular source tree, committed/pushed changes, a release tag, and code loaded by a running process. A wiki proposal or preserved patch is not an installed fix, and a successful push does not authorize or establish a runtime cutover. State which layer was inspected and keep quality or field-effectiveness claims separate from structural test results.

When another task is editing the same checkout, freeze the intended publication inputs in an isolated temporary checkout. Split by behavior and dependencies: supporting backend contracts before dependent UI, playback, experiments, and documentation. A file can contain both finished and active work; select individual hunks and preserve required imports, helpers and tests rather than assigning ownership by filename. Exclude unfinished concurrent features explicitly. Test the assembled publication tree and relevant intermediate commits; a passing shared working tree can depend on code absent from the proposed commit.

Before integrating the prepared commits, verify the expected branch and base commit and that the shared index contains no other task's staged work. Stop integration and reassess if any of those changed. Bring in the commit objects and advance the branch/index without replacing shared working files; in the 2026-10-05 publication, a guarded mixed reset performed this integration. This is not permission for a hard reset, cleanup of untracked files, or loss of another task's staged changes. Inspect the remaining diff, push without force, and confirm the remote matches the intended head. Publication permission does not imply restarting an application, replaying unfinished processing or touching shared ledgers.

For the six-commit publication ending at `d2970ab`, the isolated code passed 406 Python tests (404 passed, two optional native tests skipped) and both Node UI suites; focused intermediate checks also passed. The uncertainty-selection implementation and subsequent first-use documentation edits stayed in the shared working tree. Those counts certify that publication scope only, not later edits, live comprehension, recognition accuracy or network recovery. Preserve public sample allowlists and attribution while keeping audio, runtime records and credentials private. The [publication log](log.md#2026-10-05-publication--commit-the-completed-v09-feedback-improvements) records the validation boundary.

## Product purpose: understanding with limited attention

The purpose is to augment real-time understanding of lectures in another language. The Japanese wording is 「理解する力をAIで増強する」, using 増強 rather than 拡張. Following an English lecture in a non-native language consumes cognitive resources beyond understanding its ideas. AI support should reduce that language burden and leave attention available for the speaker. The English and Japanese READMEs state this purpose.

The 2026-10-05 feedback makes the primary reading task explicit: quickly grasp 「今何が話されているか」「ここまでどういう話をしてきたか」「何が重要な概念か」. Evaluate how well the listener can follow the current discussion, its development so far, and its important concepts with limited attention. Easy evidence inspection and history navigation are supporting means, not the primary measure of success. Stable source IDs remain necessary for provenance; that does not require source controls in the reading view. These are design priorities, not measured comprehension gains.

Distinguish a topic or question under discussion (論点) from a proposition attributed to the speaker (主張); changing a heading alone does not establish that the generated content meets the latter contract. See the [v0.9 usability feedback](migration-follow-ups.md#field-feedback-understanding-with-limited-attention) for proposed checks, which have not yet been validated in use.

The interface must preserve explanations long enough to be read and distinguish a claim from the background needed to understand it. Label operations by their actual effect: appending an untranslated passage, retrying failed work, and revising an existing translation are different. Model knowledge and retrieved background can support understanding, but must not become evidence attributed to the speaker. See the [understanding-support proposal](understanding-support.md) for the focus task, concept history, research workflow, and interview-use memo.

## Write the README for a new reader

The README must make sense without knowledge of the originating project or development conversations. Start with what the application does and why someone would use it, then describe available features, sample output, setup, costs, and practical limits. Keep the English and Japanese versions aligned. Repository extraction, canonical development ownership, and runtime migration belong in development and migration documents; they do not explain the product to a first-time reader.

Describe available behavior directly instead of narrating edits such as “we added” or “we renamed.” Put proposed designs in the wiki, release differences in the changelog, and detailed historical validation in experiment or release documents. Link to those records where useful. Retain a concise development-version notice, known limitations, the distinction between measured and estimated cost, and sample attribution. Simplifying the README must not present proposals as implemented features, old test results as current validation, or unmeasured comprehension benefits as proven outcomes.

Removing migration history is not enough: “we documented the attribution” and “we distinguish the trials” still report the author's work. State the source, what the reader can inspect or do next, and what a result does or does not establish. For example, give the speaker, publisher, source link, and license notice directly instead of saying they were included. Review each sentence for information that helps a reader understand or use the application; do not reduce this to a list of banned verbs. Use concrete descriptions such as original speech above Japanese translation, call the output simply 「日本語訳」, and show README dollar amounts to two decimal places while retaining unrounded measurements for calculations.

## Live support and later review have different objectives

Live understanding should minimize useful-output delay and reading effort using evidence already available. Later review can use the completed recording to revisit uncertain passages, establish speaker attribution, and reconstruct relationships across sections. Evaluate these separately: faster live output does not prove accurate review, and a better offline explanation does not prove it could have helped at the time.

Preserve the live record and make post-lecture corrections explicit revisions linked to saved audio and earlier source IDs. Propagate accepted revisions to dependent translations and analyses without erasing prior outputs. Before choosing chunk overlap, new models, or a billing provider, measure their effect on the relevant comprehension task. See the [live and review pipeline proposal](live-and-review-pipeline.md).

## 1. Preserve evidence independently of interpretation

Audio storage, speech recognition, translation, and understanding support are independent responsibilities. A slow or failed downstream request must not withhold new source speech or intentionally stop capture. Verify input reception and confirmed storage, rather than interpreting a running process or a successful start request as evidence of a good recording.

Operation notifications must follow the same evidence rules as the status panels. A successful start response or an active lifecycle state does not establish that fresh audio arrived; a terminal capture state does not establish that saving succeeded. Confirm recording only with fresh reception evidence, and confirm saved completion only with the explicit completed state. Starting, stalled input, failed saving, and unknown state need distinct messages. The preserved notification patch remains an adoption candidate, as described in [migration follow-ups](migration-follow-ups.md).

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

## Distinguish reachability, request recovery, and catching up

A successful connection check establishes only the layer it tested. Separate DNS/TCP/TLS failures, HTTP/provider errors, response validation, and local scope or budget blocks before deciding whether to retry. An HTTP 429 alone is insufficient to distinguish temporary throttling from quota or spending limits. Reconnection should not require repeated listener attention for recoverable failures, but retries need bounded attempts and time, visible state, current authorization, and preserved uncertain cost reservations.

The first authorized public-audio replay in this repository observed the explicit provider code `insufficient_quota` for both cloud workloads while local ASR continued to completion. This establishes a quota/balance blocker for those two requests, not the cause of earlier field failures. Do not retry this class as a network outage, substitute generated-looking fixture content, or label a missing usage response as a zero-cost successful generation. Preserve the actual recognition, failed targets and cost reservations until the account condition can be resolved.

Local ASR decouples audio capture from cloud recovery: recognized pending text can be processed later without retransmitting audio. This avoids repeating recognition, but does not establish fast catch-up. Measure completed eligible targets against newly arriving targets, oldest pending age, and time to drain. Preserve evidence and translation coverage while balancing recent understanding support with backlog processing. Same-process recovery, a new analysis of recent speech, and durable restart recovery are distinct capabilities. See the [network and recovery feedback](migration-follow-ups.md#field-feedback-network-failures-and-recovery) for current limitations and proposed checks.

The post-retrospective implementation freezes failed request input across bounded retries and offline rechecks, reserves each admitted retry separately, and permits pausing the next attempt. Dashboard countdowns expose coordinator eligibility rather than estimating generation completion. A visible zero must transition to an honest waiting/busy state; reconnecting or selecting a historical view must not invent successful work or rewrite the displayed evidence. See the current [schedule and recovery contract](../docs/architecture.md#observable-schedules-and-recovery).

## Retire a checkout without erasing unfinished work

Process absence, successful storage, completed recognition, and completed generation are separate facts. In a preserved field run, capture reported input loss after saving about 5 hours 17 minutes; recognition reached the saved audio end, while translation and analysis reported HTTP 429 and 131 eligible source lines remained untranslated. The retirement record establishes these stored states, not a fresh operational observation or an end-to-end success. It does not establish the cause of the missing PCM input or the particular provider-side condition behind HTTP 429.

An inactive checkout can be retired after its files, failure states, pending targets, and cost reservations are verifiably preserved. Confirm absence of the relevant processes and listener as well as consulting saved status; neither alone proves full processing success. Record the unresolved stages and a recovery location before removing the working copy. Archiving does not authorize retrying failed requests or starting the replacement application. A recovery design must reconcile saved target IDs with durable results and request reservations, retain failed attempts, and validate current authorization before any new request.

## Restore application evidence separately from Git history

A Git snapshot or bundle preserves the included source history; it does not establish that ignored recordings, results, caches, or runtime state can be recovered. Inventory tracked, untracked, and ignored files, plus symbolic links and their external targets, before retirement. After stopping writes, preserve private files with a manifest and verify file count, sizes, and per-file hashes. Preserve Git history and uncommitted changes separately. Hash agreement verifies copying, not the semantic correctness or successful completion of a session.

Saving a symbolic link does not save its target. Retain or explicitly account for shared model environments, authorization state, cost ledgers, and inference locks. Use a private old-to-new path map to resolve historical references without rewriting original provenance. A restored Git checkout and a preserved evidence snapshot are not automatically a runnable environment: compare with existing files before copying, verify shared dependencies and time-limited authorization, and inspect pending work before enabling processing. Public documentation contains the method; private manifests, paths, source content, and working configuration remain private.

## Identify the source that produced a result

Repository HEAD, the files selected for extraction, and the source loaded by a running process can differ. Freeze the selected file inventory including dirty and untracked source, record hashes, and compare before and after extraction. Relate it to a session through its saved `source-at-start` files and `runtime-manifest`, where available; do not infer runtime identity from a branch name or current HEAD alone. Keep that private evidence separate from the public file allowlist. Matching hashes establishes source correspondence, not a clean installation, working microphone, correct model output, or successful cloud execution; report those validation layers separately.

## Make the first recorded-audio experiment a complete path

A first-use guide must connect the user's file to the final reading screen: where to put it, how to select its length, what an input check establishes, whether to run recognition alone or cloud translation too, how to recognize completion, and how to open that exact run. Format conversion belongs inside the application, not in a prerequisite shell recipe. Put public sample acquisition and comparative experiments after this path. Keep key files and authorization examples private by default, and show the complete replay-only configuration rather than sending the reader back to microphone startup instructions.

Retain one exact prepared audio file for both recognition and playback, including prefix runs. The experiment runner now accepts MP3, M4A, video and noncanonical WAV directly; `--seconds` selects the prefix internally. Preserve the original separately, record both hashes and conversion settings, and use the canonical input path/hash for cloud authorization. A check can temporarily decode without persistent writes or inference; a run retains the verified result under ignored storage after preflight. Do not overwrite a corrupted existing import or widen authorization to make a conversion succeed. A successful decode cannot establish the completeness of an unknown recording.

Read the viewer's session path and prepared audio path from the chosen run's manifest rather than guessing the latest directory or passing the original full-length recording. Explain that the current runner is silent until completion and that ASR-only output has no Japanese translation or understanding notes. The low-level replay and standalone ASR probe can retain strict WAV contracts while the user-facing import handles format differences.

Validate conversion, processing and playback as one route. A correctly shortened WAV is not sufficient if a later component still opens the original full-length recording. Exercise the manifest-to-viewer path with synthetic recognition, verify exact duration and unchanged originals, and distinguish temporary check output from retained run output. Keep conversion parameters and decoder/implementation identity alongside input hashes; do not assume different decoder versions produce identical samples.

Validate copyable commands without starting inference or paid APIs. Test the selected publication tree separately when other agents have unfinished changes in the shared checkout: a passing development tree does not establish that the smaller commit has all its dependencies. Separate these checks from a clean installation, real recognition quality and account-specific cloud success. See the [recorded-audio walkthrough](../docs/audio-experiments.md).

## Open questions

- What fraction of eligible speech can be translated promptly under sustained high speech density?
- How should incomplete sentences be carried across groups without losing stable source coverage?
- Can name correction improve recognition without inventing plausible names?
- What restart protocol can reconcile saved results, pending work, failed requests, and cost reservations?
- Which end-to-end delays matter most while a person is simultaneously listening and reading?

Answer these with publishable fixtures or newly documented trials. Do not promote a historical short-run result into a general performance guarantee.
