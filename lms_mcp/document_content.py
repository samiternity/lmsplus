"""Bounded in-memory PDF/DOCX content preparation, without LLM calls.

PDF pages are rendered intact so equations, charts and layout survive. Simple
DOCX documents use ordered text and referenced raster images. Documents needing
Office rendering fail explicitly; no incomplete text-only result is returned.
"""

import base64
from io import BytesIO
import posixpath
import threading
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_CONTENT_BYTES = 3 * 1024 * 1024
MAX_PAGES = 10
MAX_ZIP_PAGES = 16
_PDF_LOCK = threading.Lock()  # PDFium requires serialized calls within a process.
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
PIC_URI = "http://schemas.openxmlformats.org/drawingml/2006/picture"


class RenderingRequired(ValueError):
    """The fast path cannot preserve this document; render the original layout."""


class BoundedTreeBuilder(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.elements = 0
        self.depth = 0

    def doctype(self, name, public_id, system_id):
        raise ValueError("Document XML declarations are unsupported")

    def start(self, tag, attrs):
        self.elements += 1
        self.depth += 1
        if self.elements > 50_000 or self.depth > 128:
            raise ValueError("Document XML exceeds the processing limit")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)


def _checked_xml(data):
    return ET.fromstring(data, parser=ET.XMLParser(target=BoundedTreeBuilder()))


def _image_block(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    else:
        raise RenderingRequired("Office image requires rendering")
    from PIL import Image
    try:
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > 25_000_000 or image.width <= 0 or image.height <= 0:
                raise ValueError("Document image exceeds the processing limit")
            image.verify()
    except (OSError, Image.DecompressionBombError):
        raise ValueError("Invalid document image") from None
    return {"type": "image", "mime_type": mime, "data": base64.b64encode(data).decode()}


def _check_budget(content):
    size = sum(len(item.get("text", "").encode()) + len(item.get("data", "")) for item in content)
    if size > MAX_CONTENT_BYTES:
        raise ValueError("Document exceeds the direct-content budget; no partial result returned")
    return content


def pdf_content(data, *, zip_bundle=False):
    import pypdfium2 as pdfium

    content = []
    with _PDF_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            count = len(pdf)
            max_pages = MAX_ZIP_PAGES if zip_bundle else MAX_PAGES
            if not 0 < count <= max_pages:
                raise ValueError(f"Direct-content PDF must contain 1–{max_pages} pages")
            for index in range(count):
                page = pdf[index]
                try:
                    width, height = page.get_size()
                    if not width > 0 or not height > 0:
                        raise ValueError("Invalid PDF page size")
                    bitmap = page.render(scale=1568 / max(width, height))
                    try:
                        image = bitmap.to_pil()
                        try:
                            output = BytesIO()
                            if zip_bundle:
                                image.save(output, format="WEBP", lossless=True, method=1)
                                block = {"type": "image", "mime_type": "image/webp",
                                         "data": base64.b64encode(output.getvalue()).decode()}
                            else:
                                image.save(output, format="PNG")
                                block = _image_block(output.getvalue())
                        finally:
                            image.close()
                    finally:
                        bitmap.close()
                    content.append({"type": "text", "text": f"Page {index + 1} of {count}"})
                    content.append(block)
                    _check_budget(content)
                finally:
                    page.close()
        finally:
            pdf.close()
    return content


def docx_content(data):
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
                raise ValueError("Expanded Office document exceeds the processing limit")
            if any(entry.file_size > 8 * 1024 * 1024 for entry in entries):
                raise ValueError("Office document part exceeds the processing limit")
            document = _checked_xml(archive.read("word/document.xml"))
            relationships = _checked_xml(archive.read("word/_rels/document.xml.rels"))
            rels = {item.attrib["Id"]: item.attrib for item in relationships.findall(REL + "Relationship")}
            # These features need layout rendering instead of incomplete XML extraction.
            for node in document.iter():
                local = node.tag.rsplit("}", 1)[-1]
                if local in {"oMath", "oMathPara", "pict", "object", "altChunk", "txbxContent", "srcRect", "headerReference", "footerReference"}:
                    raise RenderingRequired("Office layout or equation requires rendering")
                if node.tag == A + "graphicData" and node.get("uri") != PIC_URI:
                    raise RenderingRequired("Office chart or drawing requires rendering")

            content, text = [], []

            def flush():
                value = "".join(text).strip()
                text.clear()
                if value:
                    content.append({"type": "text", "text": value})

            def walk(node):
                if node.tag == W + "t":
                    text.append(node.text or "")
                    return
                if node.tag in (W + "tab", W + "br"):
                    text.append("\t" if node.tag == W + "tab" else "\n")
                if node.tag == A + "blip":
                    relationship = rels.get(node.get(R + "embed"))
                    if not relationship or relationship.get("TargetMode") == "External" or node.get(R + "link"):
                        raise RenderingRequired("Office image reference requires rendering")
                    target = posixpath.normpath(posixpath.join("word", relationship["Target"]))
                    if not target.startswith("word/") or "\\" in target:
                        raise ValueError("Invalid Office image reference")
                    flush()
                    content.append(_image_block(archive.read(target)))
                for child in node:
                    walk(child)
                if node.tag in (W + "p", W + "tr"):
                    text.append("\n")
                elif node.tag == W + "tc":
                    text.append(" | ")

            walk(document)
            flush()
            if not content:
                raise ValueError("Document contains no readable content")
            return _check_budget(content)
    except (BadZipFile, KeyError, ET.ParseError):
        raise ValueError("Invalid Office document") from None


def prepare_document_content(data, file_type):
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise ValueError("Document must be nonempty and at most 10 MiB")
    if file_type == "pdf":
        return pdf_content(data)
    if file_type == "docx":
        return docx_content(data)
    raise RenderingRequired("This Office format requires PDF rendering")
