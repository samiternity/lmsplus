"""Semantic assignment identities; LMS transport tokens are not durable IDs."""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import logging
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


def assignment_title_identity(account_id, semester_name, row):
    """Disambiguate reused display numbers without using transport tokens."""
    base = assignment_identity(account_id, semester_name, row)
    title = normalize_label(row.get('title'))
    if not base or not title:
        return None
    return base + '~' + hashlib.sha256(title.encode()).hexdigest()


def assignment_snapshot_payload(unified, snapshot, account_id=None):
    """Checkpoint legacy aliases in the same write that replaces their snapshot."""
    payload = {'assignments_snapshot': snapshot}
    previous = unified.get('assignments_snapshot')
    tracking = unified.get('assignment_tracking_v2') or {}
    needs_checkpoint = tracking.get('identity_version') != 2
    if previous and not needs_checkpoint and account_id:
        needs_checkpoint = reconcile_tracking(account_id, tracking, snapshot, previous) != tracking
    if (previous and not unified.get('assignment_tracking_migration_snapshot') and
            needs_checkpoint):
        # Retain only identity metadata, never assignment attachments or submission data.
        fields = ('assignment_id', 'assignment_no', 'title', 'course_code', 'course_name')
        payload['assignment_tracking_migration_snapshot'] = {
            'latest_semester': previous.get('latest_semester'),
            'latest_semester_name': previous.get('latest_semester_name'),
            'all_assignments': [{field: row.get(field) for field in fields}
                                for row in snapshot_rows(previous)],
        }
    return payload


def identity_index(account_id, snapshot, known_assignments=()):
    """Return unique identities and a count of unusable or conflicting rows."""
    groups, missing = _identity_groups(account_id, snapshot, known_assignments)
    unique = {key: rows[0] for key, rows in groups.items() if len(rows) == 1}
    return unique, len(missing) + sum(len(rows) for rows in groups.values() if len(rows) > 1)


def _identity_groups(account_id, snapshot, known_assignments=()):
    rows = snapshot_rows(snapshot)
    keys = [assignment_identity(account_id, snapshot.get('latest_semester_name'), row)
            if normalize_label(row.get('title')) else None for row in rows]
    counts = Counter(keys)
    # LMS display numbers can be reused for different work in one course. Once a
    # number has needed title disambiguation, keep that scheme even if a row disappears.
    disambiguated = {key.split('~', 1)[0] for key in known_assignments if '~' in key}
    keys = [assignment_title_identity(account_id, snapshot.get('latest_semester_name'), row)
            if key and (counts[key] > 1 or key in disambiguated) else key
            for key, row in zip(keys, rows)]
    groups = defaultdict(list)
    missing = []
    for key, row in zip(keys, rows):
        if key:
            groups[key].append(row)
        else:
            missing.append(row)
    return groups, missing


def log_identity_conflicts(account_id, snapshot):
    """Report collision evidence without logging labels or LMS action IDs."""
    groups, missing = _identity_groups(account_id, snapshot)
    counts = Counter()
    for row in missing:
        reason = 'missing_title' if not normalize_label(row.get('title')) else 'missing_context'
        counts[reason] += 1
    for rows in groups.values():
        if len(rows) < 2:
            continue
        ids = [row.get('assignment_id') for row in rows]
        if not all(ids):
            reason = 'collision_missing_action_id'
        elif len(set(ids)) == 1:
            reason = 'collision_shared_action_id'
        else:
            reason = 'collision_distinct_action_ids'
        counts[reason] += len(rows)
    if counts:
        logging.getLogger(__name__).warning('Assignment identity diagnostic: %s', dict(counts))


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


def _copy_reminders(source_id, target_id, source_deadlines, target_deadlines):
    for kind in ('new', '24h', '3d', 'overdue'):
        source = source_deadlines.get(f'{source_id}_{kind}')
        timestamp = notification_timestamp(source)
        if timestamp is None:
            continue
        destination = f'{target_id}_{kind}'
        existing = target_deadlines.get(destination)
        existing_time = notification_timestamp(existing)
        if existing_time is None or timestamp > existing_time:
            record = deepcopy(source)
            # All reminder comparisons use naive PKT, including legacy timestamps.
            if isinstance(record, str):
                record = timestamp.isoformat()
            else:
                record['last_notified'] = timestamp.isoformat()
            target_deadlines[destination] = record


def reconcile_tracking(account_id, tracking, snapshot, previous_snapshot):
    """Carry known work and reminders forward when its number becomes ambiguous."""
    previous = previous_snapshot or {}
    term = snapshot.get('latest_semester_name')
    if not term or normalize_label(previous.get('latest_semester_name')) != normalize_label(term):
        return tracking
    known = set(tracking.get('known_assignments', []))
    current, _ = identity_index(account_id, snapshot, known)
    old, _ = identity_index(account_id, previous, known)
    by_title = {assignment_title_identity(account_id, term, row): key for key, row in current.items()}
    result = deepcopy(tracking)
    for old_key, row in old.items():
        key = by_title.get(assignment_title_identity(account_id, term, row))
        if key and key != old_key and old_key in known:
            known.add(key)
            _copy_reminders(old_key, key, tracking.get('notified_deadlines', {}),
                            result.setdefault('notified_deadlines', {}))
    if known != set(tracking.get('known_assignments', [])):
        result['known_assignments'] = sorted(known)
    return result


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
        current_by_title = {assignment_title_identity(account_id, snapshot.get('latest_semester_name'), row): key
                            for key, row in current.items()}
        current_bases = defaultdict(list)
        for key, row in current.items():
            current_bases[assignment_identity(account_id, snapshot.get('latest_semester_name'), row)].append(key)
        repaired_rows = []
        for row in old_rows:
            repaired = dict(row)
            if (not normalize_label(row.get('course_name')) or
                    normalize_label(row.get('course_name')) == normalize_label(row.get('course_code'))):
                names = by_code.get(row.get('course_code'), set())
                repaired['course_name'] = next(iter(names)) if len(names) == 1 else ''
            repaired_rows.append(repaired)
            old_keys.append(assignment_title_identity(account_id, snapshot.get('latest_semester_name'), repaired))
        counts = Counter(old_keys)
        old_bases = Counter(assignment_identity(account_id, snapshot.get('latest_semester_name'), row)
                            for row in repaired_rows)
        for title_key, row, repaired in zip(old_keys, old_rows, repaired_rows):
            key = current_by_title.get(title_key)
            if not key:
                base = assignment_identity(account_id, snapshot.get('latest_semester_name'), repaired)
                candidates = current_bases.get(base, [])
                if base and old_bases[base] == 1 and len(candidates) == 1:
                    key = candidates[0]
            if key and counts[title_key] == 1:
                aliases[legacy_assignment_id(row)].add(key)

    deadlines = {}
    for alias, keys in aliases.items():
        if len(keys) != 1:
            continue
        key = next(iter(keys))
        _copy_reminders(alias, key, legacy.get('notified_deadlines', {}), deadlines)
    return {'identity_version': 2, 'known_assignments': sorted(current), 'notified_deadlines': deadlines}
