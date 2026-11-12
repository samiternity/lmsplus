"""Generate synthetic visual assignment documents entirely in memory."""

from io import BytesIO
import struct
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
import zlib


QUESTION = (
    "1. Find the shortest path from A to C in the diagram. Show your working.\n"
    "2. Solve 2x + 3 = 17."
)
MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
WIDTH, HEIGHT = 480, 210
FONT = {
    "A": ["010", "101", "111", "101", "101"],
    "B": ["110", "101", "110", "101", "110"],
    "C": ["011", "100", "100", "100", "011"],
    "0": ["111", "101", "101", "101", "111"],
    "2": ["110", "001", "010", "100", "111"],
    "4": ["101", "101", "111", "001", "001"],
    "7": ["111", "001", "010", "010", "010"],
}


def diagram_rgb():
    pixels = bytearray(b"\xff" * (WIDTH * HEIGHT * 3))

    def dot(x, y):
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            i = (y * WIDTH + x) * 3
            pixels[i:i + 3] = b"\x00\x00\x00"

    def line(x1, y1, x2, y2):
        steps = max(abs(x2 - x1), abs(y2 - y1))
        for step in range(steps + 1):
            x = round(x1 + (x2 - x1) * step / steps)
            y = round(y1 + (y2 - y1) * step / steps)
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    dot(x + dx, y + dy)

    def label(value, x, y):
        for character in value:
            for row, pattern in enumerate(FONT[character]):
                for col, bit in enumerate(pattern):
                    if bit == "1":
                        for dx in range(5):
                            for dy in range(5):
                                dot(x + col * 5 + dx, y + row * 5 + dy)
            x += 22

    line(65, 145, 235, 45)
    line(245, 45, 415, 145)
    line(65, 160, 415, 160)
    label("A", 40, 148)
    label("B", 233, 13)
    label("C", 425, 148)
    label("4", 130, 64)
    label("7", 330, 64)
    label("20", 215, 174)
    return bytes(pixels)


def diagram_png():
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    raw = diagram_rgb()
    scanlines = b"".join(b"\0" + raw[y * WIDTH * 3:(y + 1) * WIDTH * 3] for y in range(HEIGHT))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))


def pdf_bytes():
    content = (
        b"BT /F1 18 Tf 50 770 Td (LMS+ visual ingestion test) Tj ET\n"
        b"BT /F1 12 Tf 50 735 Td (1. Find the shortest path from A to C in the diagram.) Tj ET\n"
        b"BT /F1 12 Tf 50 715 Td (Show your working.) Tj ET\n"
        b"q 480 0 0 210 50 470 cm /Diagram Do Q\n"
        b"BT /F1 12 Tf 50 430 Td (2. Solve 2x + 3 = 17.) Tj ET\n"
    )
    image = zlib.compress(diagram_rgb())
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> /XObject << /Diagram 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (f"<< /Type /XObject /Subtype /Image /Width {WIDTH} /Height {HEIGHT} "
         f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length {len(image)} >>\nstream\n").encode()
        + image + b"\nendstream",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream",
    ]
    output = BytesIO()
    output.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(output.tell())
        output.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = output.tell()
    output.write(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.write(f"{offset:010} 00000 n \n".encode())
    output.write(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return output.getvalue()


def docx_bytes():
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        def write(name, data):
            info = ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, data)

        write("[Content_Types].xml", '''<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>''')
        write("_rels/.rels", '''<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>''')
        write("word/_rels/document.xml.rels", '''<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="diagram" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/diagram.png"/>
</Relationships>''')
        write("word/document.xml", '''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>
<w:p><w:r><w:t>LMS+ visual ingestion test</w:t></w:r></w:p>
<w:p><w:r><w:t>1. Find the shortest path from A to C in the diagram. Show your working.</w:t></w:r></w:p>
<w:p><w:r><w:drawing><wp:inline><wp:extent cx="5486400" cy="2400300"/>
<wp:docPr id="1" name="Diagram"/><a:graphic>
<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>
<pic:nvPicPr><pic:cNvPr id="0" name="Diagram"/><pic:cNvPicPr/></pic:nvPicPr>
<pic:blipFill><a:blip r:embed="diagram"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="5486400" cy="2400300"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>
<w:p><w:r><w:t>2. Solve 2x + 3 = 17.</w:t></w:r></w:p>
<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:bottom="1440" w:left="1440" w:right="1440"/></w:sectPr>
</w:body></w:document>''')
        write("word/media/diagram.png", diagram_png())
    return output.getvalue()


def document_bytes(case):
    if case == "pdf":
        return pdf_bytes()
    if case == "docx":
        return docx_bytes()
    raise ValueError("Unknown test document")
