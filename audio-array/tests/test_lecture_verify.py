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
from lecture_verify import Audit, verify_session
from lecture_analysis import _clean_lines
from unittest.mock import patch


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n")


def fixture(base, *, source_changes=None, reason_metadata=False):
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
    line.update(source_changes or {})
    transcript = {"chunk": chunk, "lines": [{**line, "segment": 0, "chunk": 0}]}
    put(result_dir / "transcript.jsonl", transcript)
    runtime_source = result_dir / "source-at-start/audio-array/lecture_analysis.py"
    runtime_source.parent.mkdir(parents=True)
    runtime_source.write_text('SYSTEM_PROMPT = "fixture prompt"\n')
    put(result_dir / "runtime-manifest.json", {"source_sha256": {
        "audio-array/lecture_analysis.py": sha(runtime_source.read_bytes())}})
    prompt_sha = sha(b"fixture prompt")
    # Real request canonicalization exercises the current reason metadata;
    # legacy requests had only these six fields even when raw ASR had reasons.
    request_line = (_clean_lines([line], 0.1)[0][0] if reason_metadata else
                    {key: line[key] for key in ("id", "start_seconds", "end_seconds", "text", "language", "uncertain")})
    ranges = [{"source_id": line["id"], "start_seconds": 0.0, "end_seconds": 0.1}]
    common = {"source_line_ids": [line["id"]], "source_hashes": {line["id"]: sha(canonical(request_line))},
              "source_fingerprint": sha(canonical([request_line])), "source_ranges": ranges,
              "prompt_fingerprint": prompt_sha, "through_seconds": 0.1}
    artifact = result_dir / "analyses/first"
    request = {**common, "schema": {}, "messages": [{"role": "system", "content": "fixture prompt"},
        {"role": "user", "content": json.dumps({"transcript": [request_line], "through_seconds": 0.1, "translation_ids": []})}]}
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
    if reason_metadata:
        state["translation"] = {"source_policy_version": 1}
    put(result_dir / "state.json", state)
    put(result_dir / "cost-report.json", {"successful_generation_api_usd": 0.0})
    return result_dir, audio_dir


class LectureVerificationTests(unittest.TestCase):
    def set_analysis_cost(self, result_dir, amount, *, cached_cost=None):
        path = result_dir / "analysis-history.jsonl"
        result = json.loads(path.read_text())
        result["cost_usd"] = amount
        result["cache_hit"] = cached_cost is not None
        if cached_cost is not None:
            result["cached_request_cost_usd"] = cached_cost
        put(path, result)
        put(result_dir / "analyses/first/result.json", result)
        path = result_dir / "state.json"
        state = json.loads(path.read_text())
        state["analysis"]["result"] = result
        put(path, state)

    def test_successful_cost_includes_translation_history_without_recharging_cache(self):
        for analysis_cost, cached_cost in ((0.02, None), (0.0, 8.0)):
            with self.subTest(analysis_cost=analysis_cost), tempfile.TemporaryDirectory() as tmp:
                result_dir, _ = fixture(Path(tmp))
                self.set_analysis_cost(result_dir, analysis_cost, cached_cost=cached_cost)
                rows = [{"cost_usd": 0.0079085, "cache_hit": False},
                        {"cost_usd": 0.0, "cache_hit": True, "cached_request_cost_usd": 9.0},
                        {"cost_usd": 0.010616, "cache_hit": False}]
                (result_dir / "translation-history.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
                expected = analysis_cost + 0.0185245
                put(result_dir / "cost-report.json", {"successful_generation_api_usd": expected})
                before = {str(path): sha(path.read_bytes()) for path in Path(tmp).rglob("*") if path.is_file()}
                report = verify_session(result_dir)
                self.assertEqual([], report["errors"])
                self.assertTrue(report["complete_verified"])
                self.assertAlmostEqual(expected, report["cost_reference"]["successful_generation_api_usd"])
                self.assertFalse(report["cost_reference"]["all_additional_api_cost_verified"])
                self.assertEqual(before, {str(path): sha(path.read_bytes()) for path in Path(tmp).rglob("*") if path.is_file()})

    def with_translation(self, result_dir, *, state="completed", pending=0, history_blocks=None, state_blocks=None):
        block = {"source_ids": ["c000000-l0000"], "text": "試験です。"}
        history_blocks = [block] if history_blocks is None else history_blocks
        state_blocks = history_blocks if state_blocks is None else state_blocks
        put(result_dir / "translation-history.jsonl", {"cost_usd": 0.0, "blocks": history_blocks})
        path = result_dir / "state.json"
        saved = json.loads(path.read_text())
        saved["translation"] = {"enabled": True, "state": state, "pending_lines": pending,
                                "worker_alive": False, "blocks": state_blocks}
        put(path, saved)

    def test_complete_translation_matching_history_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_dir, _ = fixture(Path(tmp))
            self.with_translation(result_dir)
            report = verify_session(result_dir)
        self.assertEqual([], report["errors"])
        self.assertEqual("verified_complete", report["status"])
        self.assertEqual(1, report["translation"]["blocks"])

    def test_failed_or_pending_translation_is_not_verified_complete(self):
        for state, pending in (("failed", 1), ("completed", 1), ("paused", 0)):
            with self.subTest(state=state, pending=pending), tempfile.TemporaryDirectory() as tmp:
                result_dir, _ = fixture(Path(tmp))
                self.with_translation(result_dir, state=state, pending=pending)
                report = verify_session(result_dir)
                self.assertEqual([], report["errors"])
                self.assertFalse(report["complete_verified"])
                self.assertFalse(report["processing_finished"])
                self.assertNotEqual("verified_complete", report["status"])

    def test_translation_blocks_must_match_sources_and_history(self):
        cases = {
            "translation_block_source_unknown": dict(history_blocks=[{"source_ids": ["c000009-l0000"], "text": "x"}]),
            "translation_block_source_overlap": dict(history_blocks=[{"source_ids": ["c000000-l0000"], "text": "a"},
                                                                     {"source_ids": ["c000000-l0000"], "text": "b"}]),
            "state_translation_blocks_mismatch": dict(state_blocks=[{"source_ids": ["c000000-l0000"], "text": "別の訳"}]),
        }
        for code, options in cases.items():
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                result_dir, _ = fixture(Path(tmp))
                self.with_translation(result_dir, **options)
                report = verify_session(result_dir)
                self.assertIn(code, [error["code"] for error in report["errors"]])
                self.assertEqual("failed", report["status"])

    def test_legacy_missing_translation_history_retains_analysis_only_aggregate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_dir, _ = fixture(Path(tmp))
            self.set_analysis_cost(result_dir, 0.03)
            put(result_dir / "cost-report.json", {"successful_generation_api_usd": 0.03})
            self.assertFalse((result_dir / "translation-history.jsonl").exists())
            report = verify_session(result_dir)
            self.assertEqual([], report["errors"])
            self.assertEqual(0.03, report["cost_reference"]["successful_generation_api_usd"])

    def test_translation_cost_cannot_be_omitted_from_saved_aggregate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_dir, _ = fixture(Path(tmp))
            put(result_dir / "translation-history.jsonl", {"cost_usd": 0.02})
            report = verify_session(result_dir)
            self.assertIn("successful_cost_aggregate_mismatch", {error["code"] for error in report["errors"]})
            self.assertFalse(report["complete_verified"])

    def test_invalid_successful_costs_fail_without_nonfinite_report_numbers(self):
        for amount in ("0.02", True, -0.02, float("nan"), float("inf"), [], 10 ** 400):
            with self.subTest(amount=amount), tempfile.TemporaryDirectory() as tmp:
                result_dir, _ = fixture(Path(tmp))
                put(result_dir / "translation-history.jsonl", {"cost_usd": amount})
                report = verify_session(result_dir)
                self.assertIn("successful_cost_invalid", {error["code"] for error in report["errors"]})
                self.assertFalse(report["complete_verified"])
                json.dumps(report, allow_nan=False)

    def test_translation_history_change_during_audit_stays_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_dir, _ = fixture(Path(tmp))
            path = result_dir / "translation-history.jsonl"
            put(path, {"cost_usd": 0.02})
            put(result_dir / "cost-report.json", {"successful_generation_api_usd": 0.02})
            original_rows = Audit.rows
            def rows_then_append(audit, supplied, snapshot):
                rows = original_rows(audit, supplied, snapshot)
                if supplied == path.resolve():
                    with path.open("a") as stream:
                        stream.write(json.dumps({"cost_usd": 0.03}) + "\n")
                return rows
            with patch.object(Audit, "rows", rows_then_append):
                report = verify_session(result_dir)
            self.assertEqual([], report["errors"])
            self.assertEqual("snapshot", report["scope"])
            self.assertFalse(report["complete_verified"])
            self.assertTrue(report["concurrent_change_detected"])
            self.assertIn("files_changed_during_verification", {row["code"] for row in report["warnings"]})

    def test_current_reason_metadata_matches_canonical_source_and_legacy_remains_valid(self):
        for current in (False, True):
            for changes in ({}, {"uncertain": True},
                            {"uncertain": True, "doubt_reasons": ["repetition", "timestamp_outside_audio", "repetition", "unsupported"]}):
                with self.subTest(current=current, changes=changes), tempfile.TemporaryDirectory() as tmp:
                    result_dir, _ = fixture(Path(tmp), source_changes=changes, reason_metadata=current)
                    before = {str(path): sha(path.read_bytes()) for path in Path(tmp).rglob("*") if path.is_file()}
                    report = verify_session(result_dir)
                    self.assertEqual([], report["errors"])
                    self.assertTrue(report["complete_verified"])
                    self.assertEqual(before, {str(path): sha(path.read_bytes()) for path in Path(tmp).rglob("*") if path.is_file()})

    def test_changed_or_missing_reason_evidence_cannot_be_verified(self):
        for target in ("state", "transcript", "input_missing", "input_changed"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                result_dir, _ = fixture(Path(tmp), source_changes={"uncertain": True,
                    "doubt_reasons": ["timestamp_outside_audio"]}, reason_metadata=True)
                if target in {"state", "transcript"}:
                    path = result_dir / ("state.json" if target == "state" else "transcript.jsonl")
                    value = json.loads(path.read_text())
                    value["lines"][0]["doubt_reasons"] = ["no_speech"]
                    put(path, value)
                    expected = "state_transcript_line_mismatch"
                else:
                    path = result_dir / "analyses/first/request.json"
                    value = json.loads(path.read_text())
                    data = json.loads(value["messages"][1]["content"])
                    if target == "input_missing":
                        data["transcript"][0].pop("doubt_reasons")
                    else:
                        data["transcript"][0]["doubt_reasons"] = ["no_speech"]
                    value["messages"][1]["content"] = json.dumps(data)
                    put(path, value)
                    expected = "analysis_input_transcript_mismatch"
                report = verify_session(result_dir)
                self.assertIn(expected, {error["code"] for error in report["errors"]})
                self.assertFalse(report["complete_verified"])

    def test_recorded_request_reason_fields_are_verified_without_state_policy_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_dir, _ = fixture(Path(tmp), source_changes={"uncertain": True,
                "doubt_reasons": ["timestamp_outside_audio"]}, reason_metadata=True)
            path = result_dir / "state.json"
            state = json.loads(path.read_text())
            state.pop("translation")
            put(path, state)
            self.assertTrue(verify_session(result_dir)["complete_verified"])
            path = result_dir / "transcript.jsonl"
            transcript = json.loads(path.read_text())
            transcript["lines"][0]["doubt_reasons"] = ["no_speech"]
            put(path, transcript)
            report = verify_session(result_dir)
            self.assertIn("analysis_input_transcript_mismatch", {error["code"] for error in report["errors"]})
            self.assertFalse(report["complete_verified"])

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
