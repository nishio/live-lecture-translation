# live-lecture-translation

Live English-to-Japanese lecture translation and notes from a Mac microphone. Audio capture and speech recognition run locally; optional cloud text processing builds continuous Japanese translations, key points, and explanations linked to the recognized speech.

**Version 0.9 is a standalone extraction of an existing application.** It preserves the continuous-translation design while separating installation, configuration, and development from the original project. Historical experiment summaries are included; recordings, transcripts, translations, API responses, and screenshots from those experiments are not distributed.

This is the canonical development home for the Mac lecture application. Future code changes, experiments, field feedback, and reusable lessons belong here; start with the [development wiki](wiki/index.md). The original device-specific project remains separate. Moving development here does not switch an already running recording session.

## What it does

- Records a selected Mac audio input as 16 kHz mono PCM and split WAV files.
- Recognizes speech locally with MLX Whisper. The reference model is `large-v3-turbo`; the application reads a local model manifest.
- Queues eligible untranslated speech in order, with a nominal translation interval of 60 seconds. Successful translations accumulate instead of disappearing at the next update.
- Updates the topic, flow, key points, and concepts on a separate nominal 120-second schedule.
- Links generated content to source utterances. An uncertain recognition remains visible and is excluded from translation input.
- Keeps capture, recognition, and cloud generation independent. A slow or failed text request does not intentionally stop audio capture.
- Shows recent processing progress while allowing the reader to hold a view or inspect an earlier result.

The dashboard is currently in Japanese. English speech with Japanese translation is the primary workflow; automatic and Japanese recognition are also available. Japanese source lines are not queued for Japanese translation.

## Start

Requirements: an Apple Silicon Mac, a microphone accessible to macOS, Python 3.12 or later, and the Swift compiler supplied with Apple's command line developer tools. MLX Whisper is the local recognition backend. This release has no Windows, Linux, or Intel Mac support claim.

From the repository directory:

```sh
./setup.command
cp config/authorization.example.json config/authorization.json
```

Setup prepares the local runtime and downloads the pinned recognition model. Review and edit your copy of the authorization file before enabling cloud processing: the shipped example has approval disabled and a zero budget. Set the allowed date, daily USD limit, and permitted text duration of at most six hours; set approval to true only after reviewing those settings. Supply your API key through `OPENAI_API_KEY` or the explicit `--key-file` option. See [cloud configuration](docs/cloud-configuration.md).

```sh
./start.command --check --cloud --authorization config/authorization.json
./start.command --cloud --authorization config/authorization.json
```

The check does not start recording. The application opens a local dashboard on port 8776; opening it also does not begin recording. Allow microphone access when macOS requests it, select the input, review readiness, and press the recording start button.

Audio remains on the Mac in the normal microphone workflow; recognized text and the context selected from it are sent for cloud translation and analysis. See [operation](docs/operation.md) for the lifecycle and [data handling](docs/data-handling.md) before sharing generated files.

`./start.command` without `--cloud` selects the local path. It does not enable continuous cloud translation. The retained local analysis path uses `qwen3:4b`; its historical semantic quality was not accepted, so it is not presented as equivalent to the cloud workflow.

After recording, use the dashboard's stop button and wait for saving and remaining recognition, translation, and final analysis to finish. Keep the Mac powered and awake during capture. Closing the terminal is not equivalent to a completed recording session.

## How the pipeline fits together

```text
Mac microphone
  -> native Swift capture -> local PCM/WAV + frame ledger
  -> local MLX Whisper    -> timestamped source utterances
  -> translation queue   -> accumulated Japanese translation
  -> analysis scheduler  -> topic, flow, key points, concepts
                              |
                         local web dashboard
```

Capture is independent of recognition and generation. Translation and analysis share one cloud request slot. Their nominal schedules describe eligibility to run, not a promise that a new result appears every 60 or 120 seconds. Chunk completion, queued work, network time, and generation add delay.

See the [architecture](docs/architecture.md) and the [development wiki](wiki/index.md) for the decisions behind this design.

## Evidence and limits

[Experiments](docs/experiments/README.md) contains newly written summaries and content-free JSON/CSV aggregates from historical development measurements. For example, a 330-second continuous-translation trial covered all 60 eligible source lines in 12 blocks, with no duplicate or missing eligible lines. Median translation request duration was 10.162 seconds across six requests. **That trial reused saved recognition output:** it did not measure new microphone capture, recognition accuracy, or speech-to-screen latency.

Version 0.9 is an experimental release:

- Long real-time microphone runs, battery life, adverse acoustics, and network recovery are not established by the published short or accelerated trials.
- Recognition and translation can be wrong, especially for names and incomplete sentences. Structural checks and source references do not prove semantic accuracy.
- Automatic recovery of unfinished work after the application exits is not implemented.
- Recognition uncertainty can exclude source lines from translation. Queue coverage describes eligible recognized lines, not every spoken word.
- Local analysis code is retained, but its historical structured-output success did not establish adequate meaning preservation. The reference translation workflow uses cloud text processing.
- Cloud costs depend on transcript density, context, output length, model, and retries. Published cost extrapolations are historical estimates, not current pricing or spending guarantees.

See [known limitations and the 0.9 boundary](docs/release-0.9.md).

## Repository guide

| Location | Purpose |
| --- | --- |
| `audio-array/` | Capture, local recognition, queues, providers, dashboard, and tests |
| `docs/` | Architecture, operation, data handling, and release boundary |
| `docs/experiments/` | Public measurement summaries without source content |
| `wiki/` | Reusable engineering decisions and open development questions |
| `CHANGELOG.md` | Release-level changes |

Recordings and generated session files are private working data. Keep them out of commits; a file being JSON, a log, a manifest, or a screenshot does not make it safe to publish. Public demos and regression fixtures should use newly authored or otherwise publishable material.

## Development checks

With the Python environment active, run the test suite and the two JavaScript checks:

```sh
python -m unittest discover -s audio-array/tests -v
node audio-array/tests/test_lecture_ui.js
node audio-array/tests/test_lecture_demo_ui.js
```

The extracted 0.9 source passed 315 Python tests; two Swift synthetic-audio tests were skipped, with no failures. Both Node checks passed. This run did not record audio, perform speech inference, send an API request, or test a clean installation. See [release validation](docs/release-0.9.md#extraction-validation) for the exact scope.

## License

A license has not yet been selected for this release. Publication of the repository alone does not grant an open-source license.
