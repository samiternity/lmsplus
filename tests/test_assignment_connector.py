"""Read-only selection, strict scraping, recovery fencing, and document safeguards."""

import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import threading
import time
from zipfile import ZIP_DEFLATED, ZipFile

from bs4 import BeautifulSoup
import pytest

from lms_mcp.assignments import AssignmentConnector
from lms_mcp.core import ConnectorError, ConnectorSettings, Deadline
from lms_mcp.fixtures import document_bytes
from lms_mcp.office_content import prepare_assignment_content, qualified_docx
from lms_mcp.sessions import RequestSession, SessionCoordinator, UniversitySession
from lms_mcp.store import MemoryStore, digest
from lms_mcp.university import issue_download_token


SETTINGS = ConnectorSettings('https://fixture-lms-plus.web.app', 'https://university-proxy.test',
                             bytes.fromhex('22' * 32), frozenset({'a', 'b'}))
ROW = {'title': 'Circuit assignment', 'assignment_no': 1, 'course_code': 'course-1',
       'course_name': 'Networks', 'submission_status': 'pending', 'deadline': '2099-01-01T23:55:00',
       'assignment_file': 'https://lms.bahria.edu.pk/assignment.pdf'}


def test_proxy_host_configuration_preserves_both_hosts(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'testing')
    monkeypatch.setenv('LMS_MCP_ENABLED', 'true')
    monkeypatch.setenv('LMS_MCP_DOWNLOAD_KEY', '22' * 32)
    monkeypatch.setenv('LMS_MCP_ALLOWED_ACCOUNTS', 'a')
    monkeypatch.setenv('LMS_MCP_WORKER_URL', SETTINGS.worker_origin)
    monkeypatch.setenv('APP_URL', SETTINGS.origin)
    hosts = ('service.us-central1.run.app', 'service-uc.a.run.app')
    for separator in (' ', ','):
        monkeypatch.setenv('LMS_MCP_PROXY_HOSTS', separator.join(hosts))
        assert ConnectorSettings.from_environment().proxy_hosts == hosts
    monkeypatch.setenv('LMS_MCP_PROXY_HOSTS', '"' + hosts[0])
    with pytest.raises(ValueError, match='explicit Cloud Run hostnames'):
        ConnectorSettings.from_environment()


def test_production_accepts_accounts_without_a_whitelist_and_honors_kill_switch(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('APP_URL', 'https://lmsplus.xyz')
    monkeypatch.setenv('LMS_MCP_ENABLED', 'true')
    monkeypatch.setenv('LMS_MCP_DOWNLOAD_KEY', '22' * 32)
    monkeypatch.setenv('LMS_MCP_WORKER_URL', SETTINGS.worker_origin)
    monkeypatch.delenv('LMS_MCP_PROXY_HOSTS', raising=False)
    monkeypatch.delenv('LMS_MCP_ALLOWED_ACCOUNTS', raising=False)
    settings = ConnectorSettings.from_environment()
    settings.check_account('any-student')

    # Stale Cloud Run environment values must not restrict production.
    monkeypatch.setenv('LMS_MCP_ALLOWED_ACCOUNTS', 'old-account')
    ConnectorSettings.from_environment().check_account('another-student')
    monkeypatch.setenv('APP_ENV', 'testing')
    with pytest.raises(ConnectorError, match='does not have connector access'):
        ConnectorSettings.from_environment().check_account('another-student')
    monkeypatch.setenv('LMS_MCP_ENABLED', 'false')
    with pytest.raises(ConnectorError, match='does not have connector access'):
        settings.check_account('any-student')


class University:
    def __init__(self):
        self.rows = [dict(ROW)]
        self.downloads = []
        self.scrapes = []
        self.option_calls = 0

    def options(self, cookies, deadline):
        self.option_calls += 1
        return {'semester': 'semester', 'courses': [{'code': 'course-1', 'name': 'Networks'}]}

    def assignments(self, cookies, semester, course, deadline):
        self.scrapes.append((dict(cookies), semester, course))
        return self.rows

    def download(self, cookies, url, deadline):
        self.downloads.append((dict(cookies), url))
        return document_bytes('docx')


def connector(university=None, snapshot=None):
    university = university or University()
    store = MemoryStore()
    coordinator = SessionCoordinator(store, SETTINGS.key, lambda account, deadline: {'account': account})
    snapshot = snapshot if snapshot is not None else {'latest_semester': 'semester',
        'refreshed_at': '2020-01-01', 'all_assignments': [dict(ROW)]}
    return AssignmentConnector(SETTINGS, store, coordinator, university, lambda account: snapshot,
                               prepare_assignment_content), university


def test_old_snapshot_only_resolves_course_and_live_status_always_wins():
    service, university = connector()
    result = service.get('a', 'networks')
    assert result.data['status'] == 'ready'
    assert result.content[1]['type'] == 'image'
    assert university.option_calls == 0
    university.rows = [dict(ROW, submission_file='submitted.docx')]
    assert service.get('a', 'networks').data['status'] == 'no_pending_work'
    assert len(university.scrapes) == 2
    assert len(university.downloads) == 1


@pytest.mark.parametrize('rows_key, timestamp_key', [('all_assignments', 'refreshed_at'), ('assignments', 'last_updated')])
def test_snapshot_listing_reclassifies_deadlines_without_live_access(rows_key, timestamp_key):
    from datetime import datetime, timedelta, timezone

    saved_at = datetime.now(timezone.utc) - timedelta(hours=6)
    rows = [dict(ROW), dict(ROW, title='Past due', deadline='2020-01-01T12:00:00+05:00'),
            dict(ROW, title='Submitted', submission_file='private.docx')]
    service, university = connector(snapshot={rows_key: rows, timestamp_key: saved_at.isoformat()})
    result = service.list_snapshot('a', 'both', Deadline()).data
    assert 21600 <= result['snapshot_age_seconds'] < 21610
    assert result['snapshot_at'] == saved_at.isoformat()
    assert result['assignments'] == {
        'pending': [{'title': ROW['title'], 'course_name': ROW['course_name'], 'deadline': ROW['deadline']}],
        'overdue': [{'title': 'Past due', 'course_name': ROW['course_name'], 'deadline': rows[1]['deadline']}],
    }
    assert list(service.list_snapshot('a', 'overdue', Deadline()).data['assignments']) == ['overdue']
    assert not (university.scrapes or university.downloads or university.option_calls)


def test_snapshot_listing_distinguishes_missing_from_empty_and_unknown_age():
    service, _ = connector(snapshot={})
    with pytest.raises(ConnectorError) as missing:
        service.list_snapshot('a', 'pending', Deadline())
    assert missing.value.code == 'snapshot_unavailable'
    service.metadata = lambda account: {'all_assignments': [], 'warning': 'Some courses failed'}
    result = service.list_snapshot('a', 'pending', Deadline()).data
    assert result['snapshot_age_seconds'] is None
    assert result['snapshot_partial']
    assert result['assignments'] == {'pending': []}


def test_recovery_and_retry_have_separate_stage_durations(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr('lms_mcp.core.time.monotonic', lambda: clock[0])
    deadline = Deadline()
    session = RequestSession(SessionCoordinator(MemoryStore(), SETTINGS.key, lambda *_: None),
                             'a', deadline)
    session.state = UniversitySession({'state': 'expired'})

    def recover(*_):
        clock[0] += 2
        return UniversitySession({'state': 'fresh'})

    def download(cookies):
        clock[0] += 1
        if cookies['state'] == 'expired':
            raise ConnectorError('authentication_failure', 'Expired')
        return b'file'

    monkeypatch.setattr(session.coordinator, 'refresh', recover)
    assert session.call(download, stage='download') == b'file'
    assert deadline.timing.snapshot() == {
        'tool_elapsed_ms': 4000,
        'stage_elapsed_ms': {'download': 2000, 'session_recovery': 2000},
        'stage_status': {'download': 'completed', 'session_recovery': 'completed'},
        'bytes': {},
    }


def test_multiple_pending_requires_selection_and_selected_id_is_account_scoped():
    service, university = connector()
    university.rows.append(dict(ROW, title='Second assignment', assignment_no=2))
    result = service.get('a', 'Networks')
    assert result.data['status'] == 'assignment_selection_required'
    assert not university.downloads
    assignment_id = result.data['assignments'][1]['assignment_id']
    assert service.get('a', 'Networks', assignment_id).data['assignment']['title'] == 'Second assignment'
    with pytest.raises(ConnectorError, match='no longer pending'):
        service.get('b', 'Networks', assignment_id)
    university.rows[1]['submission_file'] = 'submitted.docx'
    with pytest.raises(ConnectorError, match='no longer pending'):
        service.get('a', 'Networks', assignment_id)


def test_ambiguous_course_never_scrapes_or_downloads():
    service, university = connector(snapshot={'latest_semester': 'semester', 'all_assignments': [dict(ROW),
        dict(ROW, course_code='course-2', course_name='Networks Lab')]})
    assert service.get('a', 'network').data['status'] == 'course_ambiguous'
    assert not university.scrapes and not university.downloads


def test_missing_metadata_fetches_only_options_then_selected_course():
    service, university = connector(snapshot={})
    assert service.get('a', 'Networks').data['status'] == 'ready'
    assert university.option_calls == 1
    assert university.scrapes[0][2] == 'course-1'
    assert all(not key.startswith('dashboard') for key in service.store.docs)


def test_missing_attachment_and_upstream_errors_remain_distinct():
    service, university = connector()
    university.rows[0]['assignment_file'] = None
    with pytest.raises(ConnectorError) as error:
        service.get('a', 'Networks')
    assert error.value.code == 'missing_attachment'
    university.assignments = lambda *args: (_ for _ in ()).throw(ConnectorError('scraping_failure', 'Unknown page'))
    with pytest.raises(ConnectorError) as error:
        service.get('a', 'Networks')
    assert error.value.code == 'scraping_failure'


def test_submitted_and_overdue_follow_shared_categorization():
    service, university = connector()
    university.rows = [dict(ROW, submission_file='answer.docx'), dict(ROW, deadline='2020-01-01T00:00:00')]
    assert service.get('a', 'Networks').data['status'] == 'no_pending_work'
    assert not university.downloads


def test_coordinators_coalesce_refresh_and_never_store_plain_cookies():
    store, entered, release = MemoryStore(), threading.Event(), threading.Event()
    calls = []
    def recover(account, deadline):
        calls.append(account)
        entered.set()
        assert release.wait(2)
        return {'PHPSESSID': 'private-cookie'}
    first = SessionCoordinator(store, SETTINGS.key, recover)
    second = SessionCoordinator(store, SETTINGS.key, recover)
    with ThreadPoolExecutor(2) as pool:
        task1 = pool.submit(first.refresh, 'a', 0, Deadline(5))
        assert entered.wait(1)
        task2 = pool.submit(second.refresh, 'a', 0, Deadline(5))
        time.sleep(.05)
        release.set()
        assert task1.result().cookies == task2.result().cookies == {'PHPSESSID': 'private-cookie'}
    assert calls == ['a']
    assert 'private-cookie' not in str(store.docs)


def test_expired_lease_owner_cannot_publish_or_release_newer_lease():
    store = MemoryStore()
    key = 'mcp_sessions/' + digest('a')
    def superseded(account, deadline):
        record = store.get(key)
        record.update(lease_owner='new-owner', lease_until=time.time()+100, generation=8)
        store.put(key, record)
        return {'session': 'stale'}
    coordinator = SessionCoordinator(store, SETTINGS.key, superseded)
    with pytest.raises(ConnectorError, match='superseded'):
        coordinator.refresh('a', 0, Deadline())
    assert store.get(key)['lease_owner'] == 'new-owner'
    assert 'cookies' not in store.get(key)


def test_one_recovery_budget_covers_course_and_download():
    store, recoveries = MemoryStore(), []
    def recover(account, deadline):
        recoveries.append(account)
        return {'session': 'fresh'}
    coordinator = SessionCoordinator(store, SETTINGS.key, recover, initial=lambda account: {'session': 'expired'})
    session = RequestSession(coordinator, 'a', Deadline())
    def course(cookies):
        if cookies['session'] == 'expired':
            raise ConnectorError('authentication_failure', 'expired')
        return 'ok'
    assert session.call(course) == 'ok'
    with pytest.raises(ConnectorError):
        session.call(lambda cookies: (_ for _ in ()).throw(ConnectorError('authentication_failure', 'expired again')))
    assert recoveries == ['a']


def test_strict_scraper_distinguishes_broken_page_from_empty_table():
    from app.services.lms_scraper_service import LMSScraperService, LMSParsingError
    scraper = LMSScraperService()
    unrelated = BeautifulSoup('<html><table><tr><td>Service unavailable</td></tr></table></html>', 'html.parser')
    with pytest.raises(LMSParsingError):
        scraper.parse_assignment_data(unrelated, strict=True)
    assert scraper.parse_assignment_data(unrelated) == []
    empty = BeautifulSoup('<table><tr><th>Title</th><th>Deadline</th></tr><tr><td colspan="2">No records found</td></tr></table>', 'html.parser')
    assert scraper.parse_assignment_data(empty, strict=True) == []
    broken = BeautifulSoup('<table><tr><th>Title</th><th>Deadline</th></tr><tr><td>Lost deadline</td></tr></table>', 'html.parser')
    with pytest.raises(LMSParsingError):
        scraper.parse_assignment_data(broken, strict=True)


def edit_docx(replace=None, extra=None):
    output = BytesIO()
    with ZipFile(BytesIO(document_bytes('docx'))) as source, ZipFile(output, 'w', ZIP_DEFLATED) as target:
        for name in source.namelist():
            data = source.read(name)
            if name == 'word/document.xml' and replace:
                data = replace(data)
            target.writestr(name, data)
        for name, data in (extra or {}).items():
            target.writestr(name, data)
    return output.getvalue()


def test_docx_numbering_and_table_boundaries_preserve_question_structure():
    numbering = '''<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="3"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'''
    data = edit_docx(lambda data: data.replace(b'<w:p><w:r><w:t>1.', b'<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Question').replace(
        b'<w:sectPr>', b'<w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>4</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:sectPr>'),
        {'word/numbering.xml': numbering})
    blocks = qualified_docx(data)
    text = '\n'.join(b.get('text', '') for b in blocks)
    assert '3. Question' in text
    assert 'Table:' in text and '| A  | 4  |' in text
    assert len([b for b in blocks if b['type'] == 'image']) == 1


def test_complex_docx_selects_explicit_rendering_and_external_images_are_rejected(monkeypatch):
    import lms_mcp.office_content as office
    complex_doc = edit_docx(lambda data: data.replace(b'</w:body>', b'<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"/></w:body>'))
    monkeypatch.setattr(office, 'render_office', lambda data, kind, deadline: document_bytes('pdf'))
    blocks, method = prepare_assignment_content(complex_doc, Deadline())
    assert method == 'office_rendered_page_images' and blocks[1]['type'] == 'image'
    external = edit_docx(extra={'word/_rels/external.rels': '<Relationships><Relationship TargetMode="External" Type="image" Target="https://private.test/picture"/></Relationships>'})
    with pytest.raises(ConnectorError) as error:
        prepare_assignment_content(external, Deadline())
    assert error.value.code == 'unsupported_content'


def assignment_zip(entries):
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return output.getvalue()


def test_pending_zip_delivers_both_documents_in_archive_order(monkeypatch):
    service, university = connector()
    pdf = document_bytes('pdf')
    archive = assignment_zip([
        ('assignment/', b''),
        ('assignment/instructions.pdf', pdf),
        ('__MACOSX/assignment/._instructions.pdf', b'metadata'),
        ('assignment/figures.pdf', pdf),
    ])
    monkeypatch.setattr(university, 'download', lambda *args: archive)
    result = service.get('a', 'Networks')
    assert result.data['status'] == 'ready'
    assert result.data['preparation_method'] == 'zip_pdf_page_images'
    assert [b['text'] for b in result.content if b['type'] == 'text'] == [
        'File 1 of 2: instructions.pdf', 'Page 1 of 1',
        'File 2 of 2: figures.pdf', 'Page 1 of 1',
    ]
    assert [b['mime_type'] for b in result.content if b['type'] == 'image'] == ['image/webp', 'image/webp']


@pytest.mark.parametrize('name, data', [
    ('../instructions.pdf', b'%PDF-'),
    ('notes.txt', b'Unsupported document'),
    ('nested.zip', assignment_zip([('notes.txt', b'Nested document')])),
])
def test_zip_rejects_unsafe_or_unsupported_members(name, data):
    with pytest.raises(ConnectorError) as error:
        prepare_assignment_content(assignment_zip([(name, data)]), Deadline())
    assert error.value.code == 'unsupported_content'


@pytest.mark.parametrize('bundled', [False, True])
def test_odt_is_unsupported_directly_and_inside_zip(bundled):
    odt = assignment_zip([
        ('mimetype', b'application/vnd.oasis.opendocument.text'),
        ('content.xml', b'<document/>'),
    ])
    data = assignment_zip([('instructions.pdf', document_bytes('pdf')), ('notes.odt', odt)]) if bundled else odt
    with pytest.raises(ConnectorError, match='ODT assignments are unsupported') as error:
        prepare_assignment_content(data, Deadline())
    assert error.value.code == 'unsupported_content'


def test_zip_enforces_combined_output_budget(monkeypatch):
    import lms_mcp.office_content as office
    from lms_mcp.document_content import MAX_CONTENT_BYTES

    monkeypatch.setattr(office, 'render_pdf', lambda *_, **__: [
        {'type': 'image', 'mime_type': 'image/png', 'data': 'a' * (MAX_CONTENT_BYTES // 2)},
    ])
    archive = assignment_zip([('first.pdf', b'%PDF-'), ('second.pdf', b'%PDF-')])
    with pytest.raises(ConnectorError) as error:
        prepare_assignment_content(archive, Deadline())
    assert error.value.code == 'processing_limit'


def test_zip_page_limit_applies_to_the_bundle_only():
    import pypdfium2

    def blank_pdf(pages):
        output = BytesIO()
        with pypdfium2.PdfDocument.new() as pdf:
            for _ in range(pages):
                pdf.new_page(72, 72).close()
            pdf.save(output)
        return output.getvalue()

    first, second = blank_pdf(11), blank_pdf(5)
    with pytest.raises(ConnectorError) as error:
        prepare_assignment_content(first, Deadline())
    assert error.value.code == 'processing_limit'

    archive = assignment_zip([('first.pdf', first), ('second.pdf', second)])
    blocks, method = prepare_assignment_content(archive, Deadline())
    assert method == 'zip_pdf_page_images'
    assert sum(block['type'] == 'image' for block in blocks) == 16

    archive = assignment_zip([('first.pdf', first), ('second.pdf', blank_pdf(6))])
    with pytest.raises(ConnectorError, match='16-image limit'):
        prepare_assignment_content(archive, Deadline())


def test_processing_limit_cancellation_and_file_token_binding():
    deadline = Deadline()
    deadline.cancelled = True
    with pytest.raises(ConnectorError) as error:
        prepare_assignment_content(document_bytes('pdf'), deadline)
    assert error.value.code == 'processing_limit'
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import json
    token = issue_download_token(SETTINGS, ROW['assignment_file'], {'private': 'cookie'}, now=1000)
    raw = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
    payload = json.loads(AESGCM(SETTINGS.key).decrypt(raw[:12], raw[12:], b'mcp-download-v1'))
    assert payload['expires'] - payload['issued'] == 120
    assert payload['purpose'] == 'mcp-assignment-download'
    with pytest.raises(ConnectorError):
        issue_download_token(SETTINGS, 'https://evil.test/file.pdf', {})
