"""Opt-in, synthetic-text experiment using ChatGPT-authenticated Codex.

This is not a live provider. It reuses v0.9 translation/analysis request builders
and validators without capture, ASR, an API key, or the API budget ledger.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time
import uuid

import lecture_analysis as analysis
import lecture_translation as translation
import lecture_codex as codex

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_VERSION = "synthetic-bus-pilot-v1"


def synthetic_lines():
    """Newly authored fixture, not a recording or published lecture transcript."""
    rows = [
        ("s1", 0, 7, "In our pilot, 24 volunteers tested the new bus route for two weeks.", False),
        ("s2", 7, 15, "The average wait fell from 12 minutes to 9 minutes, but we did not measure total travel time.", False),
        ("s3", 15, 23, "This does not prove that the route will work in every neighborhood.", False),
        ("u1", 23, 26, "The subsidy was [unclear] million dollars.", True),
        ("s4", 26, 35, "We will extend the pilot only if residents agree and the additional cost stays below 5,000 dollars.", False),
        ("s5", 35, 44, "By a reversible decision, I mean one we can undo if new evidence shows harm.", False),
        ("s6", 44, 53, "Next month we plan to survey people who did not volunteer; that survey has not happened yet.", False),
    ]
    return [{"id": key, "start_seconds": start, "end_seconds": end,
             "text": text, "language": "en", "uncertain": uncertain}
            for key, start, end, text, uncertain in rows]


def workloads():
    lines = synthetic_lines()
    plan = translation.plan_translation(lines, [], flush=True)
    request = translation.build_translation_request(plan)
    # Match the continuous live caller: uncertain ASR stays in evidence but is
    # not sent to normal understanding support or translated as a target.
    selected = [line for line in lines if not line["uncertain"]]
    snapshot = analysis.build_snapshot_request(selected, translation_ids=[], use_previous=False)
    return [("translation", request, plan), ("analysis", snapshot, None)]


def validate_output(kind, output, request, plan):
    if kind == "translation":
        return translation.validate_translation_response(output, plan)
    return analysis.validate_snapshot_response(output, request["source_line_ids"], [])


def _save(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def _directory(out_dir):
    path = Path(out_dir).resolve()
    if not path.is_relative_to((ROOT / "results").resolve()):
        raise ValueError("probe output must be under this worktree's ignored results/")
    path.mkdir(parents=True, exist_ok=True)
    directory = path / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(mode=0o700)
    return directory


def _selected_workloads(workload):
    if workload not in ("both", "translation", "analysis"):
        raise ValueError("workload must be both, translation, or analysis")
    return [task for task in workloads() if workload == "both" or task[0] == workload]


def plan_summary(repetitions=1, workload="both"):
    tasks = _selected_workloads(workload)
    return {"fixture": FIXTURE_VERSION, "synthetic_only": True,
            "model_requests": len(tasks) * repetitions, "capture": False, "asr": False,
            "workloads": [{"kind": kind, "input_bytes": request["input_bytes"],
                           "source_count": len(request["source_line_ids"]),
                           "target_groups": request.get("groups"),
                           "excluded_uncertain_target_ids": ["u1"] if kind == "translation" else [],
                           "omitted_uncertain_input_ids": ["u1"] if kind == "analysis" else []}
                          for kind, request, _ in tasks]}


def run_probe(*, out_dir, model="gpt-6.1-sol", repetitions=1, timeout=120,
              workload="both", generate=None, preflight=None):
    if type(repetitions) is not int or not 1 <= repetitions <= 3:
        raise ValueError("repetitions must be 1 to 3 (at most six model requests)")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 300:
        raise ValueError("timeout must be 1 to 300 seconds")
    generate = generate or codex.generate
    preflight = preflight or codex.check
    tasks = _selected_workloads(workload)
    directory = _directory(out_dir)
    report = {"schema_version": 1, "fixture": FIXTURE_VERSION, "synthetic_only": True,
              "created_at": datetime.now(timezone.utc).isoformat(), "model": model, "workload": workload,
              "status": "pending", "billing_route": "chatgpt_subscription",
              "application_api_key_requests": 0, "measured_api_cost_usd": None,
              "subscription_allowance_consumption": "not measured",
              "subscription_fee_allocation": "not measured",
              "development_and_energy_cost": "not measured",
              "semantic_quality": "not automatically assessed",
              "results": [{"repetition": repetition + 1, "kind": kind, "status": "pending",
                           "source_count": len(request["source_line_ids"]),
                           "request_sha256": hashlib.sha256(json.dumps(
                               {"messages": request["messages"], "schema": request["schema"]},
                               ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
                          for repetition in range(repetitions) for kind, request, _ in tasks]}
    _save(directory / "report.json", report)
    try:
        report["preflight"] = preflight()
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                      failure_stage="preflight", error_type=type(exc).__name__)
        _save(directory / "report.json", report)
        return report, directory
    for item in report["results"]:
        kind, request, plan = next(task for task in tasks if task[0] == item["kind"])
        attempt = directory / f"{item['repetition']:02d}-{kind}"
        attempt.mkdir(mode=0o700)
        _save(attempt / "request.json", request)
        item["status"] = "running"
        item["attempt_path"] = str(attempt.relative_to(directory))
        report["status"] = "running"
        _save(directory / "report.json", report)
        started = time.monotonic()
        stage = "transport"
        try:
            generated = generate(request["messages"], request["schema"], model=model,
                                 out_dir=attempt, timeout=timeout)
            item["transport_status"] = "completed"
            item["usage"] = generated.get("usage")
            item["provider_elapsed_seconds"] = generated.get("elapsed_seconds")
            stage = "validation"
            checked = validate_output(kind, generated["output"], request, plan)
            # Completion is published only after the validated output is saved.
            stage = "persistence"
            _save(attempt / "validated.json", checked)
            item.update(status="completed", structural_validation="passed")
            if kind == "translation":
                item["covered_source_ids"] = [key for block in checked["blocks"] for key in block["source_ids"]]
                item["block_count"] = len(checked["blocks"])
            else:
                item["summary_count"] = len(checked["summary"])
                item["concept_count"] = len(checked["concepts"])
        except (Exception, KeyboardInterrupt) as exc:
            item.update(status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                        error_type=type(exc).__name__, failure_stage=stage,
                        usage_uncertain=item.get("usage") is None)
            item.setdefault("transport_status", "unconfirmed")
            item["elapsed_seconds"] = time.monotonic() - started
            report["status"] = item["status"]
            _save(directory / "report.json", report)
            # No automatic retry or fallback. Every unstarted workload stays pending.
            return report, directory
        item["elapsed_seconds"] = time.monotonic() - started
        _save(directory / "report.json", report)
    report["status"] = "completed"
    report["timing"] = {}
    for kind, _, _ in tasks:
        elapsed = [item["elapsed_seconds"] for item in report["results"] if item["kind"] == kind]
        report["timing"][kind] = {"count": len(elapsed), "min_seconds": min(elapsed),
                                 "median_seconds": statistics.median(elapsed), "max_seconds": max(elapsed)}
    _save(directory / "report.json", report)
    return report, directory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "check", "run"))
    parser.add_argument("--confirm-subscription-use", action="store_true",
                        help="explicitly allow bounded model calls using the signed-in ChatGPT allowance")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--workload", choices=("both", "translation", "analysis"), default="both")
    parser.add_argument("--repetitions", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "results" / "subscription-probe")
    args = parser.parse_args(argv)
    if args.action == "run" and not args.confirm_subscription_use:
        parser.error("run consumes subscription allowance; supply --confirm-subscription-use")
    try:
        if args.action == "plan":
            result = plan_summary(args.repetitions, args.workload)
        elif args.action == "check":
            result = codex.check()
        else:
            result, directory = run_probe(out_dir=args.out_dir, model=args.model,
                                          repetitions=args.repetitions, timeout=args.timeout,
                                          workload=args.workload)
            result = {"status": result["status"], "report": str(directory / "report.json"),
                      "completed": sum(item["status"] == "completed" for item in result["results"]),
                      "planned": len(result["results"]), "timing": result.get("timing")}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status", "completed") == "completed" else 1
    except Exception as exc:
        print(f"Subscription probe failed ({type(exc).__name__}); no API fallback.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
