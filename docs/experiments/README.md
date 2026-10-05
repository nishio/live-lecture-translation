# Historical experiment summaries

For a newly executed comparison in this repository, see the [2026-10-05 ASR chunk-duration and provisional-buffer experiment](asr-chunk-duration.md). It contains fresh local recognition measurements and explicitly estimated waiting times. The records below remain historical.

These records summarize development measurements made before the standalone 0.9 extraction. They contain aggregate numbers and methodology only. Source recordings, recognized speech, translations, API payloads and responses, semantic-review excerpts, screenshots, authenticated URLs, and machine-specific paths are excluded.

The measurements have not been rerun as part of writing this documentation. They describe their recorded implementations and conditions; they are not a fresh clean-install certification for this repository. The private source material is not available here, so readers cannot independently reproduce content-dependent quality judgments from this release.

Machine-readable forms: [experiments.json](experiments.json) holds conditions, limits, and metrics; [metrics.csv](metrics.csv) contains numerical rows keyed to those experiment IDs. A blank observation count means the statistic is a single run aggregate or extrapolation, not a population estimate.

## Continuous translation replay

One 330-second recording was supplied in real time. Saved recognition results from that same input were published at their recorded chunk times. Translation and understanding support made new cloud text requests with the historical `gpt-6.1-sol` configuration; the audio stayed local in this trial.

| Result | Recorded value |
| --- | ---: |
| Recognized source lines reused | 64 |
| Eligible translation targets | 60 |
| Uncertain lines excluded | 4 |
| Translation blocks | 12 |
| Missing eligible targets / duplicate targets | 0 / 0 |
| Translation / analysis requests | 6 / 4 |
| Translation request duration, median / maximum | 10.162 / 14.453 seconds |
| Usage-confirmed API cost | USD 0.1346125 |
| Unresolved cost reservation at completion | USD 0 |

Recognition publication continued while translation ran, and the stop sequence processed the final three source lines. A review against the saved recognized text did not identify a major meaning reversal in the 12 blocks. That is not an audio-ground-truth accuracy result; fixed grouping could split unfinished sentences, and recognition-derived uncertainty remained.

The request-duration statistic has six observations. It excludes the nominal 60-second scheduling wait, recognition, and browser rendering. This trial reused ASR and did not test new microphone input, new recognition latency, or six-hour operation.

An unexecuted extrapolation from two near-steady requests of each type gave USD 7.16145 for five hours and USD 8.59374 for six hours, using the historical 60-second translation and 120-second analysis settings. Transcript density, output length, retries, and other work sharing the budget can change that total. This is neither a current price quote nor a promise to finish within that amount.

## Recognition methods

One fixed 330-second input was divided into 22 consecutive 15-second sections. Each method completed one run. The local method used MLX Whisper `large-v3-turbo`; the online methods were the historical GPT-Transcribe and GPT-Live-Transcribe configurations.

Local and online file recognition processed sections independently. Streaming retained a connection, sent 100 ms audio packets, and committed every 15 seconds. This compares whole methods with different context behavior, rather than controlling every factor except the model.

| Method | Section end to final text: median / p95 | First section finalization | Confirmed API cost |
| --- | ---: | ---: | ---: |
| Local MLX Whisper | 0.827 / 0.901 s | 11.168 s | USD 0 |
| Online file recognition | 1.080 / 1.865 s | 1.69 s | USD 0.02475 |
| Online streaming recognition | 0.657 / 0.744 s | 0.72 s | USD 0.09350 |

The p95 calculation used nearest rank across all 22 sections, including the first. Streaming's first partial text arrived a median 1.058 seconds after the section began. This is the first partial text, not the final transcript or the recognition delay of each spoken word. The 15-second section wait is separate from finalization time. Streaming connection preparation, reported as 0.64 seconds, was outside its playback clock. Browser rendering, translation, and actual microphone input were not measured here.

Final text differed between methods, including differences that affected meaning. Without a human reference transcript or listening-based ground truth, neither model agreement nor a qualitative disagreement review can establish word error rate or a general accuracy ranking. These observations did not support replacing the local recognizer solely on the basis of faster partial text.

Usage-confirmed ASR cost was USD 0.11825. An earlier streaming connection stopped before audio append or commit when a requested configuration field was absent from the response. It returned no usage, so USD 0.187 remained an unresolved reservation. Confirmed expense plus that reservation was USD 0.30525. A missing configuration echo means the effective value was unconfirmed; it does not prove that the requested value was used.

The historical six-hour ASR-only extrapolations were USD 1.62 for file recognition and USD 6.12 for streaming. Translation, retries, and development work are excluded. These were not six-hour runs. Online recognition was a separate comparison experiment, not the normal microphone application's audio-transmission behavior.

## Accelerated capture storage

A child process repeated ten seconds of synthetic audio through the native-helper output path into the production capture reader, metering, PCM/WAV storage, and callbacks. No microphone was opened. Input was delivered without real-time waiting, with at least 4 GiB of spare disk space required before the run.

| Result | Recorded value |
| --- | ---: |
| Equivalent audio duration | 21,600.123 seconds |
| PCM frames | 345,601,968 |
| Raw PCM bytes | 691,203,936 |
| WAV files | 1,441 |
| Elapsed storage time | 47.130 seconds |
| Parent peak RSS at start / end | 20.2 / 40.4 MB |

File hashes, PCM hashes and slices, frame continuity, timestamps, and callback totals matched for every WAV. The final partial chunk retained 1,968 frames. The PCM format was 16 kHz mono, 16-bit; data moved in bounded blocks rather than loading all audio into memory.

The test establishes these integrity properties under accelerated synthetic load. It does not establish six hours of real microphone capture, clock stability, battery endurance, lid-closed behavior, acoustics, recognition accuracy, or cloud throughput. The reader-thread watchdog can be delayed by blocked filesystem I/O. Separately responsive status and stop handling do not prove automatic recovery from that condition.

## Recognition during cloud generation

An earlier 330-second real-time replay exercised local recognition alongside a separate cloud worker. It produced 22 sections and 64 source lines. During a cloud analysis lasting more than 30 seconds, four recognition publications occurred.

Section completion to source publication measured median 0.833 seconds, p95 0.918 seconds, and maximum 3.782 seconds. The 15-second section wait and browser rendering were separate. This supports the claim that cloud generation need not block source publication. It is a different experiment from the continuous-translation trial that reused saved recognition, and their timing figures must not be merged.

## Additional handoff records

[Development handoff measurements](development-handoff.md) preserves a separate historical pre-event audit and a partial live cost snapshot found while consolidating related development work. These supplemental records are not included in the original four-experiment JSON/CSV bundle and are not final event results.

## Costs and absent measurements

All currency values are historical USD amounts or estimates. A confirmed amount came from recorded API usage; an unresolved reservation is not a confirmed charge. The local ASR and synthetic capture trials made no paid API request for their local processing, but that does not make the whole workflow free.

Development-assistant usage and Mac electricity were not measured. The public aggregates do not provide the exact hardware model, operating-system build, every dependency version, raw per-request data, or a human reference transcript. These omissions limit reproduction and generalization. Future benchmarks should record them using shareable source material.
