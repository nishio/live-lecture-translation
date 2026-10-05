# live-lecture-translation

[English](README.md) | [日本語](README.ja.md)

A Mac application that captures English lectures and displays recognized speech, Japanese translations, key points, and concept explanations. Recording and speech recognition run on the Mac; translation and understanding support use cloud text processing.

**The goal is to augment people's ability to understand lectures in another language, in real time.** For people whose first language is not English, following an English lecture takes additional cognitive resources compared with listening in their native language. AI should help with that language burden so that more attention remains for understanding the speaker's ideas.

We design for use while listening: support should help the listener quickly grasp what is being discussed now, how the discussion has developed so far, and which concepts matter most. The measure of success is whether the listener can follow the lecture with less effort.

## What it does

- Records a selected Mac audio input and transcribes it locally with MLX Whisper.
- Displays recognized speech above its Japanese translation. Translations accumulate instead of disappearing at the next update.
- Organizes the topic, flow, key points, and important concepts into a view of the current discussion and supporting explanations.
- Lets the reader hold the view, revisit earlier points, and browse a history of concept explanations.
- Shows recording, recognition, translation, and analysis progress separately. A slow or failed cloud request does not intentionally stop recording.

Translation becomes eligible to run at nominal 60-second intervals, and understanding support at 120-second intervals. Recognition, network time, and generation add delay, so these are not guaranteed result intervals. See the [operation guide](docs/operation.md) for controls and processing details.

The dashboard is in Japanese and is designed to support English lecture listening with Japanese text. Automatic language detection and Japanese recognition are also available; Japanese source lines are not translated into Japanese.

The development version includes unreleased features. See the [changelog](CHANGELOG.md) for changes by release.

## Try it before a live lecture

Before using the application at a lecture, try an existing recording on your Mac to see how transcription, Japanese translation, and key points appear.

To use your own WAV file, follow the [recorded-audio guide](docs/audio-experiments.md). After setup, use `check` to inspect the audio file's format and duration, then `run` for transcription. The check performs no speech recognition or network requests.

Enable cloud processing to try Japanese translation and key points as well. Use the [replay viewer](docs/audio-experiments.md#保存結果を冒頭から実時間で観察する) to see the generated results on screen.

Explore the [sample output](samples/audrey-plurality-seoul-2023/README.md) to see the English transcription, Japanese translation, key points, concept explanations, and actual API cost. The source is [Audrey Tang's lecture](https://www.youtube.com/watch?v=4_tge6XJhGA), published by Code for Japan with a CC BY license notice. The generated output is uncorrected.

Audio is not included. To try the same lecture audio, follow the [acquisition and conversion instructions](docs/audio-experiments.md#単独講演の入力例).

## Start

Requirements: an Apple Silicon Mac, a microphone accessible to macOS, Python 3.12 or later, and the Swift compiler supplied with Apple's command line developer tools. Windows, Linux, and Intel Macs have not been validated.

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

`./start.command` without `--cloud` selects the local path. It does not enable continuous cloud translation. Local analysis uses `qwen3:4b`, but prior evaluation found that it did not preserve meaning adequately. Start with the cloud configuration above to use lecture translation and understanding support.

After recording, use the dashboard's stop button and wait for saving and remaining recognition, translation, and final analysis to finish. Keep the Mac powered and awake during capture. Closing the terminal is not equivalent to a completed recording session.

## Cost by lecture duration

Local recording and MLX Whisper recognition do not incur an API charge. Cloud translation and analysis do. In one [historical live observation](docs/experiments/development-handoff.md#partial-live-cost-observation), their combined usage-confirmed cost was about USD 0.63 over a 30-minute window, equivalent to about USD 1.26 per hour. That run used the historical `gpt-6.1-sol` configuration with translation every nominal 60 seconds and analysis every nominal 120 seconds.

The estimates below assume that same rate continues and are calculated from the unrounded observations.

| Lecture audio duration | Estimated cloud translation + analysis cost (USD) |
| --- | ---: |
| 10 minutes | 0.21 |
| 30 minutes | 0.63 |
| 1 hour | 1.26 |
| 90 minutes | 1.89 |
| 6 hours | 7.57 |

These rows are extrapolations from a partial historical session, not measured runs of each length or current price quotes. Audio length alone does not determine cost: model pricing, speech density, context, output length, scheduling, and retries matter. The table includes no separate allowance for startup or final processing, failed requests, unresolved reservations, or other work sharing the daily budget. Development-assistant usage and Mac electricity were not measured. Use [cloud configuration](docs/cloud-configuration.md) to set a spending limit; this estimate does not guarantee completion within that limit.

## Evidence and limits

This application is experimental.

- Recognition and translation can be wrong, especially for names and incomplete sentences. Generated key points and explanations can also contain errors.
- Uncertain recognition remains visible but is excluded from translation. A completed translation queue does not mean every spoken word was translated.
- Published short trials do not establish long microphone-session reliability, battery life, performance in adverse acoustics, or network recovery. Improvements in comprehension while listening have not been measured.
- Automatic recovery of unfinished work after the application exits is not implemented.

See [experiment records](docs/experiments/README.md) for detailed conditions and results. Translation trials using saved recognition output cannot establish fresh microphone capture performance, recognition accuracy, or speech-to-screen latency. See the [v0.9.0 release notes](docs/release-0.9.md) for that release's features and validation scope.

## Repository guide

| Location | Purpose |
| --- | --- |
| `audio-array/` | Capture, local recognition, queues, providers, dashboard, and tests |
| `docs/` | Architecture, operation, data handling, and release boundary |
| `docs/experiments/` | Public measurement summaries without source content |
| `samples/` | Transcription, translation, and key-point samples from public lectures |
| `wiki/` | Reusable engineering decisions and open development questions |
| `CHANGELOG.md` | Release-level changes |

Store recordings, transcripts, translations, and other session data in the ignored `data/` and `results/` directories; keep them out of commits. The Audrey Tang public-lecture text in `samples/` is an exception. Audio and raw runtime artifacts must remain private. See [data handling](docs/data-handling.md) for details.

## Development checks

To understand how processing works, read the [architecture](docs/architecture.md). For design rationale and proposed improvements, see the [development wiki](wiki/index.md).

With the Python environment active, run the test suite and the two JavaScript checks:

```sh
python -m unittest discover -s audio-array/tests -v
node audio-array/tests/test_lecture_ui.js
node audio-array/tests/test_lecture_demo_ui.js
```

Routine tests use synthetic data and do not start real microphone capture, model inference, or paid API requests. Swift synthetic-audio tests are opt-in.

## License

A license has not yet been selected for this release. Publication of the repository alone does not grant an open-source license.

The [Audrey sample's source attribution and CC BY notice](samples/audrey-plurality-seoul-2023/README.md#出典と帰属) apply to that source material, not to the repository's application code.
