"""Real LibreOffice rendering and timeout cleanup on an in-memory filesystem."""

from pathlib import Path
import shutil

import pytest

from lms_mcp.core import ConnectorError, Deadline
from lms_mcp.fixtures import document_bytes
from lms_mcp.office_content import render_office


@pytest.mark.skipif(not shutil.which('libreoffice'), reason='LibreOffice is not installed')
def test_office_renderer_outputs_pdf_and_cleans_ram_directory(monkeypatch):
    root = Path('/dev/shm')
    before = set(root.glob('lms-mcp-*'))
    rendered = render_office(document_bytes('docx'), 'docx', Deadline(20))
    assert rendered.startswith(b'%PDF-')
    assert set(root.glob('lms-mcp-*')) == before


@pytest.mark.skipif(not shutil.which('libreoffice'), reason='LibreOffice is not installed')
def test_office_timeout_kills_child_and_cleans_ram_directory():
    root = Path('/dev/shm')
    before = set(root.glob('lms-mcp-*'))
    with pytest.raises(ConnectorError) as failure:
        render_office(document_bytes('docx'), 'docx', Deadline(.05))
    assert failure.value.code == 'processing_limit'
    assert set(root.glob('lms-mcp-*')) == before
