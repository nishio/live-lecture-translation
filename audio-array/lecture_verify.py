#!/usr/bin/env python3
"""Read-only integrity audit of a saved Mac lecture session.

Prints JSON; never captures audio, loads a model, changes a ledger, or writes to
the session. A live or incomplete session is a snapshot, never a complete audit.
Hash/source consistency is not ASR correctness or semantic quality. Saved runtime
source hashes attest to those copies, not to the Python bytecode that executed.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import wave

from lecture_source_policy import SOURCE_POLICY_VERSION, source_metadata


ROOT = Path(__file__).resolve().parents[1]
LINE_FIELDS = ("id", "start_seconds", "end_seconds", "text", "language", "uncertain")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash_file(path, limit=None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        remaining = limit
        while remaining is None or remaining > 0:
            data = stream.read(1024 * 1024 if remaining is None else min(remaining, 1024 * 1024))
            if not data:
                break
            digest.update(data)
            if remaining is not None:
                remaining -= len(data)
        if remaining not in (None, 0):
            raise ValueError("File ended before the captured snapshot length")
    return digest.hexdigest()


def _signature(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _close(left, right):
    return _finite(left) and _finite(right) and abs(left - right) <= 1e-6


def _line(line, *, reason_metadata=False):
    # Legacy requests hashed six fields even when raw ASR included reasons.
    # New requests hash the allowlisted reasons too; compare those rather than
    # dropping them or manufacturing certainty from missing reason labels.
    value = {"id": line["id"], "start_seconds": float(line["start_seconds"]),
             "end_seconds": float(line["end_seconds"]), "text": line["text"].strip(),
             "language": line.get("language"), "uncertain": line.get("uncertain", False)}
    if reason_metadata:
        value.update(source_metadata(line))
    return value


def _recorded_reason_policy(record):
    version = record.get("source_policy_version") if isinstance(record, dict) else None
    return type(version) is int and version == SOURCE_POLICY_VERSION


def _inside(path, root):
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Artifact path escapes its session directory")
    return path


def _cloud_schema(value):
    # The fixed cloud adapter removes uniqueItems; the local validator enforces it.
    if isinstance(value, dict):
        return {key: _cloud_schema(item) for key, item in value.items() if key != "uniqueItems"}
    if isinstance(value, list):
        return [_cloud_schema(item) for item in value]
    return value


class Audit:
    def __init__(self):
        self.errors, self.warnings, self.unverified = [], [], []
        self.checks = 0

    def check(self, condition, code, **details):
        self.checks += 1
        if not condition:
            self.errors.append({"code": code, **details})
        return bool(condition)

    def warn(self, code, **details):
        self.warnings.append({"code": code, **details})

    def load(self, path):
        return json.loads(path.read_bytes())

    def rows(self, path, snapshot):
        if not path.exists():
            return []
        raw = path.read_bytes()
        chunks = raw.splitlines(keepends=True)
        if chunks and not chunks[-1].endswith(b"\n"):
            if snapshot:
                self.warn("jsonl_uncommitted_tail", file=path.name)
                chunks = chunks[:-1]
            else:
                self.check(False, "jsonl_uncommitted_tail", file=path.name)
        return [json.loads(row) for row in chunks if row.strip()]


def _runtime(audit, result_dir):
    manifest_path = result_dir / "runtime-manifest.json"
    if not manifest_path.exists():
        audit.unverified.append("runtime_manifest_missing")
        return {"verified": False, "present": False, "source_copies": 0}, None
    manifest = audit.load(manifest_path)
    hashes = manifest.get("source_sha256")
    if not audit.check(isinstance(hashes, dict) and bool(hashes), "runtime_source_map_invalid"):
        return {"verified": False, "present": True, "source_copies": 0}, None
    before = len(audit.errors)
    base = result_dir / "source-at-start"
    changed, prompt = [], None
    for relative, expected in hashes.items():
        path = _inside(base / relative, base)
        audit.check(path.is_file(), "runtime_source_missing", source=relative)
        if not path.is_file():
            continue
        audit.check(_hash_file(path) == expected, "runtime_source_sha256_mismatch", source=relative)
        current = _inside(ROOT / relative, ROOT)
        if current.is_file() and _hash_file(current) != expected:
            changed.append(relative)
        if relative == "audio-array/lecture_analysis.py":
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SYSTEM_PROMPT" for t in node.targets):
                    value = ast.literal_eval(node.value)
                    if isinstance(value, str):
                        prompt = _sha(value.encode("utf-8"))
                    break
    actual = {str(path.relative_to(base)) for path in base.rglob("*") if path.is_file()}
    audit.check(actual == set(hashes), "runtime_source_inventory_mismatch")
    return {"verified": before == len(audit.errors), "present": True, "source_copies": len(hashes),
            "working_tree_changed_sources": changed, "scope": "saved_source_copy_hashes_only"}, prompt


def verify_session(path):
    """Return a bounded-snapshot report. Errors are data, never repair actions."""
    audit = Audit()
    supplied = Path(path).resolve()
    state_path = supplied if supplied.name == "state.json" else supplied / "state.json"
    result_dir = state_path.parent
    report = {"schema_version": 1, "verified_at": time.time(), "result_path": str(result_dir),
              "scope": "snapshot", "complete_verified": False, "integrity_ok": False,
              "semantic_quality_verified": False, "read_only": True}
    tracked = {}
    try:
        state_raw = state_path.read_bytes()
        state = json.loads(state_raw)
        tracked[state_path] = _signature(state_path)
        session = state["session"]
        audit.check(Path(session["result_path"]).resolve() == result_dir, "state_result_path_mismatch")
        audio_dir = Path(session["data_path"]).resolve() / "audio"
        metadata_path, raw_path, ledger_path = (audio_dir / name for name in ("pcm.json", "raw.pcm", "chunks.jsonl"))
        metadata = audit.load(metadata_path)
        tracked[metadata_path] = _signature(metadata_path)
        audio_closed = metadata.get("closed") is True
        capture_done = state.get("capture", {}).get("state") in {"completed", "stopped"}
        asr_done = state.get("asr", {}).get("state") == "completed"
        analysis_state = state.get("analysis", {})
        analysis_done = analysis_state.get("state") == "completed" or (
            analysis_state.get("provider") == "off" and analysis_state.get("state") == "idle")
        processing_active = state.get("processing_active") is True
        complete_candidate = audio_closed and capture_done and asr_done and analysis_done and not processing_active
        report.update(session_id=session.get("id"), audio_closed=audio_closed,
                      processing_finished=asr_done and analysis_done and not processing_active,
                      processing_active_reported=processing_active,
                      scope="complete" if complete_candidate else "snapshot")
        snapshot = not complete_candidate
        for candidate in (ledger_path, result_dir / "transcript.jsonl", result_dir / "analysis-history.jsonl",
                          result_dir / "translation-history.jsonl", result_dir / "cost-report.json"):
            tracked[candidate] = _signature(candidate) if candidate.exists() else None
        chunks = audit.rows(ledger_path, snapshot)
        audit.check(ledger_path.is_file(), "chunk_ledger_missing")
        raw_size = raw_path.stat().st_size
        tracked[raw_path] = _signature(raw_path)
        rate = metadata.get("sample_rate")
        if not audit.check(type(rate) is int and rate > 0 and metadata.get("format") == "s16le"
                           and metadata.get("channels") == 1 and metadata.get("sample_width") == 2,
                           "pcm_format_invalid"):
            raise ValueError("Cannot verify unsupported PCM format")
        audit.check(Path(metadata.get("raw_path", "")).resolve() == raw_path.resolve(), "metadata_raw_path_mismatch")
        raw_sha = _hash_file(raw_path, raw_size)
        end_frame = 0
        chunk_by_index = {}
        with raw_path.open("rb") as raw_stream:
            for ordinal, chunk in enumerate(chunks):
                index, frames = chunk.get("index"), chunk.get("frames")
                audit.check(type(index) is int and index == ordinal, "chunk_index_sequence", ordinal=ordinal)
                if not audit.check(type(frames) is int and frames > 0, "chunk_frame_count_invalid", chunk=index):
                    continue
                audit.check(type(chunk.get("start_frame")) is int and chunk["start_frame"] == end_frame,
                            "chunk_frame_gap_or_overlap", chunk=index)
                audit.check(type(chunk.get("end_frame")) is int and chunk["end_frame"] == end_frame + frames,
                            "chunk_frame_end_mismatch", chunk=index)
                audit.check(chunk.get("sample_rate") == rate and chunk.get("channels") == 1,
                            "chunk_format_mismatch", chunk=index)
                audit.check(_close(chunk.get("start_seconds"), end_frame / rate)
                            and _close(chunk.get("end_seconds"), (end_frame + frames) / rate),
                            "chunk_seconds_mismatch", chunk=index)
                path = _inside(chunk["path"], audio_dir)
                audit.check(path.name == f"chunk-{ordinal:06d}.wav", "chunk_filename_mismatch", chunk=index)
                audit.check(_hash_file(path) == chunk.get("sha256"), "wav_sha256_mismatch", chunk=index)
                with wave.open(str(path), "rb") as wav:
                    audit.check(wav.getnchannels() == 1 and wav.getsampwidth() == 2 and wav.getframerate() == rate
                                and wav.getnframes() == frames and wav.getcomptype() == "NONE", "wav_format_mismatch", chunk=index)
                    pcm = wav.readframes(wav.getnframes())
                audit.check(len(pcm) == frames * 2 and _sha(pcm) == chunk.get("pcm_sha256"),
                            "wav_pcm_sha256_mismatch", chunk=index)
                raw_stream.seek(end_frame * 2)
                audit.check(raw_stream.read(frames * 2) == pcm, "raw_wav_pcm_mismatch", chunk=index)
                audit.check(chunk.get("partial") == (frames < metadata["chunk_frames"])
                            and (not chunk.get("partial") or ordinal == len(chunks) - 1), "chunk_partial_invalid", chunk=index)
                end_frame += frames
                chunk_by_index[index] = chunk
        audit.check(end_frame * 2 <= raw_size, "chunks_beyond_raw")
        observed_wavs = {path.resolve() for path in audio_dir.glob("*.wav")}
        ledger_wavs = {Path(chunk["path"]).resolve() for chunk in chunks}
        if audio_closed:
            audit.check(observed_wavs == ledger_wavs, "wav_inventory_mismatch")
        elif observed_wavs != ledger_wavs:
            audit.warn("wav_inventory_in_progress")
        if audio_closed:
            for key, expected in {"raw_bytes": raw_size, "frames": raw_size // 2, "derived_frames": end_frame,
                                  "chunks": len(chunks), "trailing_bytes": raw_size % 2, "raw_sha256": raw_sha}.items():
                audit.check(metadata.get(key) == expected, "closed_pcm_metadata_mismatch", field=key)
            audit.check(raw_size == end_frame * 2, "closed_raw_unaccounted_tail")
            audit.check(_close(metadata.get("audio_seconds"), end_frame / rate), "closed_pcm_duration_mismatch")
        audit.check(not complete_candidate or _close(state["capture"].get("audio_seconds"), end_frame / rate),
                    "capture_duration_mismatch")
        report["audio"] = {"chunks_verified": len(chunks), "derived_frames": end_frame,
                           "raw_bytes_inspected": raw_size, "raw_sha256": raw_sha,
                           "unledgered_raw_bytes": raw_size - end_frame * 2, "audio_seconds": end_frame / rate}

        transcript_rows = audit.rows(result_dir / "transcript.jsonl", snapshot)
        line_by_id, transcribed_indices = {}, []
        for row in transcript_rows:
            chunk = row["chunk"]
            index = chunk["index"]
            audit.check(index in chunk_by_index and chunk == chunk_by_index.get(index),
                        "transcript_chunk_mismatch", chunk=index)
            audit.check(index not in transcribed_indices and (not transcribed_indices or index > transcribed_indices[-1]),
                        "transcript_chunk_order_or_duplicate", chunk=index)
            transcribed_indices.append(index)
            previous_start = -1
            for number, line in enumerate(row["lines"]):
                identity = line.get("id")
                audit.check(identity == f"c{index:06d}-l{number:04d}" and identity not in line_by_id,
                            "transcript_line_id_invalid", chunk=index, line=number)
                start, end = line.get("start_seconds"), line.get("end_seconds")
                valid_time = _finite(start) and _finite(end) and 0 <= start <= end and start >= previous_start
                audit.check(valid_time, "transcript_time_invalid", source_id=identity)
                audit.check(isinstance(line.get("text"), str) and type(line.get("uncertain", False)) is bool,
                            "transcript_line_shape_invalid", source_id=identity)
                audit.check(line.get("segment") == index, "transcript_segment_mismatch", source_id=identity)
                in_chunk = valid_time and chunk["start_seconds"] - 1e-6 <= start and end <= chunk["end_seconds"] + 1e-6
                if not in_chunk and line.get("uncertain") is True:
                    audit.warn("asr_uncertain_timestamp_outside_chunk", source_id=identity)
                else:
                    audit.check(in_chunk, "transcript_time_outside_chunk", source_id=identity)
                if valid_time:
                    previous_start = start
                line_by_id[identity] = line
        failed_indices = {item["index"] for item in state.get("asr", {}).get("failed_chunks", [])}
        if complete_candidate:
            audit.check(set(transcribed_indices) == set(chunk_by_index), "transcript_missing_completed_chunks")
            audit.check(not failed_indices and _close(state["asr"].get("through_seconds"), end_frame / rate),
                        "asr_completion_mismatch")
        state_lines = state.get("lines", [])
        reason_metadata = _recorded_reason_policy(state.get("translation"))
        state_ids = [line.get("id") for line in state_lines]
        audit.check(len(state_ids) == len(set(state_ids)), "state_duplicate_line_ids")
        for line in state_lines:
            identity = line.get("id")
            audit.check(identity in line_by_id and _line(line, reason_metadata=reason_metadata)
                        == _line(line_by_id[identity], reason_metadata=reason_metadata),
                        "state_transcript_line_mismatch", source_id=identity)
        if complete_candidate:
            audit.check(set(state_ids) == set(line_by_id), "state_transcript_coverage_mismatch")
        report["transcript"] = {"chunks": len(transcript_rows), "lines": len(line_by_id),
                                "uncertain_lines": sum(line.get("uncertain") is True for line in line_by_id.values()),
                                "failed_chunks_reported": sorted(failed_indices)}

        runtime, runtime_prompt = _runtime(audit, result_dir)
        report["runtime"] = runtime
        history = audit.rows(result_dir / "analysis-history.jsonl", snapshot)
        if history and runtime["present"] and runtime_prompt is None:
            audit.unverified.append("runtime_analysis_prompt_unavailable")
        prior_through = -1
        for number, result in enumerate(history):
            through = result.get("through_seconds")
            audit.check(_finite(through) and prior_through <= through <= end_frame / rate + 1e-6,
                        "analysis_through_invalid", analysis=number)
            if _finite(through):
                prior_through = through
            source_ids = result.get("source_line_ids", [])
            audit.check(isinstance(source_ids, list) and bool(source_ids) and len(source_ids) == len(set(source_ids))
                        and set(source_ids).issubset(line_by_id), "analysis_unknown_or_duplicate_source", analysis=number)
            artifact = _inside(result["artifact_dir"], result_dir / "analyses")
            request = audit.load(artifact / "request.json")
            saved = audit.load(artifact / "result.json")
            payload = audit.load(artifact / "payload.json")
            cloud_payload = result.get("provider") == "openai"
            messages = payload.get("input") if cloud_payload else payload.get("messages")
            schema = payload.get("text", {}).get("format", {}).get("schema") if cloud_payload else payload.get("format")
            expected_schema = _cloud_schema(request.get("schema")) if cloud_payload else request.get("schema")
            audit.check(messages == request.get("messages") and schema == expected_schema
                        and payload.get("model") == result.get("model"),
                        "analysis_payload_request_mismatch", analysis=number)
            # lecture_live appends selection metadata after the analyzer saved its result.
            audit.check({k: v for k, v in result.items() if k != "selection"} == saved,
                        "analysis_history_result_mismatch", analysis=number)
            user_messages = [message for message in request["messages"] if message.get("role") == "user"]
            system_messages = [message for message in request["messages"] if message.get("role") == "system"]
            audit.check(len(user_messages) == 1 and len(system_messages) == 1, "analysis_message_shape_invalid", analysis=number)
            data = json.loads(user_messages[0]["content"])
            inputs = data["transcript"]
            # Either explicit policy or recorded request shape selects the new
            # canonical form. An explicit policy still requires reason fields
            # when a request has lost them; legacy six-field requests stay valid.
            input_reason_metadata = (reason_metadata or _recorded_reason_policy(result.get("selection"))
                                     or any("doubt_reasons" in line for line in inputs))
            input_ids = [line["id"] for line in inputs]
            audit.check(input_ids == source_ids == request.get("source_line_ids"), "analysis_request_ids_mismatch", analysis=number)
            audit.check(_close(data.get("through_seconds"), through) and _close(request.get("through_seconds"), through),
                        "analysis_request_boundary_mismatch", analysis=number)
            for line in inputs:
                identity = line["id"]
                audit.check(identity in line_by_id and line == _line(line_by_id[identity],
                            reason_metadata=input_reason_metadata),
                            "analysis_input_transcript_mismatch", analysis=number, source_id=identity)
                audit.check(_finite(line.get("end_seconds")) and line["end_seconds"] <= through,
                            "analysis_future_source", analysis=number, source_id=identity)
            hashes = {line["id"]: _sha(_canonical(line)) for line in inputs}
            fingerprint = _sha(_canonical(inputs))
            ranges = [{"source_id": line["id"], "start_seconds": line["start_seconds"], "end_seconds": line["end_seconds"]} for line in inputs]
            for holder, label in ((request, "request"), (result, "result")):
                audit.check(holder.get("source_hashes") == hashes and holder.get("source_fingerprint") == fingerprint
                            and holder.get("source_ranges") == ranges, "analysis_source_fingerprint_mismatch", analysis=number, artifact=label)
            prompt = _sha(system_messages[0]["content"].encode("utf-8"))
            audit.check(request.get("prompt_fingerprint") == result.get("prompt_fingerprint") == prompt,
                        "analysis_prompt_fingerprint_mismatch", analysis=number)
            if runtime_prompt:
                audit.check(prompt == runtime_prompt, "analysis_runtime_prompt_mismatch", analysis=number)
            for name in ("headline", "summary", "flow", "concepts", "questions"):
                records = [result.get(name)] if name == "headline" else result.get(name, [])
                for record in records:
                    refs = record.get("source_ids", []) if isinstance(record, dict) else []
                    audit.check(isinstance(refs, list) and bool(refs) and set(refs).issubset(source_ids),
                                "analysis_claim_source_invalid", analysis=number, layer=name)
            translated = [item.get("source_id") for item in result.get("translations", [])]
            audit.check(len(translated) == len(set(translated)) and set(translated) == set(data["translation_ids"]),
                        "analysis_translation_ids_mismatch", analysis=number)
        current_result = analysis_state.get("result")
        if current_result is not None:
            audit.check(any(current_result == item for item in history), "state_analysis_not_in_history")
            if complete_candidate and history:
                audit.check(current_result == history[-1], "state_analysis_not_latest")
            audit.check(_close(analysis_state.get("through_seconds"), current_result.get("through_seconds")),
                        "state_analysis_boundary_mismatch")
        elif analysis_state.get("state") == "completed":
            audit.check(False, "completed_analysis_missing_result")
        report["analysis"] = {"snapshots": len(history), "source_integrity_checked": True,
                              "semantic_quality_verified": False}
        # Match the runtime cost report: both workloads contribute newly billed
        # cost_usd. cached_request_cost_usd is historical, not a new charge.
        translation_history = audit.rows(result_dir / "translation-history.jsonl", snapshot)
        successful_cost = 0.0
        for kind, records in (("analysis", history), ("translation", translation_history)):
            for number, item in enumerate(records):
                amount = item.get("cost_usd")
                if amount is None:  # Preserve legacy absent/null cost records.
                    amount = 0
                valid = (type(amount) in (int, float) and 0 <= amount <= sys.float_info.max
                         and _finite(successful_cost + amount))
                if audit.check(valid, "successful_cost_invalid", stage=kind, record=number):
                    successful_cost += amount
        cost_path = result_dir / "cost-report.json"
        if cost_path.exists():
            cost = audit.load(cost_path)
            audit.check(_close(cost.get("successful_generation_api_usd"), successful_cost), "successful_cost_aggregate_mismatch")
        else:
            audit.warn("cost_report_missing")
        report["cost_reference"] = {"successful_generation_api_usd": successful_cost,
                                    "all_additional_api_cost_verified": False,
                                    "authority": "shared cloud-budget.json ledger including failed and unknown reservations",
                                    "codex_usage": "unmeasured", "electricity": "unmeasured"}
        changed = [str(path) for path, signature in tracked.items()
                   if (_signature(path) if path.exists() else None) != signature]
        if changed or state_path.read_bytes() != state_raw:
            report["scope"] = "snapshot"
            audit.warn("files_changed_during_verification", files=changed)
            report["concurrent_change_detected"] = True
        report["complete_verified"] = (report["scope"] == "complete" and not audit.errors and not audit.unverified)
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, wave.Error, SyntaxError) as exc:
        audit.errors.append({"code": "unreadable_or_malformed_artifact", "exception": type(exc).__name__})
    report.update(integrity_ok=not audit.errors, checks=audit.checks, errors=audit.errors,
                  warnings=audit.warnings, unverified=audit.unverified)
    report["status"] = ("failed" if audit.errors else "verified_complete" if report["complete_verified"]
                        else "snapshot_consistent" if report["scope"] == "snapshot" else "incomplete_provenance")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path, help="result directory or its state.json")
    args = parser.parse_args()
    result = verify_session(args.session)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result["integrity_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
