"""Isolated, synthetic LMS+ server for Inspector and repeatable evaluations.

Run with `python -m evaluations.lmsplus_fixture`. Never mount this in production.
"""

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

from lms_mcp.assignments import AssignmentConnector
from lms_mcp.core import ConnectorSettings
from lms_mcp.fixtures import diagram_png
from lms_mcp.http_security import ConnectorHTTPGuard
from lms_mcp.oauth import AssignmentOAuthProvider
from lms_mcp.runtime import ConnectorRuntime
from lms_mcp.server import build_student_server
from lms_mcp.sessions import SessionCoordinator
from lms_mcp.store import MemoryStore


ORIGIN = 'http://127.0.0.1:8765'
TOKEN = 'lmsplus-synthetic-evaluation-only'
COURSES = [
    {'code': 'net', 'name': 'Networks'}, {'code': 'alg', 'name': 'Algorithms'},
    {'code': 'db', 'name': 'Databases'}, {'code': 'prob', 'name': 'Probability'},
]
DOCUMENTS = {
    'route': 'Routing Lab. Undirected graph edges: A-B = 4 ms, B-C = 7 ms, A-C = 20 ms. '
             'Find the fastest path from A to C. A packet is 12000 bits; link rate is 6000000 bits/second. '
             'End-to-end time is path latency plus one serialization delay. See the attached graph.',
    'reliability': 'Reliability Study. Three independent links in series have availability 0.9, 0.8, 0.95. '
                   'Find path availability. A message has 24 header bytes and 96 payload bytes. Find header percentage.',
    'greedy': 'Greedy Review. Non-preemptive tasks A, B, C take 3, 2, 5 ms respectively. '
              'All arrive at time 0. Execute shortest task first, with no switching overhead. '
              'Find completion time of each task and sum of completion times.',
    'index': 'Index Analysis. A lookup reads 120 pages using a heap scan or 8 pages using an index. '
             'Find pages saved and percentage reduction. For a separate full tree of fanout 100 with '
             '1000000 leaves, find the number of edges on a root-to-leaf path.',
    'risk': 'Risk Worksheet. Two independent services in series have availability 0.98 and 0.97. '
            'Find combined availability. Each of 100 independent requests has failure probability 0.02. '
            'Find the expected number of failures.',
}


def row(key: str, course: int, title: str, day: int) -> dict:
    return {'assignment_id': key, 'assignment_no': key, 'title': title,
            'course_code': COURSES[course]['code'], 'course_name': COURSES[course]['name'],
            'submission_status': 'pending', 'deadline': f'2026-11-{day:02}T23:55:00',
            'assignment_file': f'https://synthetic.example/{key}'}


ROWS = [row('route', 0, 'Routing Lab', 20), row('reliability', 0, 'Reliability Study', 22),
        row('greedy', 1, 'Greedy Review', 21), row('index', 2, 'Index Analysis', 23),
        row('risk', 3, 'Risk Worksheet', 24)]
SNAPSHOT = {'latest_semester': 'synthetic-2026', 'refreshed_at': '2026-11-19T06:00:00+00:00',
            'all_assignments': [*ROWS, row('archive', 1, 'Archived Quiz', 18)]}


class EvaluationClock(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = datetime(2026, 11, 19, 8, tzinfo=timezone.utc)
        return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return cls.now(timezone.utc).replace(tzinfo=None)


@contextmanager
def fixed_clock():
    # Only this standalone fixture process/test sees the frozen academic clock.
    # OAuth expiry continues to use real time.
    with patch('lms_mcp.assignments.datetime', EvaluationClock), patch(
            'app.utils.assignment_categorization.datetime', EvaluationClock):
        yield


class SyntheticUniversity:
    def options(self, cookies, deadline):
        return {'semester': 'synthetic-2026', 'courses': COURSES}

    def assignments(self, cookies, semester, course, deadline):
        return [dict(item) for item in ROWS if item['course_code'] == course]

    def download(self, cookies, url, deadline):
        return url.rsplit('/', 1)[-1].encode()


def prepare(data, deadline):
    key = data.decode()
    content = [{'type': 'text', 'text': DOCUMENTS[key]}]
    if key == 'route':
        content.append({'type': 'image', 'mimeType': 'image/png',
                        'data': base64.b64encode(diagram_png()).decode()})
    return content, 'synthetic_fixture'


def build_fixture():
    store = MemoryStore()
    settings = ConnectorSettings(ORIGIN, 'https://synthetic.example', bytes(32), frozenset({'synthetic'}))
    coordinator = SessionCoordinator(store, settings.key, lambda account, deadline: {'synthetic': 'only'})
    assignments = AssignmentConnector(settings, store, coordinator, SyntheticUniversity(), lambda account: SNAPSHOT, prepare)
    oauth = AssignmentOAuthProvider(settings, store, lambda user, account: (user, account) == ('fixture', 'synthetic'))
    connection = {'user_id': 'fixture', 'account_id': 'synthetic', 'client_id': 'synthetic-inspector', 'revoked': False}
    tokens, _ = oauth._issue('fixture', connection, TOKEN, 'synthetic-refresh-only')
    for key, value in tokens.items():
        store.put(key, value)
    store.put('mcp_connections/fixture', connection)
    runtime = ConnectorRuntime(settings, store, oauth, assignments)
    return ConnectorHTTPGuard(build_student_server(runtime), ORIGIN)


if __name__ == '__main__':
    import uvicorn

    with fixed_clock():
        uvicorn.run(build_fixture(), host='127.0.0.1', port=8765, log_level='warning')
