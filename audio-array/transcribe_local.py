#!/usr/bin/env python3
"""Offline ASR probe on one recovered mono WAV. No playback, recording, or uploads.

Pause splitting is experimental and uses whole-file statistics, not a live VAD.
All samples remain in the chunks; the original recording is never modified.
"""
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import tempfile
import time
import wave

import numpy as np
from local_inference import inference_slot


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_mono(path):
    with wave.open(str(path)) as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth(), stream.getcomptype()) != (16000, 1, 2, "NONE"):
            raise ValueError("input must be 16kHz mono PCM16 WAV; use prepare_session.py first")
        count = stream.getnframes()
        if not 0 < count <= 60 * 16000:
            raise ValueError("probe requires a nonempty WAV of at most 60 seconds")
        data = stream.readframes(count)
        if len(data) != count * 2:
            raise ValueError("truncated WAV")
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768


def pause_chunks(audio, threshold_dbfs=None, minimum_pause=.5, minimum_chunk=2.):
    if (not math.isfinite(minimum_pause) or minimum_pause <= 0 or
            not math.isfinite(minimum_chunk) or minimum_chunk <= 0):
        raise ValueError("pause and chunk durations must be positive finite values")
    if not len(audio):
        raise ValueError("empty audio")
    width = 320  # 20ms at 16kHz
    power = np.array([np.mean(audio[start:start + width].astype(np.float64) ** 2)
                      for start in range(0, len(audio), width)])
    db = 10 * np.log10(np.maximum(power, 1e-20))
    threshold = float(np.percentile(db, 10) + 6) if threshold_dbfs is None else threshold_dbfs
    if not math.isfinite(threshold):
        raise ValueError("threshold must be finite")
    quiet = db < threshold
    edges = np.diff(np.r_[False, quiet, False].astype(int))
    candidates = []
    cuts = [0]
    for first, last in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        start, end = int(first * width), min(int(last * width), len(audio))
        # Leading/trailing silence does not define an internal language boundary.
        if start == 0 or end == len(audio) or end - start < minimum_pause * 16000:
            continue
        midpoint = (start + end) // 2
        if midpoint - cuts[-1] >= minimum_chunk * 16000 and len(audio) - midpoint >= minimum_chunk * 16000:
            cuts.append(midpoint)
            candidates.append({"quiet_start_frame": start, "quiet_end_frame": end, "cut_frame": midpoint})
    cuts.append(len(audio))
    return list(zip(cuts, cuts[1:])), {"threshold_dbfs": threshold, "minimum_pause_seconds": minimum_pause,
        "minimum_chunk_seconds": minimum_chunk, "candidates": candidates,
        "method": "20ms RMS, whole-file 10th percentile +6dB unless explicitly overridden; no speech classifier"}


def transcribe_file(source, model_metadata, out, mode="whole", threshold_dbfs=None, language=None):
    source, out = Path(source).resolve(), Path(out).resolve()
    if out.exists():
        raise ValueError("output already exists; choose a new path")
    audio = read_mono(source)
    if mode not in ("whole", "pauses"):
        raise ValueError("mode must be whole or pauses")
    model = json.loads(Path(model_metadata).read_text())
    model_path = Path(model["local_path"]).resolve()
    weights = model_path / "weights.safetensors"
    if not weights.is_file():
        weights = model_path / "weights.npz"
    if not weights.is_file() or not (model_path / "config.json").is_file():
        raise ValueError("model files missing; explicitly download the pinned model before running")
    # Passing an existing local directory and disabling hub access prevents implicit downloads.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    boundaries, detector = ([(0, len(audio))], None) if mode == "whole" else pause_chunks(audio, threshold_dbfs)
    source_hash = sha256(source)
    report = {"schema_version": 1, "source": str(source), "source_sha256": source_hash,
              "sample_rate": 16000, "frames": len(audio), "audio_seconds": len(audio) / 16000,
              "model": model, "model_file_sha256": {p.name: sha256(p) for p in (weights, model_path / "config.json")},
              "packages": {name: importlib.metadata.version(name) for name in ("mlx-whisper", "mlx", "numpy")},
              "mode": mode, "pause_detector": detector,
              "decode_options": {"task": "transcribe", "language": language, "temperature": 0.,
                                 "condition_on_previous_text": False, "initial_prompt": None},
              "chunks": [],
              "limits": ["offline batch probe, not live latency", "auto language detection may drop another language",
                         "pause thresholds are not validated on event noise", "no diarization or source separation",
                         "chunk timing is exact; model segment timestamps are estimates"]}
    waiting = time.monotonic()
    with inference_slot('asr'):
        report['inference_queue_wait_seconds'] = time.monotonic() - waiting
        # Import may initialize Metal; retain the same lease through every decode.
        import mlx_whisper
        for index, (start, end) in enumerate(boundaries):
            before = time.monotonic()
            result = mlx_whisper.transcribe(audio[start:end], path_or_hf_repo=str(model_path), verbose=None,
                                           **report["decode_options"])
            elapsed = time.monotonic() - before
            report["chunks"].append({"index": index, "source_start_frame": start, "source_end_frame": end,
                                     "source_start_seconds": start / 16000, "source_end_seconds": end / 16000,
                                     "inference_seconds": elapsed, "raw_result": result})
    report["inference_seconds"] = sum(c["inference_seconds"] for c in report["chunks"])
    report["real_time_factor"] = report["inference_seconds"] / report["audio_seconds"]
    report["text"] = "\n".join(c["raw_result"]["text"].strip() for c in report["chunks"])
    if sha256(source) != source_hash:
        raise ValueError("input changed during transcription")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".asr-") as temporary:
        candidate = Path(temporary) / "result.json"
        candidate.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        # No overwrite, even if another process created the destination during inference.
        os.link(candidate, out)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--model-metadata", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mode", choices=("whole", "pauses"), default="whole")
    parser.add_argument("--threshold-dbfs", type=float)
    parser.add_argument("--language", default=None, help="omit for per-chunk detection; a single language hint can hide another language")
    args = parser.parse_args()
    try:
        report = transcribe_file(args.wav, args.model_metadata, args.out, args.mode, args.threshold_dbfs, args.language)
        print(json.dumps({"output": str(args.out.resolve()), "chunks": len(report["chunks"]),
                          "audio_seconds": report["audio_seconds"], "inference_seconds": report["inference_seconds"],
                          "real_time_factor": report["real_time_factor"]}, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
