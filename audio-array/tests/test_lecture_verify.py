import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lecture_verify import verify_session


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n")


def fixture(base):
    result_dir, audio_dir = base / "results/session", base / "data/session/audio"
    result_dir.mkdir(parents=True)
    audio_dir.mkdir(parents=True)
    raw = struct.pack("<1600h", *[i - 800 for i in range(1600)])
    (audio_dir / "raw.pcm").write_bytes(raw)
    wav_path = audio_dir / "chunk-000000.wav"
    with wave.open(str(wav_path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(raw)
    chunk = {"index": 0, "path": str(wav_path), "frames": 1600, "start_frame": 0, "end_frame": 1600,
             "start_seconds": 0.0, "end_seconds": 0.1, "sample_rate": 16000, "channels": 1,
             "sha256": sha(wav_path.read_bytes()), "pcm_sha256": sha(raw), "completed_at": 1, "partial": False}
    put(audio_dir / "chunks.jsonl", chunk)
    put(audio_dir / "pcm.json", {"format": "s16le", "sample_rate": 16000, "channels": 1, "sample_width": 2,
                                "chunk_frames": 1600, "raw_path": str(audio_dir / "raw.pcm"), "raw_bytes": len(raw),
                                "frames": 1600, "audio_seconds": 0.1, "derived_frames": 1600, "chunks": 1,
                                "trailing_bytes": 0, "raw_sha256": sha(raw), "closed": True})
    line = {"id": "c000000-l0000", "start_seconds": 0.0, "end_seconds": 0.1,
            "text": "A test.", "language": "en", "uncertain": False}
    transcript = {"chunk": chunk, "lines": [{**line, "segment": 0, "chunk": 0}]}
    put(result_dir / "transcript.jsonl", transcript)
    runtime_source = result_dir / "source-at-start/audio-array/lecture_analysis.py"
    runtime_source.parent.mkdir(parents=True)
    runtime_source.write_text('SYSTEM_PROMPT = "fixture prompt"\n')
    put(result_dir / "runtime-manifest.json", {"source_sha256": {
        "audio-array/lecture_analysis.py": sha(runtime_source.read_bytes())}})
    prompt_sha = sha(b"fixture prompt")
    ranges = [{"source_id": line["id"], "start_seconds": 0.0, "end_seconds": 0.1}]
    common = {"source_line_ids": [line["id"]], "source_hashes": {line["id"]: sha(canonical(line))},
              "source_fingerprint": sha(canonical([line])), "source_ranges": ranges,
              "prompt_fingerprint": prompt_sha, "through_seconds": 0.1}
    artifact = result_dir / "analyses/first"
    request = {**common, "schema": {}, "messages": [{"role": "system", "content": "fixture prompt"},
        {"role": "user", "content": json.dumps({"transcript": [line], "through_seconds": 0.1, "translation_ids": []})}]}
    put(artifact / "request.json", request)
    put(artifact / "payload.json", {"model": "fixture", "messages": request["messages"], "format": request["schema"]})
    result = {**common, "artifact_dir": str(artifact), "headline": {"text": "試験", "source_ids": [line["id"]]},
              "summary": [], "flow": [], "concepts": [], "questions": [], "translations": [],
              "provider": "local", "model": "fixture", "cost_usd": 0.0}
    put(artifact / "result.json", result)
    put(result_dir / "analysis-history.jsonl", result)
    state = {"session": {"id": "session", "data_path": str(audio_dir.parent), "result_path": str(result_dir)},
             "capture": {"state": "completed", "audio_seconds": 0.1},
             "asr": {"state": "completed", "through_seconds": 0.1, "failed_chunks": []},
             "analysis": {"state": "completed", "provider": "local", "through_seconds": 0.1, "result": result},
             "lines": transcript["lines"]}
    put(result_dir / "state.json", state)
    put(result_dir / "cost-report.json", {"successful_generation_api_usd": 0.0})
    return result_dir, audio_dir


class LectureVerificationTests(unittest.TestCase):
    def test_complete_pass_is_read_only_and_active_capture_remains_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_dir, audio_dir = fixture(Path(tmp))
            before = {str(path): sha(path.read_bytes()) for path in Path(tmp).rglob("*") if path.is_file()}
            report = verify_session(result_dir)
            after = {str(path): sha(path.read_bytes()) for path in Path(tmp).rglob("*") if path.is_file()}
            self.assertEqual(before, after)
            self.assertEqual([], report["errors"])
            self.assertTrue(report["complete_verified"])
            self.assertFalse(report["semantic_quality_verified"])
            state = json.loads((result_dir / "state.json").read_text())
            state["capture"]["state"] = "recording"
            put(result_dir / "state.json", state)
            report = verify_session(result_dir / "state.json")
            self.assertEqual("snapshot", report["scope"])
            self.assertTrue(report["integrity_ok"])
            self.assertFalse(report["complete_verified"])
            state["capture"]["state"] = "completed"
            state["processing_active"] = True
            put(result_dir / "state.json", state)
            report = verify_session(result_dir)
            self.assertEqual("snapshot", report["scope"])
            self.assertFalse(report["complete_verified"])

    def test_actual_wav_mutation_breaks_hash_pcm_and_raw_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_dir, audio_dir = fixture(Path(tmp))
            path = audio_dir / "chunk-000000.wav"
            data = bytearray(path.read_bytes())
            data[44] ^= 1
            path.write_bytes(data)
            report = verify_session(result_dir)
            codes = {item["code"] for item in report["errors"]}
            self.assertTrue({"wav_sha256_mismatch", "wav_pcm_sha256_mismatch", "raw_wav_pcm_mismatch"}.issubset(codes))
            self.assertFalse(report["complete_verified"])

    def test_changed_transcript_time_and_text_cannot_match_analysis_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_dir, _ = fixture(Path(tmp))
            path = result_dir / "transcript.jsonl"
            row = json.loads(path.read_text())
            row["lines"][0]["end_seconds"] = 0.2
            row["lines"][0]["text"] = "A different claim."
            put(path, row)
            report = verify_session(result_dir)
            codes = {item["code"] for item in report["errors"]}
            self.assertIn("transcript_time_outside_chunk", codes)
            self.assertIn("state_transcript_line_mismatch", codes)
            self.assertIn("analysis_input_transcript_mismatch", codes)

    def test_changed_runtime_source_or_analysis_source_fingerprint_is_detected(self):
        for target in ("runtime", "analysis", "payload"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                result_dir, _ = fixture(Path(tmp))
                if target == "runtime":
                    path = result_dir / "source-at-start/audio-array/lecture_analysis.py"
                    path.write_text('SYSTEM_PROMPT = "a different prompt"\n')
                    expected = {"runtime_source_sha256_mismatch", "analysis_runtime_prompt_mismatch"}
                elif target == "analysis":
                    history_path = result_dir / "analysis-history.jsonl"
                    value = json.loads(history_path.read_text())
                    value["source_fingerprint"] = "0" * 64
                    put(history_path, value)
                    expected = {"analysis_source_fingerprint_mismatch", "analysis_history_result_mismatch"}
                else:
                    path = result_dir / "analyses/first/payload.json"
                    payload = json.loads(path.read_text())
                    payload["messages"][1]["content"] = "Different model input"
                    put(path, payload)
                    expected = {"analysis_payload_request_mismatch"}
                codes = {item["code"] for item in verify_session(result_dir)["errors"]}
                self.assertTrue(expected.issubset(codes))


if __name__ == "__main__":
    unittest.main()
