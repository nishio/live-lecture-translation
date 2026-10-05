"""Opt-in subscription experiment over chronological prefixes of saved text.

This accelerated offline experiment does not simulate ASR availability or a live
scheduler. It never records audio, calls the API provider, retries, or resumes.
The original input and all generated evidence stay in ignored results/.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import time
import uuid

import lecture_analysis as analysis
import lecture_codex as codex
import lecture_translation as translation

ROOT = Path(__file__).resolve().parents[1]
MAX_ANALYSIS_LINES = 100
MAX_CHECKPOINTS = 10000


def load_input(path, expect_sha256=None):
    """Read one immutable byte snapshot and canonicalize only evidence fields."""
    path = Path(path).expanduser().resolve()
    if path.stat().st_size > translation.MAX_TRANSCRIPT_BYTES:
        raise ValueError("input file exceeds the bounded saved-text size")
    raw = path.read_bytes()
    if len(raw) > translation.MAX_TRANSCRIPT_BYTES:
        raise ValueError("input file exceeds the bounded saved-text size")
    digest = hashlib.sha256(raw).hexdigest()
    if expect_sha256 is not None:
        if (not isinstance(expect_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expect_sha256)
                or digest != expect_sha256):
            raise ValueError("source SHA-256 does not match the reviewed input")
    value = codex._json(raw.decode("utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("lines"), list):
        raise ValueError("saved transcript must be an object containing lines")
    lines = translation._clean_lines(value["lines"])
    if not lines:
        raise ValueError("saved transcript has no source lines")
    return {"sha256": digest, "lines": lines, "raw": raw}


def _positive(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return float(value)


def _checkpoints(duration, interval):
    count = int(duration / interval)
    if count > MAX_CHECKPOINTS:
        raise ValueError("too many chronological checkpoints")
    return sorted({interval * index for index in range(1, count + 1)
                   if interval * index < duration} | {duration})


def plan_replay(lines, *, duration=None, translation_interval=60,
                analysis_interval=120, analysis_window=180, max_calls=12):
    """Pure, bounded plan; assumed coverage here is never execution coverage."""
    clean = translation._clean_lines(lines)
    if not clean:
        raise ValueError("saved transcript has no source lines")
    duration = _positive(max(row["end_seconds"] for row in clean)
                         if duration is None else duration, "duration")
    translation_interval = _positive(translation_interval, "translation_interval")
    analysis_interval = _positive(analysis_interval, "analysis_interval")
    analysis_window = _positive(analysis_window, "analysis_window")
    if type(max_calls) is not int or not 1 <= max_calls <= 100:
        raise ValueError("max_calls must be an integer from 1 to 100")
    translate_at = set(_checkpoints(duration, translation_interval))
    analyze_at = set(_checkpoints(duration, analysis_interval))
    tasks, assumed_covered, skipped_analysis = [], [], []
    previous_analysis = None

    def add(task):
        if len(tasks) == max_calls:
            raise ValueError("planned model requests exceed max_calls; no model call started")
        task["sequence"] = len(tasks) + 1
        tasks.append(task)

    for cutoff in sorted(translate_at | analyze_at):
        prefix = [row for row in clean if row["end_seconds"] <= cutoff]
        if cutoff in translate_at:
            while True:
                planned = translation.plan_translation(prefix, assumed_covered, flush=True)
                if planned is None:
                    break
                add({"kind": "translation", "cutoff_seconds": cutoff,
                     "request": translation.build_translation_request(planned),
                     "translation_plan": planned})
                assumed_covered.extend(planned["target_source_ids"])
        if cutoff in analyze_at:
            window = [row for row in prefix if row["end_seconds"] > cutoff - analysis_window
                      and row["text"] and not row["uncertain"]]
            selected = window[-MAX_ANALYSIS_LINES:]
            if not selected:
                skipped_analysis.append(cutoff)
                continue
            request = analysis.build_snapshot_request(
                selected, previous=None if previous_analysis is None
                else {"through_seconds": previous_analysis}, through_seconds=cutoff,
                translation_ids=[], use_previous=False)
            add({"kind": "analysis", "cutoff_seconds": cutoff, "request": request,
                 "translation_plan": None,
                 "window_omitted_source_ids": [row["id"] for row in window[:-MAX_ANALYSIS_LINES]]})
            previous_analysis = cutoff
    in_scope = [row for row in clean if row["end_seconds"] <= duration]
    return {"schema_version": 1, "experiment": "offline_saved_text_prefix_replay",
            "capture": False, "asr": False, "real_time_scheduler": False,
            "settings": {"duration": duration, "translation_interval": translation_interval,
                         "analysis_interval": analysis_interval, "analysis_window": analysis_window,
                         "max_calls": max_calls, "max_analysis_lines": MAX_ANALYSIS_LINES},
            "source_count": len(clean), "in_scope_source_count": len(in_scope),
            "eligible_source_ids": [row["id"] for row in in_scope if translation._eligible(row)],
            "excluded_source_ids": [row["id"] for row in in_scope if not translation._eligible(row)],
            "outside_duration_source_ids": [row["id"] for row in clean if row["end_seconds"] > duration],
            "skipped_empty_analysis_cutoffs": skipped_analysis,
            "model_requests": len(tasks), "tasks": tasks}


def _save(path, value):
    codex._write(path, value)


def _directory(out_dir):
    base = Path(out_dir).expanduser().resolve()
    if not base.is_relative_to((ROOT / "results").resolve()):
        raise ValueError("replay output must be under this worktree's ignored results/")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = base / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(mode=0o700)
    return directory


def _usage(value):
    if (isinstance(value, dict) and value and all(
            isinstance(key, str) and type(number) is int and number >= 0
            for key, number in value.items())):
        return value
    return None


def _provider_evidence(item, generated, directory):
    """Only known aggregate fields and a confined relative path enter reports."""
    item["usage"] = _usage(generated.get("usage"))
    elapsed = generated.get("elapsed_seconds")
    if type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0:
        item["provider_elapsed_seconds"] = elapsed
    path = generated.get("attempt_dir")
    if path:
        resolved = Path(path).resolve()
        if resolved.is_relative_to(directory):
            item["provider_attempt_path"] = str(resolved.relative_to(directory))


def _failure_evidence(item, exc, directory):
    item["error_type"] = type(exc).__name__
    if isinstance(exc, codex.CodexTransportError):
        # Provider reasons are fixed codes. Do not copy arbitrary exception text.
        if re.fullmatch(r"[a-z_]{1,80}", exc.reason):
            item["error_reason"] = exc.reason
        if exc.attempt_dir:
            path = Path(exc.attempt_dir).resolve()
            if path.is_relative_to(directory):
                item["provider_attempt_path"] = str(path.relative_to(directory))
                try:
                    state = codex._json((path / "state.json").read_text(encoding="utf-8"))
                    if isinstance(state, dict):
                        _provider_evidence(item, state, directory)
                except (OSError, ValueError):
                    pass
    item["usage_uncertain"] = item.get("usage") is None


def _coverage(report, eligible):
    covered = [identity for item in report["results"] if item["status"] == "completed"
               and item["kind"] == "translation" for identity in item["covered_source_ids"]]
    report["covered_source_ids"] = covered
    report["pending_source_ids"] = [identity for identity in eligible if identity not in set(covered)]
    report["coverage_complete"] = (len(covered) == len(set(covered))
                                    and set(covered) == set(eligible))


def run_replay(transcript, *, expect_sha256, out_dir, model=codex.DEFAULT_MODEL,
               timeout=120, duration=None, translation_interval=60, analysis_interval=120,
               analysis_window=180, max_calls=12, generate=None, preflight=None):
    """Execute one explicitly authorized frozen plan, stopping at first failure."""
    if expect_sha256 is None:
        raise ValueError("run requires the SHA-256 from the reviewed plan")
    timeout = _positive(timeout, "timeout")
    if timeout > 300:
        raise ValueError("timeout must not exceed 300 seconds")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a nonempty string")
    source = load_input(transcript, expect_sha256)
    plan = plan_replay(source["lines"], duration=duration, translation_interval=translation_interval,
                       analysis_interval=analysis_interval, analysis_window=analysis_window,
                       max_calls=max_calls)
    generate, preflight = generate or codex.generate, preflight or codex.check
    directory = _directory(out_dir)
    with codex._private_open(directory / "source-input.json") as stream:
        stream.write(source["raw"])
    _save(directory / "source-lines.json", source["lines"])
    _save(directory / "plan.json", {**plan, "input_sha256": source["sha256"]})
    report = {key: value for key, value in plan.items() if key != "tasks"}
    report.update(created_at=datetime.now(timezone.utc).isoformat(), status="pending",
                  input_sha256=source["sha256"], model=model, timeout_seconds=timeout,
                  billing_route="chatgpt_subscription", application_api_key_requests=0,
                  measured_api_cost_usd=None, subscription_allowance_consumption="not measured",
                  subscription_fee_allocation="not measured", development_and_energy_cost="not measured",
                  semantic_quality="not automatically assessed", automatic_retry=False,
                  results=[{"sequence": task["sequence"], "kind": task["kind"],
                            "cutoff_seconds": task["cutoff_seconds"], "status": "pending",
                            "source_count": len(task["request"]["source_line_ids"]),
                            "target_count": len(task["request"].get("target_source_ids", [])),
                            "request_sha256": translation._hash(task["request"])}
                           for task in plan["tasks"]])
    _coverage(report, plan["eligible_source_ids"])
    _save(directory / "report.json", report)
    try:
        report["preflight"] = preflight()
        if (not isinstance(report["preflight"], dict)
                or report["preflight"].get("auth_mode") != "chatgpt"
                or report["preflight"].get("ready") is False):
            raise codex.CodexTransportError("chatgpt_login_required")
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                      failure_stage="preflight", error_type=type(exc).__name__)
        _save(directory / "report.json", report)
        return report, directory
    started_run = time.monotonic()
    for task, item in zip(plan["tasks"], report["results"]):
        started, stage = time.monotonic(), "source_integrity"
        try:
            # A changed source is a different experiment, even though requests
            # are already frozen. Never silently continue after source drift.
            load_input(transcript, expect_sha256)
            stage = "persistence"
            attempt = directory / f"{task['sequence']:02d}-{task['kind']}"
            attempt.mkdir(mode=0o700)
            item["attempt_path"] = str(attempt.relative_to(directory))
            _save(attempt / "request.json", task["request"])
            if task["translation_plan"] is not None:
                _save(attempt / "translation-plan.json", task["translation_plan"])
            item["status"], report["status"] = "running", "running"
            _save(directory / "report.json", report)
            stage = "transport"
            generated = generate(task["request"]["messages"], task["request"]["schema"],
                                 model=model, out_dir=attempt, timeout=timeout)
            item["transport_status"] = "completed"
            _provider_evidence(item, generated, directory)
            stage = "validation"
            checked = (translation.validate_translation_response(generated["output"], task["translation_plan"])
                       if task["kind"] == "translation" else analysis.validate_snapshot_response(
                           generated["output"], task["request"]["source_line_ids"], []))
            stage = "persistence"
            _save(attempt / "validated.json", checked)
            item.update(status="completed", structural_validation="passed")
            if task["kind"] == "translation":
                item["covered_source_ids"] = [identity for block in checked["blocks"]
                                              for identity in block["source_ids"]]
                item["block_count"] = len(checked["blocks"])
            else:
                item["summary_count"] = len(checked["summary"])
                item["concept_count"] = len(checked["concepts"])
        except (Exception, KeyboardInterrupt) as exc:
            item.update(status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                        failure_stage=stage, elapsed_seconds=time.monotonic() - started)
            item.setdefault("transport_status", "unconfirmed" if stage == "transport" else "not_started")
            _failure_evidence(item, exc, directory)
            report.update(status=item["status"], elapsed_seconds=time.monotonic() - started_run)
            _coverage(report, plan["eligible_source_ids"])
            _save(directory / "report.json", report)
            return report, directory
        item["elapsed_seconds"] = time.monotonic() - started
        _coverage(report, plan["eligible_source_ids"])
        _save(directory / "report.json", report)
    _coverage(report, plan["eligible_source_ids"])
    report["status"] = "completed" if report["coverage_complete"] else "failed"
    report["elapsed_seconds"] = time.monotonic() - started_run
    report["timing"] = {}
    for kind in ("translation", "analysis"):
        elapsed = [item["elapsed_seconds"] for item in report["results"] if item["kind"] == kind]
        if elapsed:
            report["timing"][kind] = {"count": len(elapsed), "min_seconds": min(elapsed),
                                      "median_seconds": statistics.median(elapsed), "max_seconds": max(elapsed)}
    _save(directory / "report.json", report)
    return report, directory


def _summary(plan):
    return {**{key: value for key, value in plan.items() if key != "tasks"},
            "tasks": [{"sequence": task["sequence"], "kind": task["kind"],
                       "cutoff_seconds": task["cutoff_seconds"],
                       "input_bytes": task["request"]["input_bytes"],
                       "source_count": len(task["request"]["source_line_ids"]),
                       "target_count": len(task["request"].get("target_source_ids", [])),
                       "analysis_window_omitted_count": len(task.get("window_omitted_source_ids", []))}
                      for task in plan["tasks"]]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "run"))
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--expect-sha256")
    parser.add_argument("--confirm-subscription-use", action="store_true")
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--translation-interval", type=float, default=60)
    parser.add_argument("--analysis-interval", type=float, default=120)
    parser.add_argument("--analysis-window", type=float, default=180)
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--model", default=codex.DEFAULT_MODEL)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "results" / "subscription-replay")
    args = parser.parse_args(argv)
    if args.action == "run" and (not args.confirm_subscription_use or not args.expect_sha256):
        parser.error("run requires --expect-sha256 from plan and --confirm-subscription-use")
    settings = {key: getattr(args, key) for key in (
        "duration", "translation_interval", "analysis_interval", "analysis_window", "max_calls")}
    try:
        if args.action == "plan":
            source = load_input(args.transcript, args.expect_sha256)
            result = {**_summary(plan_replay(source["lines"], **settings)), "input_sha256": source["sha256"]}
        else:
            report, directory = run_replay(args.transcript, expect_sha256=args.expect_sha256,
                                           out_dir=args.out_dir, model=args.model,
                                           timeout=args.timeout, **settings)
            result = {"status": report["status"], "report": str(directory / "report.json"),
                      "completed": sum(item["status"] == "completed" for item in report["results"]),
                      "planned": report["model_requests"], "covered": len(report["covered_source_ids"]),
                      "pending": len(report["pending_source_ids"]), "timing": report.get("timing")}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status", "completed") == "completed" else 1
    except Exception as exc:
        print(f"Subscription replay failed ({type(exc).__name__}); no API fallback.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
