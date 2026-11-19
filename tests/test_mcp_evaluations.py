"""Verify evaluation answers from actual authenticated, read-only MCP responses."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path
import re
from xml.etree import ElementTree

from jsonschema import validate
from starlette.testclient import TestClient

from evaluations.lmsplus_fixture import ORIGIN, TOKEN, build_fixture, fixed_clock


def test_frozen_evaluation_answers_are_supported_by_tool_responses(monkeypatch):
    monkeypatch.setenv('LMS_MCP_ENABLED', 'true')
    headers = {'Authorization': 'Bearer ' + TOKEN, 'Accept': 'application/json, text/event-stream',
               'Mcp-Protocol-Version': '2025-11-25'}
    with fixed_clock(), TestClient(build_fixture(), base_url=ORIGIN, headers=headers) as client:
        def rpc(method, params):
            response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
            assert response.status_code == 200, response.text
            result = response.json()['result']
            assert not result.get('isError'), result
            return result

        tools = rpc('tools/list', {})['tools']
        schemas = {tool['name']: tool['outputSchema'] for tool in tools}

        def call(name, **arguments):
            result = rpc('tools/call', {'name': name, 'arguments': arguments})
            validate(result['structuredContent'], schemas[name])
            return result

        pending, overdue = [], []
        offset = 0
        while True:
            page = call('lmsplus_list_assignments', status='both', limit=2, offset=offset)['structuredContent']
            assert page['snapshot_age_seconds'] == 7200
            assert not page['snapshot_partial']
            assert page['total_count'] == 6
            assert page['category_counts'] == {'pending': 5, 'overdue': 1}
            pending.extend(page['assignments']['pending'])
            overdue.extend(page['assignments']['overdue'])
            if not page['has_more']:
                break
            assert page['next_offset'] > offset
            offset = page['next_offset']
        assert len(pending) == 5 and len(overdue) == 1

        documents = {}
        for course in dict.fromkeys(row['course_name'] for row in pending):
            result = call('lmsplus_get_pending_assignment', course=course)
            data = result['structuredContent']
            if data['status'] == 'assignment_selection_required':
                assert course == 'Networks'
                assert len(data['assignments']) == 2
                results = [call('lmsplus_get_pending_assignment', course=course,
                                assignment_id=choice['assignment_id']) for choice in data['assignments']]
            else:
                results = [result]
            for result in results:
                data = result['structuredContent']
                assert data['status'] == 'ready' and data['university_status_checked_live']
                documents[data['assignment']['title']] = '\n'.join(
                    block['text'] for block in result['content'][1:] if block['type'] == 'text')
                if data['assignment']['title'] == 'Routing Lab':
                    assert any(block['type'] == 'image' for block in result['content'])

        def values(title, pattern):
            match = re.search(pattern, documents[title])
            assert match, (title, pattern)
            return [Decimal(value) for value in match.groups()]

        ab, bc, direct = values('Routing Lab', r'A-B = (\d+) ms, B-C = (\d+) ms, A-C = (\d+) ms')
        fastest = min(ab + bc, direct)
        bits, rate = values('Routing Lab', r'packet is (\d+) bits; link rate is (\d+) bits/second')
        header, payload = values('Reliability Study', r'(\d+) header bytes and (\d+) payload bytes')
        heap, index = values('Index Analysis', r'(\d+) pages using a heap scan or (\d+) pages using an index')
        fanout, leaves = values('Index Analysis', r'fanout (\d+) with (\d+) leaves')
        requests, failure = values('Risk Worksheet', r'of (\d+) independent requests has failure probability (\d+(?:\.\d+)?)')
        expected = requests * failure
        tasks = values('Greedy Review', r'take (\d+), (\d+), (\d+) ms respectively')
        elapsed, completion_sum = Decimal(0), Decimal(0)
        for duration in sorted(tasks):
            elapsed += duration
            completion_sum += elapsed
        net = values('Reliability Study', r'availability ([\d.]+), ([\d.]+), ([\d.]+)\.')
        prob = values('Risk Worksheet', r'availability ([\d.]+) and ([\d.]+)\.')
        deadlines = {row['title']: datetime.fromisoformat(row['deadline']) for row in pending}
        depth, capacity = 0, Decimal(1)
        while capacity < leaves:
            capacity *= fanout
            depth += 1
        answers = [str(fastest), str(header / (header + payload) * 100), str(heap - index - fastest),
                   'Probability' if prob[0] * prob[1] > net[0] * net[1] * net[2] else 'Networks',
                   str(abs(elapsed - fastest)), str(fastest + bits / rate * 1000),
                   str((deadlines['Index Analysis'] - deadlines['Routing Lab']).days), str(expected + depth),
                   'Index Analysis' if (heap - index) / heap > (direct - fastest) / direct else 'Routing Lab',
                   str(completion_sum * expected)]
        # Normalize exact integer arithmetic to the evaluation's required strings.
        answers = [str(int(Decimal(answer))) if re.fullmatch(r'\d+(?:\.\d+)?', answer) else answer for answer in answers]
        evaluation = ElementTree.parse(Path(__file__).parents[1] / 'evaluations/lmsplus.xml')
        pairs = evaluation.findall('qa_pair')
        assert len(pairs) == 10
        assert answers == [pair.findtext('answer') for pair in pairs]


def test_snapshot_tool_pagination_validation_and_markdown(monkeypatch):
    monkeypatch.setenv('LMS_MCP_ENABLED', 'true')
    headers = {'Authorization': 'Bearer ' + TOKEN, 'Accept': 'application/json, text/event-stream',
               'Mcp-Protocol-Version': '2025-11-25'}
    with fixed_clock(), TestClient(build_fixture(), base_url=ORIGIN, headers=headers) as client:
        def call(arguments):
            return client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': 'lmsplus_list_assignments', 'arguments': arguments}}).json()['result']

        for arguments in ({'limit': 0}, {'limit': 101}, {'limit': True}, {'offset': -1}, {'status': 'submitted'}):
            assert call(arguments)['isError']
        result = call({'status': 'both', 'limit': 2, 'response_format': 'markdown'})
        assert not result.get('isError'), result
        assert result['structuredContent']['has_more']
        assert 'Snapshot age: 7200 seconds.' in result['content'][0]['text']
        assert 'Continue with offset=2' in result['content'][0]['text']
