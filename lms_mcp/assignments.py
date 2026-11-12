"""Account-scoped assignment selection without dashboard refresh side effects."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
import unicodedata

from lms_mcp.core import ConnectorError, Deadline
from lms_mcp.sessions import RequestSession
from lms_mcp.store import digest
from lms_mcp.privacy import private_document_operation


def normalize(value):
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in
                          unicodedata.normalize('NFKC', value).casefold()).split())


@dataclass
class AssignmentResult:
    data: dict
    content: list = field(default_factory=list)
    method: str | None = None


class AssignmentConnector:
    def __init__(self, settings, store, coordinator, university, metadata, prepare):
        self.settings, self.store, self.coordinator = settings, store, coordinator
        self.university, self.metadata, self.prepare = university, metadata, prepare

    @private_document_operation
    def list_snapshot(self, account_id, status, deadline):
        self.settings.check_account(account_id)
        categories = {'pending': 'pending', 'overdue': 'missed'}
        if status != 'both' and status not in categories:
            raise ConnectorError('invalid_request', 'Choose pending, overdue, or both.')
        with deadline.timing.measure('metadata_lookup'):
            snapshot = self.metadata(account_id) or {}

        rows = snapshot.get('assignments', snapshot.get('all_assignments'))
        if not isinstance(rows, list) or snapshot.get('error'):
            raise ConnectorError('snapshot_unavailable', 'No saved assignment snapshot is available. Refresh Assignments in LMS+ and try again.')

        timestamp = snapshot.get('last_updated' if 'assignments' in snapshot else 'refreshed_at')
        try:
            captured = datetime.fromisoformat(timestamp)
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=timezone.utc)
            captured = captured.astimezone(timezone.utc)
            age = max(0, int((datetime.now(timezone.utc) - captured).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            captured, age = None, None

        from app.utils.assignment_categorization import categorize_assignments
        categorized = categorize_assignments(rows)
        groups = {}
        for name, category in categories.items():
            if status not in (name, 'both'):
                continue
            direction = 1 if name == 'pending' else -1
            ordered = sorted(categorized[category], key=lambda row: (
                direction * row['hours_until_due'] if row['hours_until_due'] is not None else float('inf')))
            groups[name] = [
                {key: row.get(key) for key in ('title', 'course_name', 'deadline')}
                for row in ordered
            ]
        return AssignmentResult({
            'status': 'ready', 'source': 'saved_assignment_snapshot',
            'snapshot_at': captured.isoformat() if captured else None, 'snapshot_age_seconds': age,
            'snapshot_partial': bool(snapshot.get('failed_courses') or snapshot.get('warning')),
            'deadline_timezone': 'Asia/Karachi', 'assignments': groups,
        })

    @private_document_operation
    def get(self, account_id, course, assignment_id=None, deadline=None):
        started = time.monotonic()
        self.settings.check_account(account_id)
        if not isinstance(course, str) or not course.strip() or len(course) > 200:
            raise ConnectorError('invalid_request', 'Provide a course name or course identifier.')
        if assignment_id is not None and (not isinstance(assignment_id, str) or len(assignment_id) > 128):
            raise ConnectorError('invalid_request', 'Provide an assignment identifier returned by this tool.')
        deadline = deadline or Deadline()
        timing = deadline.timing
        with timing.measure('session_lookup'):
            session = RequestSession(self.coordinator, account_id, deadline)
        with timing.measure('metadata_lookup'):
            snapshot = self.metadata(account_id) or {}
            metadata_key = 'mcp_course_metadata/' + digest(account_id)
            known = self.store.get(metadata_key) or {}
        semester = snapshot.get('latest_semester') or known.get('semester')
        courses = {}
        if known.get('semester') == semester:
            courses.update({c['code']: c for c in known.get('courses', [])})
        for assignment in snapshot.get('all_assignments', []):
            if assignment.get('course_code') and assignment.get('course_name'):
                courses[assignment['course_code']] = {'code': assignment['course_code'], 'name': assignment['course_name']}

        def matches():
            exact = [c for c in courses.values() if course == c['code'] or normalize(course) == normalize(c['name'])]
            if exact:
                return exact
            query = normalize(course)
            return [c for c in courses.values() if query and query in normalize(c['name'])]

        candidates = matches()
        if not semester or not candidates:
            options = session.call(lambda cookies: self.university.options(cookies, deadline), stage='course_discovery')
            courses = {c['code']: c for c in options['courses']}
            semester = options['semester']
            self.store.put(metadata_key, options)
            candidates = matches()
        if not candidates:
            return AssignmentResult({'status': 'course_not_found', 'courses': list(courses.values())})
        if len(candidates) != 1:
            return AssignmentResult({'status': 'course_ambiguous', 'courses': candidates,
                                     'message': 'Ask the student which course they mean.'})
        selected_course = candidates[0]
        rows = session.call(lambda cookies: self.university.assignments(cookies, semester, selected_course['code'], deadline),
                            stage='course_scrape')
        from app.utils.assignment_categorization import categorize_assignments, sort_assignments_by_deadline
        pending = sort_assignments_by_deadline(categorize_assignments(rows)['pending'], ascending=True)

        def identifier(row):
            return digest('\0'.join(str(v or '') for v in (account_id, semester, selected_course['code'],
                          row.get('assignment_id'), row.get('assignment_no'), row.get('title'))))[:32]

        choices = [{'assignment_id': identifier(row), 'title': row.get('title'), 'deadline': row.get('deadline')}
                   for row in pending]
        if assignment_id is not None:
            matches_by_id = [row for row in pending if identifier(row) == assignment_id]
            if len(matches_by_id) != 1:
                raise ConnectorError('assignment_unavailable', 'That assignment is no longer pending in this course. Request the pending list again.')
            selected = matches_by_id[0]
        elif not pending:
            return AssignmentResult({'status': 'no_pending_work', 'course': selected_course})
        elif len(pending) > 1:
            return AssignmentResult({'status': 'assignment_selection_required', 'course': selected_course,
                                     'assignments': choices, 'message': 'Ask the student which assignment to read.'})
        else:
            selected = pending[0]
        if not selected.get('assignment_file'):
            raise ConnectorError('missing_attachment', 'This pending assignment has no downloadable attachment.')
        data = session.call(lambda cookies: self.university.download(cookies, selected['assignment_file'], deadline),
                            stage='download')
        timing.record_bytes('attachment', len(data))
        with timing.measure('document_preparation'):
            content, method = self.prepare(data, deadline)
        timing.record_bytes('content', sum(len(block.get('text', '').encode()) + len(block.get('data', ''))
                                           for block in content))
        deadline.remaining()
        return AssignmentResult({'status': 'ready', 'course': selected_course, 'assignment': {
            'assignment_id': identifier(selected), 'title': selected.get('title'), 'deadline': selected.get('deadline')},
            'preparation_method': method, 'university_status_checked_live': True,
            'server_elapsed_ms': round((time.monotonic() - started) * 1000)}, content, method)
