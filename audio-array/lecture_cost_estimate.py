#!/usr/bin/env python3
"""Estimate cloud cadence costs from published aggregates; never call a model.

No private session, key, authorization, network, or model dependency is read.
These scenarios are planning assumptions, not measured alternative schedules.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys

from lecture_settings import load_settings, validate_settings


MODEL = 'gpt-6.1-sol'
EVIDENCE = 'docs/experiments/update-cadence-cost.md'
PRICING_URL = 'https://developers.openai.com/api/docs/models/gpt-6.1-sol'
# Five middle translation requests and three fresh analysis requests. The
# startup/tail translations and the reused analysis response are excluded.
TRANSLATION_INPUT_PER_REQUEST = Decimal('0.0050885')
TRANSLATION_OUTPUT_PER_REQUEST = Decimal('0.00441')
ANALYSIS_PER_REQUEST = Decimal('0.0244135')
TRANSLATION_MEAN_SECONDS = Decimal('10.093636191793484')
ANALYSIS_MEAN_SECONDS = Decimal('22.18511120833379')


def duration_hours(value):
    try:
        hours = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('--hours must be a finite number greater than 0 and at most 24.') from None
    if not hours.is_finite() or not 0 < hours <= 24:
        raise ValueError('--hours must be a finite number greater than 0 and at most 24.')
    return hours


def _decimal(value):
    return str(value.quantize(Decimal('0.000001')))


def scenario(translation_seconds, analysis_seconds, hours):
    settings = validate_settings({'schema_version': 1,
        'translation_interval_seconds': translation_seconds,
        'analysis_interval_seconds': analysis_seconds})
    hours = duration_hours(hours)
    translation_rate = Decimal(3600) / settings['translation_interval_seconds']
    analysis_rate = Decimal(3600) / settings['analysis_interval_seconds']
    # Translating the same lecture does not multiply its translated output.
    # Repeated prompt/context input is conservatively scaled per request.
    translated_output = TRANSLATION_OUTPUT_PER_REQUEST * 60
    translated_input = TRANSLATION_INPUT_PER_REQUEST * translation_rate
    analysis = ANALYSIS_PER_REQUEST * analysis_rate
    main = translated_input + translated_output + analysis
    # Separate sensitivity scenario, not an upper bound or confidence interval.
    fixed_size = ((TRANSLATION_INPUT_PER_REQUEST + TRANSLATION_OUTPUT_PER_REQUEST)
                  * translation_rate + analysis)
    baseline = (TRANSLATION_INPUT_PER_REQUEST + TRANSLATION_OUTPUT_PER_REQUEST) * 60 + ANALYSIS_PER_REQUEST * 30
    occupied = {'translation': TRANSLATION_MEAN_SECONDS / translation_seconds,
                'analysis': ANALYSIS_MEAN_SECONDS / analysis_seconds}
    return {**settings,
        'nominal_requests_per_hour': {'translation': _decimal(translation_rate), 'analysis': _decimal(analysis_rate)},
        'translation_volume_constant': {
            'translation_input_usd_per_hour': _decimal(translated_input),
            'translation_output_usd_per_hour': _decimal(translated_output),
            'analysis_usd_per_hour': _decimal(analysis),
            'usd_per_hour': _decimal(main), 'total_usd': _decimal(main * hours),
            'increase_usd_per_hour_vs_60_120': _decimal(main - baseline),
            'ratio_vs_60_120': _decimal(main / baseline)},
        'fixed_request_size': {'usd_per_hour': _decimal(fixed_size), 'total_usd': _decimal(fixed_size * hours)},
        'fixed_duration_stage_load': {kind: _decimal(load) for kind, load in occupied.items()},
        'warnings': [f'{kind}: measured duration would fill or exceed its own processing interval; completion cadence is not guaranteed.'
                     for kind, load in occupied.items() if load >= 1]
            + (['The 15-second canonical ASR chunks limit fresh source availability; shorter translation intervals cannot guarantee more frequent text.'] if translation_seconds < 15 else [])}


def build_estimate(settings, hours=1):
    settings = validate_settings(settings)
    hours = duration_hours(hours)
    selected = scenario(settings['translation_interval_seconds'], settings['analysis_interval_seconds'], hours)
    return {'schema_version': 1, 'estimate_only': True, 'model': MODEL,
        'hours': str(hours), 'additional_api_cost_of_estimation_usd': '0',
        'evidence': EVIDENCE, 'pricing_checked_date': '2026-10-05', 'pricing_url': PRICING_URL,
        'recorded_standard_usd_per_million_tokens': {'input': '2', 'cached_input': '0.10', 'cache_write': '2.50', 'output': '10'},
        'measured_request_counts': {'translation_middle': 5, 'analysis_fresh': 3},
        'selected': selected,
        'comparisons': [scenario(t, a, hours) for t, a in [(60, 120), (30, 60), (20, 60), (15, 60)]],
        'assumptions': [
            'Main scenario keeps translated output volume per hour constant, scales all translation input cost with request frequency, and scales analysis with frequency.',
            'The fixed-request-size scenario also scales translation output; it is a sensitivity comparison, not a bound.',
            'Each independent stage load holds its recorded mean request duration constant. Parallel timing and future batch sizes are unmeasured; this does not simulate waiting time.',
            'Nominal request rates omit source gaps, sentence waits, contention, initial/final work and failures.',
            'A short single-speaker sample and three fresh analyses do not establish long-session cost, latency, or quality.',
            'No allowance for retries, unresolved reservations, other budget users, development-assistant usage or electricity.',
            'Concurrency alone does not multiply token charges; changed context, request frequency, retries and cache use can change cost.',
            'These are cloud API estimates. Local model inference has no API charge; energy and local-model quality are not estimated.',
            'Settings do not grant cloud authorization, change its budget, or modify an already running application.']}


def print_estimate(report):
    print(f"クラウド費用の試算: {report['model']} / {report['hours']}時間（この計算のAPI費用 $0）")
    print('翻訳 / 整理    翻訳量を一定にした試算       1回の要求量も同じと置く別試算')
    selected = report['selected']
    for row in report['comparisons'] + ([] if any(
            (r['translation_interval_seconds'], r['analysis_interval_seconds']) ==
            (selected['translation_interval_seconds'], selected['analysis_interval_seconds'])
            for r in report['comparisons']) else [selected]):
        main, other = row['translation_volume_constant'], row['fixed_request_size']
        mark = ' ← 選択中' if (row['translation_interval_seconds'], row['analysis_interval_seconds']) == (selected['translation_interval_seconds'], selected['analysis_interval_seconds']) else ''
        print(f"{row['translation_interval_seconds']:>4}秒 / {row['analysis_interval_seconds']:>3}秒  "
              f"${Decimal(main['usd_per_hour']):.2f}/時・合計 ${Decimal(main['total_usd']):.2f}    "
              f"${Decimal(other['usd_per_hour']):.2f}/時・合計 ${Decimal(other['total_usd']):.2f}{mark}")
    change = selected['translation_volume_constant']
    print(f"選択中の設定は60秒/120秒比 {Decimal(change['ratio_vs_60_120']):.2f}倍、差額 ${Decimal(change['increase_usd_per_hour_vs_60_120']):+.2f}/時。")
    print('短い講演の実績からの試算です。翻訳する総量は同じでも、指示・文脈の再送と整理の回数が増えます。')
    print('2つの試算は上下限ではありません。短い間隔での実費・品質・更新遅延は未測定です。')
    loads = selected['fixed_duration_stage_load']
    print(f"独立した各処理の所要時間÷間隔: 翻訳 {Decimal(loads['translation']) * 100:.0f}% / 整理 {Decimal(loads['analysis']) * 100:.0f}%（旧実測時間を使った参考値）。")
    print('翻訳と整理は並行できます。同じ要求量・回数なら並列化だけでは料金は増えません。並列実行時の所要時間は未測定です。')
    if any(Decimal(value) >= 1 for value in loads.values()):
        print('処理時間がその処理の間隔以上になる試算です。同じ処理を重ねて起動せず、完了を待ちます。')
    if selected['translation_interval_seconds'] < 15:
        print('保存する原文は15秒単位の認識です。それより短い設定でも毎回新しい原文があるとは限りません。')
    print('初回・終了時の追加処理、再試行、未確定予約、他の利用分は別です。予算上限は承認JSONで管理します。')
    print('Codex等の開発利用料・電気代は未測定です。設定は次回起動から適用し、起動済みアプリや保存再生には反映しません。')
    print(f"根拠: {EVIDENCE} / 単価確認 {report['pricing_checked_date']}: {PRICING_URL}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', type=Path)
    parser.add_argument('--hours', default='1')
    parser.add_argument('--translation-interval', type=int)
    parser.add_argument('--analysis-interval', type=int)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        intervals = (args.translation_interval, args.analysis_interval)
        if any(v is not None for v in intervals):
            if args.settings is not None or any(v is None for v in intervals):
                raise ValueError('Specify either --settings or both explicit intervals.')
            settings = {'schema_version': 1, 'translation_interval_seconds': intervals[0], 'analysis_interval_seconds': intervals[1]}
        else:
            settings = load_settings(args.settings)
        report = build_estimate(settings, args.hours)
    except (OSError, ValueError) as exc:
        print(f'見積もりを作成できません: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_estimate(report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
