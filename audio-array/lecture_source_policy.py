"""Conservative derived selection policy; original recognition stays intact.

Uncertainty describes evidence, not whether a line has useful content. Only
empty text, narrowly recognized filler, and identifiable duplicate artifacts
are excluded. Exclusion is an audit decision, separate from canonical evidence
metadata and from whether a downstream translation has completed.
"""
from __future__ import annotations

import math
import re


SOURCE_POLICY_VERSION = 1
DOUBT_REASONS = frozenset({
    'no_speech', 'repetition', 'unexpected_language', 'common_hallucination',
    'timestamp_outside_audio', 'no_speaker_turn', 'unknown',
})
_FILLER = re.compile(r'(?:um|uh|erm)(?:[\s.,!?;:…–—-]+(?:um|uh|erm))*[\s.,!?;:…–—-]*', re.IGNORECASE | re.ASCII)
_SOURCE_ID = re.compile(r'c([0-9]{6})-l[0-9]{4}')


def normalize_doubt_reasons(row):
    """Allowlist reason labels; never pass arbitrary caller text as metadata.

    ``unknown`` means the reason is absent or unsupported, not a calibrated
    confidence score. Missing reasons on otherwise certain rows stay empty.
    """
    raw = row.get('doubt_reasons')
    if raw is None:
        return ['unknown'] if row.get('uncertain') is True else []
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return ['unknown']
    reasons = {value if isinstance(value, str) and value in DOUBT_REASONS else 'unknown'
               for value in raw}
    if not reasons and row.get('uncertain') is True:
        reasons.add('unknown')
    return sorted(reasons)


def source_metadata(row):
    """Canonical evidence metadata; selection decisions are deliberately absent."""
    uncertain = row.get('uncertain', False)
    if type(uncertain) is not bool:
        raise ValueError('uncertain must be a boolean')
    return {'uncertain': uncertain, 'doubt_reasons': normalize_doubt_reasons(row)}


def _filler_only(row):
    text = row.get('text', '')
    language = row.get('language')
    return (row.get('uncertain') is True and isinstance(language, str) and language.strip() == 'en'
            and isinstance(text, str) and bool(_FILLER.fullmatch(text.strip())))


def is_content_candidate(row):
    """Per-row content check; sequence duplicate decisions need the full plan.

    Long text, repetition scores, ``so``, thanks, numbers, and negations are not
    exclusion criteria. Even filler-shaped text is retained unless uncertain.
    """
    text = row.get('text', '')
    return isinstance(text, str) and bool(text.strip()) and not _filler_only(row)


def _same_chunk(first, later):
    explicit = lambda row: all(type(row.get(key)) is int and row[key] >= 0 for key in ('segment', 'chunk'))
    if explicit(first) and explicit(later):
        return (first['segment'], first['chunk']) == (later['segment'], later['chunk'])
    identities = [_SOURCE_ID.fullmatch(row.get('id', '')) if isinstance(row.get('id'), str) else None
                  for row in (first, later)]
    return all(identities) and identities[0].group(1) == identities[1].group(1)


def _duplicate_artifact(first, later):
    if not (first['uncertain'] and later['uncertain']
            and 'timestamp_outside_audio' in first['doubt_reasons']
            and 'timestamp_outside_audio' in later['doubt_reasons']
            and 'repetition' in later['doubt_reasons']
            and first.get('language') == later.get('language')
            and first.get('text') == later.get('text') and _same_chunk(first, later)):
        return False
    identity = first.get('id')
    if not isinstance(identity, str) or not identity or identity == later.get('id'):
        return False
    for key in ('start_seconds', 'end_seconds'):
        value = first.get(key)
        other = later.get(key)
        if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
                or type(other) not in (int, float) or value != other):
            return False
    return first['end_seconds'] >= first['start_seconds']


def plan_source_policy(lines):
    """Return copied rows with recomputed exclusion audits; do not edit raw text.

    Duplicate suppression is deliberately narrow: adjacent, identical text and
    clamped timing within one ASR chunk, with invalid-timing evidence on both
    rows and repetition evidence on the later row. Real temporal repetition and
    missing provenance remain available for translation and analysis.
    """
    planned = []
    for row in lines:
        current = dict(row)
        current.update(source_metadata(row), exclusion_reason=None, duplicate_of=None)
        text = current.get('text', '')
        if not isinstance(text, str) or not text.strip():
            current['exclusion_reason'] = 'empty'
        elif _filler_only(current):
            current['exclusion_reason'] = 'filler_only'
        elif planned and _duplicate_artifact(planned[-1], current):
            previous = planned[-1]
            current.update(exclusion_reason='duplicate_invalid_timing',
                           duplicate_of=previous['duplicate_of'] or previous['id'])
        planned.append(current)
    return planned
