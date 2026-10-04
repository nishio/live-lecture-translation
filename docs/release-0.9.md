# Version 0.9 boundary

Version 0.9 preserves the existing continuous-translation application as a standalone development baseline. Extraction changes packaging, configuration boundaries, documentation, and publication hygiene. It is not a new benchmark result.

## Preserved behavior

- Native Mac capture and local MLX Whisper recognition.
- Timestamped source utterances with IDs and uncertainty.
- Oldest-first translation of eligible pending speech; completion after successful persistence.
- Separate understanding support and one shared cloud request slot.
- Nominal 60-second translation and 120-second analysis schedules.
- Accumulated translations, source navigation, held views, and historical publication ordering.
- Explicit capture and save state, and a stop path that tracks remaining processing.
- Separate transmission-scope and cost accounting.

The historical implementation includes a local analysis path. Its structured output alone did not establish acceptable semantic quality. It is not the reference translation path for this release.

## Known limits

**Recognition and meaning.** Names, incomplete sentences, mixed languages, and noisy inputs can produce errors. An uncertainty heuristic does not catch every error. Correct source IDs and valid JSON do not prove faithful interpretation. The historical evaluations did not supply human ground-truth transcripts or a general word-error-rate result.

**Coverage.** Queue completion applies to eligible recognized utterances. Uncertain, Japanese, empty, or already translated lines are handled separately. Missing recognition cannot be recovered by a coverage check over the existing transcript. Fixed grouping boundaries can split an unfinished sentence.

**Timing.** The nominal schedules are not guaranteed result intervals. The published request times exclude chunk waiting, recognition, queueing, and browser rendering where stated. Replaying saved recognition does not measure recognition latency.

**Durability.** Short replay trials and accelerated synthetic capture support specific integrity claims. They do not establish six-hour real-time microphone operation, clock stability, thermal behavior, battery endurance, or behavior with a closed laptop lid. The input watchdog cannot guarantee automatic recovery while its own reader thread is blocked in filesystem I/O.

**Recovery.** Stopping through the dashboard and interrupting the process have different semantics. Unfinished work is not automatically resumed after the application exits. Failed work must remain distinguishable from completed work.

**Cost.** A historical estimate from a few requests is not a current price quote or a guaranteed session total. Transcript density, output length, retries, and other work sharing a budget change the total. API usage, unresolved reservations, model-download costs, development-assistant usage, and electricity are separate categories. The last two were not measured in the published development trials.

**Portability.** The runtime assumes an Apple Silicon Mac, native macOS audio, Python 3.12 or later, and the MLX ecosystem. The public extraction still needs its own installation and regression validation; success recorded in the source development environment must not be relabeled as a fresh clean-install result. No new real recording, ASR inference, or paid API trial was performed to prepare this extraction while the existing application was in use.

## Setup reference

The setup pins `mlx-community/whisper-large-v3-turbo` at revision `a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb`. The extracted environment's direct dependency pins are NumPy 2.5.3, MLX 0.32.2, MLX Whisper 0.4.3, and Hugging Face Hub 2.0.0; the source runtime used Python 3.12.9. These are packaging reference values, not a claim that the historical experiment aggregates recorded every package at those versions. Setup downloads the model when run; that download was not performed during this extraction.

## Extraction validation

The copied standalone source was checked separately from the historical experiments:

- Python suite: 317 discovered tests in 54.428 seconds; 315 passed, two Swift synthetic-audio tests skipped, no failures.
- JavaScript: `test_lecture_ui.js` and `test_lecture_demo_ui.js` passed.
- Static checks: Python AST, JSON, shell syntax, and a publication-content scan passed.
- Launcher/preflight smoke checks: 13/13 passed using fake native capture, ASR, and existing-application dependencies with the real scope/cloud modules and sockets blocked. Revoked approval, disallowed date, exhausted duration, unsupported model, exhausted budget, and missing key were rejected; authorization and ledger hashes stayed unchanged. No real application launch, server query, or API request occurred.

The Python suite ran under Python 3.14.7 with NumPy available. This validation environment differs from the source ASR runtime's Python 3.12.9 and from a freshly installed pinned environment.

No real recording, speech-model loading, paid API request, or clean setup was exercised. The skips and these omitted layers remain explicit; the test result does not establish microphone permissions, model installation, live recognition quality, cloud availability, or long-session operation on another Mac.

The standard development commands are listed in the [README](../README.md#development-checks). Startup smoke checks, when added, should state whether they used fake dependencies or a real microphone and model.

## Development after the freeze

The next changes should begin from explicit evidence and a separate experiment record. Useful directions include:

1. A fully publishable synthetic or consented replay fixture that exercises source references, unfinished sentences, names, uncertainty, and stop behavior.
2. Recording the whole speech-to-screen latency distribution, with each intermediate timestamp defined.
3. Provenance-preserving terminology and name correction, evaluated separately at the recognition and translation stages.
4. Durable restart recovery that proves which source targets were saved, completed, failed, or still pending.
5. Longer real-time capture trials and network/interruption experiments with declared hardware and conditions.

These are open development questions, not features included in 0.9.
