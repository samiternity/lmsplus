"""Pure, deterministic lecture manifests. No network or archive generation."""
import hashlib
import json
import re
from collections import OrderedDict

TYPE_EXTENSIONS = {'PDF': '.pdf', 'PPT': '.ppt', 'PPTX': '.pptx', 'DOC': '.doc',
                   'DOCX': '.docx', 'XLS': '.xls', 'XLSX': '.xlsx', 'ZIP': '.zip'}


def lecture_manifest(file_urls):
    records = []
    for index, (note, url, real_name, file_type) in enumerate(file_urls):
        week = note.get('week_no') if isinstance(note, dict) else getattr(note, 'week_no', None)
        try:
            week = int(week)
            if week < 1:
                week = None
        except (TypeError, ValueError):
            week = None
        filename = real_name or url.split('/')[-1].split('?')[0] or f'file_{index + 1}'
        name = re.sub(r'[^\w.\-]', '_', filename).strip('_.') or f'file_{index + 1}'
        if not re.search(r'\.[a-zA-Z0-9]{2,5}$', name):
            ext = TYPE_EXTENSIONS.get(str(file_type).upper(), '')
            match = re.search(r'\.(pdf|pptx|ppt|docx|doc|xlsx|xls|zip)(?:$|[?&])', url, re.I)
            name += ext or (f'.{match.group(1).lower()}' if match else '')
        records.append({'url': url, 'name': name, 'week': week, '_index': index})
    records.sort(key=lambda item: (item['week'] is None, item['week'] or 0, item['_index']))
    used = set()
    for index, file in enumerate(records):
        prefix = f"Week{file['week']:02d}" if file['week'] else f'File{index + 1:02d}'
        base = f"{prefix}_{file['name']}"
        name = base
        stem, dot, extension = base.rpartition('.')
        counter = 2
        while name in used:
            name = f'{stem}_{counter}.{extension}' if dot else f'{base}_{counter}'
            counter += 1
        file['name'] = name
        file['index'] = index
        del file['_index']
        used.add(name)
    digest = hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return records, digest


def lecture_parts(files, limit=20, start=0, end=None):
    """Keep weeks together where possible, with stable global file ranges."""
    end = len(files) if end is None else end
    if not 1 <= limit <= 20 or not 0 <= start < end <= len(files):
        raise ValueError('Invalid lecture selection.')
    groups = OrderedDict()
    for file in files[start:end]:
        # Unknown weeks stay individual; do not pretend they share a week.
        key = file['week'] if file['week'] is not None else f"unknown-{file['index']}"
        groups.setdefault(key, []).append(file)
    parts, current = [], []
    for group in groups.values():
        if current and len(current) + len(group) > limit:
            parts.append(current)
            current = []
        for file in group:
            if len(current) == limit:
                parts.append(current)
                current = []
            current.append(file)
    if current:
        parts.append(current)
    return parts
