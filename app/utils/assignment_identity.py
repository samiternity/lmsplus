"""Semantic assignment identities; LMS transport tokens are not durable IDs."""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import unicodedata


def normalize_label(value):
    """Normalize readable labels without removing meaningful punctuation."""
    if not isinstance(value, str):
        return ''
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def validate_courses(courses):
    """Reject ambiguous or token-only discovery metadata before scraping."""
    names = [normalize_label(course.get('name')) for course in courses]
    codes = [course.get('code') for course in courses]
    if (not all(names) or len(set(names)) != len(names) or
            not all(isinstance(code, str) and code for code in codes) or
            len(set(codes)) != len(codes) or
            any(name == normalize_label(code) for name, code in zip(names, codes))):
        raise ValueError('Course metadata is missing or ambiguous')


def legacy_assignment_id(row):
    return row.get('assignment_id') or f"{row.get('assignment_no', '')}_{row.get('title', '')}_{row.get('course_code', '')}"


def assignment_identity(account_id, semester_name, row):
    course = normalize_label(row.get('course_name'))
    semester = normalize_label(semester_name)
    if not semester or not course or course == normalize_label(row.get('course_code')):
        return None
    number = str(row.get('assignment_no') or '').strip()
    if number.isdecimal() and int(number) > 0:
        item = ['number', str(int(number))]
    else:
        title = normalize_label(row.get('title'))
        if not title:
            return None
        item = ['title', title]
    payload = json.dumps([account_id, semester, course, item], ensure_ascii=False)
    return 'v2-' + hashlib.sha256(payload.encode()).hexdigest()


def snapshot_rows(snapshot):
    return snapshot.get('all_assignments', snapshot.get('pending', []) + snapshot.get('missed', []))


def assignment_snapshot_payload(unified, snapshot):
    """Checkpoint legacy aliases in the same write that replaces their snapshot."""
    payload = {'assignments_snapshot': snapshot}
    previous = unified.get('assignments_snapshot')
    if (previous and not unified.get('assignment_tracking_migration_snapshot') and
            unified.get('assignment_tracking_v2', {}).get('identity_version') != 2):
        # Retain only identity metadata, never assignment attachments or submission data.
        fields = ('assignment_id', 'assignment_no', 'title', 'course_code', 'course_name')
        payload['assignment_tracking_migration_snapshot'] = {
            'latest_semester': previous.get('latest_semester'),
            'latest_semester_name': previous.get('latest_semester_name'),
            'all_assignments': [{field: row.get(field) for field in fields}
                                for row in snapshot_rows(previous)],
        }
    return payload


def identity_index(account_id, snapshot):
    """Return unique identities and a count of unusable or conflicting rows."""
    rows = snapshot_rows(snapshot)
    keys = [assignment_identity(account_id, snapshot.get('latest_semester_name'), row) for row in rows]
    counts = Counter(keys)
    unique = {key: row for key, row in zip(keys, rows) if key and counts[key] == 1}
    return unique, sum(1 for key in keys if not key or counts[key] > 1)


def notification_timestamp(record):
    """Read either legacy timestamp strings or delivery metadata in naive PKT."""
    value = record if isinstance(record, str) else record.get('last_notified') if isinstance(record, dict) else None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone(timedelta(hours=5))).replace(tzinfo=None)
        return parsed
    except (ValueError, TypeError):
        return None


def migrate_tracking(account_id, legacy, snapshot, previous_snapshot):
    """Baseline current work silently and translate only unambiguous aliases."""
    current, invalid = identity_index(account_id, snapshot)
    if invalid:
        raise ValueError('Cannot migrate ambiguous assignment identities')
    aliases = defaultdict(set)
    for key, row in current.items():
        aliases[legacy_assignment_id(row)].add(key)

    previous = previous_snapshot or {}
    previous_term = previous.get('latest_semester_name')
    same_term = normalize_label(previous_term) == normalize_label(snapshot.get('latest_semester_name'))
    if not previous_term:
        same_term = bool(previous.get('latest_semester') and previous.get('latest_semester') == snapshot.get('latest_semester'))
    if same_term:
        old_rows = snapshot_rows(previous)
        # Recover token-only legacy labels only through an exact, unique course token.
        by_code = defaultdict(set)
        for row in current.values():
            by_code[row.get('course_code')].add(row['course_name'])
        old_keys = []
        for row in old_rows:
            repaired = dict(row)
            if (not normalize_label(row.get('course_name')) or
                    normalize_label(row.get('course_name')) == normalize_label(row.get('course_code'))):
                names = by_code.get(row.get('course_code'), set())
                repaired['course_name'] = next(iter(names)) if len(names) == 1 else ''
            old_keys.append(assignment_identity(account_id, snapshot.get('latest_semester_name'), repaired))
        counts = Counter(old_keys)
        for key, row in zip(old_keys, old_rows):
            if key in current and counts[key] == 1:
                aliases[legacy_assignment_id(row)].add(key)

    deadlines = {}
    for alias, keys in aliases.items():
        if len(keys) != 1:
            continue
        key = next(iter(keys))
        for kind in ('new', '24h', '3d', 'overdue'):
            source = legacy.get('notified_deadlines', {}).get(f'{alias}_{kind}')
            timestamp = notification_timestamp(source)
            if timestamp is None:
                continue
            destination = f'{key}_{kind}'
            existing = deadlines.get(destination)
            if existing is None or timestamp > notification_timestamp(existing):
                record = deepcopy(source)
                # All reminder comparisons use naive PKT, including legacy timestamps.
                if isinstance(record, str):
                    record = timestamp.isoformat()
                else:
                    record['last_notified'] = timestamp.isoformat()
                deadlines[destination] = record
    return {'identity_version': 2, 'known_assignments': sorted(current), 'notified_deadlines': deadlines}
