# live-lecture-translation

[English](README.md) | [日本語](README.ja.md)

A Mac application for reading transcriptions, Japanese translations, key points, and concept explanations while listening to English lectures. You can use a microphone or an existing recording.

**The goal is to augment people's ability to understand lectures in another language, in real time.** For people whose first language is not English, following an English lecture takes additional cognitive resources compared with listening in their native language. AI should help with that language burden so that more attention remains for understanding the speaker's ideas.

The aim is to help you follow the lecture with less effort: quickly grasp what is being discussed now, how the discussion has developed so far, and which concepts matter most.

## What it does

- Records a selected Mac audio input and transcribes it locally with MLX Whisper.
- Displays recognized speech above its Japanese translation. Translations accumulate instead of disappearing at the next update.
- Organizes the topic, flow, key points, and important concepts into a view of the current discussion and supporting explanations.
- Lets the reader hold the view, revisit earlier points, and browse a history of concept explanations.
- Shows recording, recognition, translation, and analysis progress separately. A slow or failed cloud request does not intentionally stop recording.

Transcription data is saved locally. If a coding agent such as Codex can access those files, you can ask questions about the lecture in the agent's chat, such as “Summarize the discussion so far” or “How did the speaker explain this concept?” Specify the transcript file or its saved location when asking.

Results take time to appear because speech recognition, network requests, and generation all add delay. Translation normally runs at roughly 60-second intervals, and key points and concept explanations at 120-second intervals; updates may take longer. See the [operation guide](docs/operation.md) for controls and processing details.

The dashboard is in Japanese and is designed to support English lecture listening with Japanese text. Automatic language detection and Japanese recognition are also available; Japanese source lines are not translated into Japanese.

Version **1.0.0-rc.1** is a release candidate for Apple Silicon Macs. See the [release notes and validation status](docs/release-1.0.md) before using it at a lecture, and the [changelog](CHANGELOG.md) for changes since v0.9.0.

## Requirements and setup

To run speech recognition, you need an Apple Silicon Mac, Python 3.12 or later, and the Swift compiler included in Apple's Command Line Tools. Windows, Linux, and Intel Macs have not been validated. A microphone is needed only for live recording.

Recording and transcription run on your Mac and do not require an API key. Japanese translation, key points, and concept explanations require **an OpenAI API key and paid API access**. Audio stays on the Mac; recognized text is sent to the cloud. Review the [cost estimates](#cost-by-lecture-duration) and [data handling](docs/data-handling.md) before starting.

Download or clone the repository, then run the initial setup from its directory. Setup needs an internet connection to download dependencies and the speech recognition model.

```sh
./setup.command
```

## Try it before a live lecture

Before using the application at a lecture, try an existing recording on your Mac to see how transcription, Japanese translation, and key points appear.

Follow the [recorded-audio guide](docs/audio-experiments.md), starting with the first two minutes of an English recording. You can use MP3, M4A, WAV, or a video with audio. Choose transcription alone or add Japanese translation and key points through the paid API, then view the results with audio playback. No microphone is needed.

Explore the [sample output](samples/audrey-plurality-seoul-2023/README.md) to see the English transcription, Japanese translation, key points, concept explanations, and actual API cost. The source is [Audrey Tang's lecture](https://www.youtube.com/watch?v=4_tge6XJhGA), published by Code for Japan with a CC BY license notice. Reading the sample requires no setup or API use. The generated output is uncorrected.

Audio is not included. To try the same lecture audio, follow the [acquisition and conversion instructions](docs/audio-experiments.md#単独講演の入力例).

## Use with a microphone

Complete setup and connect a microphone available to macOS. The steps below are for microphone recording with cloud translation. For existing recordings, use the recorded-audio guide above.

First, configure what text may be sent and how much the application may spend.

```sh
cp config/authorization.example.json config/authorization.json
```

In your copy, set the date of use, daily USD limit, and total duration of microphone audio whose transcript may be sent to the cloud, up to six hours. The example disables authorization and sets a zero budget, so it cannot start cloud processing unchanged. Set `human_approved` to `true` after reviewing your settings. See [cloud configuration](docs/cloud-configuration.md) for the date fields and API-key setup.

With your API key set in the `OPENAI_API_KEY` environment variable, run the following commands. To store the key in a file, use the guide's `--key-file` option instead.

```sh
./start.command --check --cloud --authorization config/authorization.json
./start.command --cloud --authorization config/authorization.json
```

The first command checks startup prerequisites; the second opens the dashboard. Select your audio input and press the recording start button. Allow microphone access when macOS requests it.

Use the cloud configuration above for continuous translation. The local analysis available without `--cloud` did not preserve meaning adequately in prior evaluation.

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

These are estimates from a past observation, not measured runs of each length or current prices. Cost varies with the model, speech density, context, output length, generation intervals, and retries. The table includes no extra allowance for startup or final processing, failed requests, requests with unconfirmed costs, or other work sharing the budget.

You can set a spending limit in [cloud configuration](docs/cloud-configuration.md), but the estimate does not guarantee completion within that limit. Development-assistant usage and Mac electricity were not measured.

## Evidence and limits

This application is experimental.

- Recognition and translation can be wrong, especially for names and incomplete sentences. Generated key points and explanations can also contain errors.
- Source text may be translated even when recognition is uncertain; a translation does not establish that the speech was heard correctly. Some fillers and repeated text are omitted from translation, while the original text and exclusion reasons remain saved.
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

Except where otherwise noted, this project is licensed under the **GNU Affero General Public License, version 3 only (AGPL-3.0-only)**. See [LICENSE](LICENSE) for the full terms.

The [Audrey sample's source attribution and CC BY notice](samples/audrey-plurality-seoul-2023/README.md#出典と帰属) apply to that source material, not to the repository's application code.
