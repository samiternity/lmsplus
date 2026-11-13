"""Qualified DOCX extraction and bounded Office rendering on a RAM volume."""

from io import BytesIO
import os
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile
from zlib import error as DecompressionError

from lms_mcp.core import ConnectorError
from lms_mcp.document_content import (
    A, R, W, MAX_SOURCE_BYTES, MAX_EXPANDED_BYTES, MAX_ZIP_PAGES, RenderingRequired,
    _checked_xml, _check_budget, docx_content,
)


_RENDER_SLOT = threading.Semaphore(1)
_SAFE_WORD_TAGS = set(('document body p pPr pStyle r rPr t tab br tbl tblPr tblGrid gridCol tr trPr tc tcPr '
    'tcW tblW tblBorders top left bottom right insideH insideV tblLook tblLayout jc spacing ind '
    'numPr numId ilvl outlineLvl keepNext keepLines widowControl contextualSpacing '
    'rFonts b bCs i iCs u color sz szCs lang noProof proofErr bookmarkStart bookmarkEnd '
    'lastRenderedPageBreak drawing sectPr pgSz pgMar cols docGrid titlePage rsidR tblCellMar '
    'shd tblInd cantSplit trHeight tblHeader highlight fitText').split())


def checked_archive(data):
    archive = ZipFile(BytesIO(data))
    entries = archive.infolist()
    if (len(entries) > 2000 or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES
            or any(e.file_size > 8 * 1024 * 1024 for e in entries)
            or len({e.filename for e in entries}) != len(entries)):
        archive.close()
        raise ValueError('Office archive exceeds the processing limit')
    try:
        for entry in entries:
            name = entry.filename
            if name.startswith('/') or '..' in Path(name).parts or '\\' in name:
                raise ValueError('Invalid Office archive path')
            if name.lower().endswith('vbaproject.bin') or name.startswith(('Basic/', 'Scripts/')):
                raise RenderingRequired('Active Office content is unsupported')
            if name.endswith(('.xml', '.rels', '.svg')):
                root = _checked_xml(archive.read(name))
                for node in root.iter():
                    local = node.tag.rsplit('}', 1)[-1]
                    if (local in {'oleObject', 'externalData', 'altChunk', 'object', 'object-ole',
                                  'dde-source', 'dde-connection', 'script', 'iframe'}):
                        raise RenderingRequired('Active or externally linked Office content is unsupported')
                    if local == 'Relationship' and node.get('TargetMode') == 'External':
                        raise RenderingRequired('Externally linked document content is unsupported')
                    if local in {'image', 'img', 'use'}:
                        for key, value in node.attrib.items():
                            if key.rsplit('}', 1)[-1] in {'href', 'src'} and (':' in value or value.startswith('//')):
                                raise RenderingRequired('Externally linked image content is unsupported')
                    if local in {'instrText', 'fldSimple'} and re.search(
                            r'\b(INCLUDEPICTURE|INCLUDETEXT|DDE|LINK|DATABASE)\b',
                            (node.text or '') + ' '.join(node.attrib.values()), re.I):
                        raise RenderingRequired('External Office fields are unsupported')
        return archive
    except Exception:
        archive.close()
        raise


def detect_format(data):
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise ValueError('Assignment must be nonempty and at most 10 MiB')
    if data.startswith(b'%PDF-'):
        return 'pdf'
    if not data.startswith((b'PK\x03\x04', b'PK\x05\x06')):
        raise RenderingRequired('This assignment file format is unsupported')
    with ZipFile(BytesIO(data)) as archive:
        names = set(archive.namelist())
        if 'word/document.xml' in names:
            kind = 'docx'
        elif 'ppt/presentation.xml' in names:
            kind = 'pptx'
        else:
            if 'mimetype' in names:
                mime = b'application/vnd.oasis.opendocument.text'
                info = archive.getinfo('mimetype')
                if (info.file_size == len(mime) and info.compress_type in (ZIP_STORED, ZIP_DEFLATED)
                        and not info.flag_bits & 1 and archive.read(info) == mime):
                    raise RenderingRequired('ODT assignments are unsupported')
            return 'zip'
    with checked_archive(data):
        return kind


def prepare_zip(data, deadline):
    with ZipFile(BytesIO(data)) as archive:
        entries = archive.infolist()
        if (len(entries) > 64 or len({entry.filename for entry in entries}) != len(entries)
                or sum(entry.file_size for entry in entries) > MAX_SOURCE_BYTES):
            raise ValueError('ZIP exceeds the processing limit')
        files = []
        for entry in entries:
            path = PurePosixPath(entry.filename)
            mode = stat.S_IFMT(entry.external_attr >> 16) if entry.create_system == 3 else 0
            if (not path.parts or path.is_absolute() or '..' in path.parts or ':' in entry.filename
                    or '\\' in entry.filename or len(entry.filename) > 240
                    or any(ord(char) < 32 for char in entry.orig_filename)
                    or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or entry.flag_bits & 1 or entry.compress_type not in (ZIP_STORED, ZIP_DEFLATED)):
                raise RenderingRequired('The assignment ZIP contains an unsupported entry')
            if entry.is_dir() or path.name == '.DS_Store' or (
                    path.parts[0] == '__MACOSX' and path.name.startswith('._')):
                continue
            files.append(entry)
        if not files:
            raise RenderingRequired('The assignment ZIP contains no documents')
        if len(files) > MAX_ZIP_PAGES:
            raise ValueError('ZIP contains too many documents')

        content, methods = [], set()
        image_count = 0
        for index, entry in enumerate(files, 1):
            data = bytearray()
            with archive.open(entry) as member:
                for chunk in iter(lambda: member.read(65536), b''):
                    deadline.remaining()
                    data.extend(chunk)
            data = bytes(data)
            kind = detect_format(data)
            if kind == 'zip':
                raise RenderingRequired('The assignment ZIP must contain only PDF, DOCX, or PPTX files')
            blocks, method = prepare_document(data, kind, deadline, zip_bundle=True)
            image_count += sum(block['type'] == 'image' for block in blocks)
            if image_count > MAX_ZIP_PAGES:
                raise ConnectorError('processing_limit', f'The assignment ZIP exceeds the {MAX_ZIP_PAGES}-image limit.')
            methods.add(method)
            content.append({'type': 'text', 'text': f'File {index} of {len(files)}: {PurePosixPath(entry.filename).name}'})
            content.extend(blocks)
            _check_budget(content)
        return content, 'zip_' + (methods.pop() if len(methods) == 1 else 'mixed_document_content')


def qualified_docx(data):
    # Reuse existing image/path/layout checks before applying richer structure.
    validated = docx_content(data)
    images = iter(block for block in validated if block['type'] == 'image')
    with checked_archive(data) as archive:
        document = _checked_xml(archive.read('word/document.xml'))
        for node in document.iter():
            if node.tag.startswith(W) and node.tag[len(W):] not in _SAFE_WORD_TAGS:
                raise RenderingRequired('This Word feature requires layout rendering')
            if node.tag.endswith('}anchor') or node.tag.endswith('}graphicFrame'):
                raise RenderingRequired('Floating Word content requires layout rendering')
            if node.tag == W + 'br' and node.get(W + 'type') not in (None, 'textWrapping'):
                raise RenderingRequired('Word page or column breaks require rendering')
            if node.tag == A + 'xfrm' and any(name in node.attrib for name in ('rot', 'flipH', 'flipV')):
                raise RenderingRequired('Transformed Word images require rendering')
            if node.tag == A + 'blip' and len(node):
                raise RenderingRequired('Word image effects require rendering')
            if node.tag == A + 'prstGeom' and node.get('prst') != 'rect':
                raise RenderingRequired('Word image masking requires rendering')
        styles = {}
        if 'word/styles.xml' in archive.namelist():
            for style in _checked_xml(archive.read('word/styles.xml')).findall(W + 'style'):
                name = style.find(W + 'name')
                styles[style.get(W + 'styleId')] = (name.get(W + 'val', '') if name is not None else '', style)
        numbers, abstract = {}, {}
        if 'word/numbering.xml' in archive.namelist():
            root = _checked_xml(archive.read('word/numbering.xml'))
            for definition in root.findall(W + 'abstractNum'):
                abstract[definition.get(W + 'abstractNumId')] = {
                    int(level.get(W + 'ilvl')): level for level in definition.findall(W + 'lvl')}
            for number in root.findall(W + 'num'):
                if number.find(W + 'lvlOverride') is not None:
                    raise RenderingRequired('List restarts require layout rendering')
                reference = number.find(W + 'abstractNumId')
                if reference is not None:
                    numbers[number.get(W + 'numId')] = abstract.get(reference.get(W + 'val'), {})
        counters = {}
        content, text = [], []

        def flush():
            value = ''.join(text).strip()
            text.clear()
            if value:
                content.append({'type': 'text', 'text': value})

        def paragraph_prefix(node):
            properties = node.find(W + 'pPr')
            if properties is None:
                return ''
            prefix = ''
            style_ref = properties.find(W + 'pStyle')
            if style_ref is not None:
                style_id, seen = style_ref.get(W + 'val'), set()
                while style_id:
                    if style_id in seen or len(seen) > 16:
                        raise RenderingRequired('Invalid Word style inheritance')
                    seen.add(style_id)
                    name, style = styles.get(style_id, ('', None))
                    if style is None:
                        raise RenderingRequired('Unresolved Word style requires rendering')
                    if style.find('.//' + W + 'numPr') is not None:
                        raise RenderingRequired('Style-based Word numbering requires rendering')
                    for property_group in (style.find(W + 'pPr'), style.find(W + 'rPr')):
                        if property_group is not None and any(
                                element.tag.startswith(W) and element.tag[len(W):] not in _SAFE_WORD_TAGS
                                for element in property_group.iter()):
                            raise RenderingRequired('This Word style requires layout rendering')
                    match = re.fullmatch(r'heading\s*([1-6])', name, re.I)
                    if match and not prefix:
                        prefix = '#' * int(match.group(1)) + ' '
                    base = style.find(W + 'basedOn')
                    style_id = base.get(W + 'val') if base is not None else None
            numbering = properties.find(W + 'numPr')
            if numbering is not None:
                number_ref, level_ref = numbering.find(W + 'numId'), numbering.find(W + 'ilvl')
                if number_ref is None:
                    raise RenderingRequired('Unresolved Word list requires rendering')
                number_id = number_ref.get(W + 'val')
                if number_id == '0':
                    return prefix
                level = int(level_ref.get(W + 'val', '0')) if level_ref is not None else 0
                definition = numbers.get(number_id, {}).get(level)
                if definition is None:
                    raise RenderingRequired('Unresolved Word list requires rendering')
                format_node, pattern_node = definition.find(W + 'numFmt'), definition.find(W + 'lvlText')
                number_format = format_node.get(W + 'val') if format_node is not None else None
                start = definition.find(W + 'start')
                counters[number_id, level] = counters.get((number_id, level), int(start.get(W + 'val', '1')) - 1 if start is not None else 0) + 1
                for counter_key in list(counters):
                    if counter_key[0] == number_id and counter_key[1] > level:
                        del counters[counter_key]
                if number_format == 'bullet':
                    marker = '-'
                elif number_format == 'decimal' and pattern_node is not None:
                    marker = pattern_node.get(W + 'val', '')
                    for index in range(9):
                        if '%' + str(index + 1) in marker:
                            if (number_id, index) not in counters:
                                raise RenderingRequired('Unresolved nested Word list requires rendering')
                            marker = marker.replace('%' + str(index + 1), str(counters[number_id, index]))
                else:
                    raise RenderingRequired('This list format requires layout rendering')
                prefix += '  ' * level + marker + ' '
            return prefix

        def walk(node, in_table=False):
            if node.tag == W + 'p':
                text.append(paragraph_prefix(node))
            if node.tag == W + 't':
                text.append((node.text or '').replace('|', '\\|') if in_table else node.text or '')
                return
            if node.tag == W + 'tab':
                text.append('\t')
            if node.tag == W + 'br':
                text.append('\n')
            if node.tag == A + 'blip':
                flush()
                content.append(next(images))
            if node.tag == W + 'tbl':
                text.append('\nTable:\n')
                for row in node.findall(W + 'tr'):
                    text.append('| ')
                    for cell in row.findall(W + 'tc'):
                        for child in cell:
                            walk(child, True)
                        text.append(' | ')
                    text.append('\n')
                text.append('\n')
                return
            for child in node:
                walk(child, in_table)
            if node.tag == W + 'p':
                text.append(' ' if in_table else '\n')
        walk(document)
        flush()
        return _check_budget(content)


def _ram_root():
    root = Path(os.environ.get('LMS_MCP_RAM_DIR', '/dev/shm'))
    if not root.is_dir():
        raise RenderingRequired('The Office RAM volume is unavailable')
    # Refuse disk-backed overrides; inspect the longest containing mount.
    mounts = []
    for line in Path('/proc/mounts').read_text().splitlines():
        fields = line.split()
        mount = Path(fields[1].replace('\\040', ' '))
        if root.resolve().is_relative_to(mount):
            mounts.append((len(str(mount)), fields[2]))
    if not mounts or max(mounts)[1] != 'tmpfs':
        raise RenderingRequired('Office processing requires an in-memory tmpfs volume')
    return root


def render_office(data, file_type, deadline):
    root = _ram_root()
    if not shutil.which('libreoffice'):
        raise RenderingRequired('Office rendering is unavailable')
    acquired = False
    while not acquired:
        acquired = _RENDER_SLOT.acquire(timeout=min(0.1, deadline.remaining()))
    try:
        with tempfile.TemporaryDirectory(prefix='lms-mcp-', dir=root) as directory:
            path = Path(directory)
            os.chmod(path, 0o700)
            profile = path / 'profile'
            profile.mkdir()
            (profile / 'user').mkdir()
            (profile / 'user' / 'registrymodifications.xcu').write_text('''<?xml version="1.0"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>false</value></prop><prop oor:name="Field" oor:op="fuse"><value>false</value></prop></item>
</oor:items>''')
            source = path / ('assignment.' + file_type)
            source.write_bytes(data)
            environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': directory,
                           'TMPDIR': directory, 'XDG_CACHE_HOME': directory,
                           'SAL_USE_VCLPLUGIN': 'svp', 'LANG': 'C.UTF-8'}
            command = ['libreoffice', '-env:UserInstallation=' + profile.as_uri(), '--headless', '--nologo',
                       '--nodefault', '--nofirststartwizard', '--norestore', '--convert-to', 'pdf',
                       '--outdir', directory, str(source)]
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       env=environment, start_new_session=True)
            try:
                while process.poll() is None:
                    try:
                        process.wait(timeout=min(0.1, deadline.remaining()))
                    except subprocess.TimeoutExpired:
                        pass
                deadline.remaining()
                output = path / 'assignment.pdf'
                if process.returncode != 0 or not output.is_file():
                    raise RenderingRequired('Office rendering failed')
                if output.stat().st_size > MAX_SOURCE_BYTES:
                    raise ValueError('Rendered document exceeds the processing limit')
                return output.read_bytes()
            finally:
                # Kill the whole process group, including LibreOffice child processes.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2)
    finally:
        _RENDER_SLOT.release()


def render_pdf(data, deadline, *, zip_bundle=False):
    """Native PDF rendering cannot outlive cancellation or hold a Python thread forever."""
    while not _RENDER_SLOT.acquire(timeout=min(0.1, deadline.remaining())):
        pass
    try:
        environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8'}
        # An anonymous RAM file avoids a partial stdin write when communicate
        # times out and retries. Only the bounded stdout pipe needs draining.
        with tempfile.TemporaryFile(dir=_ram_root()) as source:
            source.write(data)
            source.seek(0)
            command = [sys.executable, '-m', 'lms_mcp.pdf_worker', str(deadline.remaining())]
            if zip_bundle:
                command.append('--zip-bundle')
            process = subprocess.Popen(command,
                stdin=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=environment, start_new_session=True)
            try:
                while True:
                    try:
                        output, _ = process.communicate(timeout=min(.1, deadline.remaining()))
                        break
                    except subprocess.TimeoutExpired:
                        pass
                deadline.remaining()
                if process.returncode != 0:
                    raise ValueError('PDF could not be rendered within document limits')
                return _check_budget(json.loads(output))
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=2)
    finally:
        _RENDER_SLOT.release()


def prepare_document(data, file_type, deadline, *, zip_bundle=False):
    if file_type == 'pdf':
        return render_pdf(data, deadline, zip_bundle=zip_bundle), 'pdf_page_images'
    if file_type == 'docx':
        try:
            blocks = qualified_docx(data)
            deadline.remaining()
            return blocks, 'docx_text_and_images'
        except RenderingRequired:
            pass
    rendered = render_office(data, file_type, deadline)
    return render_pdf(rendered, deadline, zip_bundle=zip_bundle), 'office_rendered_page_images'


def prepare_assignment_content(data, deadline):
    try:
        deadline.remaining()
        file_type = detect_format(data)
        if file_type == 'zip':
            blocks, method = prepare_zip(data, deadline)
        else:
            blocks, method = prepare_document(data, file_type, deadline)
        deadline.remaining()
        return blocks, method
    except ConnectorError:
        raise
    except RenderingRequired as error:
        raise ConnectorError('unsupported_content', str(error)) from None
    except (ValueError, BadZipFile, DecompressionError, ET.ParseError, KeyError, RuntimeError):
        raise ConnectorError('processing_limit', 'The assignment is invalid or exceeds document processing limits.') from None
