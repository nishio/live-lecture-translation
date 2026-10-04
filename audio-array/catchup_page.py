#!/usr/bin/env python3
"""Merge per-segment ASR and speaker turns into a readable catch-up transcript.

Writes transcript.json (all lines, with provenance), transcript.md (plain text for
summarising) and index.html (self-contained, readable on Mac/iPad/iPhone). No audio
is embedded. Speaker labels are diarization estimates, not identities.
"""
import argparse
from datetime import datetime
import html
import json
import math
import os
from pathlib import Path
import tempfile

# Whisper heuristics for likely hallucination on silence/noise. Lines are kept and flagged.
NO_SPEECH_PROB, LOW_LOGPROB, HIGH_COMPRESSION = .6, -1., 2.4
EXPECTED_LANGUAGES = {"ja", "en"}
# Phrases Whisper commonly emits for silence, handling noise, or music.
COMMON_HALLUCINATIONS = {"you", "thank you", "thanks for watching", "ご視聴ありがとうございました",
                         "ご視聴ありがとうございました。", "おやすみなさい"}


def speaker_for(start, end, turns):
    overlap = {}
    for turn in turns:
        amount = min(end, turn["end_seconds"]) - max(start, turn["start_seconds"])
        if amount > 0:
            overlap[turn["speaker"]] = overlap.get(turn["speaker"], 0) + amount
    if not overlap:
        return None, []
    ranked = sorted(overlap, key=lambda s: (-overlap[s], s))
    duration = max(end - start, 1e-6)
    return ranked[0], [s for s in ranked[1:] if overlap[s] / duration >= .3]


def doubt_reasons(segment, text, language):
    reasons = []
    if segment.get("no_speech_prob", 0) > NO_SPEECH_PROB and segment.get("avg_logprob", 0) < LOW_LOGPROB:
        reasons.append("no_speech")
    if segment.get("compression_ratio", 0) > HIGH_COMPRESSION:
        reasons.append("repetition")
    if language not in EXPECTED_LANGUAGES:
        reasons.append("unexpected_language")
    if text.lower().strip(" .!。") in {p.lower().strip(" .!。") for p in COMMON_HALLUCINATIONS}:
        reasons.append("common_hallucination")
    return reasons


def bounded_model_times(item, base, limit):
    """Keep invalid model timing reviewable without emitting NaN or out-of-audio times."""
    raw_start, raw_end = item.get("start"), item.get("end")
    finite = lambda value: type(value) is int or (type(value) is float and math.isfinite(value))
    start_valid, end_valid = finite(raw_start), finite(raw_end)
    duration = limit - base
    # Absolute offset subtraction can make a valid boundary a few ulps shorter.
    outside = not (start_valid and end_valid and 0 <= raw_start <= raw_end <= duration + 1e-9)
    # Clamp offsets before addition, including unusually large integer values.
    start = min(max(raw_start, 0), duration) if start_valid else 0
    end = min(max(raw_end, start), duration) if end_valid else start
    return base + start, base + end, outside


def build_lines(manifest, asr_reports, turns=None, started_wall_ms=None):
    """asr_reports maps segment index -> transcribe_local report for that segment's mono WAV."""
    lines = []
    for segment in manifest["segments"]:
        report = asr_reports.get(segment["index"])
        if report is None:
            continue
        for chunk in report["chunks"]:
            base = segment["start_seconds"] + chunk["source_start_seconds"]
            limit = segment["start_seconds"] + chunk["source_end_seconds"]
            language = chunk["raw_result"].get("language")
            for item in chunk["raw_result"].get("segments", []):
                text = item["text"].strip()
                if not text:
                    continue
                start, end, outside = bounded_model_times(item, base, limit)
                reasons = doubt_reasons(item, text, language)
                if outside:
                    reasons.append("timestamp_outside_audio")
                # Rounding a sub-centisecond chunk edge must not move it outside the chunk.
                rounded_start = min(max(round(start, 2), base), limit)
                rounded_end = min(max(round(end, 2), rounded_start), limit)
                line = {"start_seconds": rounded_start, "end_seconds": rounded_end, "text": text,
                        "language": language, "segment": segment["index"], "chunk": chunk["index"]}
                if turns is not None:
                    line["speaker"], line["overlapping_speakers"] = speaker_for(start, end, turns)
                    if line["speaker"] is None:
                        reasons.append("no_speaker_turn")
                line["uncertain"], line["doubt_reasons"] = bool(reasons), reasons
                if started_wall_ms is not None:
                    line["approx_wall_ms"] = started_wall_ms + round(start * 1000)
                lines.append(line)
    lines.sort(key=lambda line: (line["start_seconds"], line["segment"], line["chunk"]))
    return lines


def clock(line):
    if "approx_wall_ms" in line:
        return datetime.fromtimestamp(line["approx_wall_ms"] / 1000).strftime("%H:%M:%S")
    seconds = int(line["start_seconds"])
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def elapsed(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def speaker_name(line):
    speaker = line.get("speaker")
    return speaker.replace("speaker_", "話者") if speaker else "?"


def render_markdown(title, lines):
    rows = [f"# {title}", ""]
    for line in lines:
        mark = " (不確か)" if line["uncertain"] else ""
        rows.append(f"[{clock(line)}] {speaker_name(line)}: {line['text']}{mark}")
    return "\n".join(rows) + "\n"


PAGE_STYLE = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e4e1d9;--card:#fff;--accent:#2f5d8a;
--s0:#2f5d8a;--s1:#a3462d;--s2:#3b7a45;--s3:#7a4c95;--s4:#8a6d1f;--s5:#23777a;--s6:#9c3f68;--s7:#555}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#171716;--fg:#e9e7e1;--muted:#9c9a93;
--line:#2c2b29;--card:#1f1f1d;--accent:#8db6e0;--s0:#8db6e0;--s1:#e39a82;--s2:#8fcf98;--s3:#c6a2e0;
--s4:#dcc070;--s5:#7fcfd2;--s6:#e59bbd;--s7:#bbb}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.65 -apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px;z-index:1}
h1{font-size:17px;margin:0 0 4px}.meta{color:var(--muted);font-size:13px}
.tools{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;align-items:center}
input[type=search]{flex:1;min-width:160px;font:inherit;padding:6px 10px;border:1px solid var(--line);
border-radius:8px;background:var(--card);color:var(--fg)}
label{font-size:13px;color:var(--muted)}a.jump{font-size:13px;color:var(--accent)}
main{max-width:760px;margin:0 auto;padding:8px 16px 64px}
h2{font-size:13px;color:var(--muted);font-weight:600;margin:24px 0 6px;border-bottom:1px solid var(--line)}
.l{display:grid;grid-template-columns:4.6em 3.4em 1fr;gap:8px;padding:3px 0}
.t{color:var(--muted);font-variant-numeric:tabular-nums;font-size:13px;padding-top:2px}
.s{font-size:13px;font-weight:600;padding-top:2px}.x{overflow-wrap:anywhere}
.u .x{color:var(--muted);font-style:italic}.hide-u .u{display:none}.l.gone{display:none}
.lang{font-size:11px;color:var(--muted);margin-left:4px}
details{margin-top:6px;font-size:13px;color:var(--muted)}
"""

PAGE_SCRIPT = """
const q=document.getElementById('q'),rows=[...document.querySelectorAll('.l')];
q.addEventListener('input',()=>{const v=q.value.trim().toLowerCase();
rows.forEach(r=>r.classList.toggle('gone',v&&!r.textContent.toLowerCase().includes(v)));});
document.getElementById('u').addEventListener('change',e=>document.body.classList.toggle('hide-u',!e.target.checked));
"""


DOUBT_LABELS = {"no_speech": "無音らしい", "repetition": "繰り返し", "unexpected_language": "日英以外",
               "common_hallucination": "無音時の定型句", "no_speaker_turn": "発話区間外",
               "timestamp_outside_audio": "認識時刻が音声範囲外・不正"}


def render_html(title, lines, summary):
    speakers = sorted({line.get("speaker") for line in lines if line.get("speaker")})
    colors = {s: f"var(--s{i % 8})" for i, s in enumerate(speakers)}
    body, section = [], None
    for line in lines:
        block = int(line["start_seconds"] // 300)  # 5-minute headings for skimming
        if block != section:
            section = block
            body.append(f'<h2 id="m{block * 5}">{html.escape(clock(line))}〜 '
                        f'（開始から{block * 5}分）</h2>')
        color = colors.get(line.get("speaker"), "var(--muted)")
        overlap = line.get("overlapping_speakers") or []
        who = speaker_name(line) + ("+" if overlap else "")
        lang = f'<span class="lang">{html.escape(line["language"])}</span>' if line.get("language") not in (None, "ja") else ""
        reasons = "・".join(DOUBT_LABELS[r] for r in line["doubt_reasons"])
        tip = f'経過 {elapsed(line["start_seconds"])}' + (f' / 不確か: {reasons}' if reasons else "")
        body.append(f'<div class="l{" u" if line["uncertain"] else ""}" title="{html.escape(tip)}">'
                    f'<span class="t">{html.escape(clock(line))}</span>'
                    f'<span class="s" style="color:{color}">{html.escape(who)}</span>'
                    f'<span class="x">{html.escape(line["text"])}{lang}</span></div>')
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in summary["notes"])
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>{PAGE_STYLE}</style></head><body class="hide-u">'
            f'<header><h1>{html.escape(title)}</h1><div class="meta">{html.escape(summary["line"])}</div>'
            f'<div class="tools"><input id="q" type="search" placeholder="発言を検索">'
            f'<label><input id="u" type="checkbox"> 不確かな行も表示</label>'
            f'<a class="jump" href="#end">最新へ</a></div></header><main>'
            + "".join(body) +
            f'<details id="end"><summary>この文字起こしについて</summary><ul>{notes}</ul></details>'
            f'</main><script>{PAGE_SCRIPT}</script></body></html>')


def write_text_atomic(path, text):
    """Publish a complete UTF-8 artifact, retaining the previous file on failure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_page(bundle, asr_dir, out, diarization=None, session=None, processing=None):
    bundle, asr_dir, out = Path(bundle), Path(asr_dir), Path(out)
    manifest = json.loads((bundle / "session-manifest.json").read_text())
    reports = {}
    for segment in manifest["segments"]:
        path = asr_dir / f"segment-{segment['index']:05d}.json"
        if path.exists():
            report = json.loads(path.read_text())
            if report["source_sha256"] != segment["output_sha256"]["diarization_input"]:
                raise ValueError(f"ASR output does not match bundle audio: {path}")
            reports[segment["index"]] = report
    turns = None
    if diarization is not None:
        result = json.loads((Path(diarization) / "result.json").read_text())
        if result.get("status") != "completed":
            raise ValueError("diarization did not complete")
        turns = result["turns"]
    started = json.loads((Path(session) / "session.json").read_text()).get("started_wall_ms") if session else None
    lines = build_lines(manifest, reports, turns, started)
    name = Path(manifest["source_session"]).name
    day = datetime.fromtimestamp(started / 1000).strftime("%Y-%m-%d %H:%M") if started else name
    title = f"会話の記録 {day}"
    missing = len(manifest["segments"]) - len(reports)
    doubtful = sum(line["uncertain"] for line in lines)
    processed_seconds = sum(float(report.get("audio_seconds", max(
        (chunk["source_end_seconds"] for chunk in report["chunks"]), default=0))) for report in reports.values())
    if processing is None:
        processing = {"state": "completed" if not missing else "partial", "stage": "asr",
                      "processed_segments": len(reports), "total_segments": len(manifest["segments"]),
                      "processed_audio_seconds": processed_seconds, "total_audio_seconds": manifest["audio_seconds"],
                      "remaining_audio_seconds": max(0, manifest["audio_seconds"] - processed_seconds)}
    modes = {report.get("mode") or "unknown" for report in reports.values()}
    languages = set()
    for report in reports.values():
        options = report.get('decode_options')
        language = options.get('language', 'unknown') if isinstance(options, dict) else 'unknown'
        languages.add('auto' if language is None else language)
    asr_mode = next(iter(modes)) if len(modes) == 1 else ('mixed' if modes else 'unknown')
    asr_language = next(iter(languages)) if len(languages) == 1 else ('mixed' if languages else 'unknown')
    asr_note = "日本語以外と判定された行には言語コードを付けています。"
    if modes == {"pauses"}:
        asr_note += "各保存分割を無音区切りで認識しています。"
    elif modes == {"whole"}:
        asr_note += "各音声分割全体を一つの入力として認識しています。保存分割の境界を跨ぐ文脈は使っていません。"
    else:
        asr_note += "入力の分割方法は各ASR結果を参照してください。"
    summary = {"line": f"録音済み音声の追いつき処理 / {elapsed(processed_seconds)}処理済み / 全{elapsed(manifest['audio_seconds'])}"
                       + f" / {len(lines) - doubtful}行（不確か{doubtful}行）"
                       + (f" / 話者候補{len({l['speaker'] for l in lines if l.get('speaker')})}" if turns is not None else " / 話者分離なし")
                       + (f" / 未処理{missing}分割" if missing else ""),
               "notes": [f"セッション: {name}（raw slot {manifest['mono_raw_slot']} の16kHz monoを使用）",
                         "録音済みの音声を処理した結果です。今その場で聞こえた発言のライブ表示ではありません。",
                         "時刻は録音開始時刻＋経過秒からのおおよその値です。",
                         "話者番号はこの録音内だけの推定で、人物の特定ではありません。「+」は重なり候補。",
                         "「不確か」な行（無音らしい・繰り返し・日英以外・無音時の定型句・話者分離の発話区間外）は既定で隠しています。削除はしていません。",
                         asr_note,
                         "認識精度・話者分離精度は実会話で未評価です。"]}
    out.mkdir(parents=True, exist_ok=True)
    transcript = {"schema_version": 1, "source_session": manifest["source_session"], "started_wall_ms": started,
         "source_kind": "buffered_catchup", "source_label": "録音済み音声の追いつき処理",
         "asr_language": asr_language, "asr_mode": asr_mode,
         "audio_seconds": manifest["audio_seconds"], "asr_segments": sorted(reports),
         "processed_audio_seconds": processed_seconds,
         "remaining_audio_seconds": max(0, manifest["audio_seconds"] - processed_seconds),
         "processing": processing,
         "missing_asr_segments": [s["index"] for s in manifest["segments"] if s["index"] not in reports],
         "diarization": str(Path(diarization).resolve()) if diarization else None, "lines": lines}
    markdown = render_markdown(title, lines)
    html_page = render_html(title, lines, summary)
    json_page = json.dumps(transcript, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    write_text_atomic(out / "transcript.md", markdown)
    write_text_atomic(out / "index.html", html_page)
    # JSON is the dashboard's authoritative publication and is committed last.
    write_text_atomic(out / "transcript.json", json_page)
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--asr", required=True, type=Path)
    parser.add_argument("--diarization", type=Path)
    parser.add_argument("--session", type=Path, help="raw session directory, for approximate wall-clock times")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        lines = write_page(args.bundle, args.asr, args.out, args.diarization, args.session)
        print(f"{len(lines)} lines -> {(args.out / 'index.html').resolve()}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
