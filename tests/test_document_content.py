import base64
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image
import pytest

from lms_mcp.document_content import RenderingRequired, prepare_document_content
from lms_mcp.fixtures import diagram_png, document_bytes


def test_docx_content_is_extracted_in_order_from_actual_document():
    blocks = prepare_document_content(document_bytes("docx"), "docx")
    assert [item["type"] for item in blocks] == ["text", "image", "text"]
    assert "shortest path" in blocks[0]["text"]
    assert "2x + 3 = 17" in blocks[2]["text"]
    assert base64.b64decode(blocks[1]["data"]) == diagram_png()


def test_pdf_renders_whole_page_instead_of_losing_vector_content():
    blocks = prepare_document_content(document_bytes("pdf"), "pdf")
    assert [item["type"] for item in blocks] == ["text", "image"]
    assert blocks[0]["text"] == "Page 1 of 1"
    with Image.open(BytesIO(base64.b64decode(blocks[1]["data"]))) as image:
        assert max(image.size) == 1568
        assert image.getextrema()[0][0] < 100


def test_word_equations_request_rendering_instead_of_silent_omission():
    output = BytesIO()
    with ZipFile(BytesIO(document_bytes("docx"))) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "word/document.xml":
                data = data.replace(b"</w:body>", b'<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"/></w:body>')
            target.writestr(name, data)
    with pytest.raises(RenderingRequired):
        prepare_document_content(output.getvalue(), "docx")


def test_bad_documents_and_unqualified_formats_never_return_partial_content():
    for data, case in [(b"", "pdf"), (b"x" * (10 * 1024 * 1024 + 1), "pdf"), (b"not a ZIP", "docx")]:
        with pytest.raises(ValueError):
            prepare_document_content(data, case)
    with pytest.raises(RenderingRequired):
        prepare_document_content(b"unqualified", "pptx")
