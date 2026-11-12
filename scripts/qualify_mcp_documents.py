"""Prepare 15 synthetic visual cases. This does not qualify real teacher files or Claude ingestion."""

import argparse
from io import BytesIO
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lms_mcp.core import Deadline
from lms_mcp.fixtures import QUESTION, diagram_png
from lms_mcp.office_content import prepare_assignment_content


def make_docx(case):
    from docx import Document
    from docx.shared import Inches
    from docx.oxml import OxmlElement
    document = Document()
    document.add_heading('Visual assignment ' + str(case), level=1)
    document.add_paragraph(QUESTION.splitlines()[0], style='List Number' if case == 2 else None)
    document.add_picture(BytesIO(diagram_png()), width=Inches(5))
    document.add_paragraph('Figure 1. Route costs appear only inside the diagram.')
    document.add_paragraph(QUESTION.splitlines()[1])
    if case == 3:
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).merge(table.cell(0, 1)).text = 'Compare route totals'
        table.cell(1, 0).text, table.cell(1, 1).text = 'Direct', 'Via B'
    if case == 4:
        math = OxmlElement('m:oMath')
        run, text = OxmlElement('m:r'), OxmlElement('m:t')
        text.text = '2x + 3 = 17'
        run.append(text)
        math.append(run)
        document.add_paragraph()._p.append(math)
    if case == 5:
        document.sections[0].header.paragraphs[0].text = 'Show the route and both calculations.'
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def make_pptx(case):
    from pptx import Presentation
    from pptx.util import Inches
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_textbox(Inches(.4), Inches(.2), Inches(9), Inches(1)).text_frame.text = 'Visual assignment ' + str(case)
    slide.shapes.add_textbox(Inches(.4), Inches(1), Inches(9), Inches(1)).text_frame.text = QUESTION.splitlines()[0]
    slide.shapes.add_picture(BytesIO(diagram_png()), Inches(1), Inches(2), width=Inches(6))
    slide.shapes.add_textbox(Inches(.4), Inches(5), Inches(9), Inches(1)).text_frame.text = QUESTION.splitlines()[1]
    if case == 2:
        slide.shapes[2].rotation = 15
    if case == 3:
        table = slide.shapes.add_table(2, 2, Inches(7), Inches(2), Inches(2), Inches(1)).table
        table.cell(0, 0).merge(table.cell(0, 1))
        table.cell(0, 0).text = 'Route'
        table.cell(1, 0).text, table.cell(1, 1).text = 'Direct', 'Via B'
    if case == 4:
        slide.shapes[2].crop_left = .02
    if case == 5:
        second = presentation.slides.add_slide(presentation.slide_layouts[6])
        second.shapes.add_textbox(Inches(.4), Inches(1), Inches(9), Inches(1)).text_frame.text = 'Explain why the direct route costs more.'
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def make_pdf(case):
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.utils import ImageReader
    output = BytesIO()
    canvas = Canvas(output)
    canvas.setFont('Helvetica', 16)
    canvas.drawString(40, 790, 'Visual assignment ' + str(case))
    canvas.setFont('Helvetica', 10 if case == 2 else 12)
    canvas.drawString(40, 750, QUESTION.splitlines()[0])
    canvas.drawImage(ImageReader(BytesIO(diagram_png())), 40, 450, width=480, height=210)
    canvas.drawString(40, 420, QUESTION.splitlines()[1])
    if case == 3:
        canvas.line(40, 380, 400, 380)
        canvas.drawString(40, 360, 'Compare the direct route with the route through B.')
    if case == 5:
        canvas.showPage()
        canvas.drawString(40, 750, 'Explain why the direct route costs more.')
    canvas.save()
    data = output.getvalue()
    if case == 4:
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(data)
        page = document[0]
        bitmap = page.render(scale=1.5)
        image = bitmap.to_pil()
        try:
            scanned = BytesIO()
            scan = Canvas(scanned)
            scan.drawImage(ImageReader(image), 0, 0, width=595, height=842)
            scan.save()
            data = scanned.getvalue()
        finally:
            image.close(); bitmap.close(); page.close(); document.close()
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    results = []
    for kind, make in [('pdf', make_pdf), ('docx', make_docx), ('pptx', make_pptx)]:
        for case in range(1, 6):
            data = make(case)
            started = time.monotonic()
            try:
                blocks, method = prepare_assignment_content(data, Deadline())
                result = {'format': kind, 'case': case, 'prepared': True, 'method': method,
                          'source_bytes': len(data), 'image_blocks': sum(b['type'] == 'image' for b in blocks),
                          'response_bytes': len(json.dumps(blocks).encode()), 'seconds': round(time.monotonic()-started, 3)}
            except Exception as error:
                result = {'format': kind, 'case': case, 'prepared': False, 'error_type': type(error).__name__,
                          'status': getattr(error, 'code', 'preparation_failed')}
            results.append(result)
            print(json.dumps(result), flush=True)
    args.output.write_text(json.dumps({'synthetic_only': True, 'claude_ingestion_verified': False,
        'representative_teacher_files_verified': False, 'cases': results}, indent=2) + '\n')
    raise SystemExit(0 if all(result['prepared'] for result in results) else 1)


if __name__ == '__main__':
    main()
