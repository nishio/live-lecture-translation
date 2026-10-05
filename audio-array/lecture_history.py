"""Read-only pages of a server-selected session's persisted analysis history.

The caller supplies its own result_dir, never a path supplied by an HTTP client.
A cursor freezes an append-only file prefix and generation-time cutoff. Invalid
complete records are counted and skipped; an unterminated last record is never
published. File replacement/truncation invalidates the cursor rather than mixing
sessions or silently returning a different history.
"""
import base64
import hashlib
import json
import math
from pathlib import Path
import time

MAX_LIMIT = 60
MAX_HISTORY_BYTES = 64 * 1024 * 1024
MAX_CURSOR_BYTES = 2048
HISTORY_FIELDS = frozenset({
    "headline", "summary", "flow", "concepts", "questions", "source_ranges",
    "through_seconds", "generated_at", "provider", "model", "evidence_scope",
    "semantic_quality_verified", "uncertain_source_ids", "block_translations",
})


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def read_history_page(result_dir, *, session_id, limit=30, cursor=None, through_generated_at=None):
    """Return newest-first items; next_cursor continues the same snapshot.

    through_generated_at is inclusive and may only narrow the initial present
    cutoff. On later pages the cursor's cutoff is authoritative. Missing or
    invalid generated_at records are excluded, not treated as historical proof.
    skipped counts describe the entire frozen snapshot, not just this page.
    """
    if not isinstance(session_id, str) or not session_id:
        raise ValueError('session_id is required')
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError('limit must be between 1 and 60')
    scope = hashlib.sha256((str(Path(result_dir).resolve()) + '\0' + session_id).encode()).hexdigest()
    saved = None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > MAX_CURSOR_BYTES:
                raise ValueError()
            saved = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if (saved['v'] != 1 or saved['scope'] != scope
                    or type(saved['size']) is not int or not 0 <= saved['size'] <= MAX_HISTORY_BYTES
                    or type(saved['before']) is not int or not 0 <= saved['before'] <= saved['size']
                    or not _number(saved['cutoff']) or not isinstance(saved['digest'], str)):
                raise ValueError()
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise ValueError('invalid history cursor for this session') from exc
        cutoff = saved['cutoff']
        if cutoff > time.time():
            raise ValueError('history cutoff is in the future; reload history')
        if through_generated_at is not None and through_generated_at != cutoff:
            raise ValueError('history cutoff cannot change between pages')
    else:
        cutoff = time.time()
        if through_generated_at is not None:
            if not _number(through_generated_at):
                raise ValueError('invalid generation cutoff')
            cutoff = min(cutoff, through_generated_at)
    path = Path(result_dir) / 'analysis-history.jsonl'
    if path.is_symlink():
        raise ValueError('history file must not be a symbolic link')
    try:
        with path.open('rb') as stream:
            data = stream.read((saved['size'] if saved else MAX_HISTORY_BYTES) + 1)
    except FileNotFoundError:
        if saved:
            raise ValueError('history snapshot is no longer available')
        data = b''
    if saved:
        data = data[:saved['size']]
        if len(data) != saved['size'] or hashlib.sha256(data).hexdigest() != saved['digest']:
            raise ValueError('history snapshot changed; reload history')
    elif len(data) > MAX_HISTORY_BYTES:
        raise ValueError('history exceeds the supported read limit')
    digest = hashlib.sha256(data).hexdigest()
    before = saved['before'] if saved else len(data)
    skipped = dict(malformed=0, incomplete=0, missing_generated_at=0, future=0)
    records = []
    offset = 0
    for raw in data.splitlines(keepends=True):
        start = offset
        offset += len(raw)
        if not raw.endswith(b'\n'):
            skipped['incomplete'] += 1
            continue
        try:
            item = json.loads(raw)
        except (ValueError, UnicodeError):
            skipped['malformed'] += 1
            continue
        if (not isinstance(item, dict) or not _number(item.get('through_seconds'))
                or not isinstance(item.get('headline'), dict)
                or not isinstance(item['headline'].get('text'), str)
                or not item['headline']['text'].strip()):
            skipped['malformed'] += 1
            continue
        if not _number(item.get('generated_at')):
            skipped['missing_generated_at'] += 1
            continue
        if item['generated_at'] > cutoff:
            skipped['future'] += 1
            continue
        if start < before:
            records.append((start, {key: value for key, value in item.items() if key in HISTORY_FIELDS}))
    # Persisted order breaks ties and stays stable even if wall time moves back.
    selected = list(reversed(records))[:limit]
    more = len(records) > limit
    next_cursor = None
    if more:
        payload = dict(v=1, scope=scope, size=len(data), digest=digest,
                       before=selected[-1][0], cutoff=cutoff)
        next_cursor = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode()
    return dict(session_id=session_id, items=[item for _, item in selected],
                next_cursor=next_cursor, has_more=more, skipped=skipped,
                snapshot_generated_at=cutoff)
